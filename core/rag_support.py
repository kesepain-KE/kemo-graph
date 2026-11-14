from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.rag.models import _Candidate, _ChunkContext, PreparedQuery, _MixedVectorSpaceError
from core.rag.lexical import _DIRECT_LEXICAL_MATCH_SCORE, _LEXICAL_CANDIDATE_SCORE, _MIN_LEXICAL_QUERY_LENGTH, _LEXICAL_EDGE_PUNCTUATION, _planned_lexical_match_score, _direct_lexical_match_score, _normalize_lexical_text
from core.rag.vectors import _coerce_embedding_result, _validate_vector_space_id
from core.rag.auxiliary_records import _normalize_auxiliary_records, _normalize_object_ids, _summary_hash, _auxiliary_row_is_fresh, _auxiliary_row_matches_graph
from core.rag.fusion import _fuse_vector_hits, _merge_candidates, _collapse_candidates_by_family, _candidate_context_content, _rag_result_item, _chunk_family_id
from core.rag.validation import _validate_prepared_query, _validate_score_multipliers
from core.common.time import elapsed_ms as _elapsed_ms
import hashlib
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
import numpy as np
from provider.embedding import EmbeddingResult
from core.settings.schema import AppConfig
from core.documents.query_planner import QueryPlan
from core.rag.errors import IndexIntegrityError, RAGQueryError
