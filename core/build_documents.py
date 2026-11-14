"""Read and edit derived Markdown without changing the original document."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .db import connect_sources
from .ingestor import DocumentNotFoundError
from .ingestor._utils import _safe_source_path
from core.documents.errors import DocumentContentConflictError, DocumentTooLargeError
from core.documents.formats import MAX_IMPORT_BYTES
from core.common import write_bytes_atomic as _write_bytes_atomic
from .locks import get_knowledge_base_lock


def processed_source(owner, source_id: str):
    if not owner.paths.sources_db.is_file():
        raise DocumentNotFoundError("文档不存在")
    connection = connect_sources(owner.paths)
    try:
        row = connection.execute(
            "SELECT * FROM sources WHERE source_id = ? AND exists_status = 'active'",
            (source_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise DocumentNotFoundError("文档不存在")
    path = _safe_source_path(owner.external_dir, row["relative_path"])
    if not path.is_file():
        raise DocumentNotFoundError("预处理 Markdown 不存在，请在知识库刷新原文件")
    return row, path


def read_processed(owner, source_id: str) -> dict[str, Any]:
    row, path = processed_source(owner, source_id)
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("预处理 Markdown 不是有效的 UTF-8 文本") from exc
    return {
        "source_id": source_id,
        "source_path": row["library_path"],
        "original_path": row["original_path"],
        "relative_path": row["relative_path"],
        "processed_path": str(path),
        "size": len(raw),
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(raw).hexdigest(),
        "graph_status": row["graph_status"],
        "rag_status": row["rag_status"],
        "content": content,
    }


def update_processed(owner, source_id: str, content: str, expected_hash: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_IMPORT_BYTES:
        raise DocumentTooLargeError("处理稿超过 50 MB 上限")
    lock = get_knowledge_base_lock(owner.data_dir)
    if not lock.acquire(blocking=False):
        raise DocumentContentConflictError("知识库正在构建或修改，请等待结束后再保存处理稿")
    try:
        row, path = processed_source(owner, source_id)
        previous = path.read_bytes()
        if hashlib.sha256(previous).hexdigest() != expected_hash.casefold():
            raise DocumentContentConflictError("处理稿已变化，请重新读取后再编辑")
        new_hash = hashlib.sha256(encoded).hexdigest()
        changed = encoded != previous
        connection = connect_sources(owner.paths)
        written = False
        try:
            if changed:
                _write_bytes_atomic(path, encoded)
                written = True
            # Keep the previous Graph/RAG index available until an explicit
            # rebuild succeeds. The original file and mapping are untouched.
            with connection:
                connection.execute(
                    """
                    UPDATE sources SET content_hash = ?, graph_status = 'pending',
                        rag_status = 'pending', updated_at = ?
                    WHERE source_id = ? AND content_hash != ?
                    """,
                    (new_hash, datetime.now(timezone.utc).isoformat(), source_id, new_hash),
                )
        except Exception:
            if written:
                _write_bytes_atomic(path, previous)
            raise
        finally:
            connection.close()
        return {**read_processed(owner, source_id), "changed": changed}
    finally:
        lock.release()
