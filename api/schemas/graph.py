from __future__ import annotations
from pydantic import ConfigDict, Field

from api.schemas.common import StrictRequest


class NodeCreateRequest(StrictRequest):
    model_config = ConfigDict(str_strip_whitespace=True)

    keyword: str = Field(min_length=2, max_length=20)
    summary: str = Field(min_length=1, max_length=200_000)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_id: str = Field(default="manual", min_length=1, max_length=200)
    content_hash: str = Field(default="manual", min_length=1)


class RelationCreateRequest(StrictRequest):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_node_id: str = Field(min_length=1)
    relation: str = Field(min_length=1, max_length=100)
    target_node_id: str = Field(min_length=1)
    evidence_weight: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    source_id: str = Field(default="manual", min_length=1, max_length=200)
    content_hash: str = Field(default="manual", min_length=1)


class NodeSummaryUpdateRequest(StrictRequest):
    summary: str = Field(max_length=200_000)
    expected_summary: str = Field(max_length=200_000)
