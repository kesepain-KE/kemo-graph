"""知识图谱节点匹配、BFS 扩展和节点群查询。"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .chunker import chunk
from .config import AppConfig, load_config
from .db import DatabasePaths, connect_graph, initialize_databases
from .entity_extractor import Entity, extract


class GraphError(RuntimeError):
    """图谱查询错误基类。"""


class GraphIntegrityError(GraphError):
    """图谱数据库内容不符合约定结构。"""


class GraphQueryError(GraphError):
    """图谱查询参数无效。"""


@dataclass(frozen=True)
class _Node:
    node_id: str
    keyword: str
    summary: str
    aliases: list[str]
    tags: list[str]
    weight: float
    ref_count: int


@dataclass(frozen=True)
class _MatchedNode:
    node: _Node
    match_score: float


EntityExtractor = Callable[[list[str]], list[Entity]]

_CONTAINS_MATCH_SCORE = 0.85
_MIN_PARTIAL_MATCH_LENGTH = 2


class GraphEngine:
    """执行实体抽取、节点匹配、关系扩展和群总结查询。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        entity_extractor: EntityExtractor | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths: DatabasePaths = initialize_databases(data_dir, self.settings)
        self._entity_extractor = entity_extractor or (
            lambda chunks: extract(chunks, settings=self.settings)
        )

    def query(
        self,
        query: str,
        *,
        depth: int = 3,
        direction: str = "both",
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """执行完整图谱查询并返回文档约定结构。"""

        effective_confidence = (
            confidence
            if confidence is not None
            else self.settings.default_confidence
        )
        _validate_query_parameters(
            query,
            depth,
            direction,
            effective_confidence,
            self.settings.max_query_depth,
        )
        query_chunks = chunk(query, settings=self.settings)
        entities = self._entity_extractor(query_chunks)

        connection = connect_graph(self.paths)
        try:
            matched = self._match_nodes(connection, entities, float(effective_confidence))
            expanded, edges, path_traces = self._expand_graph(
                connection,
                [item.node.node_id for item in matched],
                depth,
                direction,
            )
            all_node_ids = {
                *(item.node.node_id for item in matched),
                *(item["node_id"] for item in expanded),
            }
            groups = self._get_groups(connection, all_node_ids)
            relations = _build_relations(connection, edges)
            paths = _build_paths(
                connection,
                path_traces,
                limit=self.settings.graph_path_limit,
            )
        finally:
            connection.close()

        return {
            "query": query,
            "hit_nodes": [_matched_node_to_dict(item) for item in matched],
            "expanded_nodes": expanded,
            "edges": edges,
            "relations": relations,
            "paths": paths,
            "groups": groups,
        }

    def match_nodes(
        self,
        entities: list[Entity],
        confidence_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """单独执行节点匹配，供测试和上层复用。"""

        threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self.settings.default_confidence
        )
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise GraphQueryError("confidence_threshold 必须是数字")
        if not 0.0 <= float(threshold) <= 1.0:
            raise GraphQueryError("confidence_threshold 必须在 0 到 1 之间")
        connection = connect_graph(self.paths)
        try:
            matched = self._match_nodes(connection, entities, float(threshold))
        finally:
            connection.close()
        return [_matched_node_to_dict(item) for item in matched]

    def expand_from_nodes(
        self,
        start_nodes: list[str],
        *,
        depth: int,
        direction: str = "both",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从给定 node_id 集合执行 BFS 扩展。"""

        _validate_depth_and_direction(
            depth, direction, self.settings.max_query_depth
        )
        connection = connect_graph(self.paths)
        try:
            return self._expand_from_nodes(connection, start_nodes, depth, direction)
        finally:
            connection.close()

    def get_groups(self, node_ids: Iterable[str]) -> list[dict[str, Any]]:
        connection = connect_graph(self.paths)
        try:
            return self._get_groups(connection, set(node_ids))
        finally:
            connection.close()

    def _match_nodes(
        self,
        connection: sqlite3.Connection,
        entities: list[Entity],
        threshold: float,
    ) -> list[_MatchedNode]:
        if any(not isinstance(entity, Entity) for entity in entities):
            raise GraphQueryError("实体抽取器必须返回 Entity 列表")
        rows = connection.execute(
            """
            SELECT node_id, keyword, summary, aliases, tags, weight, ref_count
            FROM nodes
            ORDER BY keyword, node_id
            """
        ).fetchall()
        nodes = [_node_from_row(row) for row in rows]
        best_by_node: dict[str, _MatchedNode] = {}
        for entity in entities:
            if not 0.0 <= entity.confidence <= 1.0:
                raise GraphQueryError("Entity.confidence 必须在 0 到 1 之间")
            for node in nodes:
                lexical_score = _node_match_score(entity, node)
                match_score = lexical_score * entity.confidence
                if match_score < threshold:
                    continue
                previous = best_by_node.get(node.node_id)
                if previous is None or match_score > previous.match_score:
                    best_by_node[node.node_id] = _MatchedNode(
                        node=node,
                        match_score=match_score,
                    )
        return sorted(
            best_by_node.values(),
            key=lambda item: (-item.match_score, item.node.keyword, item.node.node_id),
        )

    def _expand_from_nodes(
        self,
        connection: sqlite3.Connection,
        start_nodes: list[str],
        depth: int,
        direction: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        expanded, edges, _ = self._expand_graph(
            connection,
            start_nodes,
            depth,
            direction,
        )
        return expanded, edges

    def _expand_graph(
        self,
        connection: sqlite3.Connection,
        start_nodes: list[str],
        depth: int,
        direction: str,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        visited = set(start_nodes)
        frontier = list(dict.fromkeys(start_nodes))
        expanded: list[dict[str, Any]] = []
        edges_found: dict[str, dict[str, Any]] = {}
        traces_by_node: dict[str, dict[str, Any]] = {
            node_id: {"node_ids": [node_id], "edges": [], "directions": []}
            for node_id in frontier
        }
        path_traces: list[dict[str, Any]] = []

        for current_depth in range(1, depth + 1):
            next_frontier: list[str] = []
            next_frontier_ids: set[str] = set()
            for node_id in frontier:
                for node, edge, edge_direction in _get_neighbors(
                    connection, node_id, direction
                ):
                    edges_found.setdefault(edge["edge_id"], edge)
                    if node.node_id in visited:
                        continue
                    visited.add(node.node_id)
                    if node.node_id not in next_frontier_ids:
                        next_frontier_ids.add(node.node_id)
                        next_frontier.append(node.node_id)
                    parent_trace = traces_by_node[node_id]
                    trace = {
                        "node_ids": [*parent_trace["node_ids"], node.node_id],
                        "edges": [*parent_trace["edges"], edge],
                        "directions": [*parent_trace["directions"], edge_direction],
                    }
                    traces_by_node[node.node_id] = trace
                    path_traces.append(trace)
                    expanded.append(
                        {
                            "node_id": node.node_id,
                            "keyword": node.keyword,
                            "summary": node.summary,
                            "weight": node.weight,
                            "ref_count": node.ref_count,
                            "depth": current_depth,
                            "direction": edge_direction,
                        }
                    )
            frontier = next_frontier
            if not frontier:
                break
        return expanded, list(edges_found.values()), path_traces

    def _get_groups(
        self,
        connection: sqlite3.Connection,
        node_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT g.group_id, g.summary, g.node_count, g.edge_count
            FROM group_nodes gn
            JOIN groups g ON g.group_id = gn.group_id
            WHERE gn.node_id IN ({placeholders})
            ORDER BY g.group_id
            """,
            tuple(sorted(node_ids)),
        ).fetchall()
        return [
            {
                "group_id": row["group_id"],
                "summary": row["summary"],
                "node_count": int(row["node_count"] or 0),
                "edge_count": int(row["edge_count"] or 0),
            }
            for row in rows
        ]


def _get_neighbors(
    connection: sqlite3.Connection,
    node_id: str,
    direction: str,
) -> list[tuple[_Node, dict[str, Any], str]]:
    neighbors: list[tuple[_Node, dict[str, Any], str]] = []
    if direction in {"forward", "both"}:
        rows = connection.execute(
            """
            SELECT
                e.edge_id, e.source_node_id, e.relation, e.target_node_id,
                e.weight, e.support_count,
                n.node_id, n.keyword, n.summary, n.aliases, n.tags,
                n.weight, n.ref_count
            FROM edges e
            JOIN nodes n ON n.node_id = e.target_node_id
            WHERE e.source_node_id = ?
            ORDER BY e.edge_id
            """,
            (node_id,),
        ).fetchall()
        neighbors.extend(
            (_node_from_row(row), _edge_from_row(row), "forward") for row in rows
        )
    if direction in {"backward", "both"}:
        rows = connection.execute(
            """
            SELECT
                e.edge_id, e.source_node_id, e.relation, e.target_node_id,
                e.weight, e.support_count,
                n.node_id, n.keyword, n.summary, n.aliases, n.tags,
                n.weight, n.ref_count
            FROM edges e
            JOIN nodes n ON n.node_id = e.source_node_id
            WHERE e.target_node_id = ?
            ORDER BY e.edge_id
            """,
            (node_id,),
        ).fetchall()
        neighbors.extend(
            (_node_from_row(row), _edge_from_row(row), "backward") for row in rows
        )
    return neighbors


def _node_from_row(row: sqlite3.Row) -> _Node:
    return _Node(
        node_id=row["node_id"],
        keyword=row["keyword"],
        summary=row["summary"],
        aliases=_parse_string_array(row["aliases"], "nodes.aliases", row["node_id"]),
        tags=_parse_string_array(row["tags"], "nodes.tags", row["node_id"]),
        weight=float(row["weight"] or 0.0),
        ref_count=int(row["ref_count"] or 0),
    )


def _parse_string_array(value: str | None, field: str, node_id: str) -> list[str]:
    if value is None or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise GraphIntegrityError(f"{field} 不是合法 JSON：node_id={node_id}") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise GraphIntegrityError(f"{field} 必须是字符串数组：node_id={node_id}")
    return parsed


def _node_match_score(entity: Entity, node: _Node) -> float:
    primary = _match_key(entity.normalized)
    node_keyword = _match_key(node.keyword)
    node_aliases = {_match_key(alias) for alias in node.aliases}
    if primary == node_keyword:
        return 1.0

    alternate_terms = {
        _match_key(value)
        for value in [entity.text, *entity.aliases]
        if value.strip()
    }
    if (
        primary in node_aliases
        or node_keyword in alternate_terms
        or bool(node_aliases.intersection(alternate_terms))
    ):
        return 0.9

    entity_terms = {primary, *alternate_terms}
    node_terms = {node_keyword, *node_aliases}
    if any(
        _is_meaningful_contains_match(entity_term, node_term)
        for entity_term in entity_terms
        for node_term in node_terms
    ):
        return _CONTAINS_MATCH_SCORE
    return max(
        (
            SequenceMatcher(None, entity_term, node_term).ratio()
            for entity_term in entity_terms
            for node_term in node_terms
            if entity_term and node_term
        ),
        default=0.0,
    )


def _is_meaningful_contains_match(left: str, right: str) -> bool:
    """识别双向包含关系，同时拒绝过短词造成的大范围误命中。"""

    if min(len(left), len(right)) < _MIN_PARTIAL_MATCH_LENGTH:
        return False
    return left in right or right in left


def _match_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _matched_node_to_dict(item: _MatchedNode) -> dict[str, Any]:
    return {
        "node_id": item.node.node_id,
        "keyword": item.node.keyword,
        "summary": item.node.summary,
        "aliases": item.node.aliases,
        "tags": item.node.tags,
        "weight": item.node.weight,
        "ref_count": item.node.ref_count,
        "match_score": item.match_score,
    }


def _edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "edge_id": row["edge_id"],
        "source_node_id": row["source_node_id"],
        "relation": row["relation"],
        "target_node_id": row["target_node_id"],
        "weight": float(row["weight"]),
        "support_count": int(row["support_count"]),
    }


