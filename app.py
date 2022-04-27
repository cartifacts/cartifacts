from __future__ import annotations

import dataclasses
import os
import re
from collections import defaultdict
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Mapping, TextIO, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from botocore.config import Config as BotoConfig
from botocore.response import StreamingBody
from flask import Flask, Response, json, render_template, request

from cartifacts.types import ArtifactMetadata, BuildMetadata, PipelineMetadata
from cartifacts.util import isonlydigits, s3_cp
from cartifacts.vendor.flask_boto3 import Boto3


if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ListObjectsV2OutputTypeDef


PerStepArtifactMetadata = Dict[str, List[ArtifactMetadata]]
PerStageArtifactMetadata = Dict[str, PerStepArtifactMetadata]


CHUNK_SIZE = 16 * 1024
UUID_REGEX = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


app = Flask(__name__)
app.config["BOTO3_SERVICES"] = ["s3"]

config_path = Path(os.environ.get("CARTIFACTS_CONFIG", "./config.json"))
if config_path.exists():
    print(f" * Loading config from {config_path}")
    app.config.from_mapping(json.loads(config_path.read_text()))

app.config["BOTO3_OPTIONAL_PARAMS"]["s3"]["kwargs"]["config"] = BotoConfig(
    retries={
        "max_attempts": 0,
        "mode": "standard",
    },
)


boto = Boto3(app)


APP_TZ = ZoneInfo(app.config["CARTIFACTS_TZ"])
DT_FORMAT = app.config.get("CARTIFACTS_DT_FORMAT", "%Y-%m-%d %H:%M:%S")
BUCKET = app.config["CARTIFACTS_BUCKET"]


@app.template_filter()
def dtformat(timestamp: int) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=APP_TZ)
    return dt.strftime(DT_FORMAT)


@app.template_filter()
def desigil(identifier: str) -> str:
    return identifier.replace("$", "/")


@dataclasses.dataclass(frozen=True, order=True, slots=True)
class BuildId:
    build_id: str = dataclasses.field(compare=False)
    created_at: int

    def __eq__(self, other) -> bool:
        if not isinstance(other, BuildId):
            return NotImplemented

        return self.build_id == other.build_id and self.created_at == other.created_at

    @property
    def build_id_label(self) -> str:
        return self.build_id.replace("$", "/")


def bad_request(msg: str, /, *, code: int = HTTPStatus.BAD_REQUEST) -> Response:
    return Response(response=msg + "\n", status=code, content_type="text/plain; charset=utf-8")


def get_pipeline_metadata(s3: S3Client, pipeline: str) -> Mapping[str, str]:
    response = s3.get_object(
        Bucket=BUCKET,
        Key=f"artifacts/pipelines/{pipeline}/metadata.json",
    )
    return json.load(cast(TextIO, response["Body"]))


def get_build_metadata(s3: S3Client, pipeline: str, build_id: str) -> Mapping[str, str]:
    response = s3.get_object(
        Bucket=BUCKET,
        Key=f"artifacts/pipelines/{pipeline}/builds/{build_id}/metadata.json",
    )
    return json.load(cast(TextIO, response["Body"]))


@app.get("/")
def home():
    s3: S3Client = boto.clients.get("s3")

    pipelines_prefix = "artifacts/pipelines/"
    pipelines_response = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=pipelines_prefix,
        Delimiter="/",
    )
    pipeline_names: List[str] = list(s3_cp(pipelines_response))
    while pipelines_response["IsTruncated"]:
        pipelines_response = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=pipelines_prefix,
            Delimiter="/",
            ContinuationToken=pipelines_response["NextContinuationToken"],
        )
        pipeline_names.extend(s3_cp(pipelines_response))

    return render_template("home.html", pipeline_names=pipeline_names, timezone=APP_TZ)


