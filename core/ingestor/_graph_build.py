from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.ingest.graph_build import _public_chat_with_tools, _public_chat_structured, _public_supports_structured_output, _LARGE_RELATION_MIN_WEIGHT, _large_relation_limit, _sparsify_graph_relations, _validate_graph_draft_budget, _update_graph_for_source, _extract_graph_with_tools, _extract_graph_structured, _extract_graph_sections, _match_or_create_draft_node, _match_or_create_edge, _recalculate_edges, _recalculate_nodes
from core.ingest.errors import GraphExtractionError
from core.ingest.prompt import _GRAPH_EXTRACTION_BUDGETS, _graph_extraction_budget, _bounded_graph_draft_schema, _build_graph_system_prompt, _build_graph_section_prompt, _graph_granularity_rule
import json
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any
from uuid import uuid4
from core.storage.connections import connect_graph
from core.graph.draft import GRAPH_DRAFT_SCHEMA, DraftEntity, GraphDraft, merge_graph_drafts, split_markdown_for_graph, validate_graph_draft
from core.ingest.errors import IngestError
from core.ingest.scan import _SourceRecord
from core.common.time import elapsed_ms as _elapsed_ms, now_iso as _now_iso
from core.ingest.validation import _load_json_string_list, _semantic_key, _unique_texts
