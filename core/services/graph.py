"""图谱领域服务。

图谱查询由 :mod:`core.services.retrieval` 负责；本服务聚焦节点、关系、
全量图和 GPU/分页可视化数据入口。
"""

from __future__ import annotations

import json
from typing import Any

from ..db import connect_graph, connect_sources
from ..ingestor import DocumentNotFoundError, Ingestor
from .protocols import ServiceOwner


class GraphService:
    """图谱实体、关系和可视化数据的统一入口。"""

    def __init__(self, owner: ServiceOwner) -> None:
        self.owner = owner

    def get_node(self, node_id: str) -> dict[str, Any]:
        """返回节点、双向关系和可追溯的来源绑定。"""

        owner = self.owner
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
                        SELECT source_id, original_path, relative_path, content_hash,
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
        }

    def get_relation(self, edge_id: str) -> dict[str, Any]:
        """返回一条关系、可读路径和全部文档证据绑定。"""

        owner = self.owner
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

    def delete_relation(self, edge_id: str) -> dict[str, Any]:
        return self.owner._delete_relation_impl(edge_id)

    def delete_node(self, node_id: str) -> dict[str, Any]:
        return self.owner._delete_node_impl(node_id)

    def get_full_graph(
        self,
        nodes_page: int | None = None,
        nodes_page_size: int = 100,
    ) -> dict[str, Any]:
        """返回图谱节点、全部边和群；节点可选择分页。"""

        owner = self.owner
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
        self.owner._require_available("graph")
        from ..graph_visualization import get_visualization_meta

        return get_visualization_meta(self.owner.paths)

    def list_visualization_nodes(
        self,
        *,
        page: int = 1,
        page_size: int = 1000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        self.owner._require_available("graph")
        from ..graph_visualization import list_visualization_nodes

        return list_visualization_nodes(
            self.owner.paths,
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
        self.owner._require_available("graph")
        from ..graph_visualization import list_visualization_edges

        return list_visualization_edges(
            self.owner.paths,
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 2,
        direction: str = "both",
        limit: int = 2000,
        edge_limit: int = 10000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        self.owner._require_available("graph")
        from ..graph_visualization import get_neighborhood

        return get_neighborhood(
            self.owner.paths,
            node_id,
            depth=depth,
            direction=direction,
            limit=limit,
            edge_limit=edge_limit,
            expected_revision=expected_revision,
        )


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise ValueError("图谱中的 JSON 数组字段格式错误")
    return parsed


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