@app.get("/pipeline/<pipeline>")
def pipeline_view(pipeline: str):
    s3: S3Client = boto.clients.get("s3")

    metadata = get_pipeline_metadata(s3, pipeline)

    builds_prefix = f"artifacts/pipelines/{pipeline}/builds/"
    builds_response = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=builds_prefix,
        Delimiter="^",
    )

    build_sortkeys: List[str] = list(s3_cp(builds_response))
    while builds_response["IsTruncated"]:
        builds_response = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=builds_prefix,
            Delimiter="^",
            ContinuationToken=builds_response["NextContinuationToken"],
        )
        build_sortkeys.extend(s3_cp(builds_response))

    def sep(build_sortkey: str) -> BuildId:
        parts = build_sortkey.rpartition("$")
        return BuildId(parts[0], int(parts[2][:-1]))

    build_ids_sorted = sorted(map(sep, build_sortkeys), reverse=True)

    return render_template(
        "pipeline/view.html",
        pipeline=pipeline,
        metadata=metadata,
        build_ids=build_ids_sorted,
        timezone=APP_TZ,
    )


@app.get("/pipeline/<pipeline>/build/<build_id>")
def build_view(pipeline: str, build_id: str):
    s3: S3Client = boto.clients.get("s3")

    build_prefix = f"artifacts/pipelines/{pipeline}/builds/{build_id}/"
    artifact_metadata_matcher = re.compile(
        r"^"
        + re.escape(build_prefix)
        + r"stages/([^/]+)/steps/([^/]+)/("
        + UUID_REGEX
        + r")\.json$"
    )

    artifacts_metadata: PerStageArtifactMetadata = defaultdict(lambda: defaultdict(list))

    def process_build_response(response: ListObjectsV2OutputTypeDef):
        for s3_obj in response["Contents"]:
            artifact_metadata_match = artifact_metadata_matcher.match(s3_obj["Key"])
            if not artifact_metadata_match:
                continue

            stage_id, step_id, artifact_id = artifact_metadata_match.groups()

            artifact_metadata_response = s3.get_object(
                Bucket=BUCKET,
                Key=s3_obj["Key"],
            )
            artifact_metadata = json.load(cast(TextIO, artifact_metadata_response["Body"]))
            artifacts_metadata[stage_id][step_id].append(artifact_metadata)

    build_response = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=build_prefix,
    )
    process_build_response(build_response)
    while build_response["IsTruncated"]:
        build_response = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=build_prefix,
            ContinuationToken=build_response["NextContinuationToken"],
        )
        process_build_response(build_response)

    pipeline_metadata = get_pipeline_metadata(s3, pipeline)
    build_metadata = get_build_metadata(s3, pipeline, build_id)

    return render_template(
        "build/view.html",
        pipeline=pipeline,
        build_id=build_id,
        pipeline_metadata=pipeline_metadata,
        build_metadata=build_metadata,
        artifacts_metadata=artifacts_metadata,
        timezone=APP_TZ,
    )


@app.get("/pipeline/<pipeline>/build/<build_id>/<stage_id>/<step_id>/<artifact_id>")
def artifact_download(pipeline: str, build_id: str, stage_id: str, step_id: str, artifact_id: str):
    s3: S3Client = boto.clients.get("s3")

    artifact_key = f"artifacts/pipelines/{pipeline}/builds/{build_id}/stages/{stage_id}/steps/{step_id}/{artifact_id}"
    artifact_metadata_key = f"{artifact_key}.json"

    artifact_metadata_response = s3.get_object(
        Bucket=BUCKET,
        Key=artifact_metadata_key,
    )
    artifact_metadata = json.load(cast(TextIO, artifact_metadata_response["Body"]))

    filename = os.path.basename(artifact_metadata["artifact_path"])
    content_disposition = f'attachment; filename="{filename}"'
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": BUCKET,
            "Key": artifact_key,
            "ResponseContentDisposition": content_disposition,
            "ResponseContentType": artifact_metadata["content_type"],
        },
        ExpiresIn=60,
    )

    return Response(
        response="",
        status=HTTPStatus.FOUND,
        mimetype="text/plain",
        headers={"Location": url},
    )


