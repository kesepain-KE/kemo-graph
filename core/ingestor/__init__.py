"""Markdown 文档扫描、增量 Graph/RAG 构建和来源映射。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from provider.embedding import EmbeddingResult, embed
from provider.engine import chat_structured, chat_with_tools, supports_structured_output

from ..config import AppConfig, PROJECT_ROOT, load_config
from ..db import (
    connect_graph,
    connect_sources,
    initialize_databases,
    read_graph_meta,
    write_graph_meta,
)
from ..graph_draft import GraphDraft
from ..logger import DailyTSVLogger
from ..rag_engine import RAGEngine

DEFAULT_GRAPH_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_agent.md"
DEFAULT_GRAPH_EXTRACT_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_extract.md"


class IngestError(RuntimeError):
    """文档扫描或整理失败。"""


class SourceChangedDuringIngest(IngestError):
    """扫描后、整理前文档再次发生变化。"""


from ._delete import DocumentNotFoundError, RecycleConflictError  # noqa: E402
from ._file_map import (  # noqa: E402
    FILE_MAP_VERSION,
    FileMapError,
    FileMapping,
    FileMapStore,
)
from ._graph_build import GraphExtractionError  # noqa: E402
from ._scan import ScanResult, _SourceRecord, _source_record_from_row  # noqa: E402
from ._utils import (  # noqa: E402
    _elapsed_ms,
    _get_write_lock,
    _now_iso,
    _resolve_external_dir,
    _safe_source_path,
)


Embedder = Callable[[list[str]], EmbeddingResult | list[list[float]]]


class Ingestor:
    """协调 sources 扫描、Graph/RAG 增量更新和删除级联。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        embedder: Embedder | None = None,
        rag_engine: RAGEngine | None = None,
        graph_prompt_path: Path | str = DEFAULT_GRAPH_PROMPT_PATH,
        graph_extract_prompt_path: Path | str = DEFAULT_GRAPH_EXTRACT_PROMPT_PATH,
    ) -> None:
        self.settings = settings or load_config()
        self.paths = initialize_databases(data_dir, self.settings)
        self.external_dir = _resolve_external_dir(external_dir, self.settings)
        self.external_dir.mkdir(parents=True, exist_ok=True)
        self.file_map = FileMapStore(self.external_dir / "file_map.json")
        self.graph_prompt_path = Path(graph_prompt_path)
        self.graph_extract_prompt_path = Path(graph_extract_prompt_path)
        self._embedder = embedder or (
            lambda texts: embed(texts, settings=self.settings)
        )
        self._rag_engine = rag_engine
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        if rag_engine is not None and rag_engine.paths.data_dir != self.paths.data_dir:
            raise IngestError("传入的 RAGEngine 与 Ingestor 不属于同一知识库")
        self._write_lock = _get_write_lock(self.paths.data_dir)

    def scan_sources(self) -> ScanResult:
        from . import _scan as implementation

        return implementation.scan_sources(self)

    def _rag_chunking_config_changed(self) -> bool:
        from . import _scan as implementation

        return implementation._rag_chunking_config_changed(self)

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
        *,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        """扫描并整理 pending 文档，返回 API 约定的统计结构。"""

        started_at = time.perf_counter()
        if mode not in {"graph", "rag", "both"}:
            raise ValueError("mode 必须是 graph、rag 或 both")
        with self._write_lock:
            scan = self.scan_sources()
            requested_paths = self._normalize_requested_paths(paths)
            result: dict[str, Any] = {
                "processed": 0,
                "graph_updated": 0,
                "rag_updated": 0,
                "skipped": 0,
                "failed": 0,
                "details": [],
            }

            newly_deleted = set(scan.newly_deleted_source_ids)
            for source_id in scan.deleted_source_ids:
                relative_path = self._source_relative_path(source_id)
                try:
                    graph_changed, rag_changed = self._delete_source_data(source_id)
                    if source_id in newly_deleted or graph_changed or rag_changed:
                        result["processed"] += 1
                        result["details"].append(
                            {
                                "path": relative_path,
                                "graph": "deleted" if graph_changed else "skipped",
                                "rag": "deleted" if rag_changed else "skipped",
                            }
                        )
                except Exception as exc:
                    result["processed"] += 1
                    result["failed"] += 1
                    result["details"].append(
                        {
                            "path": relative_path,
                            "graph": "failed",
                            "rag": "failed",
                            "error": str(exc),
                        }
                    )

            records = self._select_active_sources(
                requested_paths,
                mode,
                include_failed=retry_failed,
            )
            for record in records:
                graph_requested = mode in {"graph", "both"}
                rag_requested = mode in {"rag", "both"}
                graph_should_run = graph_requested and record.graph_status == "pending"
                rag_should_run = rag_requested and record.rag_status == "pending"
                if requested_paths is not None:
                    graph_should_run = graph_requested and record.graph_status in {
                        "pending",
                        "failed",
                    }
                    rag_should_run = rag_requested and record.rag_status in {
                        "pending",
                        "failed",
                    }
                elif retry_failed:
                    graph_should_run = graph_requested and record.graph_status in {
                        "pending",
                        "failed",
                    }
                    rag_should_run = rag_requested and record.rag_status in {
                        "pending",
                        "failed",
                    }

                if not graph_should_run and not rag_should_run:
                    result["skipped"] += 1
                    result["details"].append(
                        {
                            "path": record.relative_path,
                            "graph": "skipped",
                            "rag": "skipped",
                        }
                    )
                    continue

                result["processed"] += 1
                detail: dict[str, Any] = {
                    "path": record.relative_path,
                    "graph": "skipped",
                    "rag": "skipped",
                }
                document_failed = False
                try:
                    text = self._read_source_text(record)
                except SourceChangedDuringIngest as exc:
                    if graph_should_run:
                        detail["graph"] = "pending"
                    if rag_should_run:
                        detail["rag"] = "pending"
                    detail["error"] = str(exc)
                    result["failed"] += 1
                    result["details"].append(detail)
                    continue

                if graph_should_run:
                    self._set_source_status(record.source_id, "graph", "processing")
                    try:
                        self._update_graph_for_source(record, text)
                        self._mark_source_ready(
                            record.source_id, "graph", record.content_hash
                        )
                        detail["graph"] = "updated"
                        result["graph_updated"] += 1
                    except Exception as exc:
                        self._set_source_status(record.source_id, "graph", "failed")
                        detail["graph"] = "failed"
                        detail["graph_error"] = str(exc)
                        document_failed = True
                        self._log_event(
                            "graph_build_failed",
                            f"{record.relative_path}: {exc}",
                            level="ERROR",
                        )

                if rag_should_run:
                    self._set_source_status(record.source_id, "rag", "processing")
                    try:
                        self._update_rag_for_source(record, text)
                        self._mark_source_ready(
                            record.source_id, "rag", record.content_hash
                        )
                        detail["rag"] = "updated"
                        result["rag_updated"] += 1
                    except Exception as exc:
                        self._set_source_status(record.source_id, "rag", "failed")
                        detail["rag"] = "failed"
                        detail["rag_error"] = str(exc)
                        document_failed = True
                        self._log_event(
                            "rag_build_failed",
                            f"{record.relative_path}: {exc}",
                            level="ERROR",
                        )

                if document_failed:
                    result["failed"] += 1
                result["details"].append(detail)
            self._log_event(
                "ingest",
                (
                    f"mode={mode}, processed={result['processed']}, "
                    f"graph={result['graph_updated']}, rag={result['rag_updated']}, "
                    f"failed={result['failed']}"
                ),
                _elapsed_ms(started_at),
                level="ERROR" if result["failed"] else "INFO",
            )
            return result

    def delete_document(self, source_id: str) -> dict[str, Any]:
        from . import _delete as implementation

        return implementation.delete_document(self, source_id)

    def _move_source_to_recycle(self, relative_path: str) -> str | None:
        from . import _delete as implementation

        return implementation._move_source_to_recycle(self, relative_path)

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
        return text

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

    def _update_graph_for_source(self, record: _SourceRecord, text: str) -> None:
        from . import _graph_build as implementation

        return implementation._update_graph_for_source(self, record, text)

    def _extract_graph_with_tools(self, record: _SourceRecord, text: str) -> None:
        from . import _graph_build as implementation

        return implementation._extract_graph_with_tools(self, record, text)

    def _extract_graph_structured(self, record: _SourceRecord, text: str) -> None:
        from . import _graph_build as implementation

        return implementation._extract_graph_structured(self, record, text)

    def _extract_graph_sections(
        self, record: _SourceRecord, sections: list[str]
    ) -> list[GraphDraft]:
        from . import _graph_build as implementation

        return implementation._extract_graph_sections(self, record, sections)

    def _build_graph_system_prompt(self, record: _SourceRecord) -> str:
        from . import _graph_build as implementation

        return implementation._build_graph_system_prompt(self, record)

    def _update_rag_for_source(self, record: _SourceRecord, text: str) -> None:
        from . import _rag_build as implementation

        return implementation._update_rag_for_source(self, record, text)

    def _build_entity_vectors_for_source(
        self,
        source_id: str,
        content_hash: str,
    ) -> dict[str, Any]:
        from . import _rag_build as implementation

        return implementation._build_entity_vectors_for_source(
            self,
            source_id,
            content_hash,
        )

    def _delete_source_data(self, source_id: str) -> tuple[bool, bool]:
        from . import _delete as implementation

        return implementation._delete_source_data(self, source_id)

    def _delete_graph_for_source(self, source_id: str) -> bool:
        from . import _delete as implementation

        return implementation._delete_graph_for_source(self, source_id)

    def _delete_rag_for_source(self, source_id: str) -> bool:
        from . import _delete as implementation

        return implementation._delete_rag_for_source(self, source_id)

    def _replace_rag_vectors_with_recovery(
        self,
        rag_engine: RAGEngine,
        remove_ids: Sequence[int],
        add_ids: Sequence[int],
        vectors: np.ndarray,
    ) -> None:
        from . import _rag_build as implementation

        return implementation._replace_rag_vectors_with_recovery(
            self, rag_engine, remove_ids, add_ids, vectors
        )

    def _sync_chunk_nodes_for_source(
        self, source_id: str, content_hash: str, node_ids: set[str]
    ) -> None:
        from . import _rag_build as implementation

        return implementation._sync_chunk_nodes_for_source(
            self, source_id, content_hash, node_ids
        )

    def _remove_chunk_node_ids(self, node_ids: set[str]) -> None:
        from . import _rag_build as implementation

        return implementation._remove_chunk_node_ids(self, node_ids)

    def _current_graph_node_ids(self, source_id: str, content_hash: str) -> set[str]:
        from . import _rag_build as implementation

        return implementation._current_graph_node_ids(self, source_id, content_hash)

    def _refresh_graph_meta(self, *, changed: bool) -> None:
        connection = connect_graph(self.paths)
        try:
            counts = connection.execute(
                """
                    SELECT
                        (SELECT COUNT(*) FROM nodes) AS total_nodes,
                        (SELECT COUNT(*) FROM edges) AS total_edges,
                        (SELECT COUNT(*) FROM groups) AS total_groups
                    """
            ).fetchone()
        finally:
            connection.close()
        meta = read_graph_meta(self.paths)
        meta.update(
            {
                "total_nodes": int(counts["total_nodes"]),
                "total_edges": int(counts["total_edges"]),
                "total_groups": int(counts["total_groups"]),
            }
        )
        if changed:
            meta["changed_since_summary"] = (
                int(meta.get("changed_since_summary", 0)) + 1
            )
        write_graph_meta(self.paths, meta)

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        """日志写入失败不应破坏知识库事务。"""

        try:
            self._logger.log(
                "ingestor",
                action,
                detail,
                elapsed_ms,
                level,
            )
        except Exception:
            pass

    def _get_rag_engine(self) -> RAGEngine:
        if self._rag_engine is None:
            self._rag_engine = RAGEngine(
                self.paths.data_dir,
                settings=self.settings,
                embedder=self._embedder,
            )
        return self._rag_engine

    def _set_source_status(self, source_id: str, target: str, status: str) -> None:
        if target not in {"graph", "rag"} or status not in {
            "pending",
            "processing",
            "ready",
            "failed",
        }:
            raise ValueError("无效的 source 状态更新")
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                f"UPDATE sources SET {target}_status = ?, updated_at = ? WHERE source_id = ?",
                (status, _now_iso(), source_id),
            )
            connection.commit()
        finally:
            connection.close()

    def _mark_source_ready(
        self,
        source_id: str,
        target: str,
        content_hash: str,
    ) -> None:
        if target not in {"graph", "rag"}:
            raise ValueError("target 必须是 graph 或 rag")
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                f"""
                    UPDATE sources
                    SET {target}_hash = ?, {target}_status = 'ready', updated_at = ?
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


def ingest(
    paths: Sequence[Path | str] | None = None,
    mode: str = "both",
    *,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
    settings: AppConfig | None = None,
) -> dict[str, Any]:
    """使用默认 Provider 执行一次完整文档整理。"""

    return Ingestor(
        data_dir=data_dir,
        external_dir=external_dir,
        settings=settings,
    ).ingest(paths=paths, mode=mode)


def scan_sources(
    *,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
    settings: AppConfig | None = None,
) -> ScanResult:
    """使用默认配置执行一次 sources 扫描。"""

    ingestor = Ingestor(
        data_dir=data_dir,
        external_dir=external_dir,
        settings=settings,
    )
    with ingestor._write_lock:
        return ingestor.scan_sources()


def delete_document(
    source_id: str,
    *,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
    settings: AppConfig | None = None,
) -> dict[str, Any]:
    """使用默认配置删除一个文档并执行完整级联清理。"""

    return Ingestor(
        data_dir=data_dir,
        external_dir=external_dir,
        settings=settings,
    ).delete_document(source_id)


__all__ = [
    "DocumentNotFoundError",
    "Embedder",
    "FILE_MAP_VERSION",
    "FileMapError",
    "FileMapping",
    "FileMapStore",
    "GraphExtractionError",
    "IngestError",
    "Ingestor",
    "RecycleConflictError",
    "ScanResult",
    "SourceChangedDuringIngest",
    "delete_document",
    "ingest",
    "scan_sources",
    "chat_structured",
    "chat_with_tools",
    "supports_structured_output",
]
