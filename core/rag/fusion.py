from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
import numpy as np
from core.rag.errors import IndexIntegrityError, RAGQueryError
from core.rag.models import _Candidate, _ChunkContext

def _fuse_vector_hits(
    raw_hits: Iterable[Iterable[tuple[int, float]]],
    query_weights: Sequence[float],
    rrf_k: int,
) -> dict[int, float]:
    """融合多查询 FAISS 排名，同时保留向量相似度的局部顺序。"""

    hit_lists = [list(hits) for hits in raw_hits]
    if len(hit_lists) != len(query_weights):
        raise RAGQueryError("FAISS 查询结果数量与查询权重数量不一致")
    if rrf_k < 1:
        raise RAGQueryError("rrf_k 必须大于等于 1")
    if len(hit_lists) == 1:
        weight = float(query_weights[0])
        if not np.isfinite(weight) or weight <= 0:
            raise RAGQueryError("查询权重必须是正有限数")
        return {
            vector_id: weight * float(score)
            for vector_id, score in hit_lists[0]
        }

    max_scores: dict[int, float] = {}
    rrf_scores: dict[int, float] = {}
    for hits, raw_weight in zip(hit_lists, query_weights):
        weight = float(raw_weight)
        if not np.isfinite(weight) or weight <= 0:
            raise RAGQueryError("查询权重必须是正有限数")
        for rank, (vector_id, raw_score) in enumerate(hits, start=1):
            score = float(raw_score)
            if not np.isfinite(score):
                raise RAGQueryError("FAISS 返回了 NaN 或 Infinity")
            weighted_score = weight * score
            max_scores[vector_id] = max(
                weighted_score,
                max_scores.get(vector_id, float("-inf")),
            )
            rrf_scores[vector_id] = rrf_scores.get(vector_id, 0.0) + weight / (
                rrf_k + rank
            )
    return {
        vector_id: max_scores[vector_id] + rrf_scores.get(vector_id, 0.0)
        for vector_id in max_scores
    }

def _merge_candidates(
    semantic_candidates: Sequence[_Candidate],
    lexical_candidates: Sequence[_Candidate],
) -> list[_Candidate]:
    """Merge ANN and exact-text candidates, retaining the strongest evidence."""

    by_chunk: dict[str, _Candidate] = {
        candidate.chunk_id: candidate for candidate in semantic_candidates
    }
    for candidate in lexical_candidates:
        previous = by_chunk.get(candidate.chunk_id)
        if previous is None or candidate.faiss_score > previous.faiss_score:
            by_chunk[candidate.chunk_id] = candidate
    return sorted(
        by_chunk.values(),
        key=lambda candidate: (-candidate.faiss_score, candidate.chunk_id),
    )

def _collapse_candidates_by_family(
    candidates: Sequence[_Candidate],
    hierarchy: Mapping[str, _ChunkContext],
    limit: int,
) -> list[_Candidate]:
    """Keep one precise representative per hierarchy before Rerank.

    Large chunks remain available as parent context.  They only become direct
    candidates when a legacy or partial hierarchy has no smaller searchable
    chunks at all.
    """

    if limit < 1:
        raise RAGQueryError("候选池上限必须大于等于 1")
    searchable = [candidate for candidate in candidates if candidate.granularity != "large"]
    if not searchable:
        searchable = list(candidates)
    rank = {"small": 0, "medium": 1, "large": 2}
    by_family: dict[str, _Candidate] = {}
    for candidate in searchable:
        family_id = _chunk_family_id(candidate.chunk_id, hierarchy)
        previous = by_family.get(family_id)
        if previous is None or (
            candidate.faiss_score > previous.faiss_score
            or (
                candidate.faiss_score == previous.faiss_score
                and rank.get(candidate.granularity, 3)
                < rank.get(previous.granularity, 3)
            )
        ):
            by_family[family_id] = candidate
    return sorted(
        by_family.values(),
        key=lambda candidate: candidate.faiss_score,
        reverse=True,
    )[:limit]

def _candidate_context_content(
    candidate: _Candidate,
    hierarchy: Mapping[str, _ChunkContext],
) -> str:
    if candidate.parent_chunk_id is None:
        return candidate.content
    parent = hierarchy.get(candidate.parent_chunk_id)
    return parent.content if parent is not None and parent.content.strip() else candidate.content

def _rag_result_item(
    candidate: _Candidate,
    score: float,
    hierarchy: Mapping[str, _ChunkContext],
    source_paths: Mapping[str, dict[str, str | None]],
) -> dict[str, Any]:
    parent = (
        hierarchy.get(candidate.parent_chunk_id)
        if candidate.parent_chunk_id is not None
        else None
    )
    return {
        "chunk_id": candidate.chunk_id,
        "content": candidate.content,
        "score": score,
        "granularity": candidate.granularity,
        "parent_chunk_id": candidate.parent_chunk_id,
        "context": (
            {
                "chunk_id": parent.chunk_id,
                "content": parent.content,
                "granularity": parent.granularity,
            }
            if parent is not None
            else None
        ),
        "source": {
            "source_id": candidate.source_id,
            **source_paths[candidate.source_id],
        },
    }

def _chunk_family_id(
    chunk_id: str,
    hierarchy: Mapping[str, _ChunkContext],
) -> str:
    current = chunk_id
    visited: set[str] = set()
    while True:
        if current in visited:
            raise IndexIntegrityError(f"分层 chunk 出现父子循环：{chunk_id}")
        visited.add(current)
        context = hierarchy.get(current)
        if context is None or context.parent_chunk_id is None:
            return current
        current = context.parent_chunk_id
