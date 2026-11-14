from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.storage.fulltext import NATIVE_ROOT, _LOAD_LOCK, _DICTIONARY_CONFIGURED, _library, _dictionary_path, load_simple, compile_fulltext, _QUERY_STOP_WORDS, _prune_expression
import ctypes
from functools import lru_cache
from pathlib import Path
import platform
import sqlite3
import struct
import threading
from core.common.normalization import norm_key
