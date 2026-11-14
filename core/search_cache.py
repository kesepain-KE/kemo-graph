from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.retrieval.cache.keys import CACHE_FORMAT_VERSION, _KEY_LOCKS, _KEY_LOCKS_GUARD, normalize_query, search_config_hash, compute_state_hash, _compute_state_hash_uncached, make_cache_key, cache_key_lock, _hash_query_rows, _feed_digest
from core.retrieval.cache.errors import SearchCacheError, SearchCacheCleanupError
from core.retrieval.cache.store import clear_search_cache_after_mutation, SearchCache, decode_cached_result, _metadata_from_row, _validate_page
from core.common.time import now_iso as _now_iso
import hashlib
import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from core.settings.schema import AppConfig
from core.common.cache import READ_CACHE, databases_revision, files_revision
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph, connect_rag, connect_search_cache, connect_sources
from core.storage.schema import initialize_search_cache
