from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from heapq import nsmallest
from typing import Any
import numpy as np
from rapidfuzz import fuzz, process
from core.text_norm import norm_key
from core.graph.models import MergeCandidate
from core.graph.errors import GraphOrganizerError

MAX_ORGANIZER_CANDIDATES = 200

def _find_candidates(
    connection: sqlite3.Connection,
    *,
    threshold: float,
) -> list[MergeCandidate]:
    rows = connection.execute(
        """
        SELECT node_id, keyword, summary, aliases, tags, weight, ref_count
        FROM nodes ORDER BY node_id
        """
    ).fetchall()
    nodes: list[dict[str, Any]] = []
    by_term: dict[str, set[int]] = {}
    flat_terms: list[str] = []
    owners: list[int] = []
    offsets = [0]
    for index, row in enumerate(rows):
        aliases = _json_list(row["aliases"], row["node_id"], "aliases")
        terms = {norm_key(row["keyword"]), *(norm_key(value) for value in aliases)}
        nodes.append({
            "node_id": str(row["node_id"]),
            "keyword": str(row["keyword"]),
            "summary": norm_key(str(row["summary"] or ""))[:800],
            "terms": terms,
        })
        for term in sorted(terms):
            by_term.setdefault(term, set()).add(index)
            flat_terms.append(term)
            owners.append(index)
        offsets.append(len(flat_terms))
    def sort_key(item: MergeCandidate):
        return (-item.score, item.left_keyword, item.right_keyword, item.left_node_id, item.right_node_id)

    def exact_candidates():
        for left_index, left in enumerate(nodes):
            right_indexes = set().union(*(by_term[term] for term in left["terms"]))
            for right_index in right_indexes:
                if right_index > left_index:
                    right = nodes[right_index]
                    yield MergeCandidate(
                        left["node_id"], right["node_id"], left["keyword"], right["keyword"],
                        1.0, "exact_term",
                    )

    candidates = nsmallest(MAX_ORGANIZER_CANDIDATES, exact_candidates(), key=sort_key)
    # No non-exact score can exceed 1.0, so a full exact page is final.
    if len(candidates) == MAX_ORGANIZER_CANDIDATES or not flat_terms:
        return candidates
    exact_pairs = {(item.left_node_id, item.right_node_id) for item in candidates}
    neighbors = _neighbor_signatures(connection)
    term_owners = np.asarray(owners, dtype=np.intp)
    # Even perfect summaries and adjacency cannot rescue a lexical upper bound
    # below this floor. Unlike a heuristic threshold multiplier, this loses no hits.
    minimum_lexical = max(0.0, min(threshold, (threshold - 0.35) / 0.65))
    floor = max(0.0, minimum_lexical * 100.0 - 1e-10)
    for block_start in range(0, len(nodes), 64):
        block_end = min(block_start + 64, len(nodes))
        first_term = offsets[block_start]
        upper = process.cdist(
            flat_terms[first_term:offsets[block_end]], flat_terms,
            scorer=fuzz.ratio, score_cutoff=floor, dtype=np.float64, workers=-1,
        )
        for left_index in range(block_start, block_end):
            left = nodes[left_index]
            term_start = offsets[left_index] - first_term
            term_end = offsets[left_index + 1] - first_term
            lexical_upper = upper[term_start:term_end].max(axis=0)
            right_indexes = np.unique(term_owners[lexical_upper >= floor])
            batch: list[MergeCandidate] = []
            for right_index in right_indexes:
                if right_index <= left_index:
                    continue
                right = nodes[right_index]
                if (left["node_id"], right["node_id"]) in exact_pairs:
                    continue
                lexical = max(
                    (SequenceMatcher(None, left_term, right_term).ratio()
                     for left_term in left["terms"] for right_term in right["terms"]),
                    default=0.0,
                )
                if max(lexical, lexical * 0.65 + 0.35) + 1e-12 < threshold:
                    continue
                summary = SequenceMatcher(None, left["summary"], right["summary"]).ratio()
                adjacency = _jaccard(
                    neighbors.get(left["node_id"], set()),
                    neighbors.get(right["node_id"], set()),
                )
                score = max(lexical, lexical * 0.65 + summary * 0.2 + adjacency * 0.15)
                if score >= threshold:
                    batch.append(MergeCandidate(
                        left["node_id"], right["node_id"], left["keyword"], right["keyword"],
                        score, "semantic_similarity",
                    ))
            candidates = nsmallest(MAX_ORGANIZER_CANDIDATES, candidates + batch, key=sort_key)
    return candidates

def _neighbor_signatures(connection: sqlite3.Connection) -> dict[str, set[str]]:
    signatures: dict[str, set[str]] = {}
    rows = connection.execute(
        """
        SELECT e.source_node_id, e.relation, e.target_node_id,
               source.keyword AS source_keyword, target.keyword AS target_keyword
        FROM edges e
        JOIN nodes source ON source.node_id = e.source_node_id
        JOIN nodes target ON target.node_id = e.target_node_id
        """
    ).fetchall()
    for row in rows:
        signatures.setdefault(str(row["source_node_id"]), set()).add(
            f"out:{norm_key(row['relation'])}:{norm_key(row['target_keyword'])}"
        )
        signatures.setdefault(str(row["target_node_id"]), set()).add(
            f"in:{norm_key(row['relation'])}:{norm_key(row['source_keyword'])}"
        )
    return signatures

def _get_entity(connection: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT node_id, keyword, summary, aliases, tags, weight, ref_count
        FROM nodes WHERE node_id = ?
        """,
        (node_id,),
    ).fetchone()
    if row is None:
        raise GraphOrganizerError(f"节点不存在：{node_id}")
    sources = [dict(item) for item in connection.execute(
        """
        SELECT source_id, content_hash, evidence_weight, evidence
        FROM node_sources WHERE node_id = ? ORDER BY source_id
        """,
        (node_id,),
    ).fetchall()]
    relations = [dict(item) for item in connection.execute(
        """
        SELECT e.edge_id, e.source_node_id, source.keyword AS source_keyword,
               e.relation, e.target_node_id, target.keyword AS target_keyword,
               e.weight, e.support_count
        FROM edges e
        JOIN nodes source ON source.node_id = e.source_node_id
        JOIN nodes target ON target.node_id = e.target_node_id
        WHERE e.source_node_id = ? OR e.target_node_id = ?
        ORDER BY e.edge_id
        """,
        (node_id, node_id),
    ).fetchall()]
    return {
        "node_id": node_id,
        "keyword": row["keyword"],
        "summary": row["summary"],
        "aliases": _json_list(row["aliases"], node_id, "aliases"),
        "tags": _json_list(row["tags"], node_id, "tags"),
        "weight": float(row["weight"] or 0.0),
        "ref_count": int(row["ref_count"] or 0),
        "sources": sources,
        "relations": relations,
    }

def _json_list(value: Any, node_id: str, field: str) -> list[str]:
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise GraphOrganizerError(f"nodes.{field} 非法：{node_id}") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise GraphOrganizerError(f"nodes.{field} 必须是字符串数组：{node_id}")
    return parsed

def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
