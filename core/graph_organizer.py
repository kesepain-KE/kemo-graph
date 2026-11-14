from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.organizer import DEFAULT_ORGANIZER_PROMPT_PATH, ORGANIZER_TOOL_SCHEMAS, GraphOrganizer, _merge_nodes, _choose_keep_node, _cleanup_projection, _rebuild_chunk_nodes, _unique_texts, _required_text
from core.graph.candidates import MAX_ORGANIZER_CANDIDATES, _find_candidates, _neighbor_signatures, _get_entity, _json_list, _jaccard
from core.graph.errors import GraphOrganizerError
from core.graph.models import MergeCandidate
from core.common.time import now_iso as _now_iso
import json
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from heapq import nsmallest
from pathlib import Path
from typing import Any, Iterable
import numpy as np
from rapidfuzz import fuzz, process
from provider.engine import chat, chat_with_tools
from core.common.normalization import norm_key
from core.settings.schema import AppConfig, PROJECT_ROOT
from core.settings.loader import load_config
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph
from core.storage.schema import initialize_databases
from core.storage.meta import read_graph_meta, write_graph_meta
from core.storage.locks import get_knowledge_base_lock
from core.observability.logger import DailyTSVLogger
from core.rag.engine import RAGEngine
