from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from core.storage import connect_rag, connect_sources
from core.rag.errors import IndexIntegrityError, RAGQueryError
from core.rag.models import _Candidate, _ChunkContext
from core.rag.lexical import _LEXICAL_CANDIDATE_SCORE, _LEXICAL_EDGE_PUNCTUATION, _MIN_LEXICAL_QUERY_LENGTH, _normalize_lexical_text
from core.rag.fusion import _fuse_vector_hits
from core.documents import QueryPlan

def _load_candidates(
    self,
    raw_hits: Iterable[Iterable[tuple[int, float]]],
    *,
    query_weights: Sequence[float] | None = None,
    rrf_k: int = 60,
) -> list[_Candidate]:
    hit_lists = [list(query_hits) for query_hits in raw_hits]
    weights = list(query_weights) if query_weights is not None else [1.0] * len(hit_lists)
    vector_scores = _fuse_vector_hits(hit_lists, weights, rrf_k)
    if not vector_scores:
        return []

    placeholders = ",".join("?" for _ in vector_scores)
    connection = connect_rag(self.paths)
    try:
        rows = connection.execute(
            f"""
            SELECT e.vector_id, c.chunk_id, c.source_id, c.content,
                   c.granularity, c.parent_chunk_id
            FROM embeddings e
            JOIN chunks c ON c.chunk_id = e.chunk_id
            WHERE e.vector_id IN ({placeholders})
            """,
            tuple(vector_scores),
        ).fetchall()
    finally:
        connection.close()

    found_vector_ids = {int(row["vector_id"]) for row in rows}
    missing_vector_ids = set(vector_scores).difference(found_vector_ids)
    if missing_vector_ids:
        raise IndexIntegrityError(
            f"FAISS 中的 vector_id 在 rag.db 中不存在：{sorted(missing_vector_ids)}"
        )

    by_chunk: dict[str, _Candidate] = {}
    for row in rows:
        score = vector_scores[int(row["vector_id"])]
        candidate = _Candidate(
            vector_id=int(row["vector_id"]),
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            content=row["content"],
            faiss_score=score,
            granularity=row["granularity"],
            parent_chunk_id=row["parent_chunk_id"],
        )
        previous = by_chunk.get(candidate.chunk_id)
        if previous is None or candidate.faiss_score > previous.faiss_score:
            by_chunk[candidate.chunk_id] = candidate
    return sorted(
        by_chunk.values(),
        key=lambda candidate: candidate.faiss_score,
        reverse=True,
    )

