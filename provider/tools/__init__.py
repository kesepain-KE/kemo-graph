"""Provider 工具 Schema 与安全闭包的统一注册入口。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any
from uuid import uuid4

from . import delete_tools, document_tools, graph_tools
from .tool_schemas import (
    DELETE_TOOL_SCHEMAS,
    DOCUMENT_TOOL_SCHEMAS,
    FINISH_SCHEMA,
    GRAPH_TOOL_SCHEMAS,
)


ToolFunction = Callable[..., Any]
ToolRegistration = dict[str, Any]

_SCHEMA_MAP = {
    "graph": [*GRAPH_TOOL_SCHEMAS, FINISH_SCHEMA],
    "delete": DELETE_TOOL_SCHEMAS,
    "document": DOCUMENT_TOOL_SCHEMAS,
}


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


def get_delete_tools(conn: sqlite3.Connection) -> list[ToolRegistration]:
    """返回六个删除工具注册项，闭包中已注入当前 SQLite 连接。"""

    functions: dict[str, ToolFunction] = {
        "search_documents": delete_tools.search_documents,
        "get_document_nodes": delete_tools.get_document_nodes,
        "get_document_relations": delete_tools.get_document_relations,
        "delete_node": delete_tools.delete_node,
        "delete_relation": delete_tools.delete_relation,
        "delete_document": delete_tools.delete_document,
    }
    return _register(DELETE_TOOL_SCHEMAS, functions, injected_connection=conn)


def get_document_tools() -> list[ToolRegistration]:
    """返回七个本地文档转换/导入工具注册项。"""

    functions: dict[str, ToolFunction] = {
        "convert_pdf": document_tools.convert_pdf,
        "convert_docx": document_tools.convert_docx,
        "convert_html": document_tools.convert_html,
        "convert_txt": document_tools.convert_txt,
        "convert_rst": document_tools.convert_rst,
        "convert_csv": document_tools.convert_csv,
        "import_document": document_tools.import_document,
    }
    return _register(DOCUMENT_TOOL_SCHEMAS, functions)


def get_tool_schemas(category: str) -> list[dict[str, Any]]:
    """返回指定类别的独立 Schema 副本；未知类别返回空列表。"""

    if not isinstance(category, str):
        raise TypeError("工具类别必须是字符串")
    return deepcopy(_SCHEMA_MAP.get(category.casefold(), []))


def dispatch_tool(
    registrations: list[ToolRegistration],
    name: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按名称执行注册闭包，便于直接接入 ``chat_with_tools``。"""

    for registration in registrations:
        if registration["name"] == name:
            return registration["handler"](arguments or {})
    return {"ok": False, "error": f"未知工具：{name}"}


def _register(
    schemas: list[dict[str, Any]],
    functions: dict[str, ToolFunction],
    *,
    injected_connection: sqlite3.Connection | None = None,
) -> list[ToolRegistration]:
    registrations: list[ToolRegistration] = []
    for schema in schemas:
        name = str(schema["name"])
        function = functions[name]
        registrations.append(
            {
                "name": name,
                "schema": schema,
                "handler": _safe_handler(function, injected_connection),
            }
        )
    return registrations


def _safe_handler(
    function: ToolFunction,
    connection: sqlite3.Connection | None,
) -> Callable[..., dict[str, Any]]:
    def handler(
        arguments: Mapping[str, Any] | None = None,
        **keyword_arguments: Any,
    ) -> dict[str, Any]:
        try:
            if arguments is not None and not isinstance(arguments, Mapping):
                raise TypeError("工具参数必须是对象")
            parameters = dict(arguments or {})
            parameters.update(keyword_arguments)
            result = (
                function(connection, **parameters)
                if connection is not None
                else function(**parameters)
            )
            return {"ok": True, "data": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return handler


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
                INSERT INTO node_sources (node_id, source_id, content_hash)
                VALUES (?, ?, ?)
                ON CONFLICT(node_id, source_id) DO UPDATE SET
                    content_hash = excluded.content_hash
                """,
                (node_id, source_id, content_hash),
            )
            conn.execute(
                """
                UPDATE nodes
                SET ref_count = (
                    SELECT COUNT(*) FROM node_sources WHERE node_id = ?
                )
                WHERE node_id = ?
                """,
                (node_id, node_id),
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


__all__ = [
    "get_graph_tools",
    "get_delete_tools",
    "get_document_tools",
    "get_tool_schemas",
    "dispatch_tool",
]
