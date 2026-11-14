from __future__ import annotations
import unicodedata
from collections.abc import Mapping
import numpy as np
from core.rag.errors import RAGQueryError
from core.rag.models import PreparedQuery

def _validate_prepared_query(query: str, prepared: PreparedQuery) -> None:
    if not isinstance(prepared, PreparedQuery):
        raise RAGQueryError("prepared_query 类型无效")
    normalized = " ".join(unicodedata.normalize("NFKC", query).strip().split())
    if prepared.plan.original != normalized:
        raise RAGQueryError("prepared_query 与当前 query 不一致")
    if len(prepared.plan.variants) != len(prepared.embedding.vectors):
        raise RAGQueryError("prepared_query 的计划与向量数量不一致")

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
