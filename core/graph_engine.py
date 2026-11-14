from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.errors import GraphError, GraphIntegrityError, GraphQueryError
from core.graph.models import _Node, _NodeKeys, _EntityKeys, _MatchedNode
from core.graph.engine import EntityExtractor, GraphEngine
from core.graph.matching import _CONTAINS_MATCH_SCORE, _SUMMARY_CONTAINS_MATCH_SCORE, _MIN_PARTIAL_MATCH_LENGTH, _MAX_SUMMARY_FALLBACK_NODES, _get_neighbors, _node_from_row, _parse_string_array, _indexed_nodes, _node_keys, _entity_keys, _node_match_score, _entities_from_query_plan, _is_meaningful_contains_match, _matched_node_to_dict, _edge_from_row, _build_relations, _build_paths, _load_node_keywords, _validate_query_parameters, _validate_depth_and_direction
import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any
from rapidfuzz.fuzz import ratio
from core.common.normalization import norm_key
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph
from core.storage.schema import initialize_databases
from core.graph.entity import Entity, extract
from core.graph.index import NodeMatcher
from core.documents.query_planner import QueryPlan, plan_query
from core.observability.progress import step_status
