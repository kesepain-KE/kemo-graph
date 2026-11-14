from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.paths import _resolve_external_dir, _safe_source_path, _hash_relative_path, _path_key
import hashlib
import os
import unicodedata
from pathlib import Path
from core.settings.schema import AppConfig, PROJECT_ROOT
from core.documents.errors import IngestError
