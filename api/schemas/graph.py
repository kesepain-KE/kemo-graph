from __future__ import annotations
from pydantic import Field

from api.schemas.common import StrictRequest
class NodeSummaryUpdateRequest(StrictRequest):
    summary: str = Field(max_length=200_000)
    expected_summary: str = Field(max_length=200_000)
