"""Temporary migration import; removed in stage 10."""
from core.rag.engine import RAGEngine, Embedder, Reranker
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
from provider.embedding import EmbeddingResult, embed
from provider.rerank import clear_cache, rerank
from core.chunker import chunking_signature
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.storage.connections import connect_graph, connect_rag, connect_sources
from core.storage.schema import initialize_databases
from core.storage.meta import read_rag_meta, write_rag_meta
from core.logger import DailyTSVLogger
from core.rag.index import FaissIndexManager, _as_float32_matrix
from core.observability.progress import query_step, step_status, tracked_step
from core.rag.errors import FaissUnavailableError, IndexIntegrityError, RAGError, RAGQueryError
from core.rag.models import PreparedQuery, _Candidate, _ChunkContext, _MixedVectorSpaceError
from core.rag.lexical import _DIRECT_LEXICAL_MATCH_SCORE, _LEXICAL_CANDIDATE_SCORE, _LEXICAL_EDGE_PUNCTUATION, _MIN_LEXICAL_QUERY_LENGTH, _direct_lexical_match_score, _normalize_lexical_text, _planned_lexical_match_score
from core.rag.auxiliary_records import _auxiliary_row_is_fresh
from core.rag.fusion import _candidate_context_content, _chunk_family_id, _collapse_candidates_by_family, _fuse_vector_hits, _merge_candidates, _rag_result_item
from core.rag.vectors import _coerce_embedding_result, _validate_vector_space_id
from core.common.time import elapsed_ms as _elapsed_ms
from core.rag.validation import _validate_prepared_query, _validate_score_multipliers
from core.documents.query_planner import QueryPlan, filter_semantic_drift, plan_query
