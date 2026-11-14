from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from core.storage.connections import connect_graph
from core.graph.draft import GRAPH_DRAFT_SCHEMA
from core.ingest.errors import IngestError
from core.ingest.scan import _SourceRecord
from core.ingest.errors import GraphExtractionError

_GRAPH_EXTRACTION_BUDGETS: dict[str, tuple[int, int]] = {
    "small": (36, 54),
    "medium": (20, 28),
    "large": (12, 16),
}

def _graph_extraction_budget(granularity: str) -> tuple[int, int]:
    try:
        return _GRAPH_EXTRACTION_BUDGETS[granularity]
    except KeyError as exc:
        raise GraphExtractionError(
            f"不支持的图谱抽取颗粒度：{granularity}"
        ) from exc

def _bounded_graph_draft_schema(granularity: str) -> dict[str, Any]:
    """按颗粒度收紧结构化输出数组上限，避免模型返回巨量碎片。"""

    max_entities, max_relations = _graph_extraction_budget(granularity)
    schema = deepcopy(GRAPH_DRAFT_SCHEMA)
    schema["properties"]["entities"]["maxItems"] = max_entities
    schema["properties"]["relations"]["maxItems"] = max_relations
    return schema

def _build_graph_system_prompt(self, record: _SourceRecord) -> str:
    """读取基础 prompt，并注入来源身份、内容哈希和知识库规模。"""

    try:
        base_prompt = self.graph_prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise IngestError(f"无法读取图谱提示词：{self.graph_prompt_path}") from exc
    if not base_prompt:
        raise IngestError(f"图谱提示词为空：{self.graph_prompt_path}")

    connection = connect_graph(self.paths)
    try:
        node_count = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
    finally:
        connection.close()
    context = {
        "relative_path": record.relative_path,
        "source_id": record.source_id,
        "content_hash": record.content_hash,
        "existing_node_count": node_count,
        "extract_granularity": self.settings.graph_extract_granularity,
        "extract_chunk_size": self.settings.effective_graph_extract_chunk_size(),
        "max_entities": _graph_extraction_budget(
            self.settings.graph_extract_granularity
        )[0],
        "max_relations": _graph_extraction_budget(
            self.settings.graph_extract_granularity
        )[1],
        "budget_rule": (
            "上限不是目标，不得为了凑满而创建节点或关系；"
            "无法独立检索的细节写入 summary/evidence。"
        ),
    }
    return (
        f"{base_prompt}\n\n"
        "## 当前文档上下文（由系统注入，不得修改）\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
    )

def _build_graph_section_prompt(
    base_prompt: str,
    settings: Any,
    *,
    section_index: int,
    section_count: int,
    effective_chunk_size: int,
) -> str:
    """为工具模式的单个分段补充边界上下文，不重复查询数据库。"""

    context = {
        "section": section_index,
        "section_count": section_count,
        "extract_granularity": settings.graph_extract_granularity,
        "extract_chunk_size": effective_chunk_size,
        "max_entities": _graph_extraction_budget(settings.graph_extract_granularity)[0],
        "max_relations": _graph_extraction_budget(settings.graph_extract_granularity)[1],
        "granularity_rule": _graph_granularity_rule(
            settings.graph_extract_granularity
        ),
        "section_rule": (
            "当前仅处理这一段 Markdown；只写入该段明确支持的实体和关系。"
            "跨段关系可在后续段落通过 search_entities 补充。"
        ),
        "budget_rule": (
            "上限不是目标，不得为了凑满而创建节点或关系；"
            "无法独立检索的细节写入已有节点 summary。"
        ),
    }
    return (
        f"{base_prompt}\n\n"
        "## 当前分段上下文（由系统注入，不得修改）\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
    )

def _graph_granularity_rule(granularity: str) -> str:
    rules = {
        "small": (
            "细粒度：仍只保留可独立检索的稳定概念；属性、步骤和示例优先写入"
            "summary，不得把每个动作、参数或句子拆成节点；关系至少要有清楚的局部证据。"
        ),
        "medium": (
            "标准粒度：提取文档主题、主要对象和少量必要子概念；"
            "同义词、局部属性、单次步骤和示例全部合并进 summary；弱推断关系省略。"
        ),
        "large": (
            "粗粒度：只保留章节级稳定主题、主要对象和关键关系；"
            "宁可少建节点，也不要把细节、参数、操作步骤、示例或同义词单独建点；"
            "关系默认要求直接证据且 evidence_weight 不低于 0.80。"
        ),
    }
    return rules[granularity]
