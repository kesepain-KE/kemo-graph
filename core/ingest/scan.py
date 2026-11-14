from __future__ import annotations
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from core.documents.chunker.engine import chunking_signature
from core.storage.connections import connect_rag, connect_sources
from core.storage.meta import read_graph_meta, read_rag_meta
from core.documents.errors import IngestError
from core.common.time import elapsed_ms as _elapsed_ms, now_iso as _now_iso
from core.documents.paths import _hash_relative_path, _path_key, _safe_source_path

@dataclass(frozen=True)
class ScanResult:
    new_source_ids: list[str]
    changed_source_ids: list[str]
    unchanged_source_ids: list[str]
    deleted_source_ids: list[str]
    newly_deleted_source_ids: list[str]

@dataclass(frozen=True)
class _SourceRecord:
    source_id: str
    relative_path: str
    content_hash: str
    graph_hash: str | None
    rag_hash: str | None
    graph_status: str
    rag_status: str

def cleanup_orphaned_sources(
    self, *, remove_unmapped: bool = False,
) -> tuple[list[str], list[str]]:
    """Remove derived files whose original source no longer exists."""

    from core.documents.library import resolve_path

    with self._write_lock:
        connection = connect_sources(self.paths)
        try:
            rows = connection.execute(
                """
                SELECT source_id, relative_path, original_path, library_path,
                       source_uri, exists_status FROM sources
                """
            ).fetchall()
        finally:
            connection.close()
        mapped_paths = {_path_key(item.markdown_path) for item in self.file_map.list()}
        newly_deleted_ids: list[str] = []
        removed_paths: list[str] = []
        for row in rows:
            # Synced records have an upstream authority instead of a local file.
            if row["source_uri"] is not None:
                continue
            original = (
                resolve_path(self, row["library_path"], must_exist=False)
                if row["library_path"] is not None
                else Path(row["original_path"])
            )
            if original.is_file():
                continue
            if row["exists_status"] == "active":
                self.delete_document(row["source_id"])
                newly_deleted_ids.append(row["source_id"])
                removed_paths.append(row["library_path"] or row["relative_path"])
            elif (
                _path_key(row["relative_path"]) in mapped_paths
                or _safe_source_path(self.external_dir, row["relative_path"]).is_file()
            ):
                self._delete_source_file(row["relative_path"])
                self._delete_source_data(row["source_id"])
                removed_paths.append(row["library_path"] or row["relative_path"])

        for mapping in self.file_map.list():
            original = mapping.original_path
            # Source sync writes its URI mapping before registering metadata.
            if "://" in original and not original.startswith("upload://"):
                continue
            if Path(original).is_file():
                continue
            self._delete_source_file(mapping.markdown_path)
            self.file_map.delete_by_markdown(mapping.markdown_path)
            removed_paths.append(mapping.markdown_path)

        if remove_unmapped:
            tracked = {_path_key(item.markdown_path) for item in self.file_map.list()}
            tracked.update(
                _path_key(row["relative_path"]) for row in rows
                if row["exists_status"] == "active" and row["source_id"] not in newly_deleted_ids
            )
            for path in self.external_dir.rglob("*"):
                if path.is_file() and path.suffix.casefold() == ".md":
                    relative = path.relative_to(self.external_dir).as_posix()
                    if _path_key(relative) not in tracked:
                        self._delete_source_file(relative)
                        removed_paths.append(relative)
        return newly_deleted_ids, removed_paths

