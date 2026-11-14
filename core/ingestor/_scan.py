from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.ingest.scan import ScanResult, _SourceRecord, cleanup_orphaned_sources, scan_sources, _library_path, _rag_chunking_config_changed, _graph_extraction_config_changed, _source_record_from_row
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from core.documents.chunker.engine import chunking_signature
from core.storage.connections import connect_rag, connect_sources
from core.storage.meta import read_graph_meta, read_rag_meta
from core.ingest.errors import IngestError
from core.common.time import elapsed_ms as _elapsed_ms, now_iso as _now_iso
from core.ingest.paths import _hash_relative_path, _path_key, _safe_source_path
