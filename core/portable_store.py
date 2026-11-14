"""Temporary compatibility for core.storage.portable."""
from .storage.portable import *  # noqa: F401,F403

from core.service.federation import create_store_service, describe_store, federated_query

from core.service.federation import initialize_store
