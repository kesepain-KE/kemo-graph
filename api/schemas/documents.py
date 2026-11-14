from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

from api.schemas.common import StrictRequest
class IngestRequest(StrictRequest):
    paths: list[str] | None = None
    mode: Literal["graph", "rag", "both"] = "both"
    retry_failed: bool = False

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not path.strip() for path in value):
            raise ValueError("paths 中不能包含空路径")
        return [path.strip() for path in value]

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

class DocumentContentUpdateRequest(StrictRequest):
    content: str
    expected_content_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("expected_content_hash")
    @classmethod
    def validate_expected_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("expected_content_hash 必须是 SHA-256 十六进制字符串")
        return normalized

class DocumentBatchDeleteRequest(StrictRequest):
    source_ids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: list[str]) -> list[str]:
        normalized = [source_id.strip() for source_id in value]
        if any(not source_id for source_id in normalized):
            raise ValueError("source_ids 中不能包含空值")
        return list(dict.fromkeys(normalized))

class ImportResponseData(BaseModel):
    source_id: str
    original_filename: str
    detected_format: str
    markdown_relative_path: str
    conversion_status: Literal["completed"]
    ingest_status: Literal["pending", "completed", "failed"]
    size: int = Field(ge=0)
    origin_hash: str | None = None
    content_hash: str | None = None
    origin_modified_at: str | None = None
    ingest: dict[str, Any] | None = None
    ingest_error: str | None = None

class LibraryPath(StrictRequest):
    path: str = Field(default="", max_length=2048)

class LibraryCreate(StrictRequest):
    directory: str = Field(default="", max_length=2048)
    name: str = Field(min_length=1, max_length=160)
    kind: str

class LibraryMove(LibraryPath):
    destination: str = Field(min_length=1, max_length=2048)

class LibraryTransfer(LibraryPath):
    directory: str = Field(default="", max_length=2048)
    mode: Literal["copy", "cut"]
    conflict: Literal["error", "keep_both"] = "error"

class LibraryBuild(LibraryPath):
    reprocess: bool = False

class ProcessedContentUpdate(StrictRequest):
    content: str
    expected_content_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
