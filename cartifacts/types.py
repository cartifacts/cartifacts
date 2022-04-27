from __future__ import annotations

from typing import TypedDict


class ArtifactMetadata(TypedDict):
    artifact_id: str
    artifact_path: str
    content_length: int
    content_type: str
    pipeline: str
    build_id: str
    stage_id: str
    step_id: str


class BuildMetadata(TypedDict):
    pipeline: str
    build_id: str
    build_created: int
    build_link: str


class PipelineMetadata(TypedDict):
    pipeline: str
    repo_name: str
    repo_link: str


__all__ = (
    "ArtifactMetadata",
    "BuildMetadata",
    "PipelineMetadata",
)
