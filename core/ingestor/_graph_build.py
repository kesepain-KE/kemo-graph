"""图谱抽取、事务替换和权重重算。"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from ..db import connect_graph
from ..graph_draft import (
    GRAPH_DRAFT_SCHEMA,
    DraftEntity,
    GraphDraft,
    merge_graph_drafts,
    split_markdown_for_graph,
    validate_graph_draft,
)
from . import IngestError
from ._scan import _SourceRecord
from ._utils import (
    _elapsed_ms,
    _load_json_string_list,
    _now_iso,
    _semantic_key,
    _unique_texts,
)


def _public_chat_with_tools(*args: Any, **kwargs: Any) -> Any:
    from . import chat_with_tools

    return chat_with_tools(*args, **kwargs)


def _public_chat_structured(*args: Any, **kwargs: Any) -> Any:
    from . import chat_structured

    return chat_structured(*args, **kwargs)


def _public_supports_structured_output(*args: Any, **kwargs: Any) -> Any:
    from . import supports_structured_output

    return supports_structured_output(*args, **kwargs)


class GraphExtractionError(IngestError):
    """LLM 返回的文档图谱数据不符合约定。"""


def _update_graph_for_source(self, record: _SourceRecord, text: str) -> None:
    mode = self.settings.graph_build_mode
    if mode == "tools":
        self._extract_graph_with_tools(record, text)
        return
    if mode == "structured":
        self._extract_graph_structured(record, text)
        return
    try:
        structured_supported = _public_supports_structured_output(
            settings=self.settings
        )
    except Exception as exc:
        structured_supported = False
        self._log_event(
            "graph_capability_fallback",
            f"model={self.settings.models.llm}, error={type(exc).__name__}",
            level="WARNING",
        )
    if structured_supported:
        self._extract_graph_structured(record, text)
    else:
        self._extract_graph_with_tools(record, text)


def _extract_graph_with_tools(self, record: _SourceRecord, text: str) -> None:
    """使用工具调用循环构建图谱，全部工具写入共享文档事务。"""

    # 延迟导入避免 delete_tools -> Ingestor 形成模块初始化环。
    from provider.tools import get_graph_tools, get_tool_schemas

    system_prompt = self._build_graph_system_prompt(record)
    connection = connect_graph(self.paths)
    orphan_node_ids: set[str] = set()
    new_node_ids: set[str] = set()
    started_at = time.perf_counter()
    self._log_event(
        "graph_build_start",
        f"path={record.relative_path}, source_id={record.source_id}",
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_node_ids = {
            str(row["node_id"])
            for row in connection.execute(
                "SELECT node_id FROM node_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }
        old_edge_ids = {
            str(row["edge_id"])
            for row in connection.execute(
                "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }

        # 在尚未提交的事务中移除旧来源；任何后续失败都会恢复旧数据。
        connection.execute(
            "DELETE FROM node_sources WHERE source_id = ?", (record.source_id,)
        )
        connection.execute(
            "DELETE FROM edge_sources WHERE source_id = ?", (record.source_id,)
        )
        _recalculate_edges(connection, old_edge_ids)
        orphan_node_ids.update(_recalculate_nodes(connection, old_node_ids))

        if text.strip():
            registrations = get_graph_tools(
                connection,
                source_id=record.source_id,
                content_hash=record.content_hash,
            )
            registrations_by_name = {str(tool["name"]): tool for tool in registrations}
            schemas = get_tool_schemas("graph")
            finish_called = False

            def tool_handler(tool_name: str, args: dict[str, Any]) -> Any:
                nonlocal finish_called
                tool_started_at = time.perf_counter()
                if tool_name == "finish":
                    finish_called = True
                    self._log_event(
                        "graph_tool_call",
                        f"{record.relative_path}: finish",
                        _elapsed_ms(tool_started_at),
                    )
                    return {"finished": True}

                registration = registrations_by_name.get(tool_name)
                if registration is None:
                    raise GraphExtractionError(f"Unknown tool: {tool_name}")
                tool_args = dict(args)
                result = registration["handler"](tool_args)
                if not result.get("ok"):
                    error = str(result.get("error") or "工具执行失败")
                    self._log_event(
                        "graph_tool_call",
                        f"{tool_name}: {error}",
                        _elapsed_ms(tool_started_at),
                        level="WARNING",
                    )
                    raise GraphExtractionError(error)

                data = result.get("data")
                if (
                    tool_name == "delete_entity"
                    and isinstance(data, dict)
                    and data.get("deleted")
                ):
                    orphan_node_ids.add(str(tool_args["node_id"]))

                self._log_event(
                    "graph_tool_call",
                    f"{record.relative_path}: {tool_name}",
                    _elapsed_ms(tool_started_at),
                )
                return data

            llm_started_at = time.perf_counter()
            try:
                _public_chat_with_tools(
                    system=system_prompt,
                    user=text,
                    tools=schemas,
                    tool_handler=tool_handler,
                    settings=self.settings,
                    max_iterations=self.settings.graph_tool_max_iterations,
                )
            except Exception:
                self._log_event(
                    "llm_request",
                    (
                        f"purpose=graph_build, model={self.settings.models.llm}, "
                        "status=failed"
                    ),
                    _elapsed_ms(llm_started_at),
                    level="ERROR",
                )
                raise
            self._log_event(
                "llm_request",
                (
                    f"purpose=graph_build, model={self.settings.models.llm}, "
                    f"finish={finish_called}"
                ),
                _elapsed_ms(llm_started_at),
            )

        new_node_ids = {
            str(row["node_id"])
            for row in connection.execute(
                "SELECT node_id FROM node_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }
        new_edge_ids = {
            str(row["edge_id"])
            for row in connection.execute(
                "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }
        _recalculate_edges(connection, new_edge_ids)
        orphan_node_ids.update(
            _recalculate_nodes(connection, old_node_ids | new_node_ids)
        )
        connection.execute("DELETE FROM group_nodes")
        connection.execute("DELETE FROM groups")
        connection.commit()
    except Exception as exc:
        connection.rollback()
        self._log_event(
            "graph_build_rollback",
            f"path={record.relative_path}, error={type(exc).__name__}",
            _elapsed_ms(started_at),
            level="ERROR",
        )
        raise
    finally:
        connection.close()

    self._get_rag_engine().refresh_auxiliary_consistency()
    self._remove_chunk_node_ids(orphan_node_ids)
    self._sync_chunk_nodes_for_source(
        record.source_id,
        record.content_hash,
        new_node_ids,
    )
    self._refresh_graph_meta(changed=True)
    self._log_event(
        "graph_build_done",
        (
            f"path={record.relative_path}, source_id={record.source_id}, "
            f"nodes={len(new_node_ids)}"
        ),
        _elapsed_ms(started_at),
    )


def _extract_graph_structured(self, record: _SourceRecord, text: str) -> None:
    """单轮结构化抽取后，将来源事实和规范图谱作为一个事务写入。"""

    started_at = time.perf_counter()
    self._log_event(
        "graph_build_start",
        f"path={record.relative_path}, source_id={record.source_id}, mode=structured",
    )
    sections = split_markdown_for_graph(
        text,
        self.settings.graph_extract_chunk_size,
    )
    if sections:
        drafts = self._extract_graph_sections(record, sections)
        prepared = merge_graph_drafts(drafts)
    else:
        prepared = GraphDraft(entities=(), relations=())

    connection = connect_graph(self.paths)
    orphan_node_ids: set[str] = set()
    new_node_ids: set[str] = set()
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_node_ids = {
            str(row["node_id"])
            for row in connection.execute(
                "SELECT node_id FROM node_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }
        old_edge_ids = {
            str(row["edge_id"])
            for row in connection.execute(
                "SELECT edge_id FROM edge_sources WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        }
        connection.execute(
            "DELETE FROM edge_sources WHERE source_id = ?", (record.source_id,)
        )
        connection.execute(
            "DELETE FROM node_sources WHERE source_id = ?", (record.source_id,)
        )
        connection.execute(
            "DELETE FROM entity_mentions WHERE source_id = ?", (record.source_id,)
        )
        _recalculate_edges(connection, old_edge_ids)

        mention_by_local: dict[str, str] = {}
        node_by_local: dict[str, str] = {}
        for entity in prepared.entities:
            mention_id = str(uuid4())
            node_id = _match_or_create_draft_node(connection, entity)
            mention_by_local[entity.local_id] = mention_id
            node_by_local[entity.local_id] = node_id
            new_node_ids.add(node_id)
            connection.execute(
                """
                    INSERT INTO entity_mentions (
                        mention_id, source_id, content_hash, local_id, keyword,
                        summary, aliases, tags, evidence_weight, evidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    mention_id,
                    record.source_id,
                    record.content_hash,
                    entity.local_id,
                    entity.keyword,
                    entity.summary,
                    json.dumps(entity.aliases, ensure_ascii=False),
                    json.dumps(entity.tags, ensure_ascii=False),
                    entity.evidence_weight,
                    entity.evidence,
                    _now_iso(),
                ),
            )
            connection.execute(
                "INSERT INTO mention_nodes (mention_id, node_id) VALUES (?, ?)",
                (mention_id, node_id),
            )
            connection.execute(
                """
                    INSERT INTO node_sources (
                        node_id, source_id, content_hash, evidence_weight, evidence
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(node_id, source_id) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        evidence_weight = MAX(node_sources.evidence_weight, excluded.evidence_weight),
                        evidence = CASE
                            WHEN LENGTH(COALESCE(excluded.evidence, '')) > LENGTH(COALESCE(node_sources.evidence, ''))
                            THEN excluded.evidence ELSE node_sources.evidence END
                    """,
                (
                    node_id,
                    record.source_id,
                    record.content_hash,
                    entity.evidence_weight,
                    entity.evidence,
                ),
            )

        new_edge_ids: set[str] = set()
        for relation in prepared.relations:
            source_node_id = node_by_local[relation.source]
            target_node_id = node_by_local[relation.target]
            if source_node_id == target_node_id:
                continue
            edge_id = _match_or_create_edge(
                connection,
                source_node_id,
                relation.relation,
                target_node_id,
            )
            new_edge_ids.add(edge_id)
            connection.execute(
                """
                    INSERT INTO relation_mentions (
                        mention_id, source_id, content_hash, source_mention_id,
                        relation, target_mention_id, evidence_weight, evidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    str(uuid4()),
                    record.source_id,
                    record.content_hash,
                    mention_by_local[relation.source],
                    relation.relation,
                    mention_by_local[relation.target],
                    relation.evidence_weight,
                    relation.evidence,
                    _now_iso(),
                ),
            )
            connection.execute(
                """
                    INSERT INTO edge_sources (
                        edge_id, source_id, content_hash, evidence_weight
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(edge_id, source_id) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        evidence_weight = MAX(edge_sources.evidence_weight, excluded.evidence_weight)
                    """,
                (
                    edge_id,
                    record.source_id,
                    record.content_hash,
                    relation.evidence_weight,
                ),
            )

        _recalculate_edges(connection, old_edge_ids | new_edge_ids)
        orphan_node_ids.update(
            _recalculate_nodes(connection, old_node_ids | new_node_ids)
        )
        connection.execute("DELETE FROM group_nodes")
        connection.execute("DELETE FROM groups")
        connection.commit()
    except Exception as exc:
        connection.rollback()
        self._log_event(
            "graph_build_rollback",
            f"path={record.relative_path}, mode=structured, error={type(exc).__name__}",
            _elapsed_ms(started_at),
            level="ERROR",
        )
        raise
    finally:
        connection.close()

    self._get_rag_engine().refresh_auxiliary_consistency()
    self._remove_chunk_node_ids(orphan_node_ids)
    self._sync_chunk_nodes_for_source(
        record.source_id,
        record.content_hash,
        new_node_ids,
    )
    self._refresh_graph_meta(changed=True)
    self._log_event(
        "graph_build_done",
        (
            f"path={record.relative_path}, source_id={record.source_id}, "
            f"mode=structured, sections={len(sections)}, nodes={len(new_node_ids)}"
        ),
        _elapsed_ms(started_at),
    )


def _extract_graph_sections(
    self,
    record: _SourceRecord,
    sections: list[str],
) -> list[GraphDraft]:
    try:
        base_prompt = self.graph_extract_prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise IngestError(
            f"无法读取高速图谱提示词：{self.graph_extract_prompt_path}"
        ) from exc
    if not base_prompt:
        raise IngestError(f"高速图谱提示词为空：{self.graph_extract_prompt_path}")

    def extract_one(item: tuple[int, str]) -> GraphDraft:
        index, section = item
        context = {
            "relative_path": record.relative_path,
            "content_hash": record.content_hash,
            "section": index + 1,
            "section_count": len(sections),
        }
        system_prompt = (
            f"{base_prompt}\n\n## 当前来源上下文\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
        )
        response = _public_chat_structured(
            system_prompt,
            section,
            GRAPH_DRAFT_SCHEMA,
            settings=self.settings,
        )
        return validate_graph_draft(response)

    indexed = list(enumerate(sections))
    if len(indexed) == 1 or self.settings.graph_extract_concurrency == 1:
        return [extract_one(item) for item in indexed]
    with ThreadPoolExecutor(
        max_workers=min(self.settings.graph_extract_concurrency, len(indexed)),
        thread_name_prefix="kemo-graph-extract",
    ) as executor:
        return list(executor.map(extract_one, indexed))


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
    }
    return (
        f"{base_prompt}\n\n"
        "## 当前文档上下文（由系统注入，不得修改）\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
    )


def _match_or_create_draft_node(
    connection: sqlite3.Connection,
    entity: DraftEntity,
) -> str:
    """将来源实体投影到规范节点；只对唯一的完全同义项自动复用。

    高速抽取阶段刻意不做模糊合并。keyword/alias 规范化后完全相等且只命中
    一个节点时才复用；同一术语若指向多个已有节点，则创建独立节点，留给后续
    “知识图谱整理”显式消歧，避免导入阶段误合并造成不可逆的信息损失。
    """

    entity_terms = {
        _semantic_key(value)
        for value in (entity.keyword, *entity.aliases)
        if _semantic_key(value)
    }
    candidates: list[tuple[sqlite3.Row, list[str], list[str]]] = []
    rows = connection.execute(
        "SELECT node_id, keyword, summary, aliases, tags FROM nodes"
    ).fetchall()
    for row in rows:
        aliases = _load_json_string_list(row["aliases"], row["node_id"], "aliases")
        node_terms = {
            _semantic_key(value)
            for value in (row["keyword"], *aliases)
            if _semantic_key(value)
        }
        if entity_terms.intersection(node_terms):
            tags = _load_json_string_list(row["tags"], row["node_id"], "tags")
            candidates.append((row, aliases, tags))

    if len(candidates) == 1:
        selected, existing_aliases, existing_tags = candidates[0]
        aliases_to_merge = [*existing_aliases, *entity.aliases]
        if _semantic_key(entity.keyword) != _semantic_key(selected["keyword"]):
            aliases_to_merge.append(entity.keyword)
        merged_aliases = _unique_texts(aliases_to_merge)
        merged_tags = _unique_texts([*existing_tags, *entity.tags])
        existing_summary = str(selected["summary"] or "")
        summary = (
            entity.summary
            if len(entity.summary.strip()) > len(existing_summary.strip())
            else existing_summary
        )
        connection.execute(
            """
            UPDATE nodes
            SET summary = ?, aliases = ?, tags = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (
                summary,
                json.dumps(merged_aliases, ensure_ascii=False),
                json.dumps(merged_tags, ensure_ascii=False),
                _now_iso(),
                selected["node_id"],
            ),
        )
        return str(selected["node_id"])

    node_id = str(uuid4())
    now = _now_iso()
    connection.execute(
        """
        INSERT INTO nodes (
            node_id, keyword, summary, aliases, tags,
            weight, ref_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
        """,
        (
            node_id,
            entity.keyword,
            entity.summary,
            json.dumps(_unique_texts(entity.aliases), ensure_ascii=False),
            json.dumps(_unique_texts(entity.tags), ensure_ascii=False),
            now,
            now,
        ),
    )
    return node_id


def _match_or_create_edge(
    connection: sqlite3.Connection,
    source_node_id: str,
    relation: str,
    target_node_id: str,
) -> str:
    """复用完全相同的有向关系，否则创建新关系。"""

    row = connection.execute(
        """
        SELECT edge_id FROM edges
        WHERE source_node_id = ? AND relation = ? AND target_node_id = ?
        """,
        (source_node_id, relation, target_node_id),
    ).fetchone()
    if row is not None:
        return row["edge_id"]
    edge_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO edges (
            edge_id, source_node_id, relation, target_node_id,
            weight, support_count, created_at
        ) VALUES (?, ?, ?, ?, 0, 0, ?)
        """,
        (edge_id, source_node_id, relation, target_node_id, _now_iso()),
    )
    return edge_id


def _recalculate_edges(
    connection: sqlite3.Connection,
    edge_ids: set[str],
) -> None:
    for edge_id in edge_ids:
        aggregate = connection.execute(
            """
            SELECT COUNT(*) AS support_count,
                   MAX(evidence_weight) AS weight
            FROM edge_sources WHERE edge_id = ?
            """,
            (edge_id,),
        ).fetchone()
        if int(aggregate["support_count"]) == 0:
            connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
        else:
            connection.execute(
                "UPDATE edges SET support_count = ?, weight = ? WHERE edge_id = ?",
                (
                    int(aggregate["support_count"]),
                    float(aggregate["weight"]),
                    edge_id,
                ),
            )


def _recalculate_nodes(
    connection: sqlite3.Connection,
    node_ids: set[str],
) -> set[str]:
    orphan_node_ids: set[str] = set()
    for node_id in node_ids:
        aggregate = connection.execute(
            """
            SELECT COUNT(*) AS ref_count,
                   MAX(evidence_weight) AS weight
            FROM node_sources WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        ref_count = int(aggregate["ref_count"])
        if ref_count == 0:
            connection.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            orphan_node_ids.add(node_id)
        else:
            connection.execute(
                """
                UPDATE nodes
                SET ref_count = ?, weight = ?, updated_at = ?
                WHERE node_id = ?
                """,
                (ref_count, float(aggregate["weight"]), _now_iso(), node_id),
            )
    return orphan_node_ids