@app.post("/api/upload")
def api_upload():
    pipeline_header = request.headers.get("Cartifacts-Pipeline")
    build_id_header = request.headers.get("Cartifacts-Build-ID")
    build_created_header = request.headers.get("Cartifacts-Build-Created")
    build_link_header = request.headers.get("Cartifacts-Build-Link")
    stage_id_header = request.headers.get("Cartifacts-Stage-ID")
    step_id_header = request.headers.get("Cartifacts-Step-ID")
    artifact_path_header = request.headers.get("Cartifacts-Artifact-Path")
    artifact_md5_header = request.headers.get("Cartifacts-Artifact-MD5")
    repo_name_header = request.headers.get("Cartifacts-Repo-Name")
    repo_link_header = request.headers.get("Cartifacts-Repo-Link")

    if (
        not pipeline_header
        or not build_id_header
        or not build_created_header
        or not build_link_header
        or not stage_id_header
        or not step_id_header
        or not artifact_path_header
        or not artifact_md5_header
        or not repo_name_header
        or not repo_link_header
    ):
        return bad_request("One or more metadata headers are missing or empty.")

    content_length = request.content_length
    if not content_length:
        return bad_request(
            "Content Length header must be specified and greater than zero.",
            code=HTTPStatus.LENGTH_REQUIRED,
        )
    content_type = request.content_type
    if not content_type:
        return bad_request("Content Type header must be specified and non-empty.")
    if not isonlydigits(build_created_header):
        return bad_request("Build Created header must be a plain UNIX timestamp.")

    pipeline = pipeline_header.replace("/", "$")
    build_id = build_id_header.replace("/", "$")
    stage_id = stage_id_header.replace("/", "$")
    step_id = step_id_header.replace("/", "$")

    artifact_id = str(uuid4())
    artifact_metadata: ArtifactMetadata = {
        "artifact_id": artifact_id,
        "artifact_path": artifact_path_header,
        "content_length": content_length,
        "content_type": content_type,
        "pipeline": pipeline_header,
        "build_id": build_id_header,
        "stage_id": stage_id_header,
        "step_id": step_id_header,
    }
    build_metadata: BuildMetadata = {
        "pipeline": pipeline_header,
        "build_id": build_id_header,
        "build_created": int(build_created_header),
        "build_link": build_link_header,
    }
    pipeline_metadata: PipelineMetadata = {
        "pipeline": pipeline_header,
        "repo_name": repo_name_header,
        "repo_link": repo_link_header,
    }

    pipeline_key_prefix = f"artifacts/pipelines/{pipeline}"
    builds_key_prefix = f"{pipeline_key_prefix}/builds"
    build_key_prefix = f"{builds_key_prefix}/{build_id}"
    artifact_key_prefix = f"{build_key_prefix}/stages/{stage_id}/steps/{step_id}"
    artifact_file_key = f"{artifact_key_prefix}/{artifact_id}"
    artifact_metadata_key = f"{artifact_key_prefix}/{artifact_id}.json"
    build_metadata_key = f"{build_key_prefix}/metadata.json"
    builds_sort_key = f"{builds_key_prefix}/{build_id}${build_created_header}^"
    pipeline_metadata_key = f"{pipeline_key_prefix}/metadata.json"

    s3: S3Client = boto.clients.get("s3")

    s3.put_object(
        Bucket=BUCKET,
        Key=artifact_file_key,
        Body=StreamingBody(request.stream, content_length),
        ContentMD5=artifact_md5_header,
        ContentLength=content_length,
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=artifact_metadata_key,
        Body=json.dumps(artifact_metadata),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=build_metadata_key,
        Body=json.dumps(build_metadata),
        ContentType="application.json",
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=builds_sort_key,
        Body=build_id_header,
        ContentType="text/plain",
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=pipeline_metadata_key,
        Body=json.dumps(pipeline_metadata),
        ContentType="application/json",
    )

    return Response(
        response="Success!\n", status=HTTPStatus.OK, content_type="text/plain; charset=utf-8"
    )
