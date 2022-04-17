from __future__ import annotations

import dataclasses
import os
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, List
from uuid import uuid4
from zoneinfo import ZoneInfo

from botocore.config import Config as BotoConfig
from botocore.response import StreamingBody
from flask import Flask, Response, json, render_template, request, stream_with_context

from cartifacts.util import istimestamp, s3_cp
from cartifacts.vendor.flask_boto3 import Boto3

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


CHUNK_SIZE = 16 * 1024


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


@dataclasses.dataclass(frozen=True, order=True, slots=True)
class BuildId:
    build_id: str = dataclasses.field(compare=False)
    sortkey: str

    def __eq__(self, other) -> bool:
        if not isinstance(other, BuildId):
            return NotImplemented

        return self.build_id == other.build_id and self.sortkey == other.sortkey

    @property
    def build_id_label(self) -> str:
        return self.build_id.replace("$", "/")

    @property
    def created_at(self) -> str:
        timestamp = int(self.sortkey)
        dt = datetime.fromtimestamp(timestamp, tz=APP_TZ)
        return dt.strftime(DT_FORMAT)


def bad_request(msg: str) -> Response:
    return Response(
        response=msg + "\n",
        status=HTTPStatus.BAD_REQUEST,
        content_type="text/plain; charset=utf-8"
    )


@app.route("/", methods=["GET"])
def home():
    s3: S3Client = boto.clients.get("s3")

    pipelines_prefix = "artifacts/pipelines/"
    pipelines_response = s3.list_objects_v2(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Prefix=pipelines_prefix,
        Delimiter="/",
    )
    pipeline_names: List[str] = list(s3_cp(pipelines_response))
    while pipelines_response["IsTruncated"]:
        pipelines_response = s3.list_objects_v2(
            Bucket=app.config["CARTIFACTS_BUCKET"],
            Prefix=pipelines_prefix,
            Delimiter="/",
            ContinuationToken=pipelines_response["NextContinuationToken"],
        )
        pipeline_names.extend(s3_cp(pipelines_response))

    return render_template("home.html", pipeline_names=pipeline_names)


@app.route("/pipeline/<pipeline>", methods=["GET"])
def pipeline_view(pipeline: str):
    s3: S3Client = boto.clients.get("s3")

    metadata_response = s3.get_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=f"artifacts/pipelines/{pipeline}/metadata.json",
    )
    metadata = json.load(metadata_response["Body"])

    builds_prefix = f"artifacts/pipelines/{pipeline}/builds/"
    builds_response = s3.list_objects_v2(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Prefix=builds_prefix,
        Delimiter="^",
    )
    from pprint import pprint
    pprint(builds_response)
    build_sortkeys: List[str] = list(s3_cp(builds_response))
    while builds_response["IsTruncated"]:
        builds_response = s3.list_objects_v2(
            Bucket=app.config["CARTIFACTS_BUCKET"],
            Prefix=builds_prefix,
            Delimiter="^",
            ContinuationToken=builds_response["NextContinuationToken"],
        )
        build_sortkeys.extend(s3_cp(builds_response))

    def sep(build_sortkey: str) -> BuildId:
        parts = build_sortkey.rpartition("$")
        return BuildId(parts[0], parts[2][:-1])

    build_ids_sorted = sorted(map(sep, build_sortkeys))

    return render_template("pipeline/view.html", pipeline=pipeline, metadata=metadata, build_ids=build_ids_sorted)


@app.route("/pipeline/<pipeline>/build/<build_id>", methods=["GET"])
def build_view(pipeline: str, build_id: str):
    pass


@app.route("/api/upload", methods=["POST"])
def api_upload():
    pipeline_header = request.headers.get("Cartifacts-Pipeline")
    build_id_header = request.headers.get("Cartifacts-Build-ID")
    build_created_header = request.headers.get("Cartifacts-Build-Created")
    stage_id_header = request.headers.get("Cartifacts-Stage-ID")
    step_id_header = request.headers.get("Cartifacts-Step-ID")
    artifact_path_header = request.headers.get("Cartifacts-Artifact-Path")
    artifact_md5_header = request.headers.get("Cartifacts-Artifact-MD5")
    repo_name_header = request.headers.get("Cartifacts-Repo-Name")
    repo_link_header = request.headers.get("Cartifacts-Repo-Link")

    all_headers = (
        pipeline_header, build_id_header, build_created_header, stage_id_header, step_id_header,
        artifact_path_header, artifact_md5_header, repo_name_header, repo_link_header,
    )

    if any(map(lambda val: not bool(val), all_headers)):
        return bad_request("One or more metadata headers are missing.")

    content_length = request.content_length
    if not content_length:
        return bad_request("Content Length header must be specified and greater than zero.")
    content_type = request.content_type
    if not content_type:
        return bad_request("Content Type header must be specified and non-empty.")
    if not istimestamp(build_created_header):
        return bad_request("Build Created header must be a plain UNIX timestamp.")

    pipeline = pipeline_header.replace("/", "$")
    build_id = build_id_header.replace("/", "$")
    stage_id = stage_id_header.replace("/", "$")
    step_id = step_id_header.replace("/", "$")

    artifact_metadata = {
        "artifact_path": artifact_path_header,
        "content_length": content_length,
        "content_type": content_type,
        "pipeline": pipeline_header,
        "build_id": build_id_header,
        "stage_id": stage_id_header,
        "step_id": step_id_header,
    }
    build_metadata = {
        "pipeline": pipeline_header,
        "build_id": build_id_header,
        "build_created": build_created_header,
    }
    pipeline_metadata = {
        "pipeline": pipeline_header,
        "repo_name": repo_name_header,
        "repo_link": repo_link_header,
    }

    pipeline_key_prefix = f"artifacts/pipelines/{pipeline}"
    builds_key_prefix = f"{pipeline_key_prefix}/builds"
    build_key_prefix = f"{builds_key_prefix}/{build_id}"
    artifact_key_prefix = f"{build_key_prefix}/stages/{stage_id}/steps/{step_id}"
    file_id = str(uuid4())
    artifact_file_key = f"{artifact_key_prefix}/{file_id}"
    artifact_metadata_key = f"{artifact_key_prefix}/{file_id}.json"
    build_metadata_key = f"{build_key_prefix}/metadata.json"
    builds_sort_key = f"{builds_key_prefix}/{build_id}${build_created_header}^"
    pipeline_metadata_key = f"{pipeline_key_prefix}/metadata.json"

    s3: S3Client = boto.clients.get("s3")

    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=artifact_file_key,
        Body=StreamingBody(request.stream, content_length),
        ContentMD5=artifact_md5_header,
        ContentLength=content_length,
    )
    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=artifact_metadata_key,
        Body=json.dumps(artifact_metadata),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=build_metadata_key,
        Body=json.dumps(build_metadata),
        ContentType="application.json",
    )
    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=builds_sort_key,
        Body=build_id_header,
        ContentType="text/plain",
    )
    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=pipeline_metadata_key,
        Body=json.dumps(pipeline_metadata),
        ContentType="application/json",
    )

    return Response(response="Success!\n", status=HTTPStatus.OK, content_type="text/plain")
