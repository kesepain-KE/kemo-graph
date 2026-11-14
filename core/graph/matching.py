from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any
from rapidfuzz.fuzz import ratio
from core.text_norm import norm_key
from core.entity_extractor import Entity
from core.node_index import NodeMatcher
from core.query_planner import QueryPlan
from core.graph.models import _EntityKeys, _MatchedNode, _Node, _NodeKeys
from core.graph.errors import GraphIntegrityError, GraphQueryError

_CONTAINS_MATCH_SCORE = 0.85

_SUMMARY_CONTAINS_MATCH_SCORE = 0.78

_MIN_PARTIAL_MATCH_LENGTH = 2

_MAX_SUMMARY_FALLBACK_NODES = 20

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
        aliases=tuple(_parse_string_array(row["aliases"], "nodes.aliases", row["node_id"])),
        tags=tuple(_parse_string_array(row["tags"], "nodes.tags", row["node_id"])),
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

@lru_cache(maxsize=4)
def _indexed_nodes(rows: tuple[sqlite3.Row, ...]):
    # The immutable row snapshot is the cache key, so edits, transactions and
    # database replacement all invalidate without a separate mutation counter.
    nodes = tuple(_node_from_row(row) for row in rows)
    indexed = {node.node_id: (node, _node_keys(node)) for node in nodes}
    matcher = NodeMatcher((node_id, keys.terms) for node_id, (_, keys) in indexed.items())
    return indexed, matcher

def _node_keys(node: _Node) -> _NodeKeys:
    keyword = norm_key(node.keyword)
    aliases = frozenset(norm_key(alias) for alias in node.aliases)
    return _NodeKeys(
        keyword=keyword,
        aliases=aliases,
        descriptive=frozenset(
            norm_key(value) for value in [node.summary, *node.tags]
            if isinstance(value, str) and value.strip()
        ),
        terms=frozenset({keyword, *aliases}),
    )

def _entity_keys(entity: Entity) -> _EntityKeys:
    primary = norm_key(entity.normalized)
    alternate = frozenset(
        norm_key(value) for value in [entity.text, *entity.aliases] if value.strip()
    )
    return _EntityKeys(primary, alternate, frozenset({primary, *alternate}))

def _node_match_score(
    entity: _EntityKeys, node: _NodeKeys, *, fuzzy_cutoff: float = 0.0,
) -> float:
    if entity.primary == node.keyword:
        return 1.0
    if (
        entity.primary in node.aliases
        or node.keyword in entity.alternate
        or bool(node.aliases.intersection(entity.alternate))
    ):
        return 0.9
    if any(
        _is_meaningful_contains_match(left, right)
        for left in entity.terms for right in node.terms
    ):
        return _CONTAINS_MATCH_SCORE
    if any(
        _is_meaningful_contains_match(left, right)
        for left in entity.terms for right in node.descriptive
    ):
        return _SUMMARY_CONTAINS_MATCH_SCORE
    # Indel similarity bounds SequenceMatcher from above; it is not its replacement.
    cutoff = max(0.0, fuzzy_cutoff * 100.0 - 1e-10)
    return max(
        (SequenceMatcher(None, left, right).ratio()
         for left in entity.terms for right in node.terms
         if left and right and ratio(left, right, score_cutoff=cutoff) > 0.0),
        default=0.0,
    )

def _entities_from_query_plan(plan: QueryPlan) -> list[Entity]:
    """Convert a query plan into graph anchors without dropping rewrites.

    The planner may return a useful synonym/subquery while omitting it from
    ``entities`` (or may extract only one entity from a multi-intent query).
    Previously the presence of *any* entity caused all query variants to be
    discarded, making Graph recall substantially worse than RAG recall.  Keep
    the structured entities first, then append unseen high-confidence variants
    as lower-weight concept anchors.  Broad/related variants remain RAG-only
    to avoid noisy graph expansion.
    """

    entities: list[Entity] = []
    seen: set[str] = set()
    for item in plan.entities:
        key = norm_key(item.normalized)
        if key in seen:
            continue
        seen.add(key)
        entities.append(
            Entity(
                text=item.text,
                type=item.type,
                normalized=item.normalized,
                aliases=list(item.aliases),
                confidence=item.confidence,
            )
        )
        seen.update(norm_key(alias) for alias in item.aliases)
    for variant in plan.variants:
        # Graph anchors should stay precise.  Low-weight related/broader
        # variants are useful for RAG recall, but treating them as graph
        # entities can fan out through common concepts and create noisy BFS
        # neighborhoods.  Synonyms/paraphrases/subqueries (weight >= 0.8) are
        # sufficiently close to the user's stated intent to use as anchors.
        if variant.weight < 0.8:
            continue
        key = norm_key(variant.text)
        if key in seen:
            continue
        seen.add(key)
        entities.append(
            Entity(
                text=variant.text,
                type="concept",
                normalized=variant.text,
                aliases=[],
                confidence=variant.weight,
            )
        )
    return entities

def _is_meaningful_contains_match(left: str, right: str) -> bool:
    """识别双向包含关系，同时拒绝过短词造成的大范围误命中。"""

    if min(len(left), len(right)) < _MIN_PARTIAL_MATCH_LENGTH:
        return False
    return left in right or right in left

def _matched_node_to_dict(item: _MatchedNode) -> dict[str, Any]:
    return {
        "node_id": item.node.node_id,
        "keyword": item.node.keyword,
        "summary": item.node.summary,
        "aliases": list(item.node.aliases),
        "tags": list(item.node.tags),
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
    if "\0" in query or len(query) > 1000:
        raise GraphQueryError("query 不能包含 NUL，且最多 1000 个字符")
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
