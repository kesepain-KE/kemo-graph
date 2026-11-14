from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
import numpy as np
from provider.embedding import EmbeddingResult
from provider.rerank import rerank
from core.observability import query_step, step_status
from core.rag.errors import RAGQueryError
from core.rag.models import PreparedQuery, _Candidate
from core.rag.fusion import _candidate_context_content, _chunk_family_id, _collapse_candidates_by_family, _merge_candidates, _rag_result_item
from core.common import elapsed_ms as _elapsed_ms
from core.rag.lexical import _planned_lexical_match_score
from core.rag.validation import _validate_prepared_query, _validate_score_multipliers
from core.documents.query_planner import filter_semantic_drift
from core.documents import plan_query

def query(
    self,
    query: str,
    *,
    top_k: int | None = None,
    threshold: float | None = None,
    score_multipliers: Mapping[str, float] | None = None,
    prepared_query: PreparedQuery | None = None,
) -> dict[str, Any]:
    """执行完整 RAG 查询并返回 API 约定的结构化结果。"""

    if not isinstance(query, str) or not query.strip():
        raise RAGQueryError("query 必须是非空字符串")
    effective_top_k = top_k if top_k is not None else self.settings.default_top_k
    effective_threshold = (
        threshold
        if threshold is not None
        else self.settings.rag_similarity_threshold
    )
    if not isinstance(effective_top_k, int) or isinstance(effective_top_k, bool):
        raise RAGQueryError("top_k 必须是整数")
    if effective_top_k < 1:
        raise RAGQueryError("top_k 必须大于等于 1")
    if not isinstance(effective_threshold, (int, float)) or isinstance(
        effective_threshold, bool
    ):
        raise RAGQueryError("threshold 必须是数字")
    if not 0.0 <= float(effective_threshold) <= 1.0:
        raise RAGQueryError("threshold 必须在 0 到 1 之间")
    if self.index.count == 0:
        return {"query": query, "results": []}

    prepared = prepared_query or self.prepare_query(query)
    _validate_prepared_query(query, prepared)
    query_embedding = prepared.embedding
    query_vectors = query_embedding.vectors
    database_vector_space = self._database_vector_space_id()
    if (
        database_vector_space is not None
        and query_embedding.vector_space_id != database_vector_space
    ):
        raise RAGQueryError(
            "查询向量空间与 FAISS 索引不一致："
            f"查询={query_embedding.vector_space_id}，索引={database_vector_space}"
        )
    candidate_pool_limit = max(
        effective_top_k,
        self.settings.query_planning.candidate_pool_size,
    )
    search_limit = max(effective_top_k * 3, candidate_pool_limit)
    step_status("vector", "running")
    raw_hits = self.index.search(
        query_vectors,
        search_limit,
    )
    candidates = self._load_candidates(
        raw_hits,
        query_weights=prepared.plan.weights,
        rrf_k=self.settings.query_planning.rrf_k,
    )
    # FAISS is excellent for semantic similarity, but a short exact term
    # can receive a poor embedding score (especially for mixed Chinese /
    # Latin text, identifiers, and newly indexed chunks).  Add a bounded
    # lexical candidate pass before hierarchy collapse so an exact hit is
    # not lost merely because it fell outside the ANN top-k window.
    step_status("vector", "completed")
    step_status("lexical", "running")
    lexical_candidates = self._load_lexical_candidates(
        prepared.plan,
        limit=search_limit,
    )
    step_status("lexical", "completed")
    if lexical_candidates:
        candidates = _merge_candidates(candidates, lexical_candidates)
    if not candidates:
        return {"query": query, "results": []}
    if score_multipliers:
        _validate_score_multipliers(score_multipliers)
        candidates = sorted(
            (
                replace(
                    candidate,
                    faiss_score=candidate.faiss_score
                    * float(score_multipliers.get(candidate.chunk_id, 1.0)),
                )
                for candidate in candidates
            ),
            key=lambda candidate: candidate.faiss_score,
            reverse=True,
        )

    step_status("chunks", "running")
    hierarchy = self._load_chunk_hierarchy(candidates)
    candidates = _collapse_candidates_by_family(
        candidates,
        hierarchy,
        candidate_pool_limit,
    )
    step_status("chunks", "completed")
    if not candidates:
        return {"query": query, "results": []}

    requested_rerank_limit = max(effective_top_k, self.settings.rerank_top_n)
    if len(prepared.plan.variants) > 1:
        requested_rerank_limit = max(
            requested_rerank_limit,
            self.settings.query_planning.candidate_pool_size,
        )
    rerank_limit = min(len(candidates), requested_rerank_limit)
    documents = [
        _candidate_context_content(candidate, hierarchy)
        for candidate in candidates
    ]
    step_status("rerank", "running")
    rerank_started_at = time.perf_counter()
    if self._reranker is None:
        try:
            ranked = rerank(
                query,
                documents,
                rerank_limit,
                settings=self.settings,
                cache_path=self.paths.rerank_cache,
                document_ids=[candidate.chunk_id for candidate in candidates],
            )
        except Exception:
            self._log_event(
                "rerank_request",
                (
                    f"model={self.settings.models.rerank}, "
                    f"candidates={len(documents)}, status=failed"
                ),
                _elapsed_ms(rerank_started_at),
                level="ERROR",
            )
            raise
    else:
        ranked = self._reranker(query, documents, rerank_limit)
    self._log_event(
        "rerank_request",
        f"model={self.settings.models.rerank}, candidates={len(documents)}",
        _elapsed_ms(rerank_started_at),
    )
    step_status("rerank", "completed")
    source_paths = self._load_source_paths(
        {candidate.source_id for candidate in candidates}
    )
    results: list[dict[str, Any]] = []
    rescue_candidates: list[tuple[_Candidate, float]] = []
    seen_indexes: set[int] = set()
    seen_families: set[str] = set()
    for candidate_index, score in ranked:
        if (
            not isinstance(candidate_index, int)
            or candidate_index in seen_indexes
            or not 0 <= candidate_index < len(candidates)
        ):
            raise RAGQueryError("rerank 返回了无效或重复的文档索引")
        if not isinstance(score, (int, float, np.integer, np.floating)):
            raise RAGQueryError("rerank 返回了非数值分数")
        if not np.isfinite(score):
            raise RAGQueryError("rerank 返回了 NaN 或 Infinity")
        seen_indexes.add(candidate_index)
        candidate = candidates[candidate_index]
        effective_score = max(
            float(score),
            _planned_lexical_match_score(
                prepared.plan,
                _candidate_context_content(candidate, hierarchy),
            ),
        )
        if effective_score < effective_threshold:
            if effective_score > 0:
                rescue_candidates.append((candidate, effective_score))
            continue
        family_id = _chunk_family_id(candidate.chunk_id, hierarchy)
        if family_id in seen_families:
            continue
        seen_families.add(family_id)
        results.append(
            _rag_result_item(candidate, effective_score, hierarchy, source_paths)
        )
        if len(results) == effective_top_k:
            break
    if (
        not results
        and threshold is None
        and len(prepared.plan.variants) > 1
        and self.settings.query_planning.low_confidence_rescue_count > 0
    ):
        for candidate, score in sorted(
            rescue_candidates,
            key=lambda item: item[1],
            reverse=True,
        ):
            family_id = _chunk_family_id(candidate.chunk_id, hierarchy)
            if family_id in seen_families:
                continue
            seen_families.add(family_id)
            item = _rag_result_item(candidate, score, hierarchy, source_paths)
            item["low_confidence"] = True
            results.append(item)
            if len(results) == min(
                effective_top_k,
                self.settings.query_planning.low_confidence_rescue_count,
            ):
                break
    return {"query": query, "results": results}

