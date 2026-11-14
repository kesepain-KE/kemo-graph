from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.visualization import GRAPH_VISUALIZATION_FORMAT_VERSION, _SQLITE_PARAMETER_CHUNK, GraphDirection, _REVISION_QUERIES, compute_graph_revision, get_visualization_meta, list_visualization_nodes, list_visualization_edges, get_neighborhood, _graph_snapshot, _feed_digest, _node_payloads, _related_values, _decode_string_list, _edge_payload, _group_meta, _build_adjacency, _bounded_bfs, _node_rows_by_ids, _groups_for_nodes, _chunks
from core.graph.errors import GraphNodeNotFoundError, GraphRevisionChangedError
from core.graph.pagination import _validate_pagination, _validate_integer, _require_revision, _pagination
from core.common.text import required_text as _required_text
import hashlib
import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator, Literal, Sequence
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph
