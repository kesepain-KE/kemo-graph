"""Provider 工具 Schema 与安全闭包的统一注册入口。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any
from uuid import uuid4

from . import delete_tools, document_tools
from core.graph.tools import get_graph_tools
from core.common.tool_registration import _register
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
    """返回全部本地文档转换/导入工具注册项。"""

    functions: dict[str, ToolFunction] = {
        "convert_pdf": document_tools.convert_pdf,
        "convert_docx": document_tools.convert_docx,
        "convert_html": document_tools.convert_html,
        "convert_txt": document_tools.convert_txt,
        "convert_rst": document_tools.convert_rst,
        "convert_csv": document_tools.convert_csv,
        "convert_spreadsheet": document_tools.convert_spreadsheet,
        "convert_pptx": document_tools.convert_pptx,
        "convert_epub": document_tools.convert_epub,
        "convert_rtf": document_tools.convert_rtf,
        "convert_eml": document_tools.convert_eml,
        "convert_data": document_tools.convert_data,
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








__all__ = [
    "get_graph_tools",
    "get_delete_tools",
    "get_document_tools",
    "get_tool_schemas",
    "dispatch_tool",
]
