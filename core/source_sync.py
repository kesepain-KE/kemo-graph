from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.maintenance.source_sync import MAX_SOURCE_CONTENT_BYTES, MAX_SOURCE_METADATA_BYTES, _SOURCE_TYPE_PATTERN, _SAFE_STEM_PATTERN, ExternalSourceSyncError, sync_external_sources, list_external_sources, delete_external_sources, _validate_batch, _classify, _load_existing, _update_external_columns, _mark_external_deleted, _find_by_relative_path, _managed_relative_path, _contained_markdown_path, _ingest_changed, _detail, _serialize_source_row, _validate_source_uri, _validate_source_type, _normalize_timestamp, _parse_timestamp, _validate_sha256, _required_text
from core.common.jsonio import write_bytes_atomic as _write_bytes_atomic
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4
from core.storage.connections import connect_sources
from core.storage.schema import initialize_databases
from core.ingest.pipeline import Ingestor
from core.documents.paths import _hash_relative_path
from core.retrieval.cache.store import clear_search_cache_after_mutation