def _build_relations(
    connection: sqlite3.Connection,
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not edges:
        return []
    node_ids = {
        str(node_id)
        for edge in edges
        for node_id in (edge["source_node_id"], edge["target_node_id"])
    }
    keywords = _load_node_keywords(connection, node_ids)
    return [
        {
            "text": (
                f"{keywords[str(edge['source_node_id'])]}"
                f"->[{edge['relation']}]->"
                f"{keywords[str(edge['target_node_id'])]}"
            ),
            **edge,
        }
        for edge in edges
    ]


def _build_paths(
    connection: sqlite3.Connection,
    traces: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    # 直接一跳关系已由 relations 承载；paths 专注多跳链，避免前端重复。
    multi_hop = [trace for trace in traces if len(trace["edges"]) >= 2]
    if not multi_hop:
        return []
    node_ids = {
        str(node_id)
        for trace in multi_hop
        for node_id in trace["node_ids"]
    }
    keywords = _load_node_keywords(connection, node_ids)
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for trace in multi_hop:
        edges = list(trace["edges"])
        directions = list(trace["directions"])
        traversal_nodes = [str(node_id) for node_id in trace["node_ids"]]
        if all(value == "backward" for value in directions):
            # 整条链是逆向 BFS 时整体反转，文本仍保持数据库边的语义方向。
            output_nodes = list(reversed(traversal_nodes))
            output_edges = list(reversed(edges))
            output_directions = ["forward"] * len(output_edges)
        else:
            output_nodes = traversal_nodes
            output_edges = edges
            output_directions = directions
        edge_ids = tuple(str(edge["edge_id"]) for edge in output_edges)
        if edge_ids in seen:
            continue
        seen.add(edge_ids)
        text = keywords[output_nodes[0]]
        for index, edge in enumerate(output_edges):
            next_keyword = keywords[output_nodes[index + 1]]
            if output_directions[index] == "backward":
                text += f"<-[{edge['relation']}]<-{next_keyword}"
            else:
                text += f"->[{edge['relation']}]->{next_keyword}"
        results.append(
            {
                "text": text,
                "node_ids": output_nodes,
                "edge_ids": list(edge_ids),
                "weight": min(float(edge["weight"]) for edge in output_edges),
                "depth": len(output_edges),
            }
        )
        if len(results) >= limit:
            break
    return results


def _load_node_keywords(
    connection: sqlite3.Connection,
    node_ids: set[str],
) -> dict[str, str]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" for _ in node_ids)
    rows = connection.execute(
        f"SELECT node_id, keyword FROM nodes WHERE node_id IN ({placeholders})",
        tuple(sorted(node_ids)),
    ).fetchall()
    keywords = {str(row["node_id"]): str(row["keyword"]) for row in rows}
    missing = node_ids - keywords.keys()
    if missing:
        raise GraphIntegrityError(
            "关系或路径引用了不存在的节点：" + ", ".join(sorted(missing))
        )
    return keywords


def _validate_query_parameters(
    query: str,
    depth: int,
    direction: str,
    confidence: float,
    max_depth: int,
) -> None:
    if not isinstance(query, str) or not query.strip():
        raise GraphQueryError("query 必须是非空字符串")
    _validate_depth_and_direction(depth, direction, max_depth)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise GraphQueryError("confidence 必须是数字")
    if not 0.0 <= float(confidence) <= 1.0:
        raise GraphQueryError("confidence 必须在 0 到 1 之间")


def _validate_depth_and_direction(
    depth: int,
    direction: str,
    max_depth: int,
) -> None:
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise GraphQueryError("depth 必须是整数")
    if not 1 <= depth <= max_depth:
        raise GraphQueryError(f"depth 必须在 1 到 {max_depth} 之间")
    if direction not in {"forward", "backward", "both"}:
        raise GraphQueryError("direction 必须是 forward、backward 或 both")
