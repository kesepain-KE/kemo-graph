from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.ingest.rag_build import _update_rag_for_source, _build_entity_vectors_for_source, _replace_rag_vectors_with_recovery, _sync_chunk_nodes_for_source, _remove_chunk_node_ids, _current_graph_node_ids, _coerce_embedding_result, _validate_embedding_batch
import time
from collections.abc import Sequence
from typing import Any
from uuid import uuid4
import numpy as np
from provider.embedding import EmbeddingResult
from core.documents.chunker.engine import document_chunks
from core.storage.connections import connect_graph, connect_rag, connect_sources
from core.rag.engine import RAGEngine
from core.ingest.errors import IngestError
from core.ingest.scan import _SourceRecord
from core.common.time import elapsed_ms as _elapsed_ms, now_iso as _now_iso
