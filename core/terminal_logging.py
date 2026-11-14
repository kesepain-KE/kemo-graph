from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.observability.terminal_logging import configure_console_logging, TerminalLogHandler, capture_terminal_logs
from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
from threading import RLock
import yaml
from core.settings.schema import PROJECT_ROOT
from core.settings.loader import load_config
from core.observability.logger import DailyTSVLogger, redact_log_text
from core.common.cache import file_revision
