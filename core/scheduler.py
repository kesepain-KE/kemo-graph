from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.maintenance.scheduler import LOGGER, MaintenanceService, MaintenanceScheduler, _log_scheduler
import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Protocol
from core.settings.schema import AppConfig
from core.observability.logger import DailyTSVLogger
