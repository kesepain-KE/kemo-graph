from __future__ import annotations
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from core.config import AppConfig, load_config
from core.db import DatabasePaths, connect_graph, initialize_databases
from core.entity_extractor import Entity, extract
from core.query_planner import QueryPlan, plan_query
from core.query_progress import step_status
from core.graph.matching import _CONTAINS_MATCH_SCORE, _MAX_SUMMARY_FALLBACK_NODES, _SUMMARY_CONTAINS_MATCH_SCORE, _build_paths, _build_relations, _entities_from_query_plan, _entity_keys, _get_neighbors, _indexed_nodes, _matched_node_to_dict, _node_match_score, _validate_depth_and_direction, _validate_query_parameters
from core.graph.models import _MatchedNode
from core.graph.errors import GraphQueryError

EntityExtractor = Callable[[list[str]], list[Entity]]

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
        query_plan: QueryPlan | None = None,
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
        active_plan = query_plan or plan_query(query, settings=self.settings)
        step_status("graph", "running")
        if active_plan.mode == "off":
            entities = self._entity_extractor([active_plan.original])
        else:
            entities = _entities_from_query_plan(active_plan)

        connection = connect_graph(self.paths)
        try:
            matched = self._match_nodes(connection, entities, float(effective_confidence), raw_query=active_plan.original)
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

        step_status("graph", "completed")
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
        *,
        raw_query: str | None = None,
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
        indexed, matcher = _indexed_nodes(tuple(rows))
        best_by_node: dict[str, _MatchedNode] = {}
        for entity in entities:
            if not 0.0 <= entity.confidence <= 1.0:
                raise GraphQueryError("Entity.confidence 必须在 0 到 1 之间")
            entity_keys = _entity_keys(entity)
            fuzzy_cutoff = min(_CONTAINS_MATCH_SCORE, threshold / entity.confidence if entity.confidence else 1.0)
            candidate_ids = set(matcher.match_terms(entity_keys.terms))
            # Fuzzy hits can also be strong; literal candidates alone are insufficient.
            candidate_ids.update(matcher.fuzzy_candidates(entity_keys.terms, cutoff=_CONTAINS_MATCH_SCORE))
            scores = {
                node_id: _node_match_score(entity_keys, indexed[node_id][1], fuzzy_cutoff=fuzzy_cutoff)
                for node_id in candidate_ids
            }
            has_strong_match = any(score >= _CONTAINS_MATCH_SCORE for score in scores.values())
            if has_strong_match:
                scored_nodes = [(indexed[node_id][0], score) for node_id, score in scores.items()]
            else:
                # Preserve descriptive fallback and its existing global 20-node cap.
                scored_nodes = [
                    (node, scores[node.node_id] if node.node_id in scores else
                     _node_match_score(entity_keys, keys, fuzzy_cutoff=fuzzy_cutoff))
                    for node, keys in indexed.values()
                ]

            if has_strong_match:
                # If the query already names a canonical keyword/alias (or a
                # containing keyword), descriptive summaries should not turn
                # every node mentioning that common word into an anchor.
                scored_nodes = [
                    (node, score)
                    for node, score in scored_nodes
                    if score >= _CONTAINS_MATCH_SCORE
                ]
            else:
                summary_fallback = [
                    (node, score)
                    for node, score in scored_nodes
                    if score == _SUMMARY_CONTAINS_MATCH_SCORE
                ]
                if len(summary_fallback) > _MAX_SUMMARY_FALLBACK_NODES:
                    summary_fallback.sort(
                        key=lambda item: (
                            -item[0].ref_count,
                            -item[0].weight,
                            item[0].keyword,
                            item[0].node_id,
                        )
                    )
                    allowed = {
                        node.node_id
                        for node, _ in summary_fallback[:_MAX_SUMMARY_FALLBACK_NODES]
                    }
                    scored_nodes = [
                        (node, score)
                        for node, score in scored_nodes
                        if score != _SUMMARY_CONTAINS_MATCH_SCORE
                        or node.node_id in allowed
                    ]

            for node, lexical_score in scored_nodes:
                # Exact/alias/contains evidence comes from the database itself;
                # do not let an LLM's conservative confidence discard a strong
                # keyword/alias/contains hit.  Confidence still gates summary
                # and fuzzy-only matches.
                match_score = (
                    lexical_score
                    if lexical_score >= _CONTAINS_MATCH_SCORE
                    else lexical_score * entity.confidence
                )
                if match_score < threshold:
                    continue
                previous = best_by_node.get(node.node_id)
                if previous is None or match_score > previous.match_score:
                    best_by_node[node.node_id] = _MatchedNode(
                        node=node,
                        match_score=match_score,
                    )
        if raw_query:
            raw_keys = _entity_keys(Entity(raw_query, "concept", raw_query, [], 1.0))
            for node_id in matcher.match_terms([raw_query], include_containing=False):
                node, keys = indexed[node_id]
                score = _node_match_score(raw_keys, keys)
                previous = best_by_node.get(node_id)
                if score >= threshold and (previous is None or score > previous.match_score):
                    best_by_node[node_id] = _MatchedNode(node, score)
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
