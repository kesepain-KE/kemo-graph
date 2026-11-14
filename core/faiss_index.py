from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.rag.index import FaissIndexManager, _validate_vector_ids, _as_float32_matrix
import os
import threading
from pathlib import Path
from typing import Any, Sequence
import numpy as np
from core.rag.errors import FaissUnavailableError, IndexIntegrityError
