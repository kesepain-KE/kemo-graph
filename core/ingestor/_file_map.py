from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.ingest.file_map import FILE_MAP_VERSION, FileMapError, FileMapping, FileMapStore
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.ingest.paths import _path_key
from core.common.fs import validated_path as _validated_path
