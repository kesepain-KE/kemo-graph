from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any
from uuid import uuid4
from core.settings import AppConfig
from core.storage import connect_graph, connect_rag, connect_sources
from core.storage import get_database_paths
from core.storage import read_graph_meta, write_graph_meta
from core.graph import GraphOrganizer
from core.observability import DailyTSVLogger
from core.rag import FaissIndexManager
from core.rag import RAGEngine
from core.maintenance import KnowledgeBaseRebuilder
from core.maintenance.rebuild import ProgressCallback
from core.service.errors import CONFIG_API_KEY_MASK
from core.common import elapsed_ms as _elapsed_ms, now_iso as _now_iso


from core.service.context import ServiceContext
from core.service import context as runtime
from core.graph.meta import refresh_graph_meta

class MaintenanceService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context


    def generate_group_summaries(self, *, force: bool = False) -> dict[str, Any]:
        """按 06 文档规则执行一次节点群总结。"""
        started_at = time.perf_counter()
        self.context._log_event("group_summary_start", "status=started")
        self.context._require_available("graph")
        meta = read_graph_meta(self.context.paths)
        changed = int(meta.get("changed_since_summary", 0))
        if not force and changed < self.context.settings.summary_trigger_file_count:
            result = {
                "generated": 0,
                "changed_files": changed,
                "threshold": self.context.settings.summary_trigger_file_count,
                "skipped_reason": "改动数量未达阈值",
            }
            self.context._log_event(
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
            graph_connection = connect_graph(self.context.paths)
            try:
                graph_connection.execute("BEGIN IMMEDIATE")
                graph_connection.execute("DELETE FROM group_nodes")
                graph_connection.execute("DELETE FROM groups")
                graph_connection.commit()
            except Exception:
                graph_connection.rollback()
                raise
            finally:
                graph_connection.close()
            RAGEngine(self.context.data_dir, settings=self.context.settings).refresh_auxiliary_consistency()
            write_graph_meta(self.context.paths, {**meta, "changed_since_summary": 0})
            result = {
                "generated": 0,
                "qualified_components": 0,
                "total_components": len(components),
                "changed_files": changed,
            }
            self.context._log_event(
                "group_summary_done",
                f"generated=0, changed={changed}, skipped=no_component",
                _elapsed_ms(started_at),
            )
            return result

        community_records: list[tuple[str, str]] = []
        graph_connection = connect_graph(self.context.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            generated = 0
            for component in qualified:
                summary = self._summarize_component(component)
                placeholders = ",".join("?" for _ in component)
                edge_count = int(
                    graph_connection.execute(
                        f"""
                        SELECT COUNT(*) FROM edges
                        WHERE source_node_id IN ({placeholders})
                          AND target_node_id IN ({placeholders})
                        """,
                        tuple(sorted(component)) + tuple(sorted(component)),
                    ).fetchone()[0]
                )
                group_id = str(uuid4())
                now_str = _now_iso()
                graph_connection.execute(
                    """
                    INSERT INTO groups (group_id, summary, node_count, edge_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (group_id, summary, len(component), edge_count, now_str, now_str),
                )
                graph_connection.executemany(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES (?, ?)",
                    [(group_id, node_id) for node_id in sorted(component)],
                )
                community_records.append((group_id, summary))
                generated += 1
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        community_vectors = RAGEngine(
            self.context.data_dir,
            settings=self.context.settings,
        ).sync_community_vectors(community_records)

        write_graph_meta(
            self.context.paths,
            {**meta, "changed_since_summary": 0, "last_summary_at": _now_iso()},
        )
        refresh_graph_meta(self.context,changed=False)
        result = {
            "generated": generated,
            "qualified_components": len(qualified),
            "total_components": len(components),
            "changed_files": changed,
            "community_vectors": community_vectors,
        }
        self.context._log_event(
            "group_summary_done",
            f"generated={generated}, changed={changed}",
            _elapsed_ms(started_at),
        )
        return result

    def _find_connected_components(self) -> list[set[str]]:
        """从 graph.db 中查找所有连通分量。"""
        connection = connect_graph(self.context.paths)
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
        connection = connect_graph(self.context.paths)
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
        connection = connect_graph(self.context.paths)
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
                edges_info.append(
                    f"- {row['src_kw']} -[{row['relation']}]-> {row['tgt_kw']}"
                )
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
            summary = runtime.chat(
                system_prompt,
                user_prompt,
                settings=self.context.settings,
            ).strip()
            self.context._log_event(
                "llm_request",
                f"purpose=group_summary, model={self.context.settings.models.llm}",
                _elapsed_ms(started_at),
            )
            return summary
        except Exception:
            self.context._log_event(
                "llm_request",
                f"purpose=group_summary, model={self.context.settings.models.llm}, status=failed",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise

    def organize_graph(
        self,
        *,
        use_llm: bool = True,
        summarize: bool = True,
    ) -> dict[str, Any]:
        """只整理既有图谱投影，不重读文档、不调用 Embedding。"""

        self.context._require_available("graph", "rag")
        result = GraphOrganizer(
            self.context.data_dir,
            settings=self.context.settings,
        ).organize(use_llm=use_llm)
        summary: dict[str, Any] | None = None
        if summarize and result.get("groups_invalidated"):
            summary = self.generate_group_summaries(force=True)
        return {**result, "group_summary": summary}

    def rebuild_knowledge_base(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """重读新增、变化、删除和失败文档；未变化文档保持跳过。"""

        from core.maintenance.library import build_paths
        build_paths(self.context.documents, "")
        return KnowledgeBaseRebuilder(
            self.context.data_dir,
            self.context.external_dir,
            settings=self.context.settings,
        ).rebuild_changed(progress=progress)

    def rebuild_all(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """在影子目录重建 Graph、RAG、FAISS，验证后切换正式目录。"""

        from core.maintenance.library import build_paths
        build_paths(self.context.documents, "")
        return KnowledgeBaseRebuilder(
            self.context.data_dir,
            self.context.external_dir,
            settings=self.context.settings,
        ).rebuild_all(progress=progress)

    def list_jobs(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        from core.maintenance import list_jobs

        return list_jobs(self.context.data_dir, settings=self.context.settings, limit=limit)

    def get_job(self, job_id: str) -> dict[str, Any]:
        from core.maintenance import get_job

        return get_job(job_id, self.context.data_dir, settings=self.context.settings)

    def status(self) -> dict[str, Any]:
        """返回 03 文档约定的知识库状态，不隐式初始化数据库。"""

        from core.common.cache import READ_CACHE, databases_revision, file_revision

        def revision():
            databases = databases_revision(self.context.paths.sources_db, self.context.paths.graph_db, self.context.paths.rag_db)
            return None if databases is None else (databases, file_revision(self.context.paths.faiss_index))

        return READ_CACHE.get_or_load(
            ("knowledge-status", str(self.context.paths.data_dir.resolve()), self.context.settings.models.embedding_dimensions),
            revision, self._read_status,
        )

    def _read_status(self) -> dict[str, Any]:

        initialized = self.context.paths.sources_db.exists()
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

        source_connection = connect_sources(self.context.paths)
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

        if self.context.paths.graph_db.exists():
            graph_connection = connect_graph(self.context.paths)
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
        if self.context.paths.rag_db.exists():
            rag_connection = connect_rag(self.context.paths)
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

    def get_config(self) -> dict[str, Any]:
        """返回可安全回写的配置，不向客户端暴露显式 API 密钥。"""

        payload = self.context.settings.model_dump(mode="json")
        kemo = payload["kemo"]
        has_explicit_key = bool(self.context.settings.kemo.api_key)
        environment_name = self.context.settings.kemo.api_key_env.strip()
        has_environment_key = bool(
            os.getenv(environment_name, "").strip() if environment_name else ""
        )
        kemo["api_key"] = CONFIG_API_KEY_MASK if has_explicit_key else ""
        kemo["api_key_source"] = (
            "config"
            if has_explicit_key
            else "environment" if has_environment_key else "none"
        )
        return payload

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """保存配置并校验；密钥掩码表示保留当前显式密钥。"""
        from core.settings import AppConfig
        from core.settings import write_config

        candidate = deepcopy(data)
        raw_kemo = candidate.get("kemo")
        if isinstance(raw_kemo, dict):
            submitted_key = raw_kemo.get("api_key", CONFIG_API_KEY_MASK)
            if submitted_key == CONFIG_API_KEY_MASK:
                raw_kemo["api_key"] = self.context.settings.kemo.api_key
            raw_kemo.pop("api_key_source", None)

        new_config = AppConfig.model_validate(candidate)
        write_config(self.context.config_path, new_config)
        self.context.settings = new_config
        self.context.data_dir = self.context._data_dir_override or new_config.resolve_data_dir()
        self.context.external_dir = (
            self.context._external_dir_override or new_config.resolve_external_dir()
        )
        self.context.paths = get_database_paths(self.context.data_dir)
        self.context._logger = DailyTSVLogger(
            new_config.resolve_log_dir(),
            new_config.log_level,
        )
        return self.get_config()

    def _faiss_is_healthy(self, vector_ids: set[int]) -> bool:
        if not self.context.paths.faiss_index.exists():
            return not vector_ids
        try:
            manager = FaissIndexManager(
                self.context.paths.faiss_index,
                self.context.settings.models.embedding_dimensions,
            )
            return manager.ids == vector_ids
        except Exception:
            return False
