from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from botocore.config import Config as BotoConfig
from botocore.response import StreamingBody
from flask import Flask, Response, json, request, stream_with_context

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


def bad_request(msg: str) -> Response:
    return Response(
        response=msg + "\n",
        status=HTTPStatus.BAD_REQUEST,
        content_type="text/plain; charset=utf-8"
    )


@app.route("/api/upload", methods=["POST"])
def upload():
    pipeline_header = request.headers.get("Cartifacts-Pipeline")
    build_id_header = request.headers.get("Cartifacts-Build-ID")
    stage_id_header = request.headers.get("Cartifacts-Stage-ID")
    step_id_header = request.headers.get("Cartifacts-Step-ID")
    artifact_path_header = request.headers.get("Cartifacts-Artifact-Path")
    artifact_md5_header = request.headers.get("Cartifacts-Artifact-MD5")

    all_headers = (pipeline_header, build_id_header, stage_id_header, step_id_header, artifact_path_header, artifact_md5_header)

    if any(map(lambda val: not bool(val), all_headers)):
        return bad_request("One or more metadata headers are missing.")

    content_length = request.content_length
    if not content_length:
        return bad_request("Content Length header must be specified and greater than zero.")
    content_type = request.content_type
    if not content_type:
        return bad_request("Content Type header must be specified and non-empty.")

    pipeline = pipeline_header.replace("/", "$")
    build_id = build_id_header.replace("/", "$")
    stage_id = stage_id_header.replace("/", "$")
    step_id = step_id_header.replace("/", "$")

    metadata = {
        "artifact_path": artifact_path_header,
        "content_length": content_length,
        "content_type": content_type,
        "pipeline": pipeline_header,
        "build_id": build_id_header,
        "stage_id": stage_id_header,
        "step_id": step_id_header,
    }

    key_prefix = f"artifacts/pipelines/{pipeline}/builds/{build_id}/stages/{stage_id}/steps/{step_id}"
    file_id = str(uuid4())
    file_key = f"{key_prefix}/{file_id}"
    metadata_key = f"{key_prefix}/{file_id}.json"

    s3: S3Client = boto.clients.get("s3")

    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=file_key,
        Body=StreamingBody(request.stream, content_length),
        ContentMD5=artifact_md5_header,
        ContentLength=content_length,
    )
    s3.put_object(
        Bucket=app.config["CARTIFACTS_BUCKET"],
        Key=metadata_key,
        Body=json.dumps(metadata),
        ContentType="application/json",
    )

    return Response(response="Success!\n", status=HTTPStatus.OK, content_type="text/plain")
