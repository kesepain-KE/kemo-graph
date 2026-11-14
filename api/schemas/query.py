from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

from api.schemas.common import StrictRequest
class GraphQueryRequest(StrictRequest):
    query: str
    depth: int = Field(default=3, ge=1, le=10)
    direction: Literal["forward", "backward", "both"] = "both"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空")
        return value.strip()

class RAGQueryRequest(StrictRequest):
    query: str
    top_k: int | None = Field(default=None, ge=1, le=100)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空")
        return value.strip()

class HybridQueryRequest(StrictRequest):
    query: str
    graph_depth: int = Field(default=3, ge=1, le=10)
    rag_top_k: int | None = Field(default=None, ge=1, le=100)
    graph_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rag_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空")
        return value.strip()

class AnswerQueryRequest(HybridQueryRequest):
    """使用混合检索上下文生成 LLM 回答。"""

class GlobalQueryRequest(StrictRequest):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空")
        return value.strip()

class CachedQueryItem(BaseModel):
    cache_key: str
    query_mode: Literal["answer", "graph", "rag", "hybrid", "global"] | str
    query: str
    normalized_query: str
    params: dict[str, Any]
    params_json: str
    result_size: int = Field(ge=0)
    state_hash: str
    is_stale: bool
    hit_count: int = Field(ge=0)
    created_at: str
    updated_at: str
    last_hit_at: str | None = None

class CachedQueryListResponse(BaseModel):
    items: list[CachedQueryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
