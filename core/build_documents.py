from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.build import processed_source, read_processed, update_processed
import hashlib
from datetime import datetime, timezone
from typing import Any
from core.storage.connections import connect_sources
from core.documents.errors import DocumentNotFoundError
from core.documents.paths import _safe_source_path
from core.documents.errors import DocumentContentConflictError, DocumentTooLargeError
from core.documents.formats import MAX_IMPORT_BYTES
from core.common.jsonio import write_bytes_atomic as _write_bytes_atomic
from core.storage.locks import get_knowledge_base_lock