def _load_lexical_candidates(
    self,
    plan: QueryPlan,
    *,
    limit: int,
) -> list[_Candidate]:
    """Load a small exact-text candidate pool from ``rag.db``.

    This is deliberately a recall supplement, not a replacement for FAISS:
    terms are bounded, parameterized, and each variant is capped.  The
    returned score is only used to select the pre-rerank pool; the final
    result score still comes from rerank/lexical validation.
    """

    if limit < 1:
        raise RAGQueryError("词面候选池上限必须大于等于 1")
    terms: list[tuple[str, float]] = []
    seen_terms: set[str] = set()
    for variant in plan.variants:
        raw = unicodedata.normalize("NFKC", variant.text).strip()
        raw = raw.strip(_LEXICAL_EDGE_PUNCTUATION)
        # Full natural-language questions are poor LIKE candidates and can
        # force a table scan.  Keep lexical fallback for compact terms and
        # planner-produced subqueries only.
        if len(raw) < _MIN_LEXICAL_QUERY_LENGTH or len(raw) > 128:
            continue
        key = _normalize_lexical_text(raw)
        if len(key) < _MIN_LEXICAL_QUERY_LENGTH or key in seen_terms:
            continue
        seen_terms.add(key)
        terms.append((raw, float(variant.weight)))
    if not terms:
        return []

    by_chunk: dict[str, _Candidate] = {}
    # Keep the total lexical supplement bounded even when the planner emits
    # several rewrites; each term receives a fair share of the pool.
    per_term_limit = max(1, (limit + len(terms) - 1) // len(terms))
    connection = connect_rag(self.paths)
    try:
        for term, weight in terms:
            rows = connection.execute(
                """
                SELECT e.vector_id, c.chunk_id, c.source_id, c.content,
                       c.granularity, c.parent_chunk_id
                FROM chunks c
                JOIN embeddings e ON e.chunk_id = c.chunk_id
                WHERE instr(c.content, ?) > 0
                   OR instr(lower(c.content), lower(?)) > 0
                ORDER BY c.source_id, c.chunk_index, c.chunk_id
                LIMIT ?
                """,
                (term, term, int(per_term_limit)),
            ).fetchall()
            # Keep lexical candidates ahead of semantically similar but
            # unrelated chunks.  This score is internal to candidate
            # ordering and is intentionally not exposed as final relevance.
            lexical_score = _LEXICAL_CANDIDATE_SCORE + max(0.0, weight) * 0.01
            for row in rows:
                candidate = _Candidate(
                    vector_id=int(row["vector_id"]),
                    chunk_id=str(row["chunk_id"]),
                    source_id=str(row["source_id"]),
                    content=str(row["content"]),
                    faiss_score=lexical_score,
                    granularity=str(row["granularity"]),
                    parent_chunk_id=row["parent_chunk_id"],
                )
                previous = by_chunk.get(candidate.chunk_id)
                if previous is None or candidate.faiss_score > previous.faiss_score:
                    by_chunk[candidate.chunk_id] = candidate
    finally:
        connection.close()
    return sorted(
        by_chunk.values(),
        key=lambda candidate: (-candidate.faiss_score, candidate.chunk_id),
    )

def _load_chunk_hierarchy(
    self,
    candidates: Sequence[_Candidate],
) -> dict[str, _ChunkContext]:
    hierarchy = {
        candidate.chunk_id: _ChunkContext(
            chunk_id=candidate.chunk_id,
            content=candidate.content,
            granularity=candidate.granularity,
            parent_chunk_id=candidate.parent_chunk_id,
        )
        for candidate in candidates
    }
    frontier = {
        candidate.parent_chunk_id
        for candidate in candidates
        if candidate.parent_chunk_id is not None
    }
    connection = connect_rag(self.paths)
    try:
        while frontier:
            pending = frontier.difference(hierarchy)
            if not pending:
                break
            placeholders = ",".join("?" for _ in pending)
            rows = connection.execute(
                f"""
                SELECT chunk_id, content, granularity, parent_chunk_id
                FROM chunks
                WHERE chunk_id IN ({placeholders})
                """,
                tuple(sorted(pending)),
            ).fetchall()
            if len(rows) != len(pending):
                found = {row["chunk_id"] for row in rows}
                raise IndexIntegrityError(
                    "分层 chunk 的 parent_chunk_id 不存在："
                    f"{sorted(pending.difference(found))}"
                )
            frontier = set()
            for row in rows:
                context = _ChunkContext(
                    chunk_id=row["chunk_id"],
                    content=row["content"],
                    granularity=row["granularity"],
                    parent_chunk_id=row["parent_chunk_id"],
                )
                hierarchy[context.chunk_id] = context
                if context.parent_chunk_id is not None:
                    frontier.add(context.parent_chunk_id)
    finally:
        connection.close()
    return hierarchy

def _load_source_paths(self, source_ids: set[str]) -> dict[str, dict[str, str | None]]:
    if not source_ids:
        return {}
    placeholders = ",".join("?" for _ in source_ids)
    connection = connect_sources(self.paths)
    try:
        rows = connection.execute(
            f"""
            SELECT source_id, original_path, library_path, source_uri
            FROM sources
            WHERE source_id IN ({placeholders})
            """,
            tuple(source_ids),
        ).fetchall()
    finally:
        connection.close()
    paths = {
        row["source_id"]: {
            "relative_path": row["library_path"] or row["original_path"],
            "original_path": row["original_path"],
            "source_path": row["library_path"],
            "source_uri": row["source_uri"],
        }
        for row in rows
    }
    missing_source_ids = source_ids.difference(paths)
    if missing_source_ids:
        raise IndexIntegrityError(
            f"chunk 对应的 source_id 在 sources.db 中不存在：{sorted(missing_source_ids)}"
        )
    return paths
