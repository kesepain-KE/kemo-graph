"""kemo 工具调用使用的 JSON Schema 定义。"""

from __future__ import annotations

from typing import Any


def _function_schema(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        "strict": True,
    }


_NODE_ID = {"type": "string", "description": "节点 UUID"}
_SOURCE_ID = {"type": "string", "description": "来源文档 UUID"}
_ALIASES = {
    "type": "array",
    "items": {"type": "string"},
    "description": "概念别名列表",
}
_TAGS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "概念标签列表",
}


ADD_ENTITY_SCHEMA = _function_schema(
    "add_entity",
    "向知识图谱添加一个新概念节点；调用前必须先 search_entities，不会自动合并同名节点",
    {
        "keyword": {"type": "string", "description": "短概念名，2~20 字符"},
        "summary": {"type": "string", "description": "该概念在本文语境中的完整描述"},
        "aliases": _ALIASES,
        "tags": _TAGS,
    },
    ["keyword", "summary"],
)

ADD_RELATION_SCHEMA = _function_schema(
    "add_relation",
    "添加两个已有概念节点之间的关系，并记录当前文档的证据权重",
    {
        "source_node_id": {"type": "string", "description": "起点节点 UUID"},
        "relation": {"type": "string", "description": "关系名称，建议 2~8 字符"},
        "target_node_id": {"type": "string", "description": "终点节点 UUID"},
        "evidence_weight": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "当前文档对该关系的证据权重",
        },
    },
    [
        "source_node_id",
        "relation",
        "target_node_id",
        "evidence_weight",
    ],
)

SEARCH_ENTITIES_SCHEMA = _function_schema(
    "search_entities",
    "按 keyword 和 aliases 搜索已有节点；创建节点前必须先调用",
    {
        "query": {"type": "string", "description": "关键词或别名"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 10,
        },
    },
    ["query"],
)

GET_ENTITY_SCHEMA = _function_schema(
    "get_entity",
    "获取节点完整详情及其来源绑定",
    {"node_id": _NODE_ID},
    ["node_id"],
)

LIST_ENTITIES_SCHEMA = _function_schema(
    "list_entities",
    "列出知识库中的全部节点摘要",
    {},
)

UPDATE_ENTITY_SCHEMA = _function_schema(
    "update_entity",
    "更新一个已有节点；只修改明确传入的字段",
    {
        "node_id": _NODE_ID,
        "keyword": {"type": "string", "description": "新的短概念名，2~20 字符"},
        "summary": {"type": "string", "description": "新的完整描述"},
        "aliases": _ALIASES,
        "tags": _TAGS,
    },
    ["node_id"],
)

DELETE_ENTITY_SCHEMA = _function_schema(
    "delete_entity",
    "解除节点与指定来源的绑定；无其他来源时级联删除该节点",
    {"node_id": _NODE_ID},
    ["node_id"],
)

SEARCH_DOCUMENTS_SCHEMA = _function_schema(
    "search_documents",
    "按文件名或路径搜索知识库中的文档",
    {"query": {"type": "string", "description": "文件名或路径片段"}},
    ["query"],
)

GET_DOCUMENT_NODES_SCHEMA = _function_schema(
    "get_document_nodes",
    "列出指定文档关联的全部知识图谱节点",
    {"source_id": _SOURCE_ID},
    ["source_id"],
)

GET_DOCUMENT_RELATIONS_SCHEMA = _function_schema(
    "get_document_relations",
    "列出指定文档提供证据的全部关系",
    {"source_id": _SOURCE_ID},
    ["source_id"],
)

DELETE_NODE_SCHEMA = _function_schema(
    "delete_node",
    "按知识库级联规则删除节点，并处理来源解绑与条件回收",
    {"node_id": _NODE_ID},
    ["node_id"],
)

DELETE_RELATION_SCHEMA = _function_schema(
    "delete_relation",
    "删除整条关系及其全部来源证据",
    {"edge_id": {"type": "string", "description": "关系 UUID"}},
    ["edge_id"],
)

