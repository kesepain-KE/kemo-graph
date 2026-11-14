from __future__ import annotations

import unicodedata
from core.documents import QueryPlan

_DIRECT_LEXICAL_MATCH_SCORE = 0.75

_LEXICAL_CANDIDATE_SCORE = 0.9

_MIN_LEXICAL_QUERY_LENGTH = 2

_LEXICAL_EDGE_PUNCTUATION = " \t\r\n,，;；:：.!！?？()（）[]【】{}<>《》\"'“”‘’"

def _planned_lexical_match_score(plan: QueryPlan, document: str) -> float:
    """原始词优先，并允许通过漂移校验的扩展词提供较弱字面保底。"""

    return max(
        (
            _direct_lexical_match_score(variant.text, document) * variant.weight
            for variant in plan.variants
        ),
        default=0.0,
    )

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

def _normalize_lexical_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
