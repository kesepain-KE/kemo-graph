"""Temporary compatibility for core.storage."""
from .storage import *  # noqa: F401,F403
from .storage.meta import deepcopy_dict
from .storage.schema import SOURCES_SCHEMA, GRAPH_SCHEMA, RAG_SCHEMA, SEARCH_CACHE_SCHEMA
