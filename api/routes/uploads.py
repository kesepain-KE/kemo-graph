from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import UploadFile
from core.documents.formats import MAX_IMPORT_BYTES, SUPPORTED_IMPORT_SUFFIXES
from core.documents.errors import DocumentImportPathError, DocumentIngestError, DocumentTooLargeError, UnsupportedDocumentFormatError


async def _stage_uploaded_document(
    file: UploadFile,
    temporary_dir: Path,
) -> tuple[str, Path]:
    """Validate and stream one multipart document into a short-lived staging file."""

    filename = _validated_upload_filename(file.filename)
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_IMPORT_SUFFIXES:
        raise UnsupportedDocumentFormatError(
            f"不支持的文档格式：{suffix or '<无扩展名>'}"
        )

    staged = temporary_dir / filename
    total = 0
    try:
        with staged.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise DocumentTooLargeError(
                        f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限：{filename}"
                    )
                handle.write(chunk)
    finally:
        await file.close()
    return filename, staged

def _raise_import_failure(result: dict[str, Any]) -> None:
    if result.get("ingest_status") == "failed":
        raise DocumentIngestError(
            str(result.get("ingest_error") or "文档已转换，但知识库整理失败")
        )

def _validated_upload_filename(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("上传文件名不能为空")
    filename = value.strip()
    if filename != Path(filename).name or any(
        part in filename for part in ("/", "\\", "..")
    ):
        raise DocumentImportPathError("上传文件名不能包含路径")
    return filename
