from __future__ import annotations

from dataclasses import dataclass
from provider.embedding import EmbeddingResult
from core.documents.query_planner import QueryPlan
from core.rag.errors import IndexIntegrityError

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

class _MixedVectorSpaceError(IndexIntegrityError):
    """数据库中混入了不能共同索引的向量空间。"""
