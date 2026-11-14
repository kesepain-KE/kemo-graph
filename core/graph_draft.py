from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.draft import GRAPH_DRAFT_SCHEMA, GraphDraftError, DraftEntity, DraftRelation, GraphDraft, validate_graph_draft, merge_graph_drafts, split_markdown_for_graph, _split_oversized, _merge_entity, _text, _optional_text, _string_list, _weight, _unique, _available_local_id
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable
from core.text_norm import norm_key