DELETE_DOCUMENT_SCHEMA = _function_schema(
    "delete_document",
    "删除文档并级联清理 Graph、RAG、FAISS，文件移入回收站",
    {"source_id": _SOURCE_ID},
    ["source_id"],
)


def _converter_schema(name: str, label: str) -> dict[str, Any]:
    return _function_schema(
        name,
        f"将 {label} 文件转换为 Markdown 文本",
        {"path": {"type": "string", "description": "待转换文件的绝对路径"}},
        ["path"],
    )


CONVERT_PDF_SCHEMA = _converter_schema("convert_pdf", "PDF")
CONVERT_DOCX_SCHEMA = _converter_schema("convert_docx", "DOCX")
CONVERT_HTML_SCHEMA = _converter_schema("convert_html", "HTML")
CONVERT_TXT_SCHEMA = _converter_schema("convert_txt", "TXT")
CONVERT_RST_SCHEMA = _converter_schema("convert_rst", "reStructuredText")
CONVERT_CSV_SCHEMA = _converter_schema("convert_csv", "CSV")
CONVERT_SPREADSHEET_SCHEMA = _converter_schema("convert_spreadsheet", "XLSX/XLSM/XLS")
CONVERT_PPTX_SCHEMA = _converter_schema("convert_pptx", "PPTX")
CONVERT_EPUB_SCHEMA = _converter_schema("convert_epub", "EPUB")
CONVERT_RTF_SCHEMA = _converter_schema("convert_rtf", "RTF")
CONVERT_DATA_SCHEMA = _converter_schema("convert_data", "JSON/YAML/XML")

IMPORT_DOCUMENT_SCHEMA = _function_schema(
    "import_document",
    "检测文档格式、转换为 Markdown，并原子写入 external/markdown",
    {
        "path": {"type": "string", "description": "来源文件的绝对路径"},
        "external_dir": {
            "type": "string",
            "description": "external 根目录或 external/markdown 的绝对路径",
        },
    },
    ["path", "external_dir"],
)

FINISH_SCHEMA = _function_schema(
    "finish",
    "图谱构建完成，无需更多操作",
    {},
)


GRAPH_TOOL_SCHEMAS = [
    ADD_ENTITY_SCHEMA,
    ADD_RELATION_SCHEMA,
    SEARCH_ENTITIES_SCHEMA,
    GET_ENTITY_SCHEMA,
    LIST_ENTITIES_SCHEMA,
    UPDATE_ENTITY_SCHEMA,
    DELETE_ENTITY_SCHEMA,
]

DELETE_TOOL_SCHEMAS = [
    SEARCH_DOCUMENTS_SCHEMA,
    GET_DOCUMENT_NODES_SCHEMA,
    GET_DOCUMENT_RELATIONS_SCHEMA,
    DELETE_NODE_SCHEMA,
    DELETE_RELATION_SCHEMA,
    DELETE_DOCUMENT_SCHEMA,
]

DOCUMENT_TOOL_SCHEMAS = [
    CONVERT_PDF_SCHEMA,
    CONVERT_DOCX_SCHEMA,
    CONVERT_HTML_SCHEMA,
    CONVERT_TXT_SCHEMA,
    CONVERT_RST_SCHEMA,
    CONVERT_CSV_SCHEMA,
    CONVERT_SPREADSHEET_SCHEMA,
    CONVERT_PPTX_SCHEMA,
    CONVERT_EPUB_SCHEMA,
    CONVERT_RTF_SCHEMA,
    CONVERT_DATA_SCHEMA,
    IMPORT_DOCUMENT_SCHEMA,
]

ALL_TOOL_SCHEMAS = [
    *GRAPH_TOOL_SCHEMAS,
    *DELETE_TOOL_SCHEMAS,
    *DOCUMENT_TOOL_SCHEMAS,
    FINISH_SCHEMA,
]


__all__ = [name for name in globals() if name.endswith("_SCHEMA") or name.endswith("_SCHEMAS")]
