from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.chunker.rules import _TOKEN_PATTERN, _PARAGRAPH_BREAK_PATTERN, _GRANULARITY_ORDER, _pre_split_by_paragraphs, estimate_token_count, _semantic_blocks, _validated_block_ranges, _char_ranges_to_spans, _span_from_token_range, _chunk_spans, _find_parent_index
from core.documents.chunker.llm import _CHUNKING_PROMPT_PATH, _CHUNKING_PROMPT_VERSION, _CHUNKING_SCHEMA, LOGGER, _llm_chunk_ranges, _llm_part_ranges, _chunking_prompt_digest
from core.documents.chunker.models import DocumentChunk, _ChunkSpan, _TextBlock
from core.documents.chunker.engine import chunk, document_chunks, chunking_signature, llm_chunk, _semantic_hierarchy_spans, _normalize_semantic_leaf_spans, _group_adjacent_semantic_spans, _hierarchical_document_chunks
import bisect
import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from provider.engine import chat_structured
from core.settings.schema import AppConfig
from core.settings.loader import load_config