def scan_sources(self) -> ScanResult:
    """扫描全部 Markdown，并更新 sources 的身份、哈希和状态。"""

    started_at = time.perf_counter()
    newly_deleted_ids, _ = cleanup_orphaned_sources(self)

    graph_config_changed = self._graph_extraction_config_changed()
    rag_config_changed = self._rag_chunking_config_changed()
    markdown_files = sorted(
        (
            path
            for path in self.external_dir.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".md"
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    if len(markdown_files) > self.settings.max_documents:
        raise IngestError(
            f"Markdown 文档数 {len(markdown_files)} 超过配置上限 "
            f"{self.settings.max_documents}"
        )

    current_files: dict[str, tuple[Path, str, str, str]] = {}
    for path in markdown_files:
        relative_path = path.relative_to(self.external_dir).as_posix()
        path_hash = _hash_relative_path(relative_path)
        if path_hash in current_files:
            raise IngestError(f"规范化路径发生冲突：{relative_path}")
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        mapping = self.file_map.get_by_markdown(relative_path)
        original_path = mapping.original_path if mapping else str(path.resolve())
        current_files[path_hash] = (
            path,
            relative_path,
            content_hash,
            original_path,
        )

    new_ids: list[str] = []
    changed_ids: list[str] = []
    unchanged_ids: list[str] = []
    now = _now_iso()
    connection = connect_sources(self.paths)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_rows = connection.execute("SELECT * FROM sources").fetchall()
        existing_by_hash = {row["path_hash"]: row for row in existing_rows}

        for path_hash, (
            _,
            relative_path,
            content_hash,
            original_path,
        ) in current_files.items():
            existing = existing_by_hash.get(path_hash)
            if existing is None:
                source_id = str(uuid4())
                connection.execute(
                    """
                        INSERT INTO sources (
                            source_id, original_path, relative_path, path_hash,
                            content_hash, graph_status, rag_status,
                            exists_status, created_at, updated_at, library_path
                        ) VALUES (?, ?, ?, ?, ?, 'pending', 'pending', 'active', ?, ?, ?)
                        """,
                    (
                        source_id,
                        original_path,
                        relative_path,
                        path_hash,
                        content_hash,
                        now,
                        now,
                        _library_path(self, original_path),
                    ),
                )
                new_ids.append(source_id)
                continue

            source_id = existing["source_id"]
            restored = existing["exists_status"] != "active"
            changed = existing["content_hash"] != content_hash
            if restored or changed:
                graph_status = (
                    "pending"
                    if restored
                    or content_hash != existing["graph_hash"]
                    or graph_config_changed
                    else "ready"
                )
                rag_status = (
                    "pending"
                    if restored
                    or content_hash != existing["rag_hash"]
                    or rag_config_changed
                    else "ready"
                )
                connection.execute(
                    """
                        UPDATE sources
                        SET original_path = ?, relative_path = ?, content_hash = ?,
                            graph_status = ?, rag_status = ?,
                            exists_status = 'active', updated_at = ?, library_path = ?
                        WHERE source_id = ?
                        """,
                    (
                        original_path,
                        relative_path,
                        content_hash,
                        graph_status,
                        rag_status,
                        now,
                        _library_path(self, original_path),
                        source_id,
                    ),
                )
                changed_ids.append(source_id)
            else:
                if (
                    (graph_config_changed or rag_config_changed)
                    and existing["exists_status"] == "active"
                ):
                    connection.execute(
                        """
                        UPDATE sources
                        SET graph_status = CASE WHEN ? THEN 'pending' ELSE graph_status END,
                            rag_status = CASE WHEN ? THEN 'pending' ELSE rag_status END,
                            updated_at = ?
                        WHERE source_id = ?
                        """,
                        (int(graph_config_changed), int(rag_config_changed), now, source_id),
                    )
                    changed_ids.append(source_id)
                    continue
                if (
                    existing["original_path"] != original_path
                    or existing["relative_path"] != relative_path
                ):
                    connection.execute(
                        """
                            UPDATE sources
                            SET original_path = ?, relative_path = ?, updated_at = ?, library_path = ?
                            WHERE source_id = ?
                            """,
                        (original_path, relative_path, now, _library_path(self, original_path), source_id),
                    )
                unchanged_ids.append(source_id)

        current_path_hashes = set(current_files)
        for row in existing_rows:
            if row["path_hash"] in current_path_hashes:
                continue
            if row["exists_status"] != "deleted":
                connection.execute(
                    """
                        UPDATE sources
                        SET exists_status = 'deleted', updated_at = ?
                        WHERE source_id = ?
                        """,
                    (now, row["source_id"]),
                )
                newly_deleted_ids.append(row["source_id"])
        connection.commit()

        deleted_rows = connection.execute(
            "SELECT source_id FROM sources WHERE exists_status = 'deleted'"
        ).fetchall()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    result = ScanResult(
        new_source_ids=new_ids,
        changed_source_ids=changed_ids,
        unchanged_source_ids=unchanged_ids,
        deleted_source_ids=[row["source_id"] for row in deleted_rows],
        newly_deleted_source_ids=newly_deleted_ids,
    )
    self._log_event(
        "scan",
        (
            f"new={len(new_ids)}, changed={len(changed_ids)}, "
            f"unchanged={len(unchanged_ids)}, deleted={len(result.deleted_source_ids)}"
        ),
        _elapsed_ms(started_at),
    )
    return result

def _library_path(self, original_path: str) -> str | None:
    try:
        return Path(original_path).resolve().relative_to(
            (self.external_dir.parent / "originals").resolve()
        ).as_posix()
    except ValueError:
        # External synchronization has a source URI, not a local original file.
        return None

def _rag_chunking_config_changed(self) -> bool:
    meta = read_rag_meta(self.paths, self.settings)
    current_signature = chunking_signature(self.settings)
    connection = connect_rag(self.paths)
    try:
        vector_count = int(
            connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        )
    finally:
        connection.close()
    return bool(vector_count and meta.get("chunking_signature") != current_signature)

def _graph_extraction_config_changed(self) -> bool:
    """检测图谱抽取档位/Prompt profile 是否改变。"""

    meta = read_graph_meta(self.paths)
    current_signature = self.settings.graph_extraction_signature()
    connection = connect_sources(self.paths)
    try:
        built_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM sources
                WHERE exists_status = 'active'
                  AND graph_status = 'ready'
                  AND graph_hash IS NOT NULL
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    stored_signature = meta.get("extraction_signature")
    if not stored_signature:
        # 旧版本没有指纹。将已有 Graph 标记为待重建，让新的稀疏 Prompt
        # 真正作用到历史文档。指纹要等图谱成功写入后由
        # ``Ingestor._refresh_graph_meta`` 更新；扫描本身可能因路径冲突、
        # 文档上限或数据库错误失败，不能在这里提前宣称旧图谱已经重建。
        if built_count:
            return True
        return False
    return bool(built_count and stored_signature != current_signature)

def _source_record_from_row(row: sqlite3.Row) -> _SourceRecord:
    return _SourceRecord(
        source_id=row["source_id"],
        relative_path=row["relative_path"],
        content_hash=row["content_hash"],
        graph_hash=row["graph_hash"],
        rag_hash=row["rag_hash"],
        graph_status=row["graph_status"],
        rag_status=row["rag_status"],
    )
