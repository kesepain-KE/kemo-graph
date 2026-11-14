from __future__ import annotations
from core.common import escape_like as _escape_like
import hashlib
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence
from core.documents.conversion import DocumentConversionError, import_document as convert_document
from core.ingest.progress import read_builds
from core.storage import connect_sources
from core.documents import FileMapError
from core.ingest import Ingestor
from core.storage import get_knowledge_base_lock
from core.retrieval import SearchCacheCleanupError
from core.retrieval.cache.store import clear_search_cache_after_mutation
from core.documents import SUPPORTED_IMPORT_SUFFIXES
from core.documents.errors import DocumentContentConflictError, DocumentImportConflictError
from core.documents import DocumentImportError, DocumentImportPathError, UnsupportedDocumentFormatError
from core.documents.importing import _ingest_error_summary, _load_source_metadata, _resolve_import_source, _restore_import_destination, _safe_markdown_destination, _sha256_snapshot, _source_record_for_relative_path, _stable_import_snapshot, _stable_markdown_name
from core.common import elapsed_ms as _elapsed_ms, now_iso as _now_iso
from core.common import write_bytes_atomic as _write_bytes_atomic


from core.service.context import ServiceContext

_DOCUMENT_BUILD_STATUSES = ("pending", "processing", "ready", "failed")

def _document_filters(
    *,
    status: str | None,
    directory: str | None,
    search: str | None,
    graph_status: str | None = None,
    rag_status: str | None = None,
) -> tuple[str, list[str]]:
    """Filter source records by original-file directory and build status."""

    clauses: list[str] = []
    parameters: list[str] = []
    if status not in {None, "all", "active", "pending"}:
        raise ValueError("status 必须是 active、pending 或 all")
    if status == "pending":
        clauses.append(
            "exists_status = 'active' AND "
            "(graph_status = 'pending' OR rag_status = 'pending')"
        )
    elif status == "active":
        clauses.append("exists_status = 'active'")

    if directory is not None:
        if not isinstance(directory, str) or "\\" in directory or ".." in Path(directory).parts:
            raise ValueError("directory 必须是库内相对目录")
        if directory:
            clauses.append("library_path LIKE ? ESCAPE '\\'")
            parameters.append(f"{_escape_like(directory.rstrip('/'))}/%")
        else:
            clauses.append("library_path NOT LIKE '%/%'")

    if search is not None and not isinstance(search, str):
        raise ValueError("search 必须是字符串")
    if search is not None and search.strip():
        term = _escape_like(search.strip().casefold())
        clauses.append("LOWER(COALESCE(library_path, original_path)) LIKE ? ESCAPE '\\'")
        parameters.append(f"%{term}%")

    for field, value, label in (
        ("graph_status", graph_status, "graph_status"),
        ("rag_status", rag_status, "rag_status"),
    ):
        if value in (None, "", "all"):
            continue
        if value not in _DOCUMENT_BUILD_STATUSES:
            raise ValueError(f"{label} 必须是 pending、processing、ready、failed 或 all")
        clauses.append(f"{field} = ?")
        parameters.append(value)

    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", parameters)

