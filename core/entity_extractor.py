from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.entity import _QUOTED_PATTERN, _SPLIT_PATTERN, _EDGE_PUNCTUATION, _RULE_STOP_WORDS, _LLM_SYSTEM_PROMPT, EntityExtractionError, Entity, LLMCallable, extract, _extract_by_rule, _extract_by_llm, _parse_json_response, _entity_from_payload, _deduplicate, _unique_strings, display_normalize, _log_llm_request
import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from provider.engine import chat
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.observability.logger import DailyTSVLogger
from core.common.normalization import norm_key
