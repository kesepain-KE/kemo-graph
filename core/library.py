from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.library import TEXT_SUFFIXES, LibraryNameConflictError, _write_lock, _is_link, original_root, resolve_path, validate_name, source_rows, entry, list_library, create_entry, original_for_source, read_content, update_content, relocate_entry, _copy_file, transfer_entry, delete_entry
from core.maintenance.library import refresh_library, build_paths
import hashlib
import os
import shutil
import stat
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from core.storage.connections import connect_sources
from core.storage.schema import initialize_databases
from core.documents.errors import DocumentNotFoundError
from core.documents.file_map import FileMapStore, FileMapping
from core.documents.paths import _path_key
from core.documents.errors import DocumentContentConflictError
from core.documents.formats import MAX_IMPORT_BYTES, SUPPORTED_IMPORT_SUFFIXES
from core.common.jsonio import write_bytes_atomic as _write_bytes_atomic
from core.storage.locks import get_knowledge_base_lock
from markitdown._utils import read_text
