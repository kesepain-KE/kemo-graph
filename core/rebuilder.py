from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.maintenance.rebuild import ProgressCallback, RebuildError, KnowledgeBaseRebuilder, _seed_shadow_sources, _validate_shadow, _validate_auxiliary_shadow_rows, _assert_external_unchanged, _copy_job_history, _copy_search_cache_history, _swap_directories, _swap_projection_storage, _projection_entries, _is_within, _replace_with_retry, _failed_details, _progress
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph, connect_rag, connect_sources
from core.storage.schema import initialize_databases
from core.ingest.pipeline import Ingestor
from core.storage.locks import get_knowledge_base_lock
from core.observability.logger import DailyTSVLogger
from core.rag.engine import RAGEngine
