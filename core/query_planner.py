from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.query_planner import LOGGER, QUERY_PLANNER_VERSION, _PROMPT_PATH, _TYPE_WEIGHTS, _PLAN_SCHEMA, QueryPlanningError, QueryVariant, PlannedEntity, QueryPlan, StructuredPlanner, plan_query, filter_semantic_drift, query_planner_signature, _plan_from_payload, _entity_from_payload, _append_variant, _original_only, _rule_plan, _normalized_text, _optional_text, _match_key
import hashlib
import json
import logging
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
import numpy as np
from provider.engine import chat_structured
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.read_cache import READ_CACHE, file_revision
from core.observability.progress import query_step, step_status, tracked_step
