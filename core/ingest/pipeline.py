from __future__ import annotations
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
import numpy as np
from provider.embedding import EmbeddingResult, embed
from core.settings.schema import AppConfig, PROJECT_ROOT
from core.settings.loader import load_config
from core.ingest.progress import advance_build, finish_build, start_build
from core.storage.schema import initialize_databases
from core.graph.draft import GraphDraft
from core.graph.tools import get_graph_tools
from core.logger import DailyTSVLogger
from core.rag.engine import RAGEngine
from core.ingest.file_map import FileMapStore
from core.ingest.scan import ScanResult, _SourceRecord
from core.common.time import elapsed_ms as _elapsed_ms
from core.ingest.paths import _resolve_external_dir

from core.ingest import scan, delete, graph_build, rag_build, sources, state, prompt
from core.ingest.errors import IngestError, SourceChangedDuringIngest
from core.storage.locks import get_knowledge_base_lock as _get_write_lock

DEFAULT_GRAPH_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_agent.md"
DEFAULT_GRAPH_EXTRACT_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_extract.md"
Embedder = Callable[[list[str]], EmbeddingResult]

class Ingestor:
    """Coordinate scanning, build pipelines and source deletion."""

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
        tool_provider: Callable[..., list[dict[str, Any]]] | None = None,
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
        self._tool_provider = tool_provider or get_graph_tools
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        if rag_engine is not None and rag_engine.paths.data_dir != self.paths.data_dir:
            raise IngestError("传入的 RAGEngine 与 Ingestor 不属于同一知识库")
        self._write_lock = _get_write_lock(self.paths.data_dir)

    def scan_sources(self) -> ScanResult:
        return scan.scan_sources(self)

    def _rag_chunking_config_changed(self) -> bool:
        return scan._rag_chunking_config_changed(self)

    def _graph_extraction_config_changed(self) -> bool:
        return scan._graph_extraction_config_changed(self)

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
        *,
        retry_failed: bool = False,
        force: bool = False,
        failed_only: bool = False,
    ) -> dict[str, Any]:
        """扫描并整理 pending 文档，返回 API 约定的统计结构。"""

        started_at = time.perf_counter()
        if mode not in {"graph", "rag", "both"}:
            raise ValueError("mode 必须是 graph、rag 或 both")
        with self._write_lock:
            # Preserve the failed scope before scan invalidates statuses for
            # changed content/configuration. Never retry unrelated pending work.
            failed_targets = {
                record.source_id: {
                    "graph": record.graph_status == "failed",
                    "rag": record.rag_status == "failed",
                }
                for record in self._select_active_sources(None, mode, include_failed=True)
            } if failed_only else {}
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
                include_failed=retry_failed or failed_only,
            )
            for record in records:
                graph_requested = mode in {"graph", "both"}
                rag_requested = mode in {"rag", "both"}
                graph_should_run = graph_requested and record.graph_status == "pending"
                rag_should_run = rag_requested and record.rag_status == "pending"
                if failed_only:
                    targets = failed_targets.get(record.source_id, {})
                    graph_should_run = graph_requested and targets.get("graph", False)
                    rag_should_run = rag_requested and targets.get("rag", False)
                elif force:
                    graph_should_run = graph_requested
                    rag_should_run = rag_requested
                elif requested_paths is not None:
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
                pipelines = [
                    pipeline for pipeline, run in
                    (("graph", graph_should_run), ("rag", rag_should_run)) if run
                ]
                for pipeline in pipelines:
                    start_build(self.paths, record.source_id, pipeline, record.content_hash)
                try:
                    text = self._read_source_text(record)
                except Exception as exc:
                    status = "pending" if isinstance(exc, SourceChangedDuringIngest) else "failed"
                    for pipeline in pipelines:
                        self._set_source_status(record.source_id, pipeline, status)
                        finish_build(self.paths, record.source_id, pipeline, str(exc))
                        detail[pipeline] = status
                    detail["error"] = str(exc)
                    result["failed"] += 1
                    result["details"].append(detail)
                    continue

                for pipeline in pipelines:
                    finish_build(self.paths, record.source_id, pipeline)
                if graph_should_run:
                    self._set_source_status(record.source_id, "graph", "processing")
                    try:
                        self._advance_build_step(record.source_id, "graph", "extract", "抽取实体与关系")
                        self._update_graph_for_source(record, text)
                        self._mark_source_ready(
                            record.source_id, "graph", record.content_hash
                        )
                        finish_build(self.paths, record.source_id, "graph")
                        detail["graph"] = "updated"
                        result["graph_updated"] += 1
                    except Exception as exc:
                        self._set_source_status(record.source_id, "graph", "failed")
                        finish_build(self.paths, record.source_id, "graph", str(exc))
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
                        self._advance_build_step(record.source_id, "rag", "chunk", "文档切片")
                        self._update_rag_for_source(record, text)
                        self._mark_source_ready(
                            record.source_id, "rag", record.content_hash
                        )
                        finish_build(self.paths, record.source_id, "rag")
                        detail["rag"] = "updated"
                        result["rag_updated"] += 1
                    except Exception as exc:
                        self._set_source_status(record.source_id, "rag", "failed")
                        finish_build(self.paths, record.source_id, "rag", str(exc))
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
        return delete.delete_document(self, source_id)

    def _delete_source_file(self, relative_path: str) -> None:
        return delete._delete_source_file(self, relative_path)

    def _normalize_requested_paths(self, paths: Sequence[Path | str] | None) -> set[str] | None:
        return sources._normalize_requested_paths(self, paths)

    def _select_active_sources(self, requested_paths: set[str] | None, mode: str, *, include_failed: bool=False) -> list[_SourceRecord]:
        return sources._select_active_sources(self, requested_paths, mode, include_failed=include_failed)

    def _read_source_text(self, record: _SourceRecord) -> str:
        return sources._read_source_text(self, record)

    def _register_concurrent_change(self, source_id: str, content_hash: str) -> None:
        return sources._register_concurrent_change(self, source_id, content_hash)

    def _update_graph_for_source(self, record: _SourceRecord, text: str) -> None:
        return graph_build._update_graph_for_source(self, record, text)

    def _advance_build_step(self, source_id: str, pipeline: str, step: str, label: str) -> None:
        advance_build(self.paths, source_id, pipeline, step, label)

    def _extract_graph_with_tools(self, record: _SourceRecord, text: str) -> None:
        return graph_build._extract_graph_with_tools(self, record, text)

    def _extract_graph_structured(self, record: _SourceRecord, text: str) -> None:
        return graph_build._extract_graph_structured(self, record, text)

    def _extract_graph_sections(self, record: _SourceRecord, sections: list[str]) -> list[GraphDraft]:
        return graph_build._extract_graph_sections(self, record, sections)

    def _build_graph_system_prompt(self, record: _SourceRecord) -> str:
        return prompt._build_graph_system_prompt(self, record)

    def _update_rag_for_source(self, record: _SourceRecord, text: str) -> None:
        return rag_build._update_rag_for_source(self, record, text)

    def _build_entity_vectors_for_source(self, source_id: str, content_hash: str) -> dict[str, Any]:
        return rag_build._build_entity_vectors_for_source(self, source_id, content_hash)

    def _delete_source_data(self, source_id: str) -> tuple[bool, bool]:
        return delete._delete_source_data(self, source_id)

    def _delete_graph_for_source(self, source_id: str) -> bool:
        return delete._delete_graph_for_source(self, source_id)

    def _delete_rag_for_source(self, source_id: str) -> bool:
        return delete._delete_rag_for_source(self, source_id)

    def _replace_rag_vectors_with_recovery(self, rag_engine: RAGEngine, remove_ids: Sequence[int], add_ids: Sequence[int], vectors: np.ndarray) -> None:
        return rag_build._replace_rag_vectors_with_recovery(self, rag_engine, remove_ids, add_ids, vectors)

    def _sync_chunk_nodes_for_source(self, source_id: str, content_hash: str, node_ids: set[str]) -> None:
        return rag_build._sync_chunk_nodes_for_source(self, source_id, content_hash, node_ids)

    def _remove_chunk_node_ids(self, node_ids: set[str]) -> None:
        return rag_build._remove_chunk_node_ids(self, node_ids)

    def _current_graph_node_ids(self, source_id: str, content_hash: str) -> set[str]:
        return rag_build._current_graph_node_ids(self, source_id, content_hash)

    def _refresh_graph_meta(self, *, changed: bool) -> None:
        return state._refresh_graph_meta(self, changed=changed)

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

    def _get_rag_engine(self, *, for_deletion: bool=False) -> RAGEngine:
        return rag_build._get_rag_engine(self, for_deletion=for_deletion)

    def _set_source_status(self, source_id: str, target: str, status: str) -> None:
        return state._set_source_status(self, source_id, target, status)

    def _mark_source_ready(self, source_id: str, target: str, content_hash: str) -> None:
        return state._mark_source_ready(self, source_id, target, content_hash)

    def _source_relative_path(self, source_id: str) -> str:
        return sources._source_relative_path(self, source_id)

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
