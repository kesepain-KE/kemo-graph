"""FAISS 索引管理和 RAG 查询主流程。"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss
except ImportError:  # pragma: no cover - 由运行时依赖检查覆盖
    faiss = None  # type: ignore[assignment]

from provider.embedding import EmbeddingResult, embed
from provider.rerank import clear_cache, rerank

from .chunker import chunking_signature
from .config import AppConfig, load_config
from .db import (
    connect_graph,
    connect_rag,
    connect_sources,
    initialize_databases,
    read_rag_meta,
    write_rag_meta,
)
from .logger import DailyTSVLogger
from .query_planner import QueryPlan, filter_semantic_drift, plan_query


class RAGError(RuntimeError):
    """RAG 引擎错误基类。"""


class FaissUnavailableError(RAGError):
    """FAISS 依赖未安装。"""


class IndexIntegrityError(RAGError):
    """FAISS 或 SQLite 中的向量数据不一致。"""


class RAGQueryError(RAGError):
    """RAG 查询参数或重排序结果无效。"""


class FaissIndexManager:
    """管理持久化的 IndexIDMap2 + IndexFlatIP 索引。"""

    def __init__(
        self,
        index_path: Path | str,
        dimensions: int,
        *,
        autoload: bool = True,
    ) -> None:
        if faiss is None:
            raise FaissUnavailableError("缺少 faiss-cpu，请先安装 requirements.txt")
        if dimensions < 1:
            raise ValueError("dimensions 必须大于等于 1")
        self.index_path = Path(index_path)
        self.dimensions = dimensions
        self._lock = threading.RLock()
        self._index: Any | None = None
        if autoload and self.index_path.exists():
            self.load()
        else:
            self.create()

    @property
    def count(self) -> int:
        with self._lock:
            return int(self._require_index().ntotal)

    @property
    def ids(self) -> set[int]:
        with self._lock:
            index = self._require_index()
            return {
                int(value) for value in faiss.vector_to_array(index.id_map).tolist()
            }

    def create(self, *, persist: bool = False) -> None:
        """创建空的精确内积索引。"""

        with self._lock:
            new_index = self._new_index()
            if persist:
                self._persist(new_index)
            self._index = new_index

    def load(self) -> None:
        """从磁盘加载并校验索引类型和维度。"""

        with self._lock:
            try:
                loaded = faiss.read_index(str(self.index_path))
            except Exception as exc:
                raise IndexIntegrityError(
                    f"无法加载 FAISS 索引：{self.index_path}: {exc}"
                ) from exc
            self._validate_index(loaded)
            self._index = loaded

    def save(self) -> None:
        """将当前索引原子写入磁盘。"""

        with self._lock:
            self._persist(self._require_index())

    def add(
        self,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> None:
        """原子添加带稳定 ID 的向量。"""

        ids = _validate_vector_ids(vector_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(ids) != len(matrix):
            raise ValueError("vector_ids 数量必须与 vectors 数量一致")
        if not ids:
            return
        if len(set(ids)) != len(ids):
            raise IndexIntegrityError("同一批次包含重复 vector_id")
        with self._lock:
            existing_ids = {
                int(value)
                for value in faiss.vector_to_array(
                    self._require_index().id_map
                ).tolist()
            }
            duplicates = existing_ids.intersection(ids)
            if duplicates:
                raise IndexIntegrityError(
                    f"FAISS 中已存在 vector_id：{sorted(duplicates)}"
                )
            candidate = faiss.clone_index(self._require_index())
            candidate.add_with_ids(matrix, np.asarray(ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate

    def delete(self, vector_ids: Sequence[int]) -> int:
        """物理删除指定向量并返回实际删除数量。"""

        ids = _validate_vector_ids(vector_ids)
        if not ids:
            return 0
        with self._lock:
            candidate = faiss.clone_index(self._require_index())
            removed = int(candidate.remove_ids(np.asarray(ids, dtype=np.int64)))
            if removed:
                self._persist(candidate)
                self._index = candidate
            return removed

    def replace(
        self,
        remove_ids: Sequence[int],
        add_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> int:
        """在单次原子持久化中删除旧向量并加入新向量。"""

        old_ids = _validate_vector_ids(remove_ids)
        new_ids = _validate_vector_ids(add_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(new_ids) != len(matrix):
            raise ValueError("add_ids 数量必须与 vectors 数量一致")
        if len(set(new_ids)) != len(new_ids):
            raise IndexIntegrityError("同一批次包含重复的新 vector_id")
        if not old_ids and not new_ids:
            return 0

        with self._lock:
            candidate = faiss.clone_index(self._require_index())
            removed = (
                int(candidate.remove_ids(np.asarray(old_ids, dtype=np.int64)))
                if old_ids
                else 0
            )
            remaining_ids = {
                int(value) for value in faiss.vector_to_array(candidate.id_map).tolist()
            }
            duplicates = remaining_ids.intersection(new_ids)
            if duplicates:
                raise IndexIntegrityError(
                    f"FAISS 中已存在新的 vector_id：{sorted(duplicates)}"
                )
            if new_ids:
                candidate.add_with_ids(matrix, np.asarray(new_ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate
            return removed

    def search(
        self,
        vectors: Sequence[Sequence[float]] | np.ndarray,
        top_k: int,
    ) -> list[list[tuple[int, float]]]:
        """对每个查询向量返回按内积分数降序排列的 ``(id, score)``。"""

        if top_k < 1:
            raise ValueError("top_k 必须大于等于 1")
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(matrix) == 0:
            return []
        with self._lock:
            index = self._require_index()
            if index.ntotal == 0:
                return [[] for _ in range(len(matrix))]
            limit = min(top_k, int(index.ntotal))
            scores, ids = index.search(matrix, limit)
        return [
            [
                (int(vector_id), float(score))
                for vector_id, score in zip(row_ids, row_scores, strict=True)
                if vector_id >= 0
            ]
            for row_ids, row_scores in zip(ids, scores, strict=True)
        ]

    def rebuild(
        self,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> None:
        """使用完整向量集合重建并原子替换索引。"""

        ids = _validate_vector_ids(vector_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(ids) != len(matrix):
            raise ValueError("vector_ids 数量必须与 vectors 数量一致")
        if len(set(ids)) != len(ids):
            raise IndexIntegrityError("重建数据包含重复 vector_id")

        with self._lock:
            candidate = self._new_index()
            if ids:
                candidate.add_with_ids(matrix, np.asarray(ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate

    def _new_index(self) -> Any:
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimensions))

    def _require_index(self) -> Any:
        if self._index is None:
            raise IndexIntegrityError("FAISS 索引尚未创建或加载")
        return self._index

    def _validate_index(self, index: Any) -> None:
        if not isinstance(index, faiss.IndexIDMap2):
            raise IndexIntegrityError("FAISS 索引类型必须是 IndexIDMap2")
        if int(index.d) != self.dimensions:
            raise IndexIntegrityError(
                f"FAISS 维度不一致：期望 {self.dimensions}，实际 {index.d}"
            )
        ids = faiss.vector_to_array(index.id_map)
        if len(ids) != len(set(int(value) for value in ids.tolist())):
            raise IndexIntegrityError("FAISS 索引包含重复 vector_id")

    def _persist(self, index: Any) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        try:
            faiss.write_index(index, str(temporary_path))
            verification = faiss.read_index(str(temporary_path))
            self._validate_index(verification)
            expected_ids = {
                int(value) for value in faiss.vector_to_array(index.id_map).tolist()
            }
            actual_ids = {
                int(value)
                for value in faiss.vector_to_array(verification.id_map).tolist()
            }
            if expected_ids != actual_ids or int(index.ntotal) != int(
                verification.ntotal
            ):
                raise IndexIntegrityError("FAISS 临时索引校验失败")
            os.replace(temporary_path, self.index_path)
        finally:
            temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _Candidate:
    vector_id: int
    chunk_id: str
    source_id: str
    content: str
    faiss_score: float
    granularity: str
    parent_chunk_id: str | None


@dataclass(frozen=True)
class _ChunkContext:
    chunk_id: str
    content: str
    granularity: str
    parent_chunk_id: str | None


@dataclass(frozen=True)
class PreparedQuery:
    """已完成规划和一次批量向量化、可供多个 FAISS 索引复用的查询。"""

    plan: QueryPlan
    embedding: EmbeddingResult


Embedder = Callable[[list[str]], EmbeddingResult | list[list[float]]]
Reranker = Callable[[str, list[str], int], list[tuple[int, float]]]

_DIRECT_LEXICAL_MATCH_SCORE = 0.75
_LEXICAL_CANDIDATE_SCORE = 0.9
_MIN_LEXICAL_QUERY_LENGTH = 2
_LEXICAL_EDGE_PUNCTUATION = " \t\r\n,，;；:：.!！?？()（）[]【】{}<>《》\"'“”‘’"


class RAGEngine:
    """串联 query chunk、embedding、FAISS、rerank 和阈值过滤。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths = initialize_databases(data_dir, self.settings)
        self.index = FaissIndexManager(
            self.paths.faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self.entity_index = FaissIndexManager(
            self.paths.entity_faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self.community_index = FaissIndexManager(
            self.paths.community_faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self._embedder = embedder
        self._reranker = reranker
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        self._prune_stale_auxiliary_embeddings()
        self._ensure_index_consistency()
        self._ensure_entity_index_consistency()
        self._ensure_community_index_consistency()

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
        lexical_candidates = self._load_lexical_candidates(
            prepared.plan,
            limit=search_limit,
        )
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

        hierarchy = self._load_chunk_hierarchy(candidates)
        candidates = _collapse_candidates_by_family(
            candidates,
            hierarchy,
            candidate_pool_limit,
        )
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

    def build_entity_vectors(self, node_id: str, summary: str) -> dict[str, Any]:
        """为单个实体建立或刷新向量；新鲜度由摘要及向量元数据决定。"""

        return self.sync_entity_vectors([(node_id, summary)])

    def build_community_vectors(
        self,
        group_id: str,
        summary: str,
    ) -> dict[str, Any]:
        """为单个群组建立或刷新向量。"""

        return self.sync_community_vectors([(group_id, summary)])

    def sync_entity_vectors(
        self,
        entities: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        """批量补齐或刷新实体向量，跳过摘要和模型元数据均未变化的项。"""

        result = self._sync_auxiliary_vectors("entity", entities)
        self._refresh_entity_embedding_flags()
        return result

    def sync_community_vectors(
        self,
        communities: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        """批量补齐或刷新群组向量。"""

        return self._sync_auxiliary_vectors("community", communities)

    def search_entities(
        self,
        query: str,
        top_k: int | None = None,
        *,
        prepared_query: PreparedQuery | None = None,
    ) -> list[dict[str, Any]]:
        """在实体摘要索引中执行语义检索。"""

        effective_top_k = (
            top_k if top_k is not None else self.settings.vector_search.entity_top_k
        )
        return self._search_auxiliary_vectors(
            "entity",
            query,
            effective_top_k,
            self.settings.vector_search.entity_weight,
            prepared_query=prepared_query,
        )

    def search_communities(
        self,
        query: str,
        top_k: int | None = None,
        *,
        prepared_query: PreparedQuery | None = None,
    ) -> list[dict[str, Any]]:
        """在群组总结索引中执行语义检索。"""

        effective_top_k = (
            top_k
            if top_k is not None
            else self.settings.vector_search.community_top_k
        )
        return self._search_auxiliary_vectors(
            "community",
            query,
            effective_top_k,
            self.settings.vector_search.community_weight,
            prepared_query=prepared_query,
        )

    def delete_entity_vectors(self, node_ids: Sequence[str]) -> int:
        """删除实体向量，并在索引增量删除失败时从 SQLite 恢复。"""

        removed = self._delete_auxiliary_vectors("entity", node_ids)
        self._refresh_entity_embedding_flags()
        return removed

    def delete_community_vectors(self, group_ids: Sequence[str]) -> int:
        """删除群组向量，并在索引增量删除失败时从 SQLite 恢复。"""

        return self._delete_auxiliary_vectors("community", group_ids)

    def rebuild_entity_index(self) -> int:
        """从 entity_embeddings 全量重建实体索引。"""

        return self._rebuild_auxiliary_index("entity")

    def rebuild_community_index(self) -> int:
        """从 community_embeddings 全量重建群组索引。"""

        return self._rebuild_auxiliary_index("community")

    def ensure_entity_index_consistency(self) -> None:
        self._ensure_entity_index_consistency()

    def ensure_community_index_consistency(self) -> None:
        self._ensure_community_index_consistency()

    def refresh_auxiliary_consistency(self) -> None:
        """清理图谱对象已删除或摘要已变化的辅助向量并同步两个索引。"""

        self._prune_stale_auxiliary_embeddings()
        self._ensure_entity_index_consistency()
        self._ensure_community_index_consistency()

    def add_vectors(
        self,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> None:
        """将已提交到 SQLite 的向量加入 FAISS 并清空 rerank 缓存。"""

        self.index.add(vector_ids, vectors)
        self._refresh_meta()
        clear_cache(self.paths.rerank_cache)

    def delete_vectors(self, vector_ids: Sequence[int]) -> int:
        """从 FAISS 物理删除向量并清空 rerank 缓存。"""

        removed = self.index.delete(vector_ids)
        if removed:
            self._refresh_meta()
            clear_cache(self.paths.rerank_cache)
        return removed

    def replace_vectors(
        self,
        remove_ids: Sequence[int],
        add_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> int:
        """提交 SQLite 后，以一次原子索引替换完成单文档向量更新。"""

        removed = self.index.replace(remove_ids, add_ids, vectors)
        self._refresh_meta()
        clear_cache(self.paths.rerank_cache)
        return removed

    def refresh_meta(self) -> None:
        """根据当前 rag.db 计数刷新 rag_meta.json。"""

        self._refresh_meta()

    def ensure_index_consistency(self) -> None:
        """校验 FAISS 与 rag.db 的 ID 集合，不一致时从数据库原子重建。"""

        self._ensure_index_consistency()

    def search_vectors(
        self,
        vectors: Sequence[Sequence[float]] | np.ndarray,
        top_k: int,
    ) -> list[list[tuple[int, float]]]:
        return self.index.search(vectors, top_k)

    def rebuild_index(self) -> int:
        """从 rag.db 原始 embedding 全量重建 FAISS。"""

        started_at = time.perf_counter()
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                """
                SELECT vector_id, vector_blob, dimensions, model_name,
                       vector_space_id
                FROM embeddings
                ORDER BY vector_id
                """
            ).fetchall()
        finally:
            connection.close()

        vector_space_ids = {
            _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
            for row in rows
        }
        if len(vector_space_ids) > 1:
            raise IndexIntegrityError(
                "embeddings 包含多个 vector_space_id，不能构建同一个 FAISS 索引："
                f"{sorted(vector_space_ids)}"
            )

        vector_ids: list[int] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            if row["dimensions"] != self.settings.models.embedding_dimensions:
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 的维度与当前配置不一致"
                )
            if row["model_name"] != self.settings.models.embedding:
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 的模型与当前配置不一致"
                )
            vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
            if len(vector) != self.settings.models.embedding_dimensions:
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 的 BLOB 长度与 dimensions 不一致"
                )
            if not np.isfinite(vector).all():
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 包含 NaN 或 Infinity"
                )
            vector_ids.append(int(row["vector_id"]))
            vectors.append(vector.copy())

        matrix = (
            np.vstack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty(
                (0, self.settings.models.embedding_dimensions), dtype=np.float32
            )
        )
        self.index.rebuild(vector_ids, matrix)
        self._refresh_meta()
        clear_cache(self.paths.rerank_cache)
        self._log_event(
            "faiss_rebuild",
            f"vectors={len(vector_ids)}",
            _elapsed_ms(started_at),
        )
        return len(vector_ids)

    def _embed_texts(self, texts: list[str], *, input_type: str) -> EmbeddingResult:
        if self._embedder is None:
            value = embed(
                texts,
                settings=self.settings,
                input_type=input_type,
            )
        else:
            value = self._embedder(texts)
        return _coerce_embedding_result(value)

    def _sync_auxiliary_vectors(
        self,
        kind: str,
        records: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        table, id_column, manager = self._auxiliary_spec(kind)
        normalized = _normalize_auxiliary_records(records, id_column)
        if not normalized:
            return {"updated": 0, "skipped": 0, "vector_space_id": None}

        object_ids = list(normalized)
        placeholders = ",".join("?" for _ in object_ids)
        connection = connect_rag(self.paths)
        try:
            existing_rows = connection.execute(
                f"""
                SELECT vector_id, {id_column}, summary_hash, dimensions,
                       model_name, vector_space_id
                FROM {table}
                WHERE {id_column} IN ({placeholders})
                """,
                tuple(object_ids),
            ).fetchall()
        finally:
            connection.close()
        existing = {str(row[id_column]): row for row in existing_rows}
        pending = [
            (object_id, summary)
            for object_id, summary in normalized.items()
            if not _auxiliary_row_is_fresh(
                existing.get(object_id),
                summary,
                self.settings,
            )
        ]
        if not pending:
            return {
                "updated": 0,
                "skipped": len(normalized),
                "vector_space_id": self._auxiliary_vector_space_id(kind),
            }

        started_at = time.perf_counter()
        embedding_result = self._embed_texts(
            [summary for _, summary in pending],
            input_type="document",
        )
        matrix = _as_float32_matrix(
            embedding_result.vectors,
            self.settings.models.embedding_dimensions,
        )
        if len(matrix) != len(pending):
            raise IndexIntegrityError(
                f"{kind} Embedding 返回数量不一致："
                f"期望 {len(pending)}，实际 {len(matrix)}"
            )
        vector_space_id = _validate_vector_space_id(
            embedding_result.vector_space_id
        )
        pending_ids = [object_id for object_id, _ in pending]
        pending_placeholders = ",".join("?" for _ in pending_ids)
        connection = connect_rag(self.paths)
        try:
            retained_spaces = {
                _validate_vector_space_id(row["vector_space_id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT vector_space_id FROM {table}
                    WHERE {id_column} NOT IN ({pending_placeholders})
                    """,
                    tuple(pending_ids),
                ).fetchall()
            }
            if retained_spaces.difference({vector_space_id}):
                raise IndexIntegrityError(
                    f"不能在 {table} 中混用 vector_space_id："
                    f"现有={sorted(retained_spaces)}，新增={vector_space_id}"
                )
            connection.execute("BEGIN IMMEDIATE")
            old_vector_ids = [
                int(row["vector_id"])
                for row in connection.execute(
                    f"""
                    SELECT vector_id FROM {table}
                    WHERE {id_column} IN ({pending_placeholders})
                    """,
                    tuple(pending_ids),
                ).fetchall()
            ]
            connection.execute(
                f"DELETE FROM {table} WHERE {id_column} IN ({pending_placeholders})",
                tuple(pending_ids),
            )
            now = datetime.now(timezone.utc).isoformat()
            new_vector_ids: list[int] = []
            for (object_id, summary), vector in zip(pending, matrix, strict=True):
                cursor = connection.execute(
                    f"""
                    INSERT INTO {table} (
                        {id_column}, summary, summary_hash, vector_blob,
                        dimensions, model_name, vector_space_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        object_id,
                        summary,
                        _summary_hash(summary),
                        vector.tobytes(),
                        self.settings.models.embedding_dimensions,
                        self.settings.models.embedding,
                        vector_space_id,
                        now,
                        now,
                    ),
                )
                new_vector_ids.append(int(cursor.lastrowid))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        try:
            manager.replace(old_vector_ids, new_vector_ids, matrix)
        except Exception as replace_error:
            try:
                self._rebuild_auxiliary_index(kind)
            except Exception as rebuild_error:
                raise IndexIntegrityError(
                    f"{kind} FAISS 增量替换失败，且无法从 SQLite 恢复："
                    f"replace={replace_error}; rebuild={rebuild_error}"
                ) from rebuild_error
        self._log_event(
            "auxiliary_embedding_build",
            f"kind={kind}, updated={len(pending)}, model={self.settings.models.embedding}",
            _elapsed_ms(started_at),
        )
        return {
            "updated": len(pending),
            "skipped": len(normalized) - len(pending),
            "vector_space_id": vector_space_id,
        }

    def _search_auxiliary_vectors(
        self,
        kind: str,
        query: str,
        top_k: int,
        weight: float,
        *,
        prepared_query: PreparedQuery | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise RAGQueryError("query 必须是非空字符串")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise RAGQueryError("top_k 必须是大于等于 1 的整数")
        _, _, manager = self._auxiliary_spec(kind)
        if manager.count == 0:
            return []

        prepared = prepared_query or self.prepare_query(query)
        _validate_prepared_query(query, prepared)
        embedding = prepared.embedding
        database_space = self._auxiliary_vector_space_id(kind)
        if database_space is not None and embedding.vector_space_id != database_space:
            raise RAGQueryError(
                f"查询向量空间与 {kind} 索引不一致："
                f"查询={embedding.vector_space_id}，索引={database_space}"
            )
        per_query_limit = top_k
        if len(prepared.plan.variants) > 1:
            per_query_limit = max(top_k * 2, self.settings.query_planning.candidate_pool_size)
        raw_hits = manager.search(embedding.vectors, per_query_limit)
        vector_scores = _fuse_vector_hits(
            raw_hits,
            prepared.plan.weights,
            self.settings.query_planning.rrf_k,
        )
        return self._load_auxiliary_results(kind, vector_scores, float(weight))[:top_k]

    def _load_auxiliary_results(
        self,
        kind: str,
        vector_scores: Mapping[int, float],
        weight: float,
    ) -> list[dict[str, Any]]:
        if not vector_scores:
            return []
        table, id_column, _ = self._auxiliary_spec(kind)
        placeholders = ",".join("?" for _ in vector_scores)
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT vector_id, {id_column} FROM {table}
                WHERE vector_id IN ({placeholders})
                """,
                tuple(vector_scores),
            ).fetchall()
        finally:
            connection.close()
        found_ids = {int(row["vector_id"]) for row in rows}
        if found_ids != set(vector_scores):
            raise IndexIntegrityError(
                f"{kind} FAISS 中存在 SQLite 缺失的 vector_id："
                f"{sorted(set(vector_scores).difference(found_ids))}"
            )
        object_by_vector = {
            int(row["vector_id"]): str(row[id_column]) for row in rows
        }
        object_ids = set(object_by_vector.values())
        object_placeholders = ",".join("?" for _ in object_ids)
        graph = connect_graph(self.paths)
        try:
            if kind == "entity":
                graph_rows = graph.execute(
                    f"""
                    SELECT node_id, keyword, summary FROM nodes
                    WHERE node_id IN ({object_placeholders})
                    """,
                    tuple(sorted(object_ids)),
                ).fetchall()
                details = {
                    str(row["node_id"]): {
                        "node_id": str(row["node_id"]),
                        "keyword": str(row["keyword"]),
                        "summary": str(row["summary"]),
                        "source": "entity",
                    }
                    for row in graph_rows
                }
            else:
                graph_rows = graph.execute(
                    f"""
                    SELECT group_id, summary, node_count FROM groups
                    WHERE group_id IN ({object_placeholders})
                    """,
                    tuple(sorted(object_ids)),
                ).fetchall()
                details = {
                    str(row["group_id"]): {
                        "group_id": str(row["group_id"]),
                        "summary": str(row["summary"]),
                        "node_count": int(row["node_count"] or 0),
                        "source": "community",
                    }
                    for row in graph_rows
                }
        finally:
            graph.close()
        missing_objects = object_ids.difference(details)
        if missing_objects:
            raise IndexIntegrityError(
                f"{table} 引用了不存在的图谱对象：{sorted(missing_objects)}"
            )

        results: list[dict[str, Any]] = []
        for vector_id, score in sorted(
            vector_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            detail = details[object_by_vector[vector_id]].copy()
            detail["score"] = float(score) * weight
            results.append(detail)
        return results

    def _delete_auxiliary_vectors(
        self,
        kind: str,
        object_ids: Sequence[str],
    ) -> int:
        table, id_column, manager = self._auxiliary_spec(kind)
        normalized_ids = _normalize_object_ids(object_ids, id_column)
        if not normalized_ids:
            return 0
        placeholders = ",".join("?" for _ in normalized_ids)
        connection = connect_rag(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            vector_ids = [
                int(row["vector_id"])
                for row in connection.execute(
                    f"""
                    SELECT vector_id FROM {table}
                    WHERE {id_column} IN ({placeholders})
                    """,
                    tuple(normalized_ids),
                ).fetchall()
            ]
            connection.execute(
                f"DELETE FROM {table} WHERE {id_column} IN ({placeholders})",
                tuple(normalized_ids),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        try:
            manager.delete(vector_ids)
        except Exception:
            self._rebuild_auxiliary_index(kind)
        return len(vector_ids)

    def _ensure_entity_index_consistency(self) -> None:
        self._ensure_auxiliary_index_consistency("entity")

    def _ensure_community_index_consistency(self) -> None:
        self._ensure_auxiliary_index_consistency("community")

    def _ensure_auxiliary_index_consistency(self, kind: str) -> None:
        _, _, manager = self._auxiliary_spec(kind)
        database_ids = self._database_auxiliary_vector_ids(kind)
        if manager.index_path.exists():
            try:
                manager.load()
                if manager.ids == database_ids:
                    return
            except IndexIntegrityError:
                pass
        self._rebuild_auxiliary_index(kind)

    def _rebuild_auxiliary_index(self, kind: str) -> int:
        table, _, manager = self._auxiliary_spec(kind)
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT vector_id, vector_blob, dimensions, model_name,
                       vector_space_id FROM {table} ORDER BY vector_id
                """
            ).fetchall()
        finally:
            connection.close()
        vector_ids, matrix = self._validated_auxiliary_matrix(rows, table)
        manager.rebuild(vector_ids, matrix)
        self._log_event(
            "faiss_rebuild",
            f"kind={kind}, vectors={len(vector_ids)}",
        )
        return len(vector_ids)

    def _database_auxiliary_vector_ids(self, kind: str) -> set[int]:
        table, _, _ = self._auxiliary_spec(kind)
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT vector_id, vector_blob, dimensions, model_name,
                       vector_space_id FROM {table}
                """
            ).fetchall()
        finally:
            connection.close()
        self._validated_auxiliary_matrix(rows, table)
        return {int(row["vector_id"]) for row in rows}

    def _validated_auxiliary_matrix(
        self,
        rows: Sequence[Any],
        table: str,
    ) -> tuple[list[int], np.ndarray]:
        spaces = {
            _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
            for row in rows
        }
        if len(spaces) > 1:
            raise IndexIntegrityError(
                f"{table} 包含多个 vector_space_id：{sorted(spaces)}"
            )
        vector_ids: list[int] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            vector_id = int(row["vector_id"])
            if int(row["dimensions"]) != self.settings.models.embedding_dimensions:
                raise IndexIntegrityError(f"{table}.vector_id={vector_id} 维度不一致")
            if str(row["model_name"]) != self.settings.models.embedding:
                raise IndexIntegrityError(f"{table}.vector_id={vector_id} 模型不一致")
            vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
            if (
                len(vector) != self.settings.models.embedding_dimensions
                or not np.isfinite(vector).all()
            ):
                raise IndexIntegrityError(
                    f"{table}.vector_id={vector_id} 的向量 BLOB 无效"
                )
            vector_ids.append(vector_id)
            vectors.append(vector.copy())
        matrix = (
            np.vstack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty(
                (0, self.settings.models.embedding_dimensions),
                dtype=np.float32,
            )
        )
        return vector_ids, matrix

    def _auxiliary_vector_space_id(self, kind: str) -> str | None:
        table, _, _ = self._auxiliary_spec(kind)
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                f"SELECT vector_id, vector_space_id FROM {table}"
            ).fetchall()
        finally:
            connection.close()
        spaces = {
            _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
            for row in rows
        }
        if len(spaces) > 1:
            raise IndexIntegrityError(
                f"{table} 包含多个 vector_space_id：{sorted(spaces)}"
            )
        return next(iter(spaces), None)

    def _prune_stale_auxiliary_embeddings(self) -> None:
        graph = connect_graph(self.paths)
        try:
            entity_hashes = {
                str(row["node_id"]): _summary_hash(str(row["summary"]))
                for row in graph.execute("SELECT node_id, summary FROM nodes").fetchall()
            }
            community_hashes = {
                str(row["group_id"]): _summary_hash(str(row["summary"]))
                for row in graph.execute(
                    "SELECT group_id, summary FROM groups"
                ).fetchall()
            }
        finally:
            graph.close()
        connection = connect_rag(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table, id_column, expected_hashes in (
                ("entity_embeddings", "node_id", entity_hashes),
                ("community_embeddings", "group_id", community_hashes),
            ):
                rows = connection.execute(
                    f"""
                    SELECT vector_id, {id_column}, summary_hash, vector_blob,
                           dimensions, model_name FROM {table}
                    """
                ).fetchall()
                stale_ids = [
                    int(row["vector_id"])
                    for row in rows
                    if not _auxiliary_row_matches_graph(
                        row,
                        expected_hashes.get(str(row[id_column])),
                        self.settings,
                    )
                ]
                if stale_ids:
                    placeholders = ",".join("?" for _ in stale_ids)
                    connection.execute(
                        f"DELETE FROM {table} WHERE vector_id IN ({placeholders})",
                        tuple(stale_ids),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._refresh_entity_embedding_flags()

    def _refresh_entity_embedding_flags(self) -> None:
        rag = connect_rag(self.paths)
        try:
            node_ids = {
                str(row["node_id"])
                for row in rag.execute(
                    "SELECT node_id FROM entity_embeddings"
                ).fetchall()
            }
        finally:
            rag.close()
        graph = connect_graph(self.paths)
        try:
            graph.execute("BEGIN IMMEDIATE")
            graph.execute("UPDATE nodes SET has_entity_embedding = 0")
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                graph.execute(
                    f"""
                    UPDATE nodes SET has_entity_embedding = 1
                    WHERE node_id IN ({placeholders})
                    """,
                    tuple(sorted(node_ids)),
                )
            graph.commit()
        except Exception:
            graph.rollback()
            raise
        finally:
            graph.close()

    def _auxiliary_spec(
        self,
        kind: str,
    ) -> tuple[str, str, FaissIndexManager]:
        if kind == "entity":
            return "entity_embeddings", "node_id", self.entity_index
        if kind == "community":
            return "community_embeddings", "group_id", self.community_index
        raise ValueError(f"未知辅助向量类型：{kind}")

    def _ensure_index_consistency(self) -> None:
        try:
            database_ids = self._database_vector_ids()
        except _MixedVectorSpaceError:
            # 明确触发一次从数据库重建；rebuild_index 会给出不可混用的诊断。
            self.rebuild_index()
            return
        if self.paths.faiss_index.exists():
            try:
                self.index.load()
                if self.index.ids == database_ids:
                    return
            except IndexIntegrityError:
                pass
        self.rebuild_index()

    def _database_vector_ids(self) -> set[int]:
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                """
                SELECT vector_id, dimensions, model_name, vector_space_id
                FROM embeddings
                """
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            if row["dimensions"] != self.settings.models.embedding_dimensions:
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 的维度与当前配置不一致，需重新 embedding"
                )
            if row["model_name"] != self.settings.models.embedding:
                raise IndexIntegrityError(
                    f"vector_id={row['vector_id']} 的模型与当前配置不一致，需重新 embedding"
                )
        vector_space_ids = {
            _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
            for row in rows
        }
        if len(vector_space_ids) > 1:
            raise _MixedVectorSpaceError(
                "embeddings 包含多个 vector_space_id，需重新 embedding："
                f"{sorted(vector_space_ids)}"
            )
        return {int(row["vector_id"]) for row in rows}

    def _database_vector_space_id(self) -> str | None:
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                "SELECT vector_id, vector_space_id FROM embeddings"
            ).fetchall()
        finally:
            connection.close()
        spaces = {
            _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
            for row in rows
        }
        if len(spaces) > 1:
            raise IndexIntegrityError(
                f"embeddings 包含多个 vector_space_id：{sorted(spaces)}"
            )
        return next(iter(spaces), None)

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

    def _load_source_paths(self, source_ids: set[str]) -> dict[str, str]:
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        connection = connect_sources(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT source_id, relative_path
                FROM sources
                WHERE source_id IN ({placeholders})
                """,
                tuple(source_ids),
            ).fetchall()
        finally:
            connection.close()
        paths = {row["source_id"]: row["relative_path"] for row in rows}
        missing_source_ids = source_ids.difference(paths)
        if missing_source_ids:
            raise IndexIntegrityError(
                f"chunk 对应的 source_id 在 sources.db 中不存在：{sorted(missing_source_ids)}"
            )
        return paths

    def _refresh_meta(self) -> None:
        connection = connect_rag(self.paths)
        try:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM chunks) AS total_chunks,
                    (SELECT COUNT(*) FROM embeddings) AS total_vectors
                """
            ).fetchone()
        finally:
            connection.close()
        vector_space_id = self._database_vector_space_id()
        meta = read_rag_meta(self.paths, self.settings)
        meta.update(
            {
                "total_chunks": int(counts["total_chunks"]),
                "total_vectors": int(counts["total_vectors"]),
                "vector_dimensions": self.settings.models.embedding_dimensions,
                "embedding_model": self.settings.models.embedding,
                "vector_space_id": vector_space_id,
                "chunking_signature": chunking_signature(self.settings),
                "faiss_index_type": "IndexIDMap2+IndexFlatIP",
                "last_built_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_rag_meta(self.paths, meta)

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log("rag_engine", action, detail, elapsed_ms, level)
        except Exception:
            pass


class _MixedVectorSpaceError(IndexIntegrityError):
    """数据库中混入了不能共同索引的向量空间。"""


def _coerce_embedding_result(
    value: EmbeddingResult | list[list[float]],
) -> EmbeddingResult:
    """消费新签名，并兼容旧注入测试返回的裸向量列表。"""

    if isinstance(value, EmbeddingResult):
        vector_space_id = _validate_vector_space_id(value.vector_space_id)
        return EmbeddingResult(
            vectors=value.vectors,
            vector_space_id=vector_space_id,
        )
    if isinstance(value, list):
        return EmbeddingResult(vectors=value, vector_space_id="unknown")
    raise TypeError("Embedding 返回值必须是 EmbeddingResult")


def _normalize_auxiliary_records(
    records: Sequence[tuple[str, str]],
    id_column: str,
) -> dict[str, str]:
    if isinstance(records, (str, bytes)):
        raise TypeError("records 必须是 (id, summary) 序列")
    normalized: dict[str, str] = {}
    for item in records:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("records 中每一项必须是 (id, summary) 二元组")
        object_id, summary = item
        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError(f"{id_column} 必须是非空字符串")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary 必须是非空字符串")
        normalized_id = object_id.strip()
        normalized_summary = summary.strip()
        previous = normalized.get(normalized_id)
        if previous is not None and previous != normalized_summary:
            raise ValueError(f"同一 {id_column} 对应了不同 summary：{normalized_id}")
        normalized[normalized_id] = normalized_summary
    return normalized


def _normalize_object_ids(values: Sequence[str], field_name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} 列表不能是字符串")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是非空字符串")
        item = value.strip()
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized


def _summary_hash(summary: str) -> str:
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


def _auxiliary_row_is_fresh(
    row: Any,
    summary: str,
    settings: AppConfig,
) -> bool:
    return bool(
        row is not None
        and str(row["summary_hash"]) == _summary_hash(summary)
        and int(row["dimensions"]) == settings.models.embedding_dimensions
        and str(row["model_name"]) == settings.models.embedding
        and isinstance(row["vector_space_id"], str)
        and bool(str(row["vector_space_id"]).strip())
    )


def _auxiliary_row_matches_graph(
    row: Any,
    expected_summary_hash: str | None,
    settings: AppConfig,
) -> bool:
    if (
        expected_summary_hash is None
        or str(row["summary_hash"]) != expected_summary_hash
        or int(row["dimensions"]) != settings.models.embedding_dimensions
        or str(row["model_name"]) != settings.models.embedding
    ):
        return False
    vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
    return bool(
        len(vector) == settings.models.embedding_dimensions
        and np.isfinite(vector).all()
    )


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


def _planned_lexical_match_score(plan: QueryPlan, document: str) -> float:
    """原始词优先，并允许通过漂移校验的扩展词提供较弱字面保底。"""

    return max(
        (
            _direct_lexical_match_score(variant.text, document) * variant.weight
            for variant in plan.variants
        ),
        default=0.0,
    )


def _rag_result_item(
    candidate: _Candidate,
    score: float,
    hierarchy: Mapping[str, _ChunkContext],
    source_paths: Mapping[str, str | None],
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
            "relative_path": source_paths.get(candidate.source_id),
        },
    }


def _direct_lexical_match_score(query: str, document: str) -> float:
    """为文档中的直接关键词命中提供保底分，避免短词被 Rerank 尺度吞没。"""

    normalized_query = _normalize_lexical_text(query).strip(_LEXICAL_EDGE_PUNCTUATION)
    if len(normalized_query) < _MIN_LEXICAL_QUERY_LENGTH:
        return 0.0
    normalized_document = _normalize_lexical_text(document)
    if normalized_query == normalized_document:
        return 1.0
    if normalized_query in normalized_document:
        return _DIRECT_LEXICAL_MATCH_SCORE
    return 0.0


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


def _normalize_lexical_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _validate_prepared_query(query: str, prepared: PreparedQuery) -> None:
    if not isinstance(prepared, PreparedQuery):
        raise RAGQueryError("prepared_query 类型无效")
    normalized = " ".join(unicodedata.normalize("NFKC", query).strip().split())
    if prepared.plan.original != normalized:
        raise RAGQueryError("prepared_query 与当前 query 不一致")
    if len(prepared.plan.variants) != len(prepared.embedding.vectors):
        raise RAGQueryError("prepared_query 的计划与向量数量不一致")


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _validate_vector_space_id(value: Any, vector_id: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        prefix = f"vector_id={vector_id} 的" if vector_id is not None else ""
        raise IndexIntegrityError(f"{prefix}vector_space_id 为空")
    return value.strip()


def _validate_vector_ids(vector_ids: Sequence[int]) -> list[int]:
    ids = list(vector_ids)
    if any(not isinstance(value, (int, np.integer)) or int(value) < 0 for value in ids):
        raise ValueError("vector_id 必须是非负整数")
    return [int(value) for value in ids]


def _as_float32_matrix(
    vectors: Sequence[Sequence[float]] | np.ndarray,
    dimensions: int,
) -> np.ndarray:
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError("vectors 必须是规则的二维数值数组") from exc
    if matrix.size == 0:
        return np.empty((0, dimensions), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != dimensions:
        raise ValueError(f"向量维度错误：期望 (*, {dimensions})，实际 {matrix.shape}")
    if not np.isfinite(matrix).all():
        raise ValueError("向量包含 NaN 或 Infinity")
    return np.ascontiguousarray(matrix)


def _validate_score_multipliers(values: Mapping[str, float]) -> None:
    for chunk_id, factor in values.items():
        if not isinstance(chunk_id, str) or not chunk_id:
            raise RAGQueryError("score_multipliers 的键必须是非空 chunk_id")
        if (
            not isinstance(factor, (int, float, np.integer, np.floating))
            or isinstance(factor, bool)
            or not np.isfinite(factor)
            or float(factor) < 1.0
        ):
            raise RAGQueryError("score_multipliers 的增强系数必须是大于等于 1 的有限数")
