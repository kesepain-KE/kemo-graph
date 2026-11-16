from __future__ import annotations
from typing import Any

def _function_schema(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    required_fields = set(required or ())
    # strict 模式要求全部属性必填，可选字段通过 null 表达。
    properties = {
        key: {
            **field,
            "type": field["type"] if key in required_fields else [field["type"], "null"],
        }
        for key, field in properties.items()
    }
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }

_NODE_ID = {"type": "string", "description": "节点 UUID"}

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
            "description": "最多返回的节点数；null 使用默认值 10",
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
    "更新一个已有节点；null 表示不修改该字段，aliases/tags 传空数组可清空",
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
