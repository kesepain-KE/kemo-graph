from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.maintenance.jobs import ServiceFactory, UpdaterFactory, SUPPORTED_JOB_KINDS, JobNotFoundError, MaintenanceJobManager, list_jobs, get_job, _job_payload, _insert_event, _initial_detail
from core.common.time import now_iso as _now_iso
import json
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_sources
from core.storage.schema import initialize_databases
from update import ApplicationUpdater
