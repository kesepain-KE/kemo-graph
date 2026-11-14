from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.documents.preview import HTML_STYLE, OFFICE_PREVIEW_SUFFIXES, _html, preview_document
import base64
import hashlib
from html import escape
from pathlib import Path
from typing import Any
from markitdown import detect_file_type
from markitdown._utils import read_text
from core.documents.library import TEXT_SUFFIXES
from core.documents.formats import MAX_IMPORT_BYTES
from core.documents.errors import DocumentTooLargeError
