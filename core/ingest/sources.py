from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from markitdown._utils import sanitize_knowledge_markdown
from core.settings import PROJECT_ROOT
from core.storage import connect_sources
from core.ingest.scan import _SourceRecord, _source_record_from_row
from core.common import now_iso as _now_iso
from core.documents.paths import _safe_source_path

def _normalize_requested_paths(
    self, paths: Sequence[Path | str] | None
) -> set[str] | None:
    if paths is None:
        return None
    normalized: set[str] = set()
    for value in paths:
        path = Path(value)
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(self.external_dir)
            except ValueError as exc:
                raise IngestError(f"路径不在 Markdown 目录内：{path}") from exc
        else:
            project_candidate = (PROJECT_ROOT / path).resolve()
            try:
                relative = project_candidate.relative_to(self.external_dir)
            except ValueError:
                relative = path
        normalized.add(relative.as_posix().lstrip("./"))
    return normalized

def _select_active_sources(
    self,
    requested_paths: set[str] | None,
    mode: str,
    *,
    include_failed: bool = False,
) -> list[_SourceRecord]:
    connection = connect_sources(self.paths)
    try:
        rows = connection.execute(
            """
                SELECT source_id, relative_path, content_hash,
                       graph_hash, rag_hash, graph_status, rag_status
                FROM sources
                WHERE exists_status = 'active'
                ORDER BY relative_path
                """
        ).fetchall()
    finally:
        connection.close()
    records = [_source_record_from_row(row) for row in rows]
    if requested_paths is not None:
        available = {record.relative_path for record in records}
        missing = requested_paths.difference(available)
        if missing:
            raise IngestError(f"请求的 Markdown 文档不存在：{sorted(missing)}")
        return [
            record for record in records if record.relative_path in requested_paths
        ]
    return [
        record
        for record in records
        if (
            mode in {"graph", "both"}
            and record.graph_status
            in ({"pending", "failed"} if include_failed else {"pending"})
        )
        or (
            mode in {"rag", "both"}
            and record.rag_status
            in ({"pending", "failed"} if include_failed else {"pending"})
        )
    ]

def _read_source_text(self, record: _SourceRecord) -> str:
    path = _safe_source_path(self.external_dir, record.relative_path)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IngestError(
            f"无法读取 UTF-8 Markdown：{record.relative_path}: {exc}"
        ) from exc
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != record.content_hash:
        self._register_concurrent_change(record.source_id, actual_hash)
        raise SourceChangedDuringIngest(
            f"文档在扫描后再次变化，已保持 pending：{record.relative_path}"
        )
    return sanitize_knowledge_markdown(text)

def _register_concurrent_change(self, source_id: str, content_hash: str) -> None:
    connection = connect_sources(self.paths)
    try:
        connection.execute(
            """
                UPDATE sources
                SET content_hash = ?, graph_status = 'pending',
                    rag_status = 'pending', updated_at = ?
                WHERE source_id = ?
                """,
            (content_hash, _now_iso(), source_id),
        )
        connection.commit()
    finally:
        connection.close()

def _source_relative_path(self, source_id: str) -> str:
    connection = connect_sources(self.paths)
    try:
        row = connection.execute(
            "SELECT relative_path FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    finally:
        connection.close()
    return row["relative_path"] if row is not None else source_id
