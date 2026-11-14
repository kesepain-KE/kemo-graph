from __future__ import annotations
import sqlite3
from copy import deepcopy
from typing import Any
from uuid import uuid4
from core.common.tool_registration import _register, ToolFunction, ToolRegistration
from core.graph import tool_store as graph_tools
from core.graph.tool_schemas import GRAPH_TOOL_SCHEMAS, FINISH_SCHEMA

def get_graph_tools(
    conn: sqlite3.Connection,
    *,
    source_id: str | None = None,
    content_hash: str | None = None,
) -> list[ToolRegistration]:
    """返回七个图谱工具；可同时注入当前来源身份。"""

    if (source_id is None) != (content_hash is None):
        raise ValueError("source_id 和 content_hash 必须同时提供")

    functions: dict[str, ToolFunction] = {
        "add_entity": graph_tools.add_entity,
        "add_relation": graph_tools.add_relation,
        "search_entities": graph_tools.search_entities,
        "get_entity": graph_tools.get_entity,
        "list_entities": graph_tools.list_entities,
        "update_entity": graph_tools.update_entity,
        "delete_entity": graph_tools.delete_entity,
    }
    if source_id is not None and content_hash is not None:
        functions.update(
            _contextual_graph_functions(
                source_id=source_id,
                content_hash=content_hash,
            )
        )
    return _register(GRAPH_TOOL_SCHEMAS, functions, injected_connection=conn)

def _contextual_graph_functions(
    *,
    source_id: str,
    content_hash: str,
) -> dict[str, ToolFunction]:
    def add_entity(conn: sqlite3.Connection, **arguments: Any) -> Any:
        arguments.pop("source_id", None)
        arguments.pop("content_hash", None)
        return graph_tools.add_entity(
            conn,
            **arguments,
            source_id=source_id,
            content_hash=content_hash,
        )

    def add_relation(conn: sqlite3.Connection, **arguments: Any) -> Any:
        arguments.pop("source_id", None)
        arguments.pop("content_hash", None)
        return graph_tools.add_relation(
            conn,
            **arguments,
            source_id=source_id,
            content_hash=content_hash,
        )

    def update_entity(conn: sqlite3.Connection, **arguments: Any) -> Any:
        savepoint = f"provider_context_{uuid4().hex}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            result = graph_tools.update_entity(conn, **arguments)
            node_id = str(result["node_id"])
            conn.execute(
                """
                INSERT INTO node_sources (
                    node_id, source_id, content_hash, evidence_weight
                ) VALUES (?, ?, ?, 1.0)
                ON CONFLICT(node_id, source_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    evidence_weight = MAX(
                        node_sources.evidence_weight,
                        excluded.evidence_weight
                    )
                """,
                (node_id, source_id, content_hash),
            )
            conn.execute(
                """
                UPDATE nodes
                SET ref_count = (
                    SELECT COUNT(*) FROM node_sources WHERE node_id = ?
                ), weight = (
                    SELECT MAX(evidence_weight)
                    FROM node_sources WHERE node_id = ?
                )
                WHERE node_id = ?
                """,
                (node_id, node_id, node_id),
            )
        except Exception:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
        return graph_tools.get_entity(conn, node_id)

    def delete_entity(conn: sqlite3.Connection, **arguments: Any) -> Any:
        arguments.pop("source_id", None)
        return graph_tools.delete_entity(
            conn,
            **arguments,
            source_id=source_id,
        )

    return {
        "add_entity": add_entity,
        "add_relation": add_relation,
        "update_entity": update_entity,
        "delete_entity": delete_entity,
    }

def get_graph_tool_schemas() -> list[dict[str, Any]]:
    return deepcopy([*GRAPH_TOOL_SCHEMAS, FINISH_SCHEMA])