class DocumentService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context

    @property
    def settings(self):
        return self.context.settings

    @property
    def paths(self):
        return self.context.paths

    @property
    def data_dir(self):
        return self.context.data_dir

    @property
    def external_dir(self):
        return self.context.external_dir

    def _require_initialized(self) -> None:
        self.context._require_initialized()

    def _log_event(self, *args, **kwargs) -> None:
        self.context._log_event(*args, **kwargs)

    def clear_search_cache(self, stale_only: bool = False):
        return self.context.retrieval.clear_search_cache(stale_only)


    def list_documents(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        *,
        directory: str | None = None,
        search: str | None = None,
        graph_status: str | None = None,
        rag_status: str | None = None,
        include_summary: bool = False,
    ) -> dict[str, Any]:
        """分页返回文档基本信息，并支持服务端筛选与摘要统计。"""

        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page 必须是大于等于 1 的整数")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size 必须是 1 到 100 之间的整数")

        if not isinstance(include_summary, bool):
            raise ValueError("include_summary 必须是布尔值")
        # Validate filters before entering the read cache so malformed input
        # never gets retained as a cache key.
        _document_filters(
            status=status,
            directory=directory,
            search=search,
            graph_status=graph_status,
            rag_status=rag_status,
        )

        from core.common.cache import READ_CACHE, database_revision

        cache_key = (
            "document-page",
            str(self.context.paths.sources_db.resolve()),
            status,
            page,
            page_size,
            directory,
            search,
            graph_status,
            rag_status,
            include_summary,
        )
        if (
            directory is None
            and search is None
            and graph_status is None
            and rag_status is None
            and not include_summary
        ):
            loader = lambda: self._read_document_page(status, page, page_size)
        else:
            loader = lambda: self._read_document_page(
                status,
                page,
                page_size,
                directory=directory,
                search=search,
                graph_status=graph_status,
                rag_status=rag_status,
                include_summary=include_summary,
            )

        return READ_CACHE.get_or_load(
            cache_key,
            lambda: database_revision(self.context.paths.sources_db),
            loader,
        )

    def _read_document_page(
        self,
        status,
        page,
        page_size,
        *,
        directory: str | None = None,
        search: str | None = None,
        graph_status: str | None = None,
        rag_status: str | None = None,
        include_summary: bool = False,
    ) -> dict[str, Any]:
        pagination = {
            "page": page,
            "page_size": page_size,
            "total": 0,
            "total_pages": 0,
        }
        if not self.context.paths.sources_db.exists():
            result = {"documents": [], "pagination": pagination}
            if include_summary:
                result["summary"] = self._empty_document_summary()
            return result

        where_sql, parameters = _document_filters(
            status=status,
            directory=directory,
            search=search,
            graph_status=graph_status,
            rag_status=rag_status,
        )
        order_sql = "ORDER BY exists_status, relative_path"
        if status in {"active", "pending"} or directory is not None or search:
            order_sql = "ORDER BY relative_path"

        connection = connect_sources(self.context.paths)
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM sources {where_sql}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT source_id, relative_path, original_path, library_path,
                       content_hash, graph_hash, rag_hash,
                       origin_hash, origin_size, origin_modified_at,
                       source_uri, source_type, source_revision,
                       source_updated_at, source_metadata_json,
                       external_content_hash, last_synced_at,
                       graph_status, rag_status, exists_status,
                       created_at, updated_at
                FROM sources
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
                """,
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
            builds = read_builds(connection, [row["source_id"] for row in rows])
            summary = (
                self._read_document_summary(
                    connection,
                    status="active",
                    directory=None,
                    search=None,
                )
                if include_summary
                else None
            )
        finally:
            connection.close()

        pagination["total"] = total
        pagination["total_pages"] = (total + page_size - 1) // page_size
        result = {
            "documents": [
                {
                    "source_id": row["source_id"],
                    "relative_path": row["relative_path"],
                    "original_path": row["original_path"],
                    "source_path": row["library_path"],
                    "content_hash": row["content_hash"],
                    "graph_hash": row["graph_hash"],
                    "rag_hash": row["rag_hash"],
                    "origin_hash": row["origin_hash"],
                    "origin_size": row["origin_size"],
                    "origin_modified_at": row["origin_modified_at"],
                    "source_uri": row["source_uri"],
                    "source_type": row["source_type"],
                    "source_revision": row["source_revision"],
                    "source_updated_at": row["source_updated_at"],
                    "source_metadata": _load_source_metadata(
                        row["source_metadata_json"]
                    ),
                    "external_content_hash": row["external_content_hash"],
                    "last_synced_at": row["last_synced_at"],
                    "graph_status": row["graph_status"],
                    "rag_status": row["rag_status"],
                    "builds": builds.get(row["source_id"], {}),
                    "exists_status": row["exists_status"],
                    "updated_at": row["updated_at"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
            "pagination": pagination,
        }
        if summary is not None:
            result["summary"] = summary
        return result

    @staticmethod
    def _empty_document_summary() -> dict[str, Any]:
        return {
            "total_active": 0,
            "pending_documents": 0,
            "needs_rebuild_documents": 0,
            "graph": {status: 0 for status in _DOCUMENT_BUILD_STATUSES},
            "rag": {status: 0 for status in _DOCUMENT_BUILD_STATUSES},
        }

    def _read_document_summary(
        self,
        connection,
        *,
        status: str | None,
        directory: str | None,
        search: str | None,
    ) -> dict[str, Any]:
        # Summary counts intentionally ignore the individual Graph/RAG status
        # filters, so the two cards continue to explain the complete scope.
        where_sql, parameters = _document_filters(
            status=status,
            directory=directory,
            search=search,
        )
        row = connection.execute(
            f"""
            SELECT
                COUNT(*) AS total_active,
                SUM(CASE WHEN graph_status = 'pending' OR rag_status = 'pending' THEN 1 ELSE 0 END) AS pending_documents,
                SUM(CASE WHEN graph_status IN ('pending', 'failed') OR rag_status IN ('pending', 'failed') THEN 1 ELSE 0 END) AS needs_rebuild_documents,
                SUM(CASE WHEN graph_status = 'pending' THEN 1 ELSE 0 END) AS graph_pending,
                SUM(CASE WHEN graph_status = 'processing' THEN 1 ELSE 0 END) AS graph_processing,
                SUM(CASE WHEN graph_status = 'ready' THEN 1 ELSE 0 END) AS graph_ready,
                SUM(CASE WHEN graph_status = 'failed' THEN 1 ELSE 0 END) AS graph_failed,
                SUM(CASE WHEN rag_status = 'pending' THEN 1 ELSE 0 END) AS rag_pending,
                SUM(CASE WHEN rag_status = 'processing' THEN 1 ELSE 0 END) AS rag_processing,
                SUM(CASE WHEN rag_status = 'ready' THEN 1 ELSE 0 END) AS rag_ready,
                SUM(CASE WHEN rag_status = 'failed' THEN 1 ELSE 0 END) AS rag_failed
            FROM sources
            {where_sql}
            """,
            parameters,
        ).fetchone()
        values = {key: int(row[key] or 0) for key in row.keys()}
        return {
            "total_active": values["total_active"],
            "pending_documents": values["pending_documents"],
            "needs_rebuild_documents": values["needs_rebuild_documents"],
            "graph": {
                "pending": values["graph_pending"],
                "processing": values["graph_processing"],
                "ready": values["graph_ready"],
                "failed": values["graph_failed"],
            },
            "rag": {
                "pending": values["rag_pending"],
                "processing": values["rag_processing"],
                "ready": values["rag_ready"],
                "failed": values["rag_failed"],
            },
        }

    def get_document_content(self, source_id: str) -> dict[str, Any]:
        from core.documents.library import read_content
        return read_content(self, source_id)

    def update_document_content(
        self, source_id: str, content: str, *, expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        from core.documents.library import update_content
        return update_content(self, source_id, content, expected_content_hash)

    def import_document(
        self,
        source_path: Path | str,
        *,
        ingest_after_import: bool = True,
        expected_origin_hash: str | None = None,
        directory: str | None = None,
    ) -> dict[str, Any]:
        """安全转换任意受支持文件、注册来源，并可立即执行整理。"""

        started_at = time.perf_counter()
        try:
            source = _resolve_import_source(source_path)
        except DocumentImportPathError as exc:
            self.context._log_event(
                "document_import_failed",
                f"error={type(exc).__name__}",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise
        suffix = source.suffix.casefold()
        if suffix not in SUPPORTED_IMPORT_SUFFIXES:
            self.context._log_event(
                "document_import_failed",
                f"file={source.name}, error=UnsupportedDocumentFormatError",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise UnsupportedDocumentFormatError(
                f"不支持的文档格式：{suffix or '<无扩展名>'}"
            )
        from core.documents.library import original_root, resolve_path, validate_name
        with get_knowledge_base_lock(self.context.data_dir):
            root = original_root(self)
            root.mkdir(parents=True, exist_ok=True)
            if source.is_relative_to(root):
                original = resolve_path(self, source.relative_to(root).as_posix())
            else:
                parent = resolve_path(self, directory or "")
                if not parent.is_dir():
                    raise ValueError("导入目标不是目录")
                original = parent / validate_name(source.name)
            identity = str(original)

            original_previous = original.read_bytes() if original.exists() else None
            ingestor = Ingestor(
                data_dir=self.context.data_dir,
                external_dir=self.context.external_dir,
                settings=self.context.settings,
            )
            try:
                existing_mapping = ingestor.file_map.get_by_original(identity)
                markdown_relative_path = (
                    existing_mapping.markdown_path
                    if existing_mapping is not None
                    else _stable_markdown_name(source.name, identity)
                )
                destination = _safe_markdown_destination(
                    self.context.external_dir,
                    markdown_relative_path,
                )
                previous_bytes = (
                    destination.read_bytes() if destination.exists() else None
                )
                file_map_previous = ingestor.file_map.file_path.read_bytes()
                try:
                    with _stable_import_snapshot(
                        source,
                        expected_origin_hash=expected_origin_hash,
                    ) as snapshot:
                        size = snapshot.size
                        origin_hash = snapshot.sha256
                        origin_modified_at = snapshot.modified_at
                        self.context._log_event(
                            "document_import_start",
                            (
                                f"file={source.name}, "
                                f"format={suffix.removeprefix('.')}, size={size}"
                            ),
                        )
                        if source != original:
                            if original_previous is not None and hashlib.sha256(original_previous).hexdigest() != origin_hash:
                                raise DocumentContentConflictError("同名原文件已存在，请重命名后导入")
                            _write_bytes_atomic(original, snapshot.path.read_bytes())
                        conversion = convert_document(
                            snapshot.path,
                            self.context.external_dir,
                            destination_name=markdown_relative_path,
                        )
                        if _sha256_snapshot(snapshot.path) != origin_hash:
                            raise DocumentImportConflictError(
                                "导入快照在转换期间发生变化，已拒绝提交："
                                f"{source.name}"
                            )
                    self.context._log_event(
                        "document_convert",
                        f"file={source.name}, format={conversion['format']}",
                        _elapsed_ms(started_at),
                    )
                    ingestor.file_map.upsert(identity, markdown_relative_path)
                    ingestor.scan_sources()
                except Exception:
                    _restore_import_destination(destination, previous_bytes)
                    if original_previous is not None and source != original:
                        _write_bytes_atomic(original, original_previous)
                    _write_bytes_atomic(
                        ingestor.file_map.file_path,
                        file_map_previous,
                    )
                    raise
            except (DocumentImportError, DocumentConversionError, FileMapError) as exc:
                self.context._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise
            except Exception as exc:
                self.context._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise DocumentImportError(
                    f"文档注册失败：{source.name}: {type(exc).__name__}"
                ) from exc

            source_record = _source_record_for_relative_path(
                self.context.paths,
                markdown_relative_path,
            )
            source_id = str(source_record["source_id"])
            content_hash = str(source_record["content_hash"])
            source_connection = connect_sources(self.context.paths)
            try:
                source_connection.execute(
                    """
                    UPDATE sources
                    SET origin_hash = ?, origin_size = ?, origin_modified_at = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        origin_hash,
                        size,
                        origin_modified_at,
                        _now_iso(),
                        source_id,
                    ),
                )
                source_connection.commit()
            finally:
                source_connection.close()
            ingest_status = "pending"
            ingest_result: dict[str, Any] | None = None
            ingest_error: str | None = None
            if ingest_after_import:
                try:
                    ingest_result = ingestor.ingest(
                        paths=[markdown_relative_path],
                        mode="both",
                    )
                    ingest_status = (
                        "failed" if int(ingest_result.get("failed", 0)) else "completed"
                    )
                    if ingest_status == "failed":
                        ingest_error = _ingest_error_summary(ingest_result)
                except Exception as exc:
                    ingest_status = "failed"
                    ingest_error = f"{type(exc).__name__}: {str(exc)[:240]}"

        result: dict[str, Any] = {
            "source_id": source_id,
            "original_filename": original.name,
            "original_path": str(original),
            "source_path": original.relative_to(root).as_posix(),
            "detected_format": conversion["format"],
            "markdown_relative_path": markdown_relative_path,
            "conversion_status": "completed",
            "ingest_status": ingest_status,
            "size": size,
            "origin_hash": origin_hash,
            "content_hash": content_hash,
            "origin_modified_at": origin_modified_at,
        }
        if ingest_result is not None:
            result["ingest"] = ingest_result
        if ingest_error:
            result["ingest_error"] = ingest_error
        self.context._log_event(
            "document_import_done",
            f"file={source.name}, source_id={source_id}, ingest={ingest_status}",
            _elapsed_ms(started_at),
            level="ERROR" if ingest_status == "failed" else "INFO",
        )
        return result

    def upload_file(self, content: str, filename: str) -> dict[str, Any]:
        from core.documents.library import validate_name
        name = validate_name(filename)
        if not Path(name).suffix:
            name += ".md"
        with tempfile.TemporaryDirectory(prefix="kemo-graph-create-") as temporary_dir:
            source = Path(temporary_dir) / name
            source.write_bytes(content.encode("utf-8"))
            result = self.import_document(source, ingest_after_import=False)
        return {**result, "filename": name, "path": result["source_path"]}

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
        *,
        retry_failed: bool = False,
        force: bool = False,
        failed_only: bool = False,
    ) -> dict[str, Any]:
        from core.maintenance.library import build_paths
        if paths is None and not failed_only:
            build_paths(self, "")
        elif paths is not None:
            normalized = []
            for path in paths:
                path = str(path)
                original = self.context.external_dir.parent / "originals" / path
                if original.exists():
                    normalized.extend(build_paths(self, path))
                else:
                    normalized.append(path)
            paths = normalized
        return Ingestor(
            data_dir=self.context.data_dir,
            external_dir=self.context.external_dir,
            settings=self.context.settings,
        ).ingest(paths=paths, mode=mode, retry_failed=retry_failed, force=force, failed_only=failed_only)

    def sync_sources(
        self,
        records: Sequence[dict[str, Any]],
        *,
        ingest_after_sync: bool = False,
    ) -> dict[str, Any]:
        """同步上游表记录；kemo-graph 只维护派生 Markdown、Graph 与 RAG。"""

        from core.maintenance.source_sync import sync_external_sources

        return sync_external_sources(
            self,
            records,
            ingest_after_sync=ingest_after_sync,
        )

    def list_synced_sources(
        self,
        *,
        source_type: str | None = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """分页读取由外部权威数据源同步而来的记录。"""

        from core.maintenance.source_sync import list_external_sources

        return list_external_sources(
            self,
            source_type=source_type,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
        )

    def delete_synced_sources(self, source_uris: Sequence[str]) -> dict[str, Any]:
        """按稳定 URI 删除外部派生数据，保留上游权威来源。"""

        from core.maintenance.source_sync import delete_external_sources

        return delete_external_sources(self, source_uris)

    def delete_document(self, source_id: str) -> dict[str, Any]:
        self.context._require_initialized()
        result = Ingestor(
            data_dir=self.context.data_dir,
            external_dir=self.context.external_dir,
            settings=self.context.settings,
        ).delete_document(source_id)
        return clear_search_cache_after_mutation(self, result)

    def delete_documents(self, source_ids: Sequence[str]) -> dict[str, Any]:
        """逐一精确删除活动文档，并以结构化结果报告部分失败。"""

        self.context._require_initialized()
        if isinstance(source_ids, (str, bytes)):
            raise TypeError("source_ids 必须是字符串数组")
        normalized: list[str] = []
        seen: set[str] = set()
        for value in source_ids:
            source_id = str(value).strip()
            if not source_id:
                raise ValueError("source_ids 中不能包含空值")
            if source_id not in seen:
                seen.add(source_id)
                normalized.append(source_id)
        if not normalized:
            raise ValueError("至少选择一篇文档")
        if len(normalized) > 1000:
            raise ValueError("单次最多删除 1000 篇文档")

        ingestor = Ingestor(
            data_dir=self.context.data_dir,
            external_dir=self.context.external_dir,
            settings=self.context.settings,
        )
        deleted: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with ingestor._write_lock:
            for source_id in normalized:
                try:
                    deleted.append(ingestor.delete_document(source_id))
                except Exception as exc:
                    failures.append(
                        {
                            "source_id": source_id,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
        self.context._log_event(
            "delete_documents",
            f"requested={len(normalized)}, deleted={len(deleted)}, failed={len(failures)}",
            level="WARNING" if failures else "INFO",
        )
        result = {
            "requested": len(normalized),
            "deleted": len(deleted),
            "failed": len(failures),
            "documents": deleted,
            "failures": failures,
            "search_cache_deleted": 0,
        }
        return clear_search_cache_after_mutation(self, result) if deleted else result

    def delete_all_documents(self, directory: str | None = None) -> dict[str, Any]:
        """删除当前知识库内的全部活动文档，不跨越 Store 边界。"""

        self.context._require_initialized()
        where_sql, parameters = _document_filters(
            status="active",
            directory=directory,
            search=None,
        )
        connection = connect_sources(self.context.paths)
        try:
            source_ids = [
                str(row["source_id"])
                for row in connection.execute(
                    f"""
                    SELECT source_id FROM sources
                    {where_sql}
                    ORDER BY relative_path, source_id
                    """,
                    parameters,
                ).fetchall()
            ]
        finally:
            connection.close()
        if not source_ids:
            return {
                "requested": 0,
                "deleted": 0,
                "failed": 0,
                "documents": [],
                "failures": [],
                "search_cache_deleted": 0,
            }
        if len(source_ids) <= 1000:
            return self.delete_documents(source_ids)

        # The public batch endpoint is deliberately bounded to 1000 IDs, but
        # a library/directory can contain up to max_documents.  Process the
        # server-resolved scope in bounded batches and return the same aggregate
        # response shape without asking the Web client to enumerate every ID.
        aggregate = {
            "requested": len(source_ids),
            "deleted": 0,
            "failed": 0,
            "documents": [],
            "failures": [],
            "search_cache_deleted": 0,
        }
        for offset in range(0, len(source_ids), 1000):
            cleanup_error = None
            try:
                result = self.delete_documents(source_ids[offset : offset + 1000])
            except SearchCacheCleanupError as exc:
                result = exc.result
                cleanup_error = exc
            aggregate["deleted"] += int(result["deleted"])
            aggregate["failed"] += int(result["failed"])
            aggregate["documents"].extend(result["documents"])
            aggregate["failures"].extend(result["failures"])
            if cleanup_error is not None:
                aggregate["search_cache_deleted"] = None
                aggregate["search_cache_error"] = result["search_cache_error"]
                aggregate["unprocessed"] = (
                    aggregate["requested"] - aggregate["deleted"] - aggregate["failed"]
                )
                raise SearchCacheCleanupError(aggregate) from cleanup_error
            aggregate["search_cache_deleted"] += int(
                result.get("search_cache_deleted", 0)
            )
        return aggregate
