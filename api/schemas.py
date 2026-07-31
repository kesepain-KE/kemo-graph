"""外部 API 和 Web 入口共用的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestRequest(StrictRequest):
    paths: list[str] | None = None
    mode: Literal["graph", "rag", "both"] = "both"

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not path.strip() for path in value):
            raise ValueError("paths 中不能包含空路径")
        return [path.strip() for path in value]


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


class ErrorDetail(BaseModel):
    code: str
    message: str


class UploadRequest(StrictRequest):
    content: str
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename 不能为空")
        if any(c in value for c in ("/", "\\", "..")):
            raise ValueError("filename 不能包含路径分隔符")
        return value.strip()


class ImportResponseData(BaseModel):
    source_id: str
    original_filename: str
    detected_format: str
    markdown_relative_path: str
    conversion_status: Literal["completed"]
    ingest_status: Literal["pending", "completed", "failed"]
    size: int = Field(ge=0)
    ingest: dict[str, Any] | None = None
    ingest_error: str | None = None


class ConfigSaveRequest(StrictRequest):
    """接受完整配置 JSON 的请求体，字段由 AppConfig 模型校验。"""
    model_config = ConfigDict(extra="allow")


class APIResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: ErrorDetail | None = None
