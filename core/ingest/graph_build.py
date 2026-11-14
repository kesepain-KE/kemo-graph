from __future__ import annotations
from provider.engine import chat_with_tools, chat_structured, supports_structured_output
from core.documents.errors import GraphExtractionError
import json
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4
from core.storage import connect_graph
from core.graph import DraftEntity, GraphDraft
from core.graph.draft import merge_graph_drafts, split_markdown_for_graph, validate_graph_draft
from core.documents.errors import IngestError
from core.ingest.scan import _SourceRecord
from core.common import elapsed_ms as _elapsed_ms, now_iso as _now_iso
from core.ingest.validation import _load_json_string_list, _semantic_key, _unique_texts
from core.ingest.prompt import _bounded_graph_draft_schema, _build_graph_section_prompt, _graph_extraction_budget, _graph_granularity_rule





_LARGE_RELATION_MIN_WEIGHT = 0.80

def _large_relation_limit(entity_count: int, max_relations: int) -> int:
    """Return the sparse relation budget for one large-granularity result."""

    if entity_count <= 0 or max_relations <= 0:
        return 0
    return min(max_relations, math.ceil(entity_count * 1.5))

def _sparsify_graph_relations(
    draft: GraphDraft,
    granularity: str,
) -> GraphDraft:
    """Apply conservative relation filtering only to the large profile.

    The structured path receives one merged draft for the complete document.  We
    therefore filter after cross-section deduplication so the density limit is
    document-wide rather than accidentally multiplied by the number of sections.
    Medium and small profiles intentionally retain their historical weighting
    behavior.
    """

    if granularity != "large":
        return draft
    _, max_relations = _graph_extraction_budget(granularity)
    eligible = [
        relation
        for relation in draft.relations
        if relation.evidence_weight >= _LARGE_RELATION_MIN_WEIGHT
    ]
    limit = _large_relation_limit(len(draft.entities), max_relations)
    if len(eligible) <= limit:
        return GraphDraft(draft.entities, tuple(eligible))
    # Keep strongest evidence first while retaining stable input order for ties.
    ranked = sorted(
        enumerate(eligible),
        key=lambda item: (-item[1].evidence_weight, item[0]),
    )
    selected_indexes = {index for index, _ in ranked[:limit]}
    selected = tuple(
        relation for index, relation in enumerate(eligible) if index in selected_indexes
    )
    return GraphDraft(draft.entities, selected)

def _validate_graph_draft_budget(
    draft: GraphDraft,
    granularity: str,
) -> None:
    max_entities, max_relations = _graph_extraction_budget(granularity)
    if len(draft.entities) > max_entities:
        raise GraphExtractionError(
            f"{granularity} 粒度每段最多 {max_entities} 个实体，"
            f"实际收到 {len(draft.entities)} 个"
        )
    if len(draft.relations) > max_relations:
        raise GraphExtractionError(
            f"{granularity} 粒度每段最多 {max_relations} 条关系，"
            f"实际收到 {len(draft.relations)} 条"
        )

