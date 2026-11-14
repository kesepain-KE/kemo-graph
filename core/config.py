"""Temporary compatibility for core.settings."""
from .settings import *  # noqa: F401,F403
from core.settings.loader import _deep_merge, _get_nested, _set_nested, _validate_with_fallback
from core.settings.schema import _migrate_legacy_provider_config, _resolve_runtime_path
