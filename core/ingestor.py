"""Markdown 文档扫描、增量 Graph/RAG 构建和来源映射。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from provider.embedding import EmbeddingResult, embed
from provider.engine import chat_with_tools

from .chunker import chunking_signature, document_chunks
from .config import AppConfig, PROJECT_ROOT, load_config
from .db import (
    connect_graph,
    connect_rag,
    connect_sources,
    initialize_databases,
    read_graph_meta,
    read_rag_meta,
    write_graph_meta,
)
from .rag_engine import RAGEngine
from .logger import DailyTSVLogger


FILE_MAP_VERSION = 1
DEFAULT_GRAPH_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_agent.md"
_WRITE_LOCKS: dict[Path, threading.RLock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


class IngestError(RuntimeError):
    """文档扫描或整理失败。"""


class GraphExtractionError(IngestError):
    """LLM 返回的文档图谱数据不符合约定。"""


class SourceChangedDuringIngest(IngestError):
    """扫描后、整理前文档再次发生变化。"""


class DocumentNotFoundError(IngestError):
    """请求删除的活动文档不存在。"""


class RecycleConflictError(IngestError):
    """回收站中已存在同路径文件，拒绝覆盖。"""


@dataclass(frozen=True)
class GraphEntity:
    """Deprecated：仅供旧 graph_extractor 注入和兼容测试使用。"""

    keyword: str
    summary: str
    aliases: list[str]
    tags: list[str]


@dataclass(frozen=True)
class GraphRelation:
    """Deprecated：仅供旧 graph_extractor 注入和兼容测试使用。"""

    source: str
    relation: str
    target: str
    evidence_weight: float


@dataclass(frozen=True)
class PreparedGraph:
    """Deprecated：生产图谱构建已改用工具调用循环。"""

    entities: list[GraphEntity]
    relations: list[GraphRelation]


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


GraphExtractor = Callable[[str], PreparedGraph]
Embedder = Callable[[list[str]], EmbeddingResult | list[list[float]]]


class FileMapError(RuntimeError):
    """file_map.json 格式无效时抛出的错误。"""


@dataclass(frozen=True)
class FileMapping:
    """一条原始路径与 Markdown 相对路径的一对一映射。"""

    original_path: str
    markdown_path: str


class FileMapStore:
    """external/markdown/file_map.json 的 CRUD 存储。"""

    def __init__(self, file_path: Path | str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            not self.file_path.exists()
            or not self.file_path.read_text(encoding="utf-8").strip()
        ):
            self._write([])

    @classmethod
    def from_config(cls, settings: AppConfig | None = None) -> "FileMapStore":
        active_settings = settings or load_config()
        return cls(active_settings.resolve_external_dir() / "file_map.json")

    def list(self) -> list[FileMapping]:
        """返回所有映射，保持文件中的顺序。"""

        payload = self._load_payload()
        mappings: list[FileMapping] = []
        for index, item in enumerate(payload["mappings"]):
            if not isinstance(item, dict):
                raise FileMapError(f"第 {index} 条映射必须是对象")
            original_path = item.get("original_path")
            markdown_path = item.get("markdown_path")
            if not isinstance(original_path, str) or not original_path.strip():
                raise FileMapError(f"第 {index} 条映射缺少有效 original_path")
            if not isinstance(markdown_path, str) or not markdown_path.strip():
                raise FileMapError(f"第 {index} 条映射缺少有效 markdown_path")
            mappings.append(
                FileMapping(
                    original_path=original_path,
                    markdown_path=markdown_path,
                )
            )
        return mappings

    def get_by_original(self, original_path: Path | str) -> FileMapping | None:
        lookup_key = _path_key(original_path)
        return next(
            (
                mapping
                for mapping in self.list()
                if _path_key(mapping.original_path) == lookup_key
            ),
            None,
        )

    def get_by_markdown(self, markdown_path: Path | str) -> FileMapping | None:
        lookup_key = _path_key(markdown_path)
        return next(
            (
                mapping
                for mapping in self.list()
                if _path_key(mapping.markdown_path) == lookup_key
            ),
            None,
        )

    def upsert(
        self, original_path: Path | str, markdown_path: Path | str
    ) -> FileMapping:
        """创建或更新映射，并保证两边路径都是一对一。"""

        mapping = FileMapping(
            original_path=_validated_path(original_path, "original_path"),
            markdown_path=_validated_path(markdown_path, "markdown_path"),
        )
        original_key = _path_key(mapping.original_path)
        markdown_key = _path_key(mapping.markdown_path)
        retained = [
            current
            for current in self.list()
            if _path_key(current.original_path) != original_key
            and _path_key(current.markdown_path) != markdown_key
        ]
        retained.append(mapping)
        self._write(retained)
        return mapping

    def delete_by_original(self, original_path: Path | str) -> bool:
        lookup_key = _path_key(original_path)
        current = self.list()
        retained = [
            mapping
            for mapping in current
            if _path_key(mapping.original_path) != lookup_key
        ]
        if len(retained) == len(current):
            return False
        self._write(retained)
        return True

    def delete_by_markdown(self, markdown_path: Path | str) -> bool:
        lookup_key = _path_key(markdown_path)
        current = self.list()
        retained = [
            mapping
            for mapping in current
            if _path_key(mapping.markdown_path) != lookup_key
        ]
        if len(retained) == len(current):
            return False
        self._write(retained)
        return True

    def _load_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FileMapError(f"映射表不是合法 JSON：{self.file_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise FileMapError("映射表根节点必须是对象")
        if payload.get("version") != FILE_MAP_VERSION:
            raise FileMapError(f"不支持的映射表版本：{payload.get('version')!r}")
        if not isinstance(payload.get("mappings"), list):
            raise FileMapError("mappings 必须是数组")
        return payload

    def _write(self, mappings: list[FileMapping]) -> None:
        payload = {
            "version": FILE_MAP_VERSION,
            "mappings": [asdict(mapping) for mapping in mappings],
        }
        temporary_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.file_path)


class Ingestor:
    """协调 sources 扫描、Graph/RAG 增量更新和删除级联。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        graph_extractor: GraphExtractor | None = None,
        embedder: Embedder | None = None,
        rag_engine: RAGEngine | None = None,
        graph_prompt_path: Path | str = DEFAULT_GRAPH_PROMPT_PATH,
    ) -> None:
        self.settings = settings or load_config()
        self.paths = initialize_databases(data_dir, self.settings)
        self.external_dir = _resolve_external_dir(external_dir, self.settings)
        self.external_dir.mkdir(parents=True, exist_ok=True)
        self.file_map = FileMapStore(self.external_dir / "file_map.json")
        self.graph_prompt_path = Path(graph_prompt_path)
        self._graph_extractor = graph_extractor
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
        """扫描全部 Markdown，并更新 sources 的身份、哈希和状态。"""

        started_at = time.perf_counter()
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
        newly_deleted_ids: list[str] = []
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
                            exists_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'pending', 'pending', 'active', ?, ?)
                        """,
                        (
                            source_id,
                            original_path,
                            relative_path,
                            path_hash,
                            content_hash,
                            now,
                            now,
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
                        if restored or content_hash != existing["graph_hash"]
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
                            exists_status = 'active', updated_at = ?
                        WHERE source_id = ?
                        """,
                        (
                            original_path,
                            relative_path,
                            content_hash,
                            graph_status,
                            rag_status,
                            now,
                            source_id,
                        ),
                    )
                    changed_ids.append(source_id)
                else:
                    if rag_config_changed and existing["exists_status"] == "active":
                        connection.execute(
                            """
                            UPDATE sources
                            SET rag_status = 'pending', updated_at = ?
                            WHERE source_id = ?
                            """,
                            (now, source_id),
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
                            SET original_path = ?, relative_path = ?, updated_at = ?
                            WHERE source_id = ?
                            """,
                            (original_path, relative_path, now, source_id),
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
        return bool(
            vector_count and meta.get("chunking_signature") != current_signature
        )

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
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

            records = self._select_active_sources(requested_paths, mode)
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
        """按 source_id 删除文档数据，并将 Markdown 移入回收站。"""

        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id 必须是非空字符串")
        source_id = source_id.strip()
        with self._write_lock:
            connection = connect_sources(self.paths)
            try:
                row = connection.execute(
                    """
                    SELECT source_id, relative_path
                    FROM sources
                    WHERE source_id = ? AND exists_status = 'active'
                    """,
                    (source_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                raise DocumentNotFoundError(f"活动文档不存在：{source_id}")

            relative_path = row["relative_path"]
            recycled_path = self._move_source_to_recycle(relative_path)
            connection = connect_sources(self.paths)
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE sources
                    SET exists_status = 'deleted', updated_at = ?
                    WHERE source_id = ? AND exists_status = 'active'
                    """,
                    (_now_iso(), source_id),
                )
                if cursor.rowcount != 1:
                    raise DocumentNotFoundError(f"活动文档不存在：{source_id}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            graph_changed, rag_changed = self._delete_source_data(source_id)
            result = {
                "deleted_source_id": source_id,
                "relative_path": relative_path,
                "recycled_path": recycled_path,
                "graph_deleted": graph_changed,
                "rag_deleted": rag_changed,
            }
            self._log_event(
                "delete_document",
                f"{relative_path}: graph={graph_changed}, rag={rag_changed}",
            )
            return result

    def _move_source_to_recycle(self, relative_path: str) -> str | None:
        source_path = _safe_source_path(self.external_dir, relative_path)
        if not source_path.exists():
            return None

        recycle_root = (self.external_dir.parent / "recycle").resolve()
        destination = (recycle_root / relative_path).resolve()
        try:
            destination.relative_to(recycle_root)
        except ValueError as exc:
            raise IngestError(f"非法回收站相对路径：{relative_path}") from exc
        meta_path = destination.with_name(destination.name + ".meta.json")
        if destination.exists() or meta_path.exists():
            raise RecycleConflictError(f"回收站中已存在同路径文件：{relative_path}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        metadata = {
            "original_path": relative_path,
            "recycled_at": now.isoformat(),
            "expires_at": (
                now + timedelta(days=self.settings.recycle_life_days)
            ).isoformat(),
        }
        shutil.move(str(source_path), str(destination))
        try:
            _write_json_atomic(meta_path, metadata)
        except Exception:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source_path))
            raise
        return destination.relative_to(recycle_root.parent).as_posix()

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
            if (mode in {"graph", "both"} and record.graph_status == "pending")
            or (mode in {"rag", "both"} and record.rag_status == "pending")
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
        if self._graph_extractor is None:
            self._extract_graph_with_llm(record, text)
            return

        # Deprecated 兼容路径：测试或外部调用方仍可注入 PreparedGraph extractor。
        prepared = _validate_prepared_graph(self._graph_extractor(text))
        self._update_graph_from_prepared(record, prepared)

    def _update_graph_from_prepared(
        self,
        record: _SourceRecord,
        prepared: PreparedGraph,
    ) -> None:
        """Deprecated：以旧 PreparedGraph 结构原子替换来源图谱。"""

        connection = connect_graph(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_node_ids = {
                row["node_id"]
                for row in connection.execute(
                    "SELECT node_id FROM node_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }
            old_edge_ids = {
                row["edge_id"]
                for row in connection.execute(
                    "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }

            node_ids_by_key: dict[str, str] = {}
            new_node_ids: set[str] = set()
            for entity in prepared.entities:
                node_id = _match_or_create_node(
                    connection,
                    entity,
                    preferred_node_ids=old_node_ids,
                )
                new_node_ids.add(node_id)
                for value in [entity.keyword, *entity.aliases]:
                    node_ids_by_key.setdefault(_semantic_key(value), node_id)

            new_edge_ids: set[str] = set()
            relation_bindings: list[tuple[str, float]] = []
            for relation in prepared.relations:
                source_node_id = node_ids_by_key[_semantic_key(relation.source)]
                target_node_id = node_ids_by_key[_semantic_key(relation.target)]
                edge_id = _match_or_create_edge(
                    connection,
                    source_node_id,
                    relation.relation,
                    target_node_id,
                )
                new_edge_ids.add(edge_id)
                relation_bindings.append((edge_id, relation.evidence_weight))

            connection.execute(
                "DELETE FROM node_sources WHERE source_id = ?", (record.source_id,)
            )
            connection.execute(
                "DELETE FROM edge_sources WHERE source_id = ?", (record.source_id,)
            )
            connection.executemany(
                """
                INSERT INTO node_sources (node_id, source_id, content_hash)
                VALUES (?, ?, ?)
                """,
                [
                    (node_id, record.source_id, record.content_hash)
                    for node_id in sorted(new_node_ids)
                ],
            )
            connection.executemany(
                """
                INSERT INTO edge_sources (
                    edge_id, source_id, content_hash, evidence_weight
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (edge_id, record.source_id, record.content_hash, weight)
                    for edge_id, weight in relation_bindings
                ],
            )
            _recalculate_edges(connection, old_edge_ids | new_edge_ids)
            orphan_node_ids = _recalculate_nodes(
                connection, old_node_ids | new_node_ids
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        self._remove_chunk_node_ids(orphan_node_ids)
        self._sync_chunk_nodes_for_source(
            record.source_id,
            record.content_hash,
            new_node_ids,
        )
        self._refresh_graph_meta(changed=True)

    def _extract_graph_with_llm(self, record: _SourceRecord, text: str) -> None:
        """使用工具调用循环构建图谱，全部工具写入共享文档事务。"""

        # 延迟导入避免 delete_tools -> Ingestor 形成模块初始化环。
        from provider.tools import get_graph_tools, get_tool_schemas

        system_prompt = self._build_graph_system_prompt(record)
        connection = connect_graph(self.paths)
        orphan_node_ids: set[str] = set()
        new_node_ids: set[str] = set()
        started_at = time.perf_counter()
        self._log_event(
            "graph_build_start",
            f"path={record.relative_path}, source_id={record.source_id}",
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_node_ids = {
                str(row["node_id"])
                for row in connection.execute(
                    "SELECT node_id FROM node_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }
            old_edge_ids = {
                str(row["edge_id"])
                for row in connection.execute(
                    "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }

            # 在尚未提交的事务中移除旧来源；任何后续失败都会恢复旧数据。
            connection.execute(
                "DELETE FROM node_sources WHERE source_id = ?", (record.source_id,)
            )
            connection.execute(
                "DELETE FROM edge_sources WHERE source_id = ?", (record.source_id,)
            )
            _recalculate_edges(connection, old_edge_ids)
            orphan_node_ids.update(_recalculate_nodes(connection, old_node_ids))

            if text.strip():
                registrations = get_graph_tools(
                    connection,
                    source_id=record.source_id,
                    content_hash=record.content_hash,
                )
                registrations_by_name = {
                    str(tool["name"]): tool for tool in registrations
                }
                schemas = get_tool_schemas("graph")
                finish_called = False

                def tool_handler(tool_name: str, args: dict[str, Any]) -> Any:
                    nonlocal finish_called
                    tool_started_at = time.perf_counter()
                    if tool_name == "finish":
                        finish_called = True
                        self._log_event(
                            "graph_tool_call",
                            f"{record.relative_path}: finish",
                            _elapsed_ms(tool_started_at),
                        )
                        return {"finished": True}

                    registration = registrations_by_name.get(tool_name)
                    if registration is None:
                        raise GraphExtractionError(f"Unknown tool: {tool_name}")
                    tool_args = dict(args)
                    result = registration["handler"](tool_args)
                    if not result.get("ok"):
                        error = str(result.get("error") or "工具执行失败")
                        self._log_event(
                            "graph_tool_call",
                            f"{tool_name}: {error}",
                            _elapsed_ms(tool_started_at),
                            level="WARNING",
                        )
                        raise GraphExtractionError(error)

                    data = result.get("data")
                    if (
                        tool_name == "delete_entity"
                        and isinstance(data, dict)
                        and data.get("deleted")
                    ):
                        orphan_node_ids.add(str(tool_args["node_id"]))

                    self._log_event(
                        "graph_tool_call",
                        f"{record.relative_path}: {tool_name}",
                        _elapsed_ms(tool_started_at),
                    )
                    return data

                llm_started_at = time.perf_counter()
                try:
                    chat_with_tools(
                        system=system_prompt,
                        user=text,
                        tools=schemas,
                        tool_handler=tool_handler,
                        settings=self.settings,
                        max_iterations=self.settings.graph_tool_max_iterations,
                    )
                except Exception:
                    self._log_event(
                        "llm_request",
                        (
                            f"purpose=graph_build, model={self.settings.models.llm}, "
                            "status=failed"
                        ),
                        _elapsed_ms(llm_started_at),
                        level="ERROR",
                    )
                    raise
                self._log_event(
                    "llm_request",
                    (
                        f"purpose=graph_build, model={self.settings.models.llm}, "
                        f"finish={finish_called}"
                    ),
                    _elapsed_ms(llm_started_at),
                )

            new_node_ids = {
                str(row["node_id"])
                for row in connection.execute(
                    "SELECT node_id FROM node_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }
            new_edge_ids = {
                str(row["edge_id"])
                for row in connection.execute(
                    "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            }
            _recalculate_edges(connection, new_edge_ids)
            orphan_node_ids.update(
                _recalculate_nodes(connection, old_node_ids | new_node_ids)
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            self._log_event(
                "graph_build_rollback",
                f"path={record.relative_path}, error={type(exc).__name__}",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise
        finally:
            connection.close()

        self._remove_chunk_node_ids(orphan_node_ids)
        self._sync_chunk_nodes_for_source(
            record.source_id,
            record.content_hash,
            new_node_ids,
        )
        self._refresh_graph_meta(changed=True)
        self._log_event(
            "graph_build_done",
            (
                f"path={record.relative_path}, source_id={record.source_id}, "
                f"nodes={len(new_node_ids)}"
            ),
            _elapsed_ms(started_at),
        )

    def _build_graph_system_prompt(self, record: _SourceRecord) -> str:
        """读取基础 prompt，并注入来源身份、内容哈希和知识库规模。"""

        try:
            base_prompt = self.graph_prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise IngestError(f"无法读取图谱提示词：{self.graph_prompt_path}") from exc
        if not base_prompt:
            raise IngestError(f"图谱提示词为空：{self.graph_prompt_path}")

        connection = connect_graph(self.paths)
        try:
            node_count = int(
                connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            )
        finally:
            connection.close()
        context = {
            "relative_path": record.relative_path,
            "source_id": record.source_id,
            "content_hash": record.content_hash,
            "existing_node_count": node_count,
        }
        return (
            f"{base_prompt}\n\n"
            "## 当前文档上下文（由系统注入，不得修改）\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
        )

    def _update_rag_for_source(self, record: _SourceRecord, text: str) -> None:
        started_at = time.perf_counter()
        self._log_event(
            "rag_build_start",
            f"path={record.relative_path}, source_id={record.source_id}",
        )
        chunk_specs = document_chunks(text, settings=self.settings)
        text_chunks = [item.content for item in chunk_specs]
        embedding_started_at = time.perf_counter()
        try:
            embedding_result = _coerce_embedding_result(self._embedder(text_chunks))
        except Exception:
            self._log_event(
                "embedding_request",
                (
                    f"purpose=rag_build, model={self.settings.models.embedding}, "
                    f"count={len(text_chunks)}, status=failed"
                ),
                _elapsed_ms(embedding_started_at),
                level="ERROR",
            )
            raise
        self._log_event(
            "embedding_request",
            (
                f"purpose=rag_build, model={self.settings.models.embedding}, "
                f"count={len(text_chunks)}"
            ),
            _elapsed_ms(embedding_started_at),
        )
        vectors = _validate_embedding_batch(
            embedding_result.vectors,
            expected_count=len(text_chunks),
            dimensions=self.settings.models.embedding_dimensions,
        )
        chunk_ids = [str(uuid4()) for _ in text_chunks]
        node_ids = self._current_graph_node_ids(record.source_id, record.content_hash)
        rag_engine = self._get_rag_engine()
        rag_engine.ensure_index_consistency()

        connection = connect_rag(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_vector_ids = [
                int(row["vector_id"])
                for row in connection.execute(
                    "SELECT vector_id FROM embeddings WHERE source_id = ?",
                    (record.source_id,),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM chunks WHERE source_id = ?", (record.source_id,)
            )
            existing_vector_spaces = {
                str(row["vector_space_id"])
                for row in connection.execute(
                    "SELECT DISTINCT vector_space_id FROM embeddings"
                ).fetchall()
            }
            if vectors and existing_vector_spaces.difference(
                {embedding_result.vector_space_id}
            ):
                raise IngestError(
                    "不能在同一个 FAISS 索引中混用 vector_space_id："
                    f"现有={sorted(existing_vector_spaces)}，"
                    f"新增={embedding_result.vector_space_id}"
                )
            new_vector_ids: list[int] = []
            for chunk_id, spec, vector in zip(
                chunk_ids, chunk_specs, vectors, strict=True
            ):
                parent_chunk_id = (
                    chunk_ids[spec.parent_index]
                    if spec.parent_index is not None
                    else None
                )
                connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, source_id, content, chunk_index,
                        token_count, granularity, parent_chunk_id,
                        token_start, token_end, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        record.source_id,
                        spec.content,
                        spec.chunk_index,
                        spec.token_end - spec.token_start,
                        spec.granularity,
                        parent_chunk_id,
                        spec.token_start,
                        spec.token_end,
                        _now_iso(),
                    ),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO embeddings (
                        chunk_id, source_id, vector_blob,
                        dimensions, model_name, vector_space_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        record.source_id,
                        vector.tobytes(),
                        self.settings.models.embedding_dimensions,
                        self.settings.models.embedding,
                        embedding_result.vector_space_id,
                        _now_iso(),
                    ),
                )
                new_vector_ids.append(int(cursor.lastrowid))
            connection.executemany(
                "INSERT INTO chunk_nodes (chunk_id, node_id) VALUES (?, ?)",
                [
                    (chunk_id, node_id)
                    for chunk_id in chunk_ids
                    for node_id in sorted(node_ids)
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        matrix = (
            np.vstack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty(
                (0, self.settings.models.embedding_dimensions), dtype=np.float32
            )
        )
        self._replace_rag_vectors_with_recovery(
            rag_engine,
            old_vector_ids,
            new_vector_ids,
            matrix,
        )
        self._log_event(
            "rag_build_done",
            f"path={record.relative_path}, chunks={len(chunk_ids)}",
            _elapsed_ms(started_at),
        )

    def _delete_source_data(self, source_id: str) -> tuple[bool, bool]:
        graph_changed = self._delete_graph_for_source(source_id)
        rag_changed = self._delete_rag_for_source(source_id)
        return graph_changed, rag_changed

    def _delete_graph_for_source(self, source_id: str) -> bool:
        connection = connect_graph(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_node_ids = {
                row["node_id"]
                for row in connection.execute(
                    "SELECT node_id FROM node_sources WHERE source_id = ?", (source_id,)
                ).fetchall()
            }
            old_edge_ids = {
                row["edge_id"]
                for row in connection.execute(
                    "SELECT edge_id FROM edge_sources WHERE source_id = ?", (source_id,)
                ).fetchall()
            }
            connection.execute(
                "DELETE FROM node_sources WHERE source_id = ?", (source_id,)
            )
            connection.execute(
                "DELETE FROM edge_sources WHERE source_id = ?", (source_id,)
            )
            _recalculate_edges(connection, old_edge_ids)
            orphan_node_ids = _recalculate_nodes(connection, old_node_ids)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        changed = bool(old_node_ids or old_edge_ids)
        if orphan_node_ids:
            self._remove_chunk_node_ids(orphan_node_ids)
        if changed:
            self._refresh_graph_meta(changed=True)
        return changed

    def _delete_rag_for_source(self, source_id: str) -> bool:
        rag_engine = self._get_rag_engine()
        rag_engine.ensure_index_consistency()
        connection = connect_rag(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_vector_ids = [
                int(row["vector_id"])
                for row in connection.execute(
                    "SELECT vector_id FROM embeddings WHERE source_id = ?", (source_id,)
                ).fetchall()
            ]
            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE source_id = ?", (source_id,)
                ).fetchone()[0]
            )
            connection.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        changed = bool(old_vector_ids or chunk_count)
        if changed:
            empty = np.empty(
                (0, self.settings.models.embedding_dimensions), dtype=np.float32
            )
            self._replace_rag_vectors_with_recovery(
                rag_engine,
                old_vector_ids,
                [],
                empty,
            )
        return changed

    def _replace_rag_vectors_with_recovery(
        self,
        rag_engine: RAGEngine,
        remove_ids: Sequence[int],
        add_ids: Sequence[int],
        vectors: np.ndarray,
    ) -> None:
        """增量替换失败时，以已提交的 rag.db 为权威源恢复索引。"""

        try:
            rag_engine.replace_vectors(remove_ids, add_ids, vectors)
        except Exception as replace_error:
            try:
                rebuild_started_at = time.perf_counter()
                rag_engine.rebuild_index()
                self._log_event(
                    "faiss_rebuild",
                    f"reason=replace_failed, vectors={len(add_ids)}",
                    _elapsed_ms(rebuild_started_at),
                    level="WARNING",
                )
            except Exception as rebuild_error:
                raise IngestError(
                    "FAISS 增量替换失败，且无法从 rag.db 恢复："
                    f"replace={replace_error}; rebuild={rebuild_error}"
                ) from rebuild_error

    def _sync_chunk_nodes_for_source(
        self,
        source_id: str,
        content_hash: str,
        node_ids: set[str],
    ) -> None:
        source_connection = connect_sources(self.paths)
        try:
            row = source_connection.execute(
                "SELECT rag_hash FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        finally:
            source_connection.close()
        should_link = row is not None and row["rag_hash"] == content_hash

        connection = connect_rag(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            chunk_ids = [
                row["chunk_id"]
                for row in connection.execute(
                    "SELECT chunk_id FROM chunks WHERE source_id = ?", (source_id,)
                ).fetchall()
            ]
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                connection.execute(
                    f"DELETE FROM chunk_nodes WHERE chunk_id IN ({placeholders})",
                    tuple(chunk_ids),
                )
                if should_link:
                    connection.executemany(
                        "INSERT INTO chunk_nodes (chunk_id, node_id) VALUES (?, ?)",
                        [
                            (chunk_id, node_id)
                            for chunk_id in chunk_ids
                            for node_id in sorted(node_ids)
                        ],
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _remove_chunk_node_ids(self, node_ids: set[str]) -> None:
        if not node_ids:
            return
        placeholders = ",".join("?" for _ in node_ids)
        connection = connect_rag(self.paths)
        try:
            connection.execute(
                f"DELETE FROM chunk_nodes WHERE node_id IN ({placeholders})",
                tuple(sorted(node_ids)),
            )
            connection.commit()
        finally:
            connection.close()

    def _current_graph_node_ids(self, source_id: str, content_hash: str) -> set[str]:
        source_connection = connect_sources(self.paths)
        try:
            row = source_connection.execute(
                "SELECT graph_hash FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        finally:
            source_connection.close()
        if row is None or row["graph_hash"] != content_hash:
            return set()
        graph_connection = connect_graph(self.paths)
        try:
            rows = graph_connection.execute(
                """
                SELECT node_id FROM node_sources
                WHERE source_id = ? AND content_hash = ?
                """,
                (source_id, content_hash),
            ).fetchall()
        finally:
            graph_connection.close()
        return {row["node_id"] for row in rows}

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
            self._rag_engine = RAGEngine(self.paths.data_dir, settings=self.settings)
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


def _parse_graph_response(response: str) -> PreparedGraph:
    """Deprecated：仅用于兼容旧 JSON 图谱响应。"""

    if not isinstance(response, str) or not response.strip():
        raise GraphExtractionError("图谱 LLM 返回了空响应")
    candidate = response.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise GraphExtractionError(f"图谱 LLM 响应不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise GraphExtractionError("图谱 LLM 响应根节点必须是对象")
    raw_entities = payload.get("entities")
    raw_relations = payload.get("relations")
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise GraphExtractionError("图谱 LLM 响应必须包含 entities 和 relations 数组")

    entities: list[GraphEntity] = []
    for index, item in enumerate(raw_entities):
        if not isinstance(item, dict):
            raise GraphExtractionError(f"第 {index} 个 entity 必须是对象")
        entities.append(
            GraphEntity(
                keyword=item.get("keyword"),
                summary=item.get("summary"),
                aliases=item.get("aliases", []),
                tags=item.get("tags", []),
            )
        )
    relations: list[GraphRelation] = []
    for index, item in enumerate(raw_relations):
        if not isinstance(item, dict):
            raise GraphExtractionError(f"第 {index} 个 relation 必须是对象")
        relations.append(
            GraphRelation(
                source=item.get("source"),
                relation=item.get("relation"),
                target=item.get("target"),
                evidence_weight=item.get("evidence_weight", 1.0),
            )
        )
    return _validate_prepared_graph(
        PreparedGraph(entities=entities, relations=relations)
    )


def _validate_prepared_graph(prepared: PreparedGraph) -> PreparedGraph:
    if not isinstance(prepared, PreparedGraph):
        raise GraphExtractionError("graph_extractor 必须返回 PreparedGraph")

    entities_by_key: dict[str, GraphEntity] = {}
    entity_order: list[str] = []
    for index, entity in enumerate(prepared.entities):
        if not isinstance(entity, GraphEntity):
            raise GraphExtractionError(f"第 {index} 个实体必须是 GraphEntity")
        keyword = _required_text(entity.keyword, f"第 {index} 个实体 keyword")
        summary = _required_text(entity.summary, f"第 {index} 个实体 summary")
        aliases = _validate_string_list(entity.aliases, f"第 {index} 个实体 aliases")
        tags = _validate_string_list(entity.tags, f"第 {index} 个实体 tags")
        key = _semantic_key(keyword)
        normalized = GraphEntity(keyword, summary, aliases, tags)
        previous = entities_by_key.get(key)
        if previous is None:
            entities_by_key[key] = normalized
            entity_order.append(key)
        else:
            entities_by_key[key] = GraphEntity(
                keyword=previous.keyword,
                summary=summary
                if len(summary) >= len(previous.summary)
                else previous.summary,
                aliases=_unique_texts([*previous.aliases, *aliases]),
                tags=_unique_texts([*previous.tags, *tags]),
            )

    endpoint_map: dict[str, GraphEntity] = {}
    for entity in entities_by_key.values():
        for value in [entity.keyword, *entity.aliases]:
            endpoint_map.setdefault(_semantic_key(value), entity)

    relations_by_key: dict[tuple[str, str, str], GraphRelation] = {}
    relation_order: list[tuple[str, str, str]] = []
    for index, relation in enumerate(prepared.relations):
        if not isinstance(relation, GraphRelation):
            raise GraphExtractionError(f"第 {index} 个关系必须是 GraphRelation")
        source = _required_text(relation.source, f"第 {index} 个关系 source")
        target = _required_text(relation.target, f"第 {index} 个关系 target")
        relation_text = _required_text(relation.relation, f"第 {index} 个关系 relation")
        if not 2 <= len(relation_text) <= 8:
            raise GraphExtractionError(f"第 {index} 个关系描述必须为 2~8 个字符")
        if (
            not isinstance(relation.evidence_weight, (int, float))
            or isinstance(relation.evidence_weight, bool)
            or not 0.0 <= float(relation.evidence_weight) <= 1.0
        ):
            raise GraphExtractionError(f"第 {index} 个关系权重必须在 0~1")
        source_entity = endpoint_map.get(_semantic_key(source))
        target_entity = endpoint_map.get(_semantic_key(target))
        if source_entity is None or target_entity is None:
            raise GraphExtractionError(
                f"第 {index} 个关系的 source/target 必须出现在 entities 中"
            )
        normalized = GraphRelation(
            source=source_entity.keyword,
            relation=relation_text,
            target=target_entity.keyword,
            evidence_weight=float(relation.evidence_weight),
        )
        key = (
            _semantic_key(normalized.source),
            normalized.relation,
            _semantic_key(normalized.target),
        )
        previous = relations_by_key.get(key)
        if previous is None:
            relations_by_key[key] = normalized
            relation_order.append(key)
        elif normalized.evidence_weight > previous.evidence_weight:
            relations_by_key[key] = normalized

    return PreparedGraph(
        entities=[entities_by_key[key] for key in entity_order],
        relations=[relations_by_key[key] for key in relation_order],
    )


def _match_or_create_node(
    connection: sqlite3.Connection,
    entity: GraphEntity,
    *,
    preferred_node_ids: set[str],
) -> str:
    """Deprecated：仅供 PreparedGraph 兼容路径使用。"""

    entity_terms = {_semantic_key(value) for value in [entity.keyword, *entity.aliases]}
    candidates: list[tuple[int, float, sqlite3.Row, list[str], list[str]]] = []
    rows = connection.execute(
        "SELECT node_id, keyword, summary, aliases, tags FROM nodes"
    ).fetchall()
    for row in rows:
        aliases = _load_json_string_list(row["aliases"], row["node_id"], "aliases")
        tags = _load_json_string_list(row["tags"], row["node_id"], "tags")
        node_terms = {_semantic_key(value) for value in [row["keyword"], *aliases]}
        if not entity_terms.intersection(node_terms):
            continue
        similarity = SequenceMatcher(
            None,
            _semantic_key(entity.summary),
            _semantic_key(row["summary"]),
        ).ratio()
        preferred = 1 if row["node_id"] in preferred_node_ids else 0
        if preferred or similarity >= 0.55:
            candidates.append((preferred, similarity, row, aliases, tags))

    if candidates:
        _, _, selected, existing_aliases, existing_tags = max(
            candidates,
            key=lambda item: (item[0], item[1], item[2]["node_id"]),
        )
        merged_aliases = _unique_texts(
            [
                *existing_aliases,
                *entity.aliases,
                *([entity.keyword] if entity.keyword != selected["keyword"] else []),
            ]
        )
        merged_tags = _unique_texts([*existing_tags, *entity.tags])
        connection.execute(
            """
            UPDATE nodes
            SET summary = ?, aliases = ?, tags = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (
                entity.summary,
                json.dumps(merged_aliases, ensure_ascii=False),
                json.dumps(merged_tags, ensure_ascii=False),
                _now_iso(),
                selected["node_id"],
            ),
        )
        return selected["node_id"]

    node_id = str(uuid4())
    now = _now_iso()
    connection.execute(
        """
        INSERT INTO nodes (
            node_id, keyword, summary, aliases, tags,
            ref_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            node_id,
            entity.keyword,
            entity.summary,
            json.dumps(entity.aliases, ensure_ascii=False),
            json.dumps(entity.tags, ensure_ascii=False),
            now,
            now,
        ),
    )
    return node_id


def _match_or_create_edge(
    connection: sqlite3.Connection,
    source_node_id: str,
    relation: str,
    target_node_id: str,
) -> str:
    """Deprecated：仅供 PreparedGraph 兼容路径使用。"""

    row = connection.execute(
        """
        SELECT edge_id FROM edges
        WHERE source_node_id = ? AND relation = ? AND target_node_id = ?
        """,
        (source_node_id, relation, target_node_id),
    ).fetchone()
    if row is not None:
        return row["edge_id"]
    edge_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO edges (
            edge_id, source_node_id, relation, target_node_id,
            weight, support_count, created_at
        ) VALUES (?, ?, ?, ?, 0, 0, ?)
        """,
        (edge_id, source_node_id, relation, target_node_id, _now_iso()),
    )
    return edge_id


def _recalculate_edges(
    connection: sqlite3.Connection,
    edge_ids: set[str],
) -> None:
    for edge_id in edge_ids:
        aggregate = connection.execute(
            """
            SELECT COUNT(*) AS support_count,
                   MAX(evidence_weight) AS weight
            FROM edge_sources WHERE edge_id = ?
            """,
            (edge_id,),
        ).fetchone()
        if int(aggregate["support_count"]) == 0:
            connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
        else:
            connection.execute(
                "UPDATE edges SET support_count = ?, weight = ? WHERE edge_id = ?",
                (
                    int(aggregate["support_count"]),
                    float(aggregate["weight"]),
                    edge_id,
                ),
            )


def _recalculate_nodes(
    connection: sqlite3.Connection,
    node_ids: set[str],
) -> set[str]:
    orphan_node_ids: set[str] = set()
    for node_id in node_ids:
        ref_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM node_sources WHERE node_id = ?", (node_id,)
            ).fetchone()[0]
        )
        if ref_count == 0:
            connection.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            orphan_node_ids.add(node_id)
        else:
            connection.execute(
                "UPDATE nodes SET ref_count = ?, updated_at = ? WHERE node_id = ?",
                (ref_count, _now_iso(), node_id),
            )
    return orphan_node_ids


def _coerce_embedding_result(
    value: EmbeddingResult | list[list[float]],
) -> EmbeddingResult:
    """消费 EmbeddingResult，并暂时兼容旧测试注入的裸向量列表。"""

    if isinstance(value, EmbeddingResult):
        if (
            not isinstance(value.vector_space_id, str)
            or not value.vector_space_id.strip()
        ):
            raise IngestError("EmbeddingResult.vector_space_id 不能为空")
        return EmbeddingResult(
            vectors=value.vectors,
            vector_space_id=value.vector_space_id.strip(),
        )
    if isinstance(value, list):
        return EmbeddingResult(vectors=value, vector_space_id="unknown")
    raise IngestError("Embedding 返回值必须是 EmbeddingResult")


def _validate_embedding_batch(
    vectors: list[list[float]],
    *,
    expected_count: int,
    dimensions: int,
) -> list[np.ndarray]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        actual = len(vectors) if isinstance(vectors, list) else "非列表"
        raise IngestError(
            f"Embedding 返回数量不一致：期望 {expected_count}，实际 {actual}"
        )
    validated: list[np.ndarray] = []
    for index, vector in enumerate(vectors):
        try:
            array = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise IngestError(f"第 {index} 个 embedding 含非数值内容") from exc
        if array.ndim != 1 or len(array) != dimensions:
            raise IngestError(
                f"第 {index} 个 embedding 维度错误：期望 {dimensions}，实际 {array.shape}"
            )
        if not np.isfinite(array).all():
            raise IngestError(f"第 {index} 个 embedding 包含 NaN 或 Infinity")
        validated.append(array)
    return validated


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


def _resolve_external_dir(
    value: Path | str | None,
    settings: AppConfig,
) -> Path:
    if value is None:
        return settings.resolve_external_dir()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _safe_source_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IngestError(f"非法 Markdown 相对路径：{relative_path}") from exc
    if candidate.suffix.casefold() != ".md":
        raise IngestError(f"源文件不是 Markdown：{relative_path}")
    return candidate


def _hash_relative_path(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path.replace("\\", "/"))
    normalized = normalized.strip("/").casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _semantic_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphExtractionError(f"{field_name} 必须是非空字符串")
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _validate_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GraphExtractionError(f"{field_name} 必须是字符串数组")
    return _unique_texts(value)


def _unique_texts(values: Sequence[str]) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            results.append(normalized)
    return results


def _load_json_string_list(value: Any, node_id: str, field_name: str) -> list[str]:
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        raise IngestError(f"nodes.{field_name} 不是 JSON 文本：node_id={node_id}")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise IngestError(
            f"nodes.{field_name} 不是合法 JSON：node_id={node_id}"
        ) from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise IngestError(f"nodes.{field_name} 必须是字符串数组：node_id={node_id}")
    return parsed


def _get_write_lock(data_dir: Path) -> threading.RLock:
    resolved = data_dir.resolve()
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(resolved, threading.RLock())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _validated_path(value: Path | str, field_name: str) -> str:
    text = os.fspath(value).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _path_key(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(value).strip()))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
