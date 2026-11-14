"""Temporary compatibility for core.storage."""
from .storage import *  # noqa: F401,F403
from core.storage.meta import deepcopy_dict
from core.storage.schema import SOURCES_SCHEMA, GRAPH_SCHEMA, RAG_SCHEMA, SEARCH_CACHE_SCHEMA
