"""供 CLI、外部 API 和 Web 入口共用的知识库门面。"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from provider.engine import chat
from provider.tools.document_tools import (
    DocumentConversionError,
    import_document as convert_document,
)

from .config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from .db import (
    connect_graph,
    connect_rag,
    connect_sources,
    get_database_paths,
    read_graph_meta,
    write_graph_meta,
)
from .graph_engine import GraphEngine
from .hybrid import HybridEngine
from .ingestor import (
    DocumentNotFoundError,
    FileMapError,
    IngestError,
    Ingestor,
    RecycleConflictError,
)
from .logger import DailyTSVLogger
from .rag_engine import FaissIndexManager, RAGEngine


class KnowledgeBaseNotInitializedError(RuntimeError):
    """知识库的 sources.db 尚不存在。"""


class KnowledgeBaseProcessingError(RuntimeError):
    """所请求的数据当前正在整理。"""


class DocumentImportError(RuntimeError):
    """文档导入入口错误基类。"""


class UnsupportedDocumentFormatError(DocumentImportError):
    """用户上传了不受支持的文件格式。"""


class DocumentImportPathError(DocumentImportError):
    """导入路径不安全、缺失或不是普通文件。"""


class DocumentTooLargeError(DocumentImportError):
    """导入文件超过服务端大小上限。"""


class DocumentIngestError(DocumentImportError):
    """转换已完成，但后续知识库整理无法启动。"""


SUPPORTED_IMPORT_SUFFIXES = frozenset(
    {".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm", ".rst", ".csv"}
)
MAX_IMPORT_BYTES = 50 * 1024 * 1024


class KnowledgeBaseService:
    """将入口层请求转换为稳定的 core 调用。"""

    def __init__(
        self,
        *,
        settings: AppConfig | None = None,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.config_path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
        self._data_dir_override = (
            Path(data_dir).expanduser().resolve() if data_dir is not None else None
        )
        self._external_dir_override = (
            Path(external_dir).expanduser().resolve()
            if external_dir is not None
            else None
        )
        self.data_dir = (
            self._data_dir_override or self.settings.resolve_data_dir()
        )
        self.external_dir = (
            self._external_dir_override or self.settings.resolve_external_dir()
        )
        self.paths = get_database_paths(self.data_dir)
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )

    def status(self) -> dict[str, Any]:
        """返回 03 文档约定的知识库状态，不隐式初始化数据库。"""

        initialized = self.paths.sources_db.exists()
        result: dict[str, Any] = {
            "initialized": initialized,
            "sources": {
                "total": 0,
                "active": 0,
                "pending_graph": 0,
                "pending_rag": 0,
            },
            "graph": {"total_nodes": 0, "total_edges": 0, "total_groups": 0},
            "rag": {
                "total_chunks": 0,
                "total_vectors": 0,
                "faiss_healthy": False,
            },
        }
        if not initialized:
            return result

        source_connection = connect_sources(self.paths)
        try:
            source_counts = source_connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN exists_status = 'active' THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN exists_status = 'active' AND graph_status = 'pending'
                                THEN 1 ELSE 0 END) AS pending_graph,
                       SUM(CASE WHEN exists_status = 'active' AND rag_status = 'pending'
                                THEN 1 ELSE 0 END) AS pending_rag
                FROM sources
                """
            ).fetchone()
        finally:
            source_connection.close()
        result["sources"] = {
            "total": int(source_counts["total"] or 0),
            "active": int(source_counts["active"] or 0),
            "pending_graph": int(source_counts["pending_graph"] or 0),
            "pending_rag": int(source_counts["pending_rag"] or 0),
        }

        if self.paths.graph_db.exists():
            graph_connection = connect_graph(self.paths)
            try:
                graph_counts = graph_connection.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM nodes) AS total_nodes,
                           (SELECT COUNT(*) FROM edges) AS total_edges,
                           (SELECT COUNT(*) FROM groups) AS total_groups
                    """
                ).fetchone()
            finally:
                graph_connection.close()
            result["graph"] = {
                "total_nodes": int(graph_counts["total_nodes"]),
                "total_edges": int(graph_counts["total_edges"]),
                "total_groups": int(graph_counts["total_groups"]),
            }

        vector_ids: set[int] = set()
        if self.paths.rag_db.exists():
            rag_connection = connect_rag(self.paths)
            try:
                rag_counts = rag_connection.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM chunks) AS total_chunks,
                           (SELECT COUNT(*) FROM embeddings) AS total_vectors
                    """
                ).fetchone()
                vector_ids = {
                    int(row["vector_id"])
                    for row in rag_connection.execute(
                        "SELECT vector_id FROM embeddings"
                    ).fetchall()
                }
            finally:
                rag_connection.close()
            result["rag"] = {
                "total_chunks": int(rag_counts["total_chunks"]),
                "total_vectors": int(rag_counts["total_vectors"]),
                "faiss_healthy": self._faiss_is_healthy(vector_ids),
            }
        return result

    def list_documents(self, status: str | None = None) -> list[dict[str, Any]]:
        """返回所有文档的基本信息，支持按状态筛选。"""
        if not self.paths.sources_db.exists():
            return []
        connection = connect_sources(self.paths)
        try:
            if status == "pending":
                rows = connection.execute(
                    """
                    SELECT source_id, relative_path, original_path,
                           content_hash, graph_hash, rag_hash,
                           graph_status, rag_status, exists_status,
                           created_at, updated_at
                    FROM sources
                    WHERE exists_status = 'active'
                      AND (graph_status = 'pending' OR rag_status = 'pending')
                    ORDER BY relative_path
                    """
                ).fetchall()
            elif status == "active":
                rows = connection.execute(
                    """
                    SELECT source_id, relative_path, original_path,
                           content_hash, graph_hash, rag_hash,
                           graph_status, rag_status, exists_status,
                           created_at, updated_at
                    FROM sources
                    WHERE exists_status = 'active'
                    ORDER BY relative_path
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT source_id, relative_path, original_path,
                           content_hash, graph_hash, rag_hash,
                           graph_status, rag_status, exists_status,
                           created_at, updated_at
                    FROM sources
                    ORDER BY exists_status, relative_path
                    """
                ).fetchall()
        finally:
            connection.close()
        return [
            {
                "source_id": row["source_id"],
                "relative_path": row["relative_path"],
                "original_path": row["original_path"],
                "graph_status": row["graph_status"],
                "rag_status": row["rag_status"],
                "exists_status": row["exists_status"],
                "updated_at": row["updated_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_document_content(self, source_id: str) -> dict[str, Any]:
        """返回指定文档的 Markdown 文本内容。"""
        self._require_initialized()
        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                """
                SELECT source_id, relative_path, exists_status
                FROM sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise DocumentNotFoundError(f"文档不存在：{source_id}")
        if row["exists_status"] != "active":
            raise DocumentNotFoundError(f"文档已删除：{source_id}")

        file_path = self.external_dir / row["relative_path"]
        if not file_path.exists():
            raise DocumentNotFoundError(f"文档文件缺失：{row['relative_path']}")

        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DocumentNotFoundError(f"无法读取文档：{row['relative_path']}: {exc}") from exc

        return {
            "source_id": row["source_id"],
            "relative_path": row["relative_path"],
            "content": content,
        }

    def delete_node(self, node_id: str) -> dict[str, Any]:
        """删除指定节点，按 06 文档规则执行级联操作。"""
        self._require_available("graph")
        with Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )._write_lock:
            result = self._execute_node_deletion(node_id)
        self._log_event(
            "delete_node",
            f"node_id={node_id}, edges={result['cascade_deleted_edges']}",
        )
        return result

    def _execute_node_deletion(self, node_id: str) -> dict[str, Any]:
        graph_connection = connect_graph(self.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            node_row = graph_connection.execute(
                "SELECT node_id, keyword FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if node_row is None:
                raise DocumentNotFoundError(f"节点不存在：{node_id}")

            source_rows = graph_connection.execute(
                """
                SELECT ns.source_id, s.relative_path
                FROM node_sources ns
                JOIN sources s ON s.source_id = ns.source_id
                WHERE ns.node_id = ?
                """,
                (node_id,),
            ).fetchall()

            recycled_files: list[str] = []
            unlinked_files: list[str] = []
            for s_row in source_rows:
                other_nodes = int(
                    graph_connection.execute(
                        "SELECT COUNT(*) FROM node_sources WHERE source_id = ? AND node_id != ?",
                        (s_row["source_id"], node_id),
                    ).fetchone()[0]
                )
                if other_nodes == 0:
                    self._move_source_to_recycle(s_row["relative_path"])
                    recycled_files.append(s_row["relative_path"])
                else:
                    unlinked_files.append(s_row["relative_path"])

            edge_ids = {
                row["edge_id"]
                for row in graph_connection.execute(
                    """
                    SELECT edge_id FROM edges
                    WHERE source_node_id = ? OR target_node_id = ?
                    """,
                    (node_id, node_id),
                ).fetchall()
            }
            graph_connection.execute(
                "DELETE FROM edge_sources WHERE edge_id IN ("
                + ",".join("?" for _ in edge_ids)
                + ")",
                tuple(sorted(edge_ids)),
            ) if edge_ids else None
            for edge_id in edge_ids:
                graph_connection.execute(
                    "DELETE FROM edges WHERE edge_id = ?", (edge_id,)
                )

            graph_connection.execute(
                "DELETE FROM node_sources WHERE node_id = ?", (node_id,)
            )
            graph_connection.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            graph_connection.execute("DELETE FROM groups")
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        # 清理 chunk_nodes 中的孤立引用
        rag_connection = connect_rag(self.paths)
        try:
            rag_connection.execute(
                "DELETE FROM chunk_nodes WHERE node_id = ?", (node_id,)
            )
            rag_connection.commit()
        finally:
            rag_connection.close()

        self._refresh_graph_meta(changed=True)
        return {
            "deleted_node_id": node_id,
            "deleted_keyword": node_row["keyword"],
            "cascade_deleted_edges": len(edge_ids),
            "recycled_files": recycled_files,
            "unlinked_files": unlinked_files,
        }

    def _move_source_to_recycle(self, relative_path: str) -> str | None:
        """将 Markdown 文件移入回收站。回滚保证原子性。"""
        source_path = (self.external_dir / relative_path).resolve()
        try:
            source_path.relative_to(self.external_dir)
        except ValueError as exc:
            raise IngestError(f"非法 Markdown 相对路径：{relative_path}") from exc
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
            return str(destination.relative_to(recycle_root.parent))

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

    def cleanup_recycle(self, *, force: bool = False) -> dict[str, Any]:
        """清理回收站文件；``force`` 为真时永久清空全部内容。"""
        started_at = time.perf_counter()
        external_parent = self.external_dir.parent.resolve()
        recycle_root = (external_parent / "recycle").resolve()
        if recycle_root.parent != external_parent or recycle_root.name != "recycle":
            raise IngestError("回收站目录不安全，拒绝执行清理")

        if not recycle_root.exists():
            if force:
                recycle_root.mkdir(parents=True, exist_ok=True)
            result = {"deleted": 0, "forced": force}
            self._log_event(
                "recycle_cleanup",
                f"force={str(force).lower()}, deleted=0",
                _elapsed_ms(started_at),
            )
            return result

        if not recycle_root.is_dir():
            raise IngestError("回收站路径不是目录，拒绝执行清理")

        if force:
            deleted = _clear_directory_contents(recycle_root)
            result = {"deleted": deleted, "forced": True}
            self._log_event(
                "recycle_cleanup",
                f"force=true, deleted={deleted}",
                _elapsed_ms(started_at),
            )
            return result

        now = datetime.now(timezone.utc)
        deleted = 0
        for meta_path in sorted(recycle_root.rglob("*.meta.json")):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(payload["expires_at"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

            if expires_at > now:
                continue

            base = meta_path.with_name(meta_path.name.replace(".meta.json", ""))
            meta_path.unlink(missing_ok=True)
            if base.exists():
                base.unlink()
                deleted += 1

        result = {"deleted": deleted, "forced": False}
        self._log_event(
            "recycle_cleanup",
            f"force=false, deleted={deleted}",
            _elapsed_ms(started_at),
        )
        return result

    def generate_group_summaries(self) -> dict[str, Any]:
        """按 06 文档规则执行一次节点群总结。"""
        started_at = time.perf_counter()
        self._log_event("group_summary_start", "status=started")
        self._require_available("graph")
        meta = read_graph_meta(self.paths)
        changed = int(meta.get("changed_since_summary", 0))
        if changed < self.settings.summary_trigger_file_count:
            result = {
                "generated": 0,
                "changed_files": changed,
                "threshold": self.settings.summary_trigger_file_count,
                "skipped_reason": "改动数量未达阈值",
            }
            self._log_event(
                "group_summary_done",
                f"generated=0, changed={changed}, skipped=threshold",
                _elapsed_ms(started_at),
            )
            return result

        components = self._find_connected_components()
        qualified = [
            comp
            for comp in components
            if len(comp) >= 3 and self._component_depth(comp) >= 3
        ]
        if not qualified:
            write_graph_meta(self.paths, {**meta, "changed_since_summary": 0})
            result = {
                "generated": 0,
                "qualified_components": 0,
                "total_components": len(components),
                "changed_files": changed,
            }
            self._log_event(
                "group_summary_done",
                f"generated=0, changed={changed}, skipped=no_component",
                _elapsed_ms(started_at),
            )
            return result

        graph_connection = connect_graph(self.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            generated = 0
            for component in qualified:
                summary = self._summarize_component(component)
                group_id = str(uuid4())
                now_str = _now_iso()
                graph_connection.execute(
                    """
                    INSERT INTO groups (group_id, summary, node_count, edge_count, created_at, updated_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (group_id, summary, len(component), now_str, now_str),
                )
                graph_connection.executemany(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES (?, ?)",
                    [(group_id, node_id) for node_id in sorted(component)],
                )
                generated += 1
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        write_graph_meta(self.paths, {**meta, "changed_since_summary": 0, "last_summary_at": _now_iso()})
        self._refresh_graph_meta(changed=False)
        result = {
            "generated": generated,
            "qualified_components": len(qualified),
            "total_components": len(components),
            "changed_files": changed,
        }
        self._log_event(
            "group_summary_done",
            f"generated={generated}, changed={changed}",
            _elapsed_ms(started_at),
        )
        return result

    def _find_connected_components(self) -> list[set[str]]:
        """从 graph.db 中查找所有连通分量。"""
        connection = connect_graph(self.paths)
        try:
            node_ids = {
                row["node_id"]
                for row in connection.execute("SELECT node_id FROM nodes").fetchall()
            }
            adjacency: dict[str, set[str]] = {nid: set() for nid in node_ids}
            for row in connection.execute(
                "SELECT source_node_id, target_node_id FROM edges"
            ).fetchall():
                a, b = row["source_node_id"], row["target_node_id"]
                if a in adjacency and b in adjacency:
                    adjacency[a].add(b)
                    adjacency[b].add(a)
        finally:
            connection.close()

        visited: set[str] = set()
        components: list[set[str]] = []
        for node_id in sorted(node_ids):
            if node_id in visited:
                continue
            component: set[str] = set()
            frontier = {node_id}
            while frontier:
                current = frontier.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                frontier.update(adjacency.get(current, set()) - visited)
            components.append(component)
        return components

    def _component_depth(self, component: set[str]) -> int:
        """计算连通分量中任意节点能到达的最大 BFS 深度。"""
        connection = connect_graph(self.paths)
        try:
            adjacency: dict[str, set[str]] = {nid: set() for nid in component}
            for row in connection.execute(
                """
                SELECT source_node_id, target_node_id FROM edges
                WHERE source_node_id IN ({}) AND target_node_id IN ({})
                """.format(
                    ",".join("?" * len(component)),
                    ",".join("?" * len(component)),
                ),
                tuple(sorted(component)) + tuple(sorted(component)),
            ).fetchall():
                a, b = row["source_node_id"], row["target_node_id"]
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)
        finally:
            connection.close()

        max_depth = 0
        for start in component:
            visited = {start}
            frontier = {start}
            depth = 0
            while frontier and depth <= 5:
                next_frontier: set[str] = set()
                for nid in frontier:
                    for neighbor in adjacency.get(nid, set()):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_frontier.add(neighbor)
                if next_frontier:
                    depth += 1
                frontier = next_frontier
            max_depth = max(max_depth, depth)
        return max_depth

    def _summarize_component(self, component: set[str]) -> str:
        """用 LLM 为一个节点群生成总结。"""
        connection = connect_graph(self.paths)
        try:
            nodes_info: list[str] = []
            placeholders = ",".join("?" for _ in component)
            for row in connection.execute(
                f"SELECT keyword, summary FROM nodes WHERE node_id IN ({placeholders})",
                tuple(sorted(component)),
            ).fetchall():
                nodes_info.append(f"- {row['keyword']}: {row['summary']}")
            edges_info: list[str] = []
            for row in connection.execute(
                f"""
                SELECT n1.keyword AS src_kw, e.relation, n2.keyword AS tgt_kw
                FROM edges e
                JOIN nodes n1 ON n1.node_id = e.source_node_id
                JOIN nodes n2 ON n2.node_id = e.target_node_id
                WHERE e.source_node_id IN ({placeholders})
                  AND e.target_node_id IN ({placeholders})
                """,
                tuple(sorted(component)) + tuple(sorted(component)),
            ).fetchall():
                edges_info.append(f"- {row['src_kw']} -[{row['relation']}]-> {row['tgt_kw']}")
        finally:
            connection.close()

        system_prompt = (
            "你是知识图谱群组总结助手。根据给出的节点摘要和关系，"
            "用 2~3 句话总结该群组的核心主题和关键关系。"
            "要求：简洁、专业，只输出一段中文总结，不要额外解释。"
        )
        user_prompt = (
            f"节点（{len(nodes_info)} 个）：\n"
            + "\n".join(nodes_info[:20])
            + "\n\n关系（{len(edges_info)} 条）：\n"
            + "\n".join(edges_info[:30])
            + "\n\n请为该群组生成一段总结。"
        )

        started_at = time.perf_counter()
        try:
            summary = chat(system_prompt, user_prompt, settings=self.settings).strip()
            self._log_event(
                "llm_request",
                    f"purpose=group_summary, model={self.settings.models.llm}",
                _elapsed_ms(started_at),
            )
            return summary
        except Exception:
            self._log_event(
                "llm_request",
                    f"purpose=group_summary, model={self.settings.models.llm}, status=failed",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            return ""

    def _refresh_graph_meta(self, *, changed: bool) -> None:
        """刷新 graph_meta.json，并在有变更时递增 changed_since_summary。"""
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
        meta.update({
            "total_nodes": int(counts["total_nodes"]),
            "total_edges": int(counts["total_edges"]),
            "total_groups": int(counts["total_groups"]),
        })
        if changed:
            meta["changed_since_summary"] = int(meta.get("changed_since_summary", 0)) + 1
        write_graph_meta(self.paths, meta)

    def get_config(self) -> dict[str, Any]:
        """返回可由 PUT 原样保存的完整配置；其中仅含密钥环境变量名。"""
        return self.settings.model_dump(mode="json")

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """保存配置并校验，返回新配置。"""
        from .config import AppConfig

        new_config = AppConfig.model_validate(data)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary.write_text(
            new_config.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.config_path)
        self.settings = new_config
        self.data_dir = self._data_dir_override or new_config.resolve_data_dir()
        self.external_dir = (
            self._external_dir_override or new_config.resolve_external_dir()
        )
        self.paths = get_database_paths(self.data_dir)
        self._logger = DailyTSVLogger(
            new_config.resolve_log_dir(),
            new_config.log_level,
        )
        return self.get_config()

    def import_document(
        self,
        source_path: Path | str,
        *,
        ingest_after_import: bool = True,
        _original_identity: str | None = None,
    ) -> dict[str, Any]:
        """安全转换任意受支持文件、注册来源，并可立即执行整理。"""

        started_at = time.perf_counter()
        try:
            source = _resolve_import_source(source_path)
        except DocumentImportPathError as exc:
            self._log_event(
                "document_import_failed",
                f"error={type(exc).__name__}",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise
        suffix = source.suffix.casefold()
        if suffix not in SUPPORTED_IMPORT_SUFFIXES:
            self._log_event(
                "document_import_failed",
                f"file={source.name}, error=UnsupportedDocumentFormatError",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise UnsupportedDocumentFormatError(
                f"不支持的文档格式：{suffix or '<无扩展名>'}"
            )
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise DocumentImportPathError(f"无法读取文件信息：{source.name}") from exc
        if size > MAX_IMPORT_BYTES:
            self._log_event(
                "document_import_failed",
                f"file={source.name}, error=DocumentTooLargeError",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise DocumentTooLargeError(
                f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限：{source.name}"
            )

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        identity = _original_identity or str(source)
        existing_mapping = ingestor.file_map.get_by_original(identity)
        markdown_relative_path = (
            existing_mapping.markdown_path
            if existing_mapping is not None
            else _stable_markdown_name(source.name, identity)
        )
        destination = _safe_markdown_destination(
            self.external_dir,
            markdown_relative_path,
        )
        previous_bytes = destination.read_bytes() if destination.exists() else None
        self._log_event(
            "document_import_start",
            f"file={source.name}, format={suffix.removeprefix('.')}, size={size}",
        )

        with ingestor._write_lock:
            try:
                conversion = convert_document(
                    source,
                    self.external_dir,
                    destination_name=markdown_relative_path,
                )
                self._log_event(
                    "document_convert",
                    f"file={source.name}, format={conversion['format']}",
                    _elapsed_ms(started_at),
                )
                try:
                    ingestor.file_map.upsert(identity, markdown_relative_path)
                except Exception:
                    _restore_import_destination(destination, previous_bytes)
                    raise
                ingestor.scan_sources()
            except (DocumentConversionError, FileMapError) as exc:
                self._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise
            except Exception as exc:
                self._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise DocumentImportError(
                    f"文档注册失败：{source.name}: {type(exc).__name__}"
                ) from exc

            source_id = _source_id_for_relative_path(
                self.paths,
                markdown_relative_path,
            )
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
            "original_filename": source.name,
            "detected_format": _detected_format(suffix),
            "markdown_relative_path": markdown_relative_path,
            "conversion_status": "completed",
            "ingest_status": ingest_status,
            "size": size,
        }
        if ingest_result is not None:
            result["ingest"] = ingest_result
        if ingest_error:
            result["ingest_error"] = ingest_error
        self._log_event(
            "document_import_done",
            f"file={source.name}, source_id={source_id}, ingest={ingest_status}",
            _elapsed_ms(started_at),
            level="ERROR" if ingest_status == "failed" else "INFO",
        )
        return result

    def upload_file(self, content: str, filename: str) -> dict[str, Any]:
        """将文本内容保存为 Markdown 文件，并注册到 sources。"""
        safe_filename = filename.strip()
        if not safe_filename.endswith(".md"):
            safe_filename += ".md"
        safe_filename = safe_filename.replace("\\", "/").split("/")[-1]

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        dest_path = self.external_dir / safe_filename
        with ingestor._write_lock:
            if dest_path.exists():
                raise RecycleConflictError(f"文件已存在：{safe_filename}")
            dest_path.write_text(content, encoding="utf-8")
            try:
                ingestor.scan_sources()
            except Exception:
                dest_path.unlink(missing_ok=True)
                raise

        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                "SELECT source_id FROM sources WHERE relative_path = ?",
                (safe_filename,),
            ).fetchone()
        finally:
            connection.close()
        result = {
            "source_id": row["source_id"] if row is not None else None,
            "filename": safe_filename,
            "path": dest_path.relative_to(self.external_dir).as_posix(),
            "size": len(content),
        }
        self._log_event(
            "document_import_done",
            f"file={safe_filename}, source_id={result['source_id']}, ingest=pending",
        )
        return result

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
    ) -> dict[str, Any]:
        return Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        ).ingest(paths=paths, mode=mode)

    def query_graph(
        self,
        query: str,
        *,
        depth: int = 3,
        direction: str = "both",
        confidence: float | None = None,
    ) -> dict[str, Any]:
        self._require_available("graph")
        return GraphEngine(self.data_dir, settings=self.settings).query(
            query,
            depth=depth,
            direction=direction,
            confidence=confidence,
        )

    def query_rag(
        self,
        query: str,
        *,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> dict[str, Any]:
        self._require_available("rag")
        return RAGEngine(self.data_dir, settings=self.settings).query(
            query,
            top_k=top_k,
            threshold=threshold,
        )

    def query_hybrid(
        self,
        query: str,
        *,
        graph_depth: int = 3,
        rag_top_k: int | None = None,
        graph_confidence: float | None = None,
        rag_threshold: float | None = None,
        direction: str = "both",
    ) -> dict[str, Any]:
        self._require_available("graph", "rag")
        return HybridEngine(self.data_dir, settings=self.settings).query(
            query,
            graph_depth=graph_depth,
            rag_top_k=rag_top_k,
            graph_confidence=graph_confidence,
            rag_threshold=rag_threshold,
            direction=direction,
        )

    def delete_document(self, source_id: str) -> dict[str, Any]:
        self._require_initialized()
        return Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        ).delete_document(source_id)

    def get_full_graph(self) -> dict[str, Any]:
        """返回前端力导向图可直接使用的全部节点、边和群。"""

        self._require_available("graph")
        connection = connect_graph(self.paths)
        try:
            node_rows = connection.execute(
                """
                SELECT node_id, keyword, summary, aliases, tags, ref_count,
                       created_at, updated_at
                FROM nodes ORDER BY keyword, node_id
                """
            ).fetchall()
            edge_rows = connection.execute(
                """
                SELECT edge_id, source_node_id, relation, target_node_id,
                       weight, support_count, created_at
                FROM edges ORDER BY edge_id
                """
            ).fetchall()
            group_rows = connection.execute(
                """
                SELECT g.group_id, g.summary, g.node_count, g.edge_count,
                       g.created_at, g.updated_at, gn.node_id
                FROM groups g
                LEFT JOIN group_nodes gn ON gn.group_id = g.group_id
                ORDER BY g.group_id, gn.node_id
                """
            ).fetchall()
        finally:
            connection.close()

        groups_by_id: dict[str, dict[str, Any]] = {}
        for row in group_rows:
            group = groups_by_id.setdefault(
                row["group_id"],
                {
                    "group_id": row["group_id"],
                    "summary": row["summary"],
                    "node_count": int(row["node_count"] or 0),
                    "edge_count": int(row["edge_count"] or 0),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "node_ids": [],
                },
            )
            if row["node_id"] is not None:
                group["node_ids"].append(row["node_id"])

        return {
            "nodes": [
                {
                    "node_id": row["node_id"],
                    "keyword": row["keyword"],
                    "summary": row["summary"],
                    "aliases": _parse_json_list(row["aliases"]),
                    "tags": _parse_json_list(row["tags"]),
                    "ref_count": int(row["ref_count"] or 0),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in node_rows
            ],
            "edges": [
                {
                    "edge_id": row["edge_id"],
                    "source_node_id": row["source_node_id"],
                    "relation": row["relation"],
                    "target_node_id": row["target_node_id"],
                    "weight": float(row["weight"]),
                    "support_count": int(row["support_count"]),
                    "created_at": row["created_at"],
                }
                for row in edge_rows
            ],
            "groups": list(groups_by_id.values()),
        }

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log(
                "knowledge_base",
                action,
                detail,
                elapsed_ms,
                level,
            )
        except Exception:
            pass

    def _require_initialized(self) -> None:
        if not self.paths.sources_db.exists():
            raise KnowledgeBaseNotInitializedError("知识库尚未初始化")

    def _require_available(self, *targets: str) -> None:
        self._require_initialized()
        conditions: list[str] = []
        if "graph" in targets:
            conditions.append("graph_status = 'processing'")
        if "rag" in targets:
            conditions.append("rag_status = 'processing'")
        connection = connect_sources(self.paths)
        try:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sources "
                    "WHERE exists_status = 'active' AND ("
                    + " OR ".join(conditions)
                    + ")"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        if count:
            raise KnowledgeBaseProcessingError("知识库正在处理中，请稍后重试")

    def _faiss_is_healthy(self, vector_ids: set[int]) -> bool:
        if not self.paths.faiss_index.exists():
            return not vector_ids
        try:
            manager = FaissIndexManager(
                self.paths.faiss_index,
                self.settings.models.embedding_dimensions,
            )
            return manager.ids == vector_ids
        except Exception:
            return False


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise ValueError("图谱中的 JSON 数组字段格式错误")
    return parsed


def _clear_directory_contents(root: Path) -> int:
    """删除目录中的全部内容且不跟随符号链接，返回永久删除的文件数。"""

    deleted = 0
    for child in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        junction_check = getattr(child, "is_junction", None)
        is_junction = bool(junction_check and junction_check())
        if child.is_symlink() or is_junction:
            if not child.name.endswith(".meta.json"):
                deleted += 1
            if is_junction and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
            continue
        if child.is_dir():
            deleted += _clear_directory_contents(child)
            child.rmdir()
            continue
        child.unlink()
        if not child.name.endswith(".meta.json"):
            deleted += 1
    return deleted


def _resolve_import_source(value: Path | str) -> Path:
    if not isinstance(value, (str, Path)):
        raise DocumentImportPathError("source_path 必须是路径字符串")
    raw = os.fspath(value).strip()
    if not raw:
        raise DocumentImportPathError("source_path 不能为空")
    candidate = Path(raw).expanduser()
    if ".." in candidate.parts:
        raise DocumentImportPathError("导入路径不能包含 '..'")
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.exists():
        raise DocumentImportPathError(f"文件不存在：{resolved.name}")
    if not resolved.is_file():
        raise DocumentImportPathError(f"路径不是普通文件：{resolved.name}")
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise DocumentImportPathError(f"文件不可读：{resolved.name}") from exc
    return resolved


def _stable_markdown_name(filename: str, identity: str) -> str:
    stem = Path(filename).stem
    safe_stem = "".join(
        "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
        for character in stem
    ).strip(" .") or "document"
    digest = hashlib.sha256(
        os.path.normcase(os.path.normpath(identity)).encode("utf-8")
    ).hexdigest()[:10]
    return f"{safe_stem}-{digest}.md"


def _safe_markdown_destination(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DocumentImportPathError("Markdown 映射路径不安全")
    destination = (root / relative).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise DocumentImportPathError("Markdown 映射路径超出目标目录") from exc
    if destination.suffix.casefold() != ".md":
        raise DocumentImportPathError("Markdown 映射路径必须以 .md 结尾")
    return destination


def _restore_import_destination(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.restore")
    try:
        temporary.write_bytes(previous)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_id_for_relative_path(paths: Any, relative_path: str) -> str:
    connection = connect_sources(paths)
    try:
        row = connection.execute(
            "SELECT source_id FROM sources WHERE relative_path = ? AND exists_status = 'active'",
            (relative_path,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise DocumentImportError(f"导入后未注册来源：{relative_path}")
    return str(row["source_id"])


def _detected_format(suffix: str) -> str:
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    return suffix.removeprefix(".")


def _ingest_error_summary(result: dict[str, Any]) -> str:
    messages: list[str] = []
    for detail in result.get("details", []):
        if not isinstance(detail, dict):
            continue
        for key in ("error", "graph_error", "rag_error"):
            value = detail.get(key)
            if value:
                messages.append(str(value)[:160])
    return "; ".join(messages[:3]) or "文档整理返回失败状态"


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
