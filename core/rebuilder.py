"""变化文档重建与可恢复的全项目影子重建。"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import AppConfig, load_config
from .db import (
    DatabasePaths,
    connect_graph,
    connect_rag,
    connect_sources,
    initialize_databases,
)
from .ingestor import Ingestor
from .locks import get_knowledge_base_lock
from .logger import DailyTSVLogger
from .rag_engine import RAGEngine


ProgressCallback = Callable[[float, str], None]


class RebuildError(RuntimeError):
    """知识库重建未通过完整性检查。"""


class KnowledgeBaseRebuilder:
    def __init__(
        self,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths = initialize_databases(data_dir, self.settings)
        self.external_dir = (
            Path(external_dir).expanduser().resolve()
            if external_dir is not None
            else self.settings.resolve_external_dir()
        )
        self._write_lock = get_knowledge_base_lock(self.paths.data_dir)
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )

    def rebuild_changed(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        _progress(progress, 0.05, "扫描变化、新增、删除及上次失败的文档")
        ingestor = Ingestor(
            self.paths.data_dir,
            self.external_dir,
            settings=self.settings,
        )
        result = ingestor.ingest(mode="both", retry_failed=True)
        _progress(progress, 0.95, "Graph、RAG 与 FAISS 增量重建完成")
        if int(result.get("failed", 0)):
            raise RebuildError(
                f"变化文档重建有 {result['failed']} 个失败项："
                + _failed_details(result)
            )
        return result

    def rebuild_all(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """在同级影子目录重建，验证成功后才切换正式数据目录。"""

        started_at = time.perf_counter()
        data_dir = self.paths.data_dir.resolve()
        parent = data_dir.parent.resolve()
        if data_dir == parent or not data_dir.name:
            raise RebuildError(f"拒绝重建不安全的数据目录：{data_dir}")
        parent.mkdir(parents=True, exist_ok=True)

        with self._write_lock:
            _progress(progress, 0.02, "创建影子知识库")
            shadow_dir = Path(
                tempfile.mkdtemp(prefix=f".{data_dir.name}.rebuild-", dir=parent)
            ).resolve()
            backup_dir: Path | None = None
            try:
                shadow_paths = initialize_databases(shadow_dir, self.settings)
                stable_ids = _seed_shadow_sources(self.paths, shadow_paths)
                _progress(progress, 0.08, "已保留来源身份，开始重读 Markdown")
                ingestor = Ingestor(
                    shadow_dir,
                    self.external_dir,
                    settings=self.settings,
                )
                ingest_result = ingestor.ingest(mode="both", retry_failed=True)
                if int(ingest_result.get("failed", 0)):
                    raise RebuildError(
                        f"影子知识库有 {ingest_result['failed']} 个失败项："
                        + _failed_details(ingest_result)
                    )
                _progress(progress, 0.8, "模型构建完成，校验数据库与向量索引")
                validation = _validate_shadow(
                    shadow_paths,
                    self.external_dir,
                    self.settings,
                    stable_ids,
                )
                _assert_external_unchanged(shadow_paths, self.external_dir)

                # 任务记录在构建期间仍写入正式 sources.db；切换前取最新快照。
                _copy_job_history(self.paths.sources_db, shadow_paths.sources_db)
                # 搜索历史是运行数据而非知识投影；全量重建后仍应保留为过期历史。
                _copy_search_cache_history(
                    self.paths.search_cache_db,
                    shadow_paths.search_cache_db,
                )
                _progress(progress, 0.93, "影子知识库校验通过，切换正式目录")
                backup_dir = parent / (
                    f".{data_dir.name}.backup-"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                    f"{uuid4().hex[:8]}"
                )
                _swap_directories(data_dir, shadow_dir, backup_dir)
                _progress(progress, 0.98, "正式知识库已切换，旧版本保留为可恢复备份")
            except Exception:
                if shadow_dir.exists():
                    shutil.rmtree(shadow_dir, ignore_errors=True)
                self._log("rebuild_all_rollback", "shadow_discarded", level="ERROR")
                raise

        result = {
            "ingest": ingest_result,
            "validation": validation,
            "stable_source_ids": len(stable_ids),
            "backup_path": str(backup_dir) if backup_dir is not None else None,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
        }
        self._log(
            "rebuild_all_done",
            f"sources={validation['active_sources']}, backup={backup_dir}",
            result["elapsed_ms"],
        )
        return result

    def _log(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log("rebuilder", action, detail, elapsed_ms, level)
        except Exception:
            pass


def _seed_shadow_sources(
    production: DatabasePaths,
    shadow: DatabasePaths,
) -> dict[str, str]:
    source = connect_sources(production)
    target = connect_sources(shadow)
    try:
        rows = source.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        target.execute("BEGIN IMMEDIATE")
        target.execute("DELETE FROM sources")
        for row in rows:
            active = row["exists_status"] == "active"
            target.execute(
                """
                INSERT INTO sources (
                    source_id, original_path, relative_path, path_hash,
                    content_hash, graph_hash, rag_hash, graph_status, rag_status,
                    exists_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["source_id"],
                    row["original_path"],
                    row["relative_path"],
                    row["path_hash"],
                    row["content_hash"],
                    None if active else row["graph_hash"],
                    None if active else row["rag_hash"],
                    "pending" if active else row["graph_status"],
                    "pending" if active else row["rag_status"],
                    row["exists_status"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()
    return {
        str(row["path_hash"]): str(row["source_id"])
        for row in rows
        if row["exists_status"] == "active"
    }


def _validate_shadow(
    paths: DatabasePaths,
    external_dir: Path,
    settings: AppConfig,
    stable_ids: dict[str, str],
) -> dict[str, Any]:
    sources = connect_sources(paths)
    try:
        rows = sources.execute(
            "SELECT * FROM sources WHERE exists_status = 'active' ORDER BY source_id"
        ).fetchall()
        not_ready = [
            str(row["relative_path"])
            for row in rows
            if row["graph_status"] != "ready"
            or row["rag_status"] != "ready"
            or row["graph_hash"] != row["content_hash"]
            or row["rag_hash"] != row["content_hash"]
        ]
        if not_ready:
            raise RebuildError("影子来源未全部就绪：" + ", ".join(not_ready[:10]))
        for row in rows:
            expected = stable_ids.get(str(row["path_hash"]))
            if expected is not None and expected != row["source_id"]:
                raise RebuildError(
                    f"来源 ID 未保持稳定：{row['relative_path']}"
                )
        source_hashes = {
            str(row["source_id"]): str(row["content_hash"])
            for row in rows
        }
    finally:
        sources.close()

    graph = connect_graph(paths)
    try:
        for table in ("node_sources", "edge_sources", "entity_mentions", "relation_mentions"):
            stale = graph.execute(
                f"SELECT source_id, content_hash FROM {table}"
            ).fetchall()
            invalid = [
                row for row in stale
                if source_hashes.get(str(row["source_id"])) != str(row["content_hash"])
            ]
            if invalid:
                raise RebuildError(f"{table} 存在失效来源哈希绑定")
        graph_counts = graph.execute(
            """
            SELECT (SELECT COUNT(*) FROM nodes),
                   (SELECT COUNT(*) FROM edges),
                   (SELECT COUNT(*) FROM entity_mentions),
                   (SELECT COUNT(*) FROM relation_mentions)
            """
        ).fetchone()
        node_summary_hashes = {
            str(row["node_id"]): hashlib.sha256(
                str(row["summary"]).encode("utf-8")
            ).hexdigest()
            for row in graph.execute("SELECT node_id, summary FROM nodes").fetchall()
        }
        group_summary_hashes = {
            str(row["group_id"]): hashlib.sha256(
                str(row["summary"]).encode("utf-8")
            ).hexdigest()
            for row in graph.execute("SELECT group_id, summary FROM groups").fetchall()
        }
    finally:
        graph.close()

    rag = connect_rag(paths)
    try:
        embeddings = rag.execute(
            """
            SELECT vector_id, dimensions, model_name, vector_space_id
            FROM embeddings ORDER BY vector_id
            """
        ).fetchall()
        for row in embeddings:
            if int(row["dimensions"]) != settings.models.embedding_dimensions:
                raise RebuildError("Embedding 维度与配置不一致")
            if row["model_name"] != settings.models.embedding:
                raise RebuildError("Embedding 模型与配置不一致")
            if not isinstance(row["vector_space_id"], str) or not row["vector_space_id"].strip():
                raise RebuildError("Embedding 缺少 vector_space_id")
        vector_ids = {int(row["vector_id"]) for row in embeddings}
        entity_embeddings = rag.execute(
            """
            SELECT vector_id, node_id, summary_hash, dimensions,
                   model_name, vector_space_id
            FROM entity_embeddings ORDER BY vector_id
            """
        ).fetchall()
        community_embeddings = rag.execute(
            """
            SELECT vector_id, group_id, summary_hash, dimensions,
                   model_name, vector_space_id
            FROM community_embeddings ORDER BY vector_id
            """
        ).fetchall()
        _validate_auxiliary_shadow_rows(
            entity_embeddings,
            id_column="node_id",
            expected_hashes=node_summary_hashes,
            settings=settings,
        )
        _validate_auxiliary_shadow_rows(
            community_embeddings,
            id_column="group_id",
            expected_hashes=group_summary_hashes,
            settings=settings,
        )
        entity_vector_ids = {
            int(row["vector_id"]) for row in entity_embeddings
        }
        community_vector_ids = {
            int(row["vector_id"]) for row in community_embeddings
        }
        rag_counts = rag.execute(
            "SELECT (SELECT COUNT(*) FROM chunks), (SELECT COUNT(*) FROM chunk_nodes)"
        ).fetchone()
    finally:
        rag.close()

    engine = RAGEngine(paths.data_dir, settings=settings)
    engine.ensure_index_consistency()
    if engine.index.ids != vector_ids:
        raise RebuildError("FAISS ID 与 rag.db embeddings 不一致")
    if engine.entity_index.ids != entity_vector_ids:
        raise RebuildError("实体 FAISS ID 与 entity_embeddings 不一致")
    if engine.community_index.ids != community_vector_ids:
        raise RebuildError("群组 FAISS ID 与 community_embeddings 不一致")
    return {
        "active_sources": len(rows),
        "nodes": int(graph_counts[0]),
        "edges": int(graph_counts[1]),
        "entity_mentions": int(graph_counts[2]),
        "relation_mentions": int(graph_counts[3]),
        "chunks": int(rag_counts[0]),
        "chunk_node_links": int(rag_counts[1]),
        "vectors": len(vector_ids),
        "entity_vectors": len(entity_vector_ids),
        "community_vectors": len(community_vector_ids),
        "faiss_healthy": True,
    }


def _validate_auxiliary_shadow_rows(
    rows: list[sqlite3.Row],
    *,
    id_column: str,
    expected_hashes: dict[str, str],
    settings: AppConfig,
) -> None:
    spaces: set[str] = set()
    for row in rows:
        object_id = str(row[id_column])
        if expected_hashes.get(object_id) != str(row["summary_hash"]):
            raise RebuildError(f"{id_column}={object_id} 的摘要向量已失效")
        if int(row["dimensions"]) != settings.models.embedding_dimensions:
            raise RebuildError(f"{id_column}={object_id} 的向量维度不一致")
        if str(row["model_name"]) != settings.models.embedding:
            raise RebuildError(f"{id_column}={object_id} 的向量模型不一致")
        vector_space_id = str(row["vector_space_id"]).strip()
        if not vector_space_id:
            raise RebuildError(f"{id_column}={object_id} 缺少 vector_space_id")
        spaces.add(vector_space_id)
    if len(spaces) > 1:
        raise RebuildError(f"{id_column} 向量混用了多个 vector_space_id")


def _assert_external_unchanged(paths: DatabasePaths, external_dir: Path) -> None:
    sources = connect_sources(paths)
    try:
        rows = sources.execute(
            """
            SELECT relative_path, content_hash FROM sources
            WHERE exists_status = 'active'
            """
        ).fetchall()
    finally:
        sources.close()
    for row in rows:
        path = (external_dir / str(row["relative_path"])).resolve()
        try:
            path.relative_to(external_dir.resolve())
        except ValueError as exc:
            raise RebuildError(f"非法来源路径：{row['relative_path']}") from exc
        if not path.is_file():
            raise RebuildError(f"重建期间来源文件消失：{row['relative_path']}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != row["content_hash"]:
            raise RebuildError(f"重建期间来源文件变化：{row['relative_path']}")


def _copy_job_history(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path)
    try:
        jobs = source.execute("SELECT * FROM maintenance_jobs").fetchall()
        events = source.execute("SELECT * FROM maintenance_job_events").fetchall()
        target.execute("PRAGMA foreign_keys = ON")
        target.execute("BEGIN IMMEDIATE")
        target.execute("DELETE FROM maintenance_job_events")
        target.execute("DELETE FROM maintenance_jobs")
        target.executemany(
            """
            INSERT INTO maintenance_jobs (
                job_id, kind, status, progress, detail, result_json, error,
                created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [tuple(row) for row in jobs],
        )
        target.executemany(
            """
            INSERT INTO maintenance_job_events (
                event_id, job_id, level, message, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [tuple(row) for row in events],
        )
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def _copy_search_cache_history(source_path: Path, target_path: Path) -> None:
    """用 SQLite 在线备份保留搜索历史，避免复制半写入数据库文件。"""

    if not source_path.is_file():
        return
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        source.close()
        target.close()


def _swap_directories(data_dir: Path, shadow_dir: Path, backup_dir: Path) -> None:
    if backup_dir.exists():
        raise RebuildError(f"备份目标已存在：{backup_dir}")
    moved_original = False
    try:
        if data_dir.exists():
            _replace_with_retry(data_dir, backup_dir)
            moved_original = True
        _replace_with_retry(shadow_dir, data_dir)
    except Exception:
        if moved_original and backup_dir.exists() and not data_dir.exists():
            _replace_with_retry(backup_dir, data_dir)
        raise


def _replace_with_retry(source: Path, target: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(30):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last_error = exc
            if attempt == 29:
                break
            time.sleep(0.1)
    raise RebuildError(f"无法切换知识库目录：{source} -> {target}: {last_error}")


def _failed_details(result: dict[str, Any]) -> str:
    details = [
        (
            f"{item.get('path') or 'unknown'}: "
            f"{item.get('graph_error') or item.get('rag_error') or item.get('error') or 'failed'}"
        )
        for item in result.get("details", [])
        if isinstance(item, dict)
        and (item.get("graph") == "failed" or item.get("rag") == "failed")
    ]
    return ", ".join(details[:10]) or "详见任务日志"


def _progress(callback: ProgressCallback | None, value: float, detail: str) -> None:
    if callback is not None:
        callback(max(0.0, min(1.0, float(value))), detail)


__all__ = ["KnowledgeBaseRebuilder", "ProgressCallback", "RebuildError"]