def _update_graph_for_source(self, record: _SourceRecord, text: str) -> None:
    mode = self.settings.graph_build_mode
    if mode == "tools":
        self._extract_graph_with_tools(record, text)
        return
    if mode == "structured":
        self._extract_graph_structured(record, text)
        return
    try:
        structured_supported = supports_structured_output(
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

    from core.graph import get_graph_tool_schemas

    # 工具模式以前把整篇文档作为一次请求发送，长文档容易超出网关上下文
    # 或让模型在一次循环中遗漏后半段。沿用结构化模式的 Markdown 语义分段，
    # 但仍在同一个 SQLite 事务中逐段调用工具，保证整篇文档的替换具备原子性。
    effective_chunk_size = self.settings.effective_graph_extract_chunk_size()
    max_entities, max_relations = _graph_extraction_budget(
        self.settings.graph_extract_granularity
    )
    sections = split_markdown_for_graph(text, effective_chunk_size)
    base_system_prompt = self._build_graph_system_prompt(record)
    section_prompts = [
        _build_graph_section_prompt(
            base_system_prompt,
            self.settings,
            section_index=index + 1,
            section_count=len(sections),
            effective_chunk_size=effective_chunk_size,
        )
        for index in range(len(sections))
    ]
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

        if sections:
            registrations = self._tool_provider(
                connection,
                source_id=record.source_id,
                content_hash=record.content_hash,
            )
            registrations_by_name = {str(tool["name"]): tool for tool in registrations}
            schemas = get_graph_tool_schemas()
            finish_called = False
            section_counts = {"entities": 0, "relations": 0}

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

                tool_args = dict(args)
                if (
                    tool_name == "add_entity"
                    and section_counts["entities"] >= max_entities
                ):
                    raise GraphExtractionError(
                        f"budget_exhausted: 当前分段已达到实体预算 {max_entities}；"
                        "请复用已有节点、把细节写入 summary，然后调用 finish"
                    )
                if tool_name == "add_relation":
                    relation_limit = max_relations
                    if self.settings.graph_extract_granularity == "large":
                        try:
                            evidence_weight = float(tool_args.get("evidence_weight", 0.0))
                        except (TypeError, ValueError):
                            evidence_weight = 0.0
                        if evidence_weight < _LARGE_RELATION_MIN_WEIGHT:
                            self._log_event(
                                "graph_tool_call",
                                (
                                    "add_relation: relation_evidence_too_weak; "
                                    f"required={_LARGE_RELATION_MIN_WEIGHT:.2f}"
                                ),
                                _elapsed_ms(tool_started_at),
                                level="WARNING",
                            )
                            # Return a structured, recoverable tool result.  The
                            # gateway can show the error to the LLM and continue
                            # toward finish; one weak edge must not roll back the
                            # otherwise valid nodes from this source.
                            return {
                                "ok": False,
                                "error": (
                                    "relation_evidence_too_weak: 粗粒度关系需要直接证据，"
                                    f"evidence_weight 必须不低于 {_LARGE_RELATION_MIN_WEIGHT:.2f}"
                                ),
                            }
                        relation_limit = _large_relation_limit(
                            section_counts["entities"],
                            max_relations,
                        )
                    if section_counts["relations"] >= relation_limit:
                        raise GraphExtractionError(
                            f"budget_exhausted: 当前分段已达到关系预算 {relation_limit}；"
                            "不要继续添加共现或传递关系，请调用 finish"
                        )

                registration = registrations_by_name.get(tool_name)
                if registration is None:
                    raise GraphExtractionError(f"Unknown tool: {tool_name}")
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

                if tool_name == "add_entity":
                    section_counts["entities"] += 1
                elif tool_name == "add_relation":
                    section_counts["relations"] += 1

                self._log_event(
                    "graph_tool_call",
                    f"{record.relative_path}: {tool_name}",
                    _elapsed_ms(tool_started_at),
                )
                return data

            for section_index, section in enumerate(sections):
                llm_started_at = time.perf_counter()
                finish_called = False
                section_counts["entities"] = 0
                section_counts["relations"] = 0
                try:
                    chat_with_tools(
                        system=section_prompts[section_index],
                        user=section,
                        tools=schemas,
                        tool_handler=tool_handler,
                        settings=self.settings,
                        max_iterations=self.settings.graph_tool_max_iterations,
                        # Graph extraction uses the Kemo model's parallel tool
                        # contract.  The handler still applies each returned
                        # call in order so budget checks and SQLite writes
                        # remain deterministic within the transaction.
                        parallel_tool_calls=True,
                    )
                except Exception:
                    self._log_event(
                        "llm_request",
                        (
                            f"purpose=graph_build, model={self.settings.models.llm}, "
                            f"section={section_index + 1}/{len(sections)}, "
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
                        f"section={section_index + 1}/{len(sections)}, "
                        f"finish={finish_called}"
                    ),
                    _elapsed_ms(llm_started_at),
                )

        self._advance_build_step(record.source_id, "graph", "persist", "提交图谱事务")
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

    self._advance_build_step(record.source_id, "graph", "link", "同步来源关联")
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
        self.settings.effective_graph_extract_chunk_size(),
    )
    if sections:
        drafts = self._extract_graph_sections(record, sections)
        prepared = merge_graph_drafts(drafts)
        prepared = _sparsify_graph_relations(
            prepared,
            self.settings.graph_extract_granularity,
        )
    else:
        prepared = GraphDraft(entities=(), relations=())

    self._advance_build_step(record.source_id, "graph", "persist", "写入实体与关系")
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

    self._advance_build_step(record.source_id, "graph", "link", "同步来源关联")
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
            "extract_granularity": self.settings.graph_extract_granularity,
            "extract_chunk_size": self.settings.effective_graph_extract_chunk_size(),
            "max_entities": _graph_extraction_budget(
                self.settings.graph_extract_granularity
            )[0],
            "max_relations": _graph_extraction_budget(
                self.settings.graph_extract_granularity
            )[1],
            "granularity_rule": _graph_granularity_rule(
                self.settings.graph_extract_granularity
            ),
            "budget_rule": (
                "上限不是目标，不得为了凑满而创建节点或关系；"
                "无法独立检索的细节写入 summary/evidence。"
            ),
        }
        system_prompt = (
            f"{base_prompt}\n\n## 当前来源上下文\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
        )
        response = chat_structured(
            system_prompt,
            section,
            _bounded_graph_draft_schema(self.settings.graph_extract_granularity),
            settings=self.settings,
        )
        draft = validate_graph_draft(response)
        _validate_graph_draft_budget(
            draft,
            self.settings.graph_extract_granularity,
        )
        return draft

    indexed = list(enumerate(sections))
    if len(indexed) == 1 or self.settings.graph_extract_concurrency == 1:
        return [extract_one(item) for item in indexed]
    with ThreadPoolExecutor(
        max_workers=min(self.settings.graph_extract_concurrency, len(indexed)),
        thread_name_prefix="kemo-graph-extract",
    ) as executor:
        return list(executor.map(extract_one, indexed))

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
