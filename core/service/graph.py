from __future__ import annotations
from core.storage import connect_graph, connect_sources

from datetime import datetime, timezone
from typing import Any
from core.storage import connect_graph, connect_rag, connect_sources
from core.graph.visualization import GraphDirection, get_neighborhood, get_visualization_meta, list_visualization_edges, list_visualization_nodes
from core.documents.errors import DocumentNotFoundError
from core.ingest import Ingestor
from core.storage import get_knowledge_base_lock
from core.retrieval.cache.store import clear_search_cache_after_mutation
from core.rag import RAGEngine
from core.documents.errors import DocumentContentConflictError
from core.common import parse_json_list as _parse_json_list


from core.service.context import ServiceContext
from core.graph.meta import refresh_graph_meta
from core.graph.tool_store import add_entity, add_relation, get_entity

class GraphService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context

    def create_node(
        self,
        keyword: str,
        summary: str,
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
        source_id: str = "manual",
        content_hash: str = "manual",
    ) -> dict[str, Any]:
        """创建带来源绑定的独立节点，不自动调用模型或合并同名节点。"""

        owner = self.context
        lock = get_knowledge_base_lock(owner.data_dir)
        if not lock.acquire(blocking=False):
            raise DocumentContentConflictError("图谱正在构建或修改，请稍后创建节点")
        try:
            owner._require_available("graph")
            connection = connect_graph(owner.paths)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    node_id = add_entity(
                        connection, keyword=keyword, summary=summary,
                        aliases=aliases, tags=tags,
                        source_id=source_id, content_hash=content_hash,
                    )
                    node = get_entity(connection, node_id)
            finally:
                connection.close()
            refresh_graph_meta(owner, changed=True)
            return clear_search_cache_after_mutation(owner.documents, node)
        finally:
            lock.release()

    def create_relation(
        self,
        source_node_id: str,
        relation: str,
        target_node_id: str,
        evidence_weight: float,
        source_id: str = "manual",
        content_hash: str = "manual",
    ) -> dict[str, Any]:
        """按关系三元组去重，更新同来源证据并重算当前最大权重。"""

        owner = self.context
        lock = get_knowledge_base_lock(owner.data_dir)
        if not lock.acquire(blocking=False):
            raise DocumentContentConflictError("图谱正在构建或修改，请稍后创建关系")
        try:
            owner._require_available("graph")
            connection = connect_graph(owner.paths)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        edge_id = add_relation(
                            connection, source_node_id=source_node_id,
                            relation=relation, target_node_id=target_node_id,
                            evidence_weight=evidence_weight,
                            source_id=source_id, content_hash=content_hash,
                        )
                    except LookupError as exc:
                        raise DocumentNotFoundError(str(exc)) from exc
                    edge = connection.execute(
                        """
                        SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                               e.relation, e.target_node_id, tn.keyword AS target_keyword,
                               e.weight, e.support_count, e.created_at
                        FROM edges e
                        JOIN nodes sn ON sn.node_id = e.source_node_id
                        JOIN nodes tn ON tn.node_id = e.target_node_id
                        WHERE e.edge_id = ?
                        """,
                        (edge_id,),
                    ).fetchone()
                    result = _relation_row(edge)
            finally:
                connection.close()
            refresh_graph_meta(owner, changed=True)
            return clear_search_cache_after_mutation(owner.documents, result)
        finally:
            lock.release()

    def get_node(self, node_id: str) -> dict[str, Any]:
        """返回节点、双向关系和可追溯的来源绑定。"""

        owner = self.context
        owner._require_available("graph")
        graph_connection = connect_graph(owner.paths)
        try:
            node = graph_connection.execute(
                """
                SELECT node_id, keyword, summary, aliases, tags, weight,
                       ref_count, created_at, updated_at
                FROM nodes WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone()
            if node is None:
                raise DocumentNotFoundError(f"节点不存在：{node_id}")
            bindings = graph_connection.execute(
                """
                SELECT source_id, content_hash, evidence_weight, evidence
                FROM node_sources WHERE node_id = ? ORDER BY source_id
                """,
                (node_id,),
            ).fetchall()
            relations = graph_connection.execute(
                """
                SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                       e.relation, e.target_node_id, tn.keyword AS target_keyword,
                       e.weight, e.support_count, e.created_at
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                WHERE e.source_node_id = ? OR e.target_node_id = ?
                ORDER BY sn.keyword, e.relation, tn.keyword, e.edge_id
                """,
                (node_id, node_id),
            ).fetchall()
            groups = [dict(row) for row in graph_connection.execute(
                """SELECT g.group_id, g.summary, g.node_count, g.edge_count
                   FROM groups g JOIN group_nodes gn ON gn.group_id = g.group_id
                   WHERE gn.node_id = ? ORDER BY g.group_id""",
                (node_id,),
            ).fetchall()]
        finally:
            graph_connection.close()

        source_ids = [str(row["source_id"]) for row in bindings]
        source_by_id: dict[str, Any] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_connection = connect_sources(owner.paths)
            try:
                source_by_id = {
                    str(row["source_id"]): row
                    for row in source_connection.execute(
                        f"""
                        SELECT source_id, original_path, library_path, relative_path, content_hash,
                               origin_hash, graph_status, rag_status, exists_status
                        FROM sources WHERE source_id IN ({placeholders})
                        """,
                        tuple(source_ids),
                    ).fetchall()
                }
            finally:
                source_connection.close()

        return {
            "node_id": node["node_id"],
            "keyword": node["keyword"],
            "summary": node["summary"],
            "aliases": _parse_json_list(node["aliases"]),
            "tags": _parse_json_list(node["tags"]),
            "weight": float(node["weight"] or 0.0),
            "ref_count": int(node["ref_count"] or 0),
            "created_at": node["created_at"],
            "updated_at": node["updated_at"],
            "sources": [
                {
                    "source_id": binding["source_id"],
                    "content_hash": binding["content_hash"],
                    "evidence_weight": float(binding["evidence_weight"] or 0.0),
                    "evidence": binding["evidence"],
                    "original_path": (
                        source_by_id[str(binding["source_id"])] ["original_path"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "relative_path": (
                        source_by_id[str(binding["source_id"])] ["relative_path"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "source_path": (
                        source_by_id[str(binding["source_id"])]["library_path"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "origin_hash": (
                        source_by_id[str(binding["source_id"])] ["origin_hash"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "graph_status": (
                        source_by_id[str(binding["source_id"])] ["graph_status"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "rag_status": (
                        source_by_id[str(binding["source_id"])] ["rag_status"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                    "exists_status": (
                        source_by_id[str(binding["source_id"])] ["exists_status"]
                        if str(binding["source_id"]) in source_by_id
                        else None
                    ),
                }
                for binding in bindings
            ],
            "relations": [_relation_row(row) for row in relations],
            "groups": groups,
        }

    def search_nodes(self, query: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        from core.graph.search import prepare_node_search, search_nodes

        self.context._require_available("graph")
        connection = connect_graph(self.context.paths)
        try:
            prepare_node_search(connection)
            return search_nodes(connection, query, limit=limit, offset=offset)
        finally:
            connection.close()

    def update_node_summary(
        self, node_id: str, summary: str, expected_summary: str,
    ) -> dict[str, Any]:
        from core.rag import RAGEngine

        owner = self.context
        lock = get_knowledge_base_lock(owner.data_dir)
        if not lock.acquire(blocking=False):
            raise DocumentContentConflictError("图谱正在构建或修改，请稍后保存摘要")
        try:
            owner._require_available("graph")
            connection = connect_graph(owner.paths)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        "SELECT summary FROM nodes WHERE node_id = ?", (node_id,),
                    ).fetchone()
                    if row is None:
                        raise DocumentNotFoundError(f"节点不存在：{node_id}")
                    if row["summary"] != expected_summary:
                        raise DocumentContentConflictError("节点摘要已变化，请重新读取后再编辑")
                    if summary == row["summary"]:
                        return self.get_node(node_id)
                    connection.execute(
                        "UPDATE nodes SET summary = ?, updated_at = ? WHERE node_id = ?",
                        (summary, datetime.now(timezone.utc).isoformat(), node_id),
                    )
                    connection.execute(
                        "DELETE FROM groups WHERE group_id IN "
                        "(SELECT group_id FROM group_nodes WHERE node_id = ?)", (node_id,),
                    )
            finally:
                connection.close()
            refresh_graph_meta(owner,changed=True)
            RAGEngine(owner.data_dir, settings=owner.settings).refresh_auxiliary_consistency()
            owner.retrieval.clear_search_cache()
            return self.get_node(node_id)
        finally:
            lock.release()

    def get_relation(self, edge_id: str) -> dict[str, Any]:
        """返回一条关系、可读路径和全部文档证据绑定。"""

        owner = self.context
        owner._require_available("graph")
        graph_connection = connect_graph(owner.paths)
        try:
            edge = graph_connection.execute(
                """
                SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                       e.relation, e.target_node_id, tn.keyword AS target_keyword,
                       e.weight, e.support_count, e.created_at
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                WHERE e.edge_id = ?
                """,
                (edge_id,),
            ).fetchone()
            if edge is None:
                raise DocumentNotFoundError(f"关系不存在：{edge_id}")
            evidence_rows = graph_connection.execute(
                """
                SELECT source_id, content_hash, evidence_weight
                FROM edge_sources WHERE edge_id = ? ORDER BY source_id
                """,
                (edge_id,),
            ).fetchall()
        finally:
            graph_connection.close()

        source_ids = [str(row["source_id"]) for row in evidence_rows]
        source_by_id: dict[str, Any] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_connection = connect_sources(owner.paths)
            try:
                source_by_id = {
                    str(row["source_id"]): row
                    for row in source_connection.execute(
                        f"""
                        SELECT source_id, original_path, relative_path, origin_hash,
                               graph_status, rag_status, exists_status
                        FROM sources WHERE source_id IN ({placeholders})
                        """,
                        tuple(source_ids),
                    ).fetchall()
                }
            finally:
                source_connection.close()

        result = _relation_row(edge)
        result["sources"] = [
            {
                "source_id": evidence["source_id"],
                "content_hash": evidence["content_hash"],
                "evidence_weight": float(evidence["evidence_weight"] or 0.0),
                "original_path": (
                    source_by_id[str(evidence["source_id"])] ["original_path"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
                "relative_path": (
                    source_by_id[str(evidence["source_id"])] ["relative_path"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
                "origin_hash": (
                    source_by_id[str(evidence["source_id"])] ["origin_hash"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
                "graph_status": (
                    source_by_id[str(evidence["source_id"])] ["graph_status"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
                "rag_status": (
                    source_by_id[str(evidence["source_id"])] ["rag_status"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
                "exists_status": (
                    source_by_id[str(evidence["source_id"])] ["exists_status"]
                    if str(evidence["source_id"]) in source_by_id
                    else None
                ),
            }
            for evidence in evidence_rows
        ]
        return result

    def get_full_graph(
        self,
        nodes_page: int | None = None,
        nodes_page_size: int = 100,
    ) -> dict[str, Any]:
        """返回图谱节点、全部边和群；节点可选择分页。"""

        owner = self.context
        if nodes_page is not None and (
            isinstance(nodes_page, bool)
            or not isinstance(nodes_page, int)
            or nodes_page < 1
        ):
            raise ValueError("nodes_page 必须为空或大于等于 1 的整数")
        if (
            isinstance(nodes_page_size, bool)
            or not isinstance(nodes_page_size, int)
            or not 1 <= nodes_page_size <= 1000
        ):
            raise ValueError("nodes_page_size 必须是 1 到 1000 之间的整数")

        owner._require_available("graph")
        connection = connect_graph(owner.paths)
        try:
            total_nodes = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
            node_sql = """
                SELECT node_id, keyword, summary, aliases, tags, weight, ref_count,
                       created_at, updated_at
                FROM nodes ORDER BY keyword, node_id
                """
            node_parameters: tuple[int, ...] = ()
            if nodes_page is not None:
                node_sql += " LIMIT ? OFFSET ?"
                node_parameters = (nodes_page_size, (nodes_page - 1) * nodes_page_size)
            node_rows = connection.execute(node_sql, node_parameters).fetchall()
            edge_rows = connection.execute(
                """
                SELECT e.edge_id, e.source_node_id,
                       sn.keyword AS source_keyword, e.relation,
                       e.target_node_id, tn.keyword AS target_keyword,
                       e.weight, e.support_count, e.created_at
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                ORDER BY e.edge_id
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
                    "weight": float(row["weight"] or 0.0),
                    "ref_count": int(row["ref_count"] or 0),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in node_rows
            ],
            "edges": [_relation_row(row) for row in edge_rows],
            "groups": list(groups_by_id.values()),
            "nodes_pagination": {
                "page": nodes_page,
                "page_size": nodes_page_size if nodes_page is not None else total_nodes,
                "total": total_nodes,
                "total_pages": (
                    (total_nodes + nodes_page_size - 1) // nodes_page_size
                    if nodes_page is not None
                    else (1 if total_nodes else 0)
                ),
            },
        }

    def get_visualization_meta(self) -> dict[str, Any]:
        self.context._require_available("graph")
        from core.graph.visualization import get_visualization_meta

        return get_visualization_meta(self.context.paths)

    def list_visualization_nodes(
        self,
        *,
        page: int = 1,
        page_size: int = 1000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        self.context._require_available("graph")
        from core.graph.visualization import list_visualization_nodes

        return list_visualization_nodes(
            self.context.paths,
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def list_visualization_edges(
        self,
        *,
        page: int = 1,
        page_size: int = 2000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        self.context._require_available("graph")
        from core.graph.visualization import list_visualization_edges

        return list_visualization_edges(
            self.context.paths,
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 2,
        direction: GraphDirection = "both",
        limit: int = 2000,
        edge_limit: int = 10000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        self.context._require_available("graph")
        from core.graph.visualization import get_neighborhood

        return get_neighborhood(
            self.context.paths,
            node_id,
            depth=depth,
            direction=direction,
            limit=limit,
            edge_limit=edge_limit,
            expected_revision=expected_revision,
        )

    def delete_relation(self, edge_id: str) -> dict[str, Any]:
        """删除关系及其全部证据，并使群组总结和搜索缓存失效。"""

        self.context._require_available("graph")
        graph_connection = connect_graph(self.context.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            edge = graph_connection.execute(
                """
                SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                       e.relation, e.target_node_id, tn.keyword AS target_keyword,
                       e.weight, e.support_count, e.created_at
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                WHERE e.edge_id = ?
                """,
                (edge_id,),
            ).fetchone()
            if edge is None:
                raise DocumentNotFoundError(f"关系不存在：{edge_id}")
            evidence_count = int(
                graph_connection.execute(
                    "SELECT COUNT(*) FROM edge_sources WHERE edge_id = ?",
                    (edge_id,),
                ).fetchone()[0]
            )
            mention_rows = graph_connection.execute(
                """
                SELECT rm.mention_id
                FROM relation_mentions rm
                JOIN mention_nodes sm ON sm.mention_id = rm.source_mention_id
                JOIN mention_nodes tm ON tm.mention_id = rm.target_mention_id
                WHERE sm.node_id = ? AND rm.relation = ? AND tm.node_id = ?
                """,
                (
                    edge["source_node_id"],
                    edge["relation"],
                    edge["target_node_id"],
                ),
            ).fetchall()
            if mention_rows:
                placeholders = ",".join("?" for _ in mention_rows)
                graph_connection.execute(
                    f"DELETE FROM relation_mentions WHERE mention_id IN ({placeholders})",
                    tuple(row["mention_id"] for row in mention_rows),
                )
            graph_connection.execute(
                "DELETE FROM edge_sources WHERE edge_id = ?", (edge_id,)
            )
            graph_connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        refresh_graph_meta(self.context,changed=True)
        RAGEngine(self.context.data_dir, settings=self.context.settings).refresh_auxiliary_consistency()
        result = {
            "deleted": True,
            "edge": _relation_row(edge),
            "deleted_evidence_count": evidence_count,
            "deleted_mention_count": len(mention_rows),
            "groups_invalidated": True,
        }
        self.context._log_event(
            "delete_relation",
            f"edge_id={edge_id}, evidence={evidence_count}",
        )
        return clear_search_cache_after_mutation(self.context.documents, result)

    def delete_node(self, node_id: str) -> dict[str, Any]:
        """删除指定节点，按 06 文档规则执行级联操作。"""
        self.context._require_available("graph")
        with Ingestor(
            data_dir=self.context.data_dir,
            external_dir=self.context.external_dir,
            settings=self.context.settings,
        )._write_lock:
            result = self._execute_node_deletion(node_id)
        self.context._log_event(
            "delete_node",
            f"node_id={node_id}, edges={result['cascade_deleted_edges']}",
        )
        return clear_search_cache_after_mutation(self.context.documents, result)

    def _execute_node_deletion(self, node_id: str) -> dict[str, Any]:
        graph_connection = connect_graph(self.context.paths)
        try:
            node_row = graph_connection.execute(
                "SELECT node_id, keyword FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if node_row is None:
                raise DocumentNotFoundError(f"节点不存在：{node_id}")
            bindings = graph_connection.execute(
                """
                SELECT ns.source_id,
                       (SELECT COUNT(*) FROM node_sources other
                        WHERE other.source_id = ns.source_id
                          AND other.node_id != ns.node_id) AS other_node_count
                FROM node_sources ns WHERE ns.node_id = ?
                ORDER BY ns.source_id
                """,
                (node_id,),
            ).fetchall()
        finally:
            graph_connection.close()

        source_ids = [str(binding["source_id"]) for binding in bindings]
        source_by_id: dict[str, Any] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_connection = connect_sources(self.context.paths)
            try:
                source_by_id = {
                    str(row["source_id"]): row
                    for row in source_connection.execute(
                        f"""
                        SELECT source_id, relative_path, exists_status
                        FROM sources WHERE source_id IN ({placeholders})
                        """,
                        tuple(source_ids),
                    ).fetchall()
                }
            finally:
                source_connection.close()

        isolated_source_ids = [
            str(binding["source_id"])
            for binding in bindings
            if int(binding["other_node_count"] or 0) == 0
            and str(binding["source_id"]) in source_by_id
            and source_by_id[str(binding["source_id"])]["exists_status"] == "active"
        ]
        unlinked_files = [
            str(source_by_id[str(binding["source_id"])]["relative_path"])
            for binding in bindings
            if int(binding["other_node_count"] or 0) > 0
            and str(binding["source_id"]) in source_by_id
        ]

        ingestor = Ingestor(
            data_dir=self.context.data_dir,
            external_dir=self.context.external_dir,
            settings=self.context.settings,
        )
        deleted_files: list[str] = []
        for source_id in isolated_source_ids:
            deletion = ingestor.delete_document(source_id)
            deleted_files.append(str(deletion["relative_path"]))

        graph_connection = connect_graph(self.context.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
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
            mention_ids = [
                str(row["mention_id"])
                for row in graph_connection.execute(
                    "SELECT mention_id FROM mention_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchall()
            ]
            deleted_relation_mentions = 0
            if mention_ids:
                placeholders = ",".join("?" for _ in mention_ids)
                deleted_relation_mentions = int(
                    graph_connection.execute(
                        f"""
                        SELECT COUNT(*) FROM relation_mentions
                        WHERE source_mention_id IN ({placeholders})
                           OR target_mention_id IN ({placeholders})
                        """,
                        tuple(mention_ids + mention_ids),
                    ).fetchone()[0]
                )
                graph_connection.execute(
                    f"""
                    DELETE FROM relation_mentions
                    WHERE source_mention_id IN ({placeholders})
                       OR target_mention_id IN ({placeholders})
                    """,
                    tuple(mention_ids + mention_ids),
                )
                graph_connection.execute(
                    f"DELETE FROM entity_mentions WHERE mention_id IN ({placeholders})",
                    tuple(mention_ids),
                )
            if edge_ids:
                placeholders = ",".join("?" for _ in edge_ids)
                parameters = tuple(sorted(edge_ids))
                graph_connection.execute(
                    f"DELETE FROM edge_sources WHERE edge_id IN ({placeholders})",
                    parameters,
                )
                graph_connection.execute(
                    f"DELETE FROM edges WHERE edge_id IN ({placeholders})",
                    parameters,
                )

            graph_connection.execute(
                "DELETE FROM node_sources WHERE node_id = ?", (node_id,)
            )
            graph_connection.execute(
                "DELETE FROM mention_nodes WHERE node_id = ?", (node_id,)
            )
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            graph_connection.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        # 清理 chunk_nodes 中的孤立引用
        rag_connection = connect_rag(self.context.paths)
        try:
            rag_connection.execute(
                "DELETE FROM chunk_nodes WHERE node_id = ?", (node_id,)
            )
            rag_connection.commit()
        finally:
            rag_connection.close()

        refresh_graph_meta(self.context,changed=True)
        RAGEngine(self.context.data_dir, settings=self.context.settings).refresh_auxiliary_consistency()
        return {
            "deleted_node_id": node_id,
            "deleted_keyword": node_row["keyword"],
            "cascade_deleted_edges": len(edge_ids),
            "deleted_mention_count": len(mention_ids),
            "deleted_relation_mention_count": deleted_relation_mentions,
            "deleted_source_ids": isolated_source_ids,
            "deleted_files": deleted_files,
            "unlinked_files": unlinked_files,
        }

def _relation_row(row: Any) -> dict[str, Any]:
    source_keyword = str(row["source_keyword"])
    relation = str(row["relation"])
    target_keyword = str(row["target_keyword"])
    return {
        "edge_id": row["edge_id"],
        "source_node_id": row["source_node_id"],
        "source_keyword": source_keyword,
        "relation": relation,
        "target_node_id": row["target_node_id"],
        "target_keyword": target_keyword,
        "path": f"{source_keyword}->[{relation}]->{target_keyword}",
        "weight": float(row["weight"] or 0.0),
        "support_count": int(row["support_count"] or 0),
        "created_at": row["created_at"],
    }