def prepare_query(self, query: str) -> PreparedQuery:
    """规划查询并一次批量向量化，供 chunk、实体和群组索引共同使用。"""

    if not isinstance(query, str) or not query.strip():
        raise RAGQueryError("query 必须是非空字符串")
    plan = plan_query(query, settings=self.settings)
    embedding_started_at = time.perf_counter()
    try:
        with query_step("embedding"):
            embedding = self._embed_texts(plan.texts, input_type="query")
    except Exception:
        self._log_event(
            "embedding_request",
            (
                f"purpose=query, model={self.settings.models.embedding}, "
                f"count={len(plan.variants)}, status=failed"
            ),
            _elapsed_ms(embedding_started_at),
            level="ERROR",
        )
        raise
    if len(embedding.vectors) != len(plan.variants):
        raise RAGQueryError("Embedding 返回向量数量与查询计划数量不一致")
    try:
        filtered_plan, kept_indexes = filter_semantic_drift(
            plan,
            embedding.vectors,
            self.settings.query_planning.semantic_drift_threshold,
        )
    except Exception as exc:
        raise RAGQueryError(f"查询扩展向量校验失败：{exc}") from exc
    filtered_embedding = EmbeddingResult(
        vectors=[embedding.vectors[index] for index in kept_indexes],
        vector_space_id=embedding.vector_space_id,
    )
    self._log_event(
        "embedding_request",
        (
            f"purpose=query, model={self.settings.models.embedding}, "
            f"planned={len(plan.variants)}, retained={len(filtered_plan.variants)}, "
            f"planner_mode={plan.mode}, degraded={str(plan.degraded).lower()}"
        ),
        _elapsed_ms(embedding_started_at),
    )
    return PreparedQuery(filtered_plan, filtered_embedding)
