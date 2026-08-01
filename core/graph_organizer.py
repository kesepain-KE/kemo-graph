"""来源事实层之上的知识图谱整理与规范投影维护。"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from provider.engine import chat, chat_with_tools

from .config import AppConfig, PROJECT_ROOT, load_config
from .db import (
    DatabasePaths,
    connect_graph,
    initialize_databases,
    read_graph_meta,
    write_graph_meta,
)
from .locks import get_knowledge_base_lock
from .logger import DailyTSVLogger
from .rag_engine import RAGEngine


DEFAULT_ORGANIZER_PROMPT_PATH = PROJECT_ROOT / "config" / "graph_organizer.md"
MAX_ORGANIZER_CANDIDATES = 200


class GraphOrganizerError(RuntimeError):
    """图谱整理无法安全完成。"""


@dataclass(frozen=True)
class MergeCandidate:
    left_node_id: str
    right_node_id: str
    left_keyword: str
    right_keyword: str
    score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_node_id": self.left_node_id,
            "right_node_id": self.right_node_id,
            "left_keyword": self.left_keyword,
            "right_keyword": self.right_keyword,
            "score": round(self.score, 6),
            "reason": self.reason,
        }


ORGANIZER_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_entity",
        "description": "读取节点、来源证据以及正反向关系。合并前必须先读取两个节点。",
        "parameters": {
            "type": "object",
            "properties": {"node_id": {"type": "string", "minLength": 1}},
            "required": ["node_id"],
            "additionalProperties": False,
        },
        "strict": True,
        "permission": "read",
        "metadata": {"purpose": "graph_organization"},
        "extensions": {},
    },
    {
        "type": "function",
        "name": "merge_entities",
        "description": "把 merge_node_id 的全部事实、来源和关系合并到 keep_node_id。",
        "parameters": {
            "type": "object",
            "properties": {
                "keep_node_id": {"type": "string", "minLength": 1},
                "merge_node_id": {"type": "string", "minLength": 1},
                "keyword": {"type": ["string", "null"], "maxLength": 100},
                "summary": {"type": ["string", "null"], "maxLength": 10000},
            },
            "required": ["keep_node_id", "merge_node_id", "keyword", "summary"],
            "additionalProperties": False,
        },
        "strict": True,
        "permission": "write",
        "metadata": {"purpose": "graph_organization"},
        "extensions": {},
    },
    {
        "type": "function",
        "name": "keep_separate",
        "description": "确认两个候选节点语义不同，本轮不合并。",
        "parameters": {
            "type": "object",
            "properties": {
                "left_node_id": {"type": "string", "minLength": 1},
                "right_node_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
            "required": ["left_node_id", "right_node_id", "reason"],
            "additionalProperties": False,
        },
        "strict": True,
        "permission": "write",
        "metadata": {"purpose": "graph_organization"},
        "extensions": {},
    },
    {
        "type": "function",
        "name": "finish",
        "description": "候选已检查完成，结束本轮图谱整理。",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "maxLength": 2000},
            },
            "required": ["note"],
            "additionalProperties": False,
        },
        "strict": True,
        "permission": "write",
        "metadata": {"purpose": "graph_organization"},
        "extensions": {},
    },
]


class GraphOrganizer:
    """扫描重叠候选，并在一个跨 Graph/RAG 事务中规范化投影。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        prompt_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths: DatabasePaths = initialize_databases(data_dir, self.settings)
        self.prompt_path = Path(prompt_path or DEFAULT_ORGANIZER_PROMPT_PATH).resolve()
        self._write_lock = get_knowledge_base_lock(self.paths.data_dir)
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )

    def find_candidates(self) -> list[dict[str, Any]]:
        connection = connect_graph(self.paths)
        try:
            return [candidate.as_dict() for candidate in _find_candidates(
                connection,
                threshold=self.settings.graph_organize_similarity,
            )]
        finally:
            connection.close()

    def organize(self, *, use_llm: bool = True) -> dict[str, Any]:
        started_at = time.perf_counter()
        self._log("graph_organize_start", f"use_llm={use_llm}")
        with self._write_lock:
            result = self._organize_locked(use_llm=use_llm)
        self._log(
            "graph_organize_done",
            (
                f"candidates={result['candidates']}, merged={result['merged_nodes']}, "
                f"kept={result['kept_separate']}"
            ),
            round((time.perf_counter() - started_at) * 1000),
        )
        return result

    def _organize_locked(self, *, use_llm: bool) -> dict[str, Any]:
        redirects: dict[str, str] = {}
        inspected: set[str] = set()
        kept_pairs: set[tuple[str, str]] = set()
        finish_called = False
        merge_plans: list[dict[str, str]] = []
        virtual_entities: dict[str, dict[str, Any]] = {}

        planning_connection = connect_graph(self.paths)
        try:
            candidates = _find_candidates(
                planning_connection,
                threshold=self.settings.graph_organize_similarity,
            )

            def resolve(node_id: Any) -> str:
                current = _required_text(node_id, "node_id")
                visited: set[str] = set()
                while current in redirects and current not in visited:
                    visited.add(current)
                    current = redirects[current]
                return current

            def entity_state(node_id: str) -> dict[str, Any]:
                if node_id not in virtual_entities:
                    virtual_entities[node_id] = _get_entity(
                        planning_connection,
                        node_id,
                    )
                return virtual_entities[node_id]

            def plan_merge(arguments: dict[str, Any]) -> dict[str, Any]:
                keep_id = resolve(arguments.get("keep_node_id"))
                merge_id = resolve(arguments.get("merge_node_id"))
                if keep_id == merge_id:
                    return {
                        "merged": False,
                        "node_id": keep_id,
                        "reason": "already_merged",
                    }
                if keep_id not in inspected or merge_id not in inspected:
                    raise GraphOrganizerError(
                        "merge_entities 前必须先 get_entity 检查两个节点"
                    )
                keep = entity_state(keep_id)
                merged = entity_state(merge_id)
                keyword_argument = arguments.get("keyword")
                summary_argument = arguments.get("summary")
                chosen_keyword = (
                    _required_text(keyword_argument, "keyword")
                    if keyword_argument is not None
                    else str(keep["keyword"])
                )
                if summary_argument is not None:
                    chosen_summary = _required_text(summary_argument, "summary")
                elif use_llm:
                    chosen_summary = self._synthesize_description(
                        str(keep["summary"]),
                        str(merged["summary"]),
                    )
                else:
                    chosen_summary = max(
                        (str(keep["summary"]), str(merged["summary"])),
                        key=len,
                    )
                merge_plans.append(
                    {
                        "keep_node_id": keep_id,
                        "merge_node_id": merge_id,
                        "keyword": chosen_keyword,
                        "summary": chosen_summary,
                    }
                )
                virtual_entities[keep_id] = {
                    **keep,
                    "keyword": chosen_keyword,
                    "summary": chosen_summary,
                }
                redirects[merge_id] = keep_id
                inspected.add(keep_id)
                return {
                    "merged": True,
                    "node_id": keep_id,
                    "removed_node_id": merge_id,
                    "keyword": chosen_keyword,
                    "summary": chosen_summary,
                    "status": "planned",
                }

            def tool_handler(name: str, arguments: dict[str, Any]) -> Any:
                nonlocal finish_called
                if name == "get_entity":
                    node_id = resolve(arguments.get("node_id"))
                    payload = entity_state(node_id)
                    inspected.add(node_id)
                    return payload
                if name == "merge_entities":
                    return plan_merge(arguments)
                if name == "keep_separate":
                    left = resolve(arguments.get("left_node_id"))
                    right = resolve(arguments.get("right_node_id"))
                    reason = _required_text(arguments.get("reason"), "reason")
                    if left == right:
                        return {"kept": False, "reason": "already_merged"}
                    if left not in inspected or right not in inspected:
                        raise GraphOrganizerError(
                            "keep_separate 前必须先 get_entity 检查两个节点"
                        )
                    kept_pairs.add(tuple(sorted((left, right))))
                    return {
                        "kept": True,
                        "left_node_id": left,
                        "right_node_id": right,
                        "reason": reason,
                    }
                if name == "finish":
                    finish_called = True
                    return {
                        "finished": True,
                        "note": str(arguments.get("note") or ""),
                    }
                raise GraphOrganizerError(f"未知整理工具：{name}")

            if candidates and use_llm:
                system = self._build_system_prompt()
                user = json.dumps(
                    {
                        "candidate_count": len(candidates),
                        "candidates": [item.as_dict() for item in candidates],
                        "instruction": "逐项检查；合并或保留后调用 finish。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                chat_with_tools(
                    system,
                    user,
                    ORGANIZER_TOOL_SCHEMAS,
                    tool_handler,
                    settings=self.settings,
                    max_iterations=self.settings.graph_tool_max_iterations,
                )
                if not finish_called:
                    raise GraphOrganizerError("整理智能体未调用 finish，未写入任何修改")
            elif candidates:
                for candidate in candidates:
                    if candidate.reason != "exact_term":
                        continue
                    left = resolve(candidate.left_node_id)
                    right = resolve(candidate.right_node_id)
                    if left == right:
                        continue
                    keep, merge = _choose_keep_node(
                        planning_connection,
                        left,
                        right,
                    )
                    inspected.update((keep, merge))
                    plan_merge(
                        {
                            "keep_node_id": keep,
                            "merge_node_id": merge,
                        }
                    )
        except Exception:
            self._log(
                "graph_organize_rollback",
                "planning=aborted, writes=0",
                level="ERROR",
            )
            raise
        finally:
            planning_connection.close()

        connection = connect_graph(self.paths)
        merged_nodes = 0
        cleanup: dict[str, int] = {}
        chunk_links = 0
        try:
            connection.execute("ATTACH DATABASE ? AS ragdb", (str(self.paths.rag_db),))
            connection.execute(
                "ATTACH DATABASE ? AS sourcesdb",
                (str(self.paths.sources_db),),
            )
            connection.execute("BEGIN IMMEDIATE")
            for plan in merge_plans:
                _merge_nodes(
                    connection,
                    plan["keep_node_id"],
                    plan["merge_node_id"],
                    keyword=plan["keyword"],
                    summary=plan["summary"],
                )
                merged_nodes += 1

            cleanup = _cleanup_projection(connection)
            topology_changed = merged_nodes > 0 or cleanup["removed_edges"] > 0 or cleanup["removed_nodes"] > 0
            if topology_changed:
                connection.execute("DELETE FROM group_nodes")
                connection.execute("DELETE FROM groups")
                chunk_links = _rebuild_chunk_nodes(connection)
            else:
                chunk_links = int(
                    connection.execute("SELECT COUNT(*) FROM ragdb.chunk_nodes").fetchone()[0]
                )
            connection.commit()
        except Exception:
            connection.rollback()
            self._log("graph_organize_rollback", "transaction=rolled_back", level="ERROR")
            raise
        finally:
            connection.close()

        RAGEngine(
            self.paths.data_dir,
            settings=self.settings,
        ).refresh_auxiliary_consistency()

        meta = read_graph_meta(self.paths)
        graph = connect_graph(self.paths)
        try:
            counts = graph.execute(
                """
                SELECT (SELECT COUNT(*) FROM nodes),
                       (SELECT COUNT(*) FROM edges),
                       (SELECT COUNT(*) FROM groups)
                """
            ).fetchone()
            remaining = len(_find_candidates(
                graph,
                threshold=self.settings.graph_organize_similarity,
            ))
        finally:
            graph.close()
        changed = merged_nodes > 0 or cleanup.get("removed_edges", 0) > 0 or cleanup.get("removed_nodes", 0) > 0
        write_graph_meta(
            self.paths,
            {
                **meta,
                "total_nodes": int(counts[0]),
                "total_edges": int(counts[1]),
                "total_groups": int(counts[2]),
                "changed_since_summary": int(meta.get("changed_since_summary", 0)) + (1 if changed else 0),
            },
        )
        return {
            "candidates": len(candidates),
            "merged_nodes": merged_nodes,
            "kept_separate": len(kept_pairs),
            "remaining_candidates": remaining,
            "removed_edges": cleanup.get("removed_edges", 0),
            "removed_nodes": cleanup.get("removed_nodes", 0),
            "weights_recalculated": cleanup.get("weights_recalculated", 0),
            "chunk_links": chunk_links,
            "groups_invalidated": bool(changed),
            "used_llm": bool(use_llm and candidates),
        }

    def _synthesize_description(
        self,
        keep_summary: str,
        merge_summary: str,
    ) -> str:
        """在数据库写事务之外合成描述；失败时回退到较完整的旧描述。"""

        if keep_summary == merge_summary:
            return keep_summary
        system_prompt = (
            "你是知识图谱整理助手。请将以下关于同一实体的两个描述合并为一条"
            "简洁、完整的描述。\n"
            "规则：1) 保留所有关键信息 2) 去除重复 3) 输出纯文本一段"
        )
        user_prompt = f"描述A：{keep_summary}\n\n描述B：{merge_summary}"
        started_at = time.perf_counter()
        try:
            synthesized = chat(
                system_prompt,
                user_prompt,
                settings=self.settings,
            ).strip()
            if not synthesized:
                raise GraphOrganizerError("描述合成返回空文本")
            self._log(
                "graph_description_synthesis",
                f"status=completed, model={self.settings.models.llm}",
                round((time.perf_counter() - started_at) * 1000),
            )
            return synthesized
        except Exception as exc:
            self._log(
                "graph_description_synthesis",
                f"status=fallback, error={type(exc).__name__}",
                round((time.perf_counter() - started_at) * 1000),
                level="WARNING",
            )
            return max((keep_summary, merge_summary), key=len)

    def _build_system_prompt(self) -> str:
        try:
            base = self.prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise GraphOrganizerError(f"无法读取图谱整理提示词：{self.prompt_path}") from exc
        if not base:
            raise GraphOrganizerError("图谱整理提示词为空")
        return (
            f"{base}\n\n"
            "必须对候选的两个节点分别调用 get_entity 后才能 merge_entities 或 "
            "keep_separate。不要凭名称直接合并；处理结束必须调用 finish。"
        )

    def _log(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log("graph_organizer", action, detail, elapsed_ms, level)
        except Exception:
            pass


def _find_candidates(
    connection: sqlite3.Connection,
    *,
    threshold: float,
) -> list[MergeCandidate]:
    rows = connection.execute(
        """
        SELECT node_id, keyword, summary, aliases, tags, weight, ref_count
        FROM nodes ORDER BY node_id
        """
    ).fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        aliases = _json_list(row["aliases"], row["node_id"], "aliases")
        nodes.append(
            {
                "node_id": str(row["node_id"]),
                "keyword": str(row["keyword"]),
                "summary": str(row["summary"] or ""),
                "terms": {_key(row["keyword"]), *(_key(value) for value in aliases)},
            }
        )
    neighbors = _neighbor_signatures(connection)
    candidates: list[MergeCandidate] = []
    for left_index, left in enumerate(nodes):
        for right in nodes[left_index + 1 :]:
            if left["terms"].intersection(right["terms"]):
                score = 1.0
                reason = "exact_term"
            else:
                lexical = max(
                    (
                        SequenceMatcher(None, left_term, right_term).ratio()
                        for left_term in left["terms"]
                        for right_term in right["terms"]
                    ),
                    default=0.0,
                )
                summary = SequenceMatcher(
                    None,
                    _key(left["summary"])[:800],
                    _key(right["summary"])[:800],
                ).ratio()
                adjacency = _jaccard(
                    neighbors.get(left["node_id"], set()),
                    neighbors.get(right["node_id"], set()),
                )
                score = max(lexical, lexical * 0.65 + summary * 0.2 + adjacency * 0.15)
                if score < threshold:
                    continue
                reason = "semantic_similarity"
            candidates.append(
                MergeCandidate(
                    left_node_id=left["node_id"],
                    right_node_id=right["node_id"],
                    left_keyword=left["keyword"],
                    right_keyword=right["keyword"],
                    score=score,
                    reason=reason,
                )
            )
    candidates.sort(key=lambda item: (-item.score, item.left_keyword, item.right_keyword, item.left_node_id, item.right_node_id))
    return candidates[:MAX_ORGANIZER_CANDIDATES]


def _neighbor_signatures(connection: sqlite3.Connection) -> dict[str, set[str]]:
    signatures: dict[str, set[str]] = {}
    rows = connection.execute(
        """
        SELECT e.source_node_id, e.relation, e.target_node_id,
               source.keyword AS source_keyword, target.keyword AS target_keyword
        FROM edges e
        JOIN nodes source ON source.node_id = e.source_node_id
        JOIN nodes target ON target.node_id = e.target_node_id
        """
    ).fetchall()
    for row in rows:
        signatures.setdefault(str(row["source_node_id"]), set()).add(
            f"out:{_key(row['relation'])}:{_key(row['target_keyword'])}"
        )
        signatures.setdefault(str(row["target_node_id"]), set()).add(
            f"in:{_key(row['relation'])}:{_key(row['source_keyword'])}"
        )
    return signatures


def _get_entity(connection: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT node_id, keyword, summary, aliases, tags, weight, ref_count
        FROM nodes WHERE node_id = ?
        """,
        (node_id,),
    ).fetchone()
    if row is None:
        raise GraphOrganizerError(f"节点不存在：{node_id}")
    sources = [dict(item) for item in connection.execute(
        """
        SELECT source_id, content_hash, evidence_weight, evidence
        FROM node_sources WHERE node_id = ? ORDER BY source_id
        """,
        (node_id,),
    ).fetchall()]
    relations = [dict(item) for item in connection.execute(
        """
        SELECT e.edge_id, e.source_node_id, source.keyword AS source_keyword,
               e.relation, e.target_node_id, target.keyword AS target_keyword,
               e.weight, e.support_count
        FROM edges e
        JOIN nodes source ON source.node_id = e.source_node_id
        JOIN nodes target ON target.node_id = e.target_node_id
        WHERE e.source_node_id = ? OR e.target_node_id = ?
        ORDER BY e.edge_id
        """,
        (node_id, node_id),
    ).fetchall()]
    return {
        "node_id": node_id,
        "keyword": row["keyword"],
        "summary": row["summary"],
        "aliases": _json_list(row["aliases"], node_id, "aliases"),
        "tags": _json_list(row["tags"], node_id, "tags"),
        "weight": float(row["weight"] or 0.0),
        "ref_count": int(row["ref_count"] or 0),
        "sources": sources,
        "relations": relations,
    }


def _merge_nodes(
    connection: sqlite3.Connection,
    keep_node_id: str,
    merge_node_id: str,
    *,
    keyword: Any = None,
    summary: Any = None,
) -> dict[str, Any]:
    keep = _get_entity(connection, keep_node_id)
    merged = _get_entity(connection, merge_node_id)
    if keep_node_id == merge_node_id:
        raise GraphOrganizerError("不能把节点合并到自身")

    chosen_keyword = (
        _required_text(keyword, "keyword")
        if keyword is not None
        else str(keep["keyword"])
    )
    chosen_summary = (
        _required_text(summary, "summary")
        if summary is not None
        else max((str(keep["summary"]), str(merged["summary"])), key=len)
    )
    aliases = _unique_texts(
        [
            *keep["aliases"],
            *merged["aliases"],
            str(keep["keyword"]),
            str(merged["keyword"]),
        ]
    )
    aliases = [value for value in aliases if _key(value) != _key(chosen_keyword)]
    tags = _unique_texts([*keep["tags"], *merged["tags"]])

    connection.execute(
        "UPDATE mention_nodes SET node_id = ? WHERE node_id = ?",
        (keep_node_id, merge_node_id),
    )
    connection.execute(
        """
        INSERT INTO node_sources (
            node_id, source_id, content_hash, evidence_weight, evidence
        )
        SELECT ?, source_id, content_hash, evidence_weight, evidence
        FROM node_sources WHERE node_id = ?
        ON CONFLICT(node_id, source_id) DO UPDATE SET
            content_hash = CASE
                WHEN excluded.evidence_weight >= node_sources.evidence_weight
                THEN excluded.content_hash ELSE node_sources.content_hash END,
            evidence_weight = MAX(node_sources.evidence_weight, excluded.evidence_weight),
            evidence = CASE
                WHEN LENGTH(COALESCE(excluded.evidence, '')) > LENGTH(COALESCE(node_sources.evidence, ''))
                THEN excluded.evidence ELSE node_sources.evidence END
        """,
        (keep_node_id, merge_node_id),
    )
    connection.execute("DELETE FROM node_sources WHERE node_id = ?", (merge_node_id,))

    edge_rows = connection.execute(
        """
        SELECT edge_id, source_node_id, relation, target_node_id
        FROM edges WHERE source_node_id = ? OR target_node_id = ?
        ORDER BY edge_id
        """,
        (merge_node_id, merge_node_id),
    ).fetchall()
    merged_edges = 0
    removed_self_loops = 0
    for edge in edge_rows:
        edge_id = str(edge["edge_id"])
        new_source = keep_node_id if edge["source_node_id"] == merge_node_id else str(edge["source_node_id"])
        new_target = keep_node_id if edge["target_node_id"] == merge_node_id else str(edge["target_node_id"])
        if new_source == new_target:
            connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            removed_self_loops += 1
            continue
        existing = connection.execute(
            """
            SELECT edge_id FROM edges
            WHERE source_node_id = ? AND relation = ? AND target_node_id = ?
              AND edge_id != ?
            """,
            (new_source, edge["relation"], new_target, edge_id),
        ).fetchone()
        if existing is not None:
            existing_id = str(existing["edge_id"])
            connection.execute(
                """
                INSERT INTO edge_sources (
                    edge_id, source_id, content_hash, evidence_weight
                )
                SELECT ?, source_id, content_hash, evidence_weight
                FROM edge_sources WHERE edge_id = ?
                ON CONFLICT(edge_id, source_id) DO UPDATE SET
                    content_hash = CASE
                        WHEN excluded.evidence_weight >= edge_sources.evidence_weight
                        THEN excluded.content_hash ELSE edge_sources.content_hash END,
                    evidence_weight = MAX(edge_sources.evidence_weight, excluded.evidence_weight)
                """,
                (existing_id, edge_id),
            )
            connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            merged_edges += 1
        else:
            connection.execute(
                """
                UPDATE edges SET source_node_id = ?, target_node_id = ?
                WHERE edge_id = ?
                """,
                (new_source, new_target, edge_id),
            )

    connection.execute(
        """
        UPDATE nodes SET keyword = ?, summary = ?, aliases = ?, tags = ?, updated_at = ?
        WHERE node_id = ?
        """,
        (
            chosen_keyword,
            chosen_summary,
            json.dumps(aliases, ensure_ascii=False),
            json.dumps(tags, ensure_ascii=False),
            _now_iso(),
            keep_node_id,
        ),
    )
    connection.execute("DELETE FROM nodes WHERE node_id = ?", (merge_node_id,))
    return {
        "merged": True,
        "node_id": keep_node_id,
        "removed_node_id": merge_node_id,
        "keyword": chosen_keyword,
        "merged_edges": merged_edges,
        "removed_self_loops": removed_self_loops,
    }


def _choose_keep_node(
    connection: sqlite3.Connection,
    left_node_id: str,
    right_node_id: str,
) -> tuple[str, str]:
    rows = connection.execute(
        """
        SELECT node_id, ref_count, weight, keyword FROM nodes
        WHERE node_id IN (?, ?)
        """,
        (left_node_id, right_node_id),
    ).fetchall()
    if len(rows) != 2:
        raise GraphOrganizerError("候选节点在整理过程中消失")
    rows = sorted(
        rows,
        key=lambda row: (
            -int(row["ref_count"] or 0),
            -float(row["weight"] or 0.0),
            len(str(row["keyword"])),
            str(row["node_id"]),
        ),
    )
    return str(rows[0]["node_id"]), str(rows[1]["node_id"])


def _cleanup_projection(connection: sqlite3.Connection) -> dict[str, int]:
    removed_edges = connection.execute(
        "DELETE FROM edges WHERE source_node_id = target_node_id"
    ).rowcount
    edge_rows = connection.execute("SELECT edge_id, weight, support_count FROM edges").fetchall()
    weights_recalculated = 0
    for edge in edge_rows:
        aggregate = connection.execute(
            "SELECT COUNT(*), MAX(evidence_weight) FROM edge_sources WHERE edge_id = ?",
            (edge["edge_id"],),
        ).fetchone()
        support_count = int(aggregate[0])
        if support_count == 0:
            connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge["edge_id"],))
            removed_edges += 1
            continue
        weight = float(aggregate[1])
        if support_count != int(edge["support_count"] or 0) or abs(weight - float(edge["weight"] or 0.0)) > 1e-12:
            weights_recalculated += 1
        connection.execute(
            "UPDATE edges SET support_count = ?, weight = ? WHERE edge_id = ?",
            (support_count, weight, edge["edge_id"]),
        )

    removed_nodes = 0
    node_rows = connection.execute("SELECT node_id, weight, ref_count FROM nodes").fetchall()
    for node in node_rows:
        aggregate = connection.execute(
            "SELECT COUNT(*), MAX(evidence_weight) FROM node_sources WHERE node_id = ?",
            (node["node_id"],),
        ).fetchone()
        ref_count = int(aggregate[0])
        if ref_count == 0:
            connection.execute("DELETE FROM nodes WHERE node_id = ?", (node["node_id"],))
            removed_nodes += 1
            continue
        weight = float(aggregate[1])
        if ref_count != int(node["ref_count"] or 0) or abs(weight - float(node["weight"] or 0.0)) > 1e-12:
            weights_recalculated += 1
        connection.execute(
            "UPDATE nodes SET ref_count = ?, weight = ?, updated_at = ? WHERE node_id = ?",
            (ref_count, weight, _now_iso(), node["node_id"]),
        )
    return {
        "removed_edges": int(removed_edges),
        "removed_nodes": removed_nodes,
        "weights_recalculated": weights_recalculated,
    }


def _rebuild_chunk_nodes(connection: sqlite3.Connection) -> int:
    connection.execute("DELETE FROM ragdb.chunk_nodes")
    connection.execute(
        """
        INSERT OR IGNORE INTO ragdb.chunk_nodes (chunk_id, node_id)
        SELECT chunks.chunk_id, node_sources.node_id
        FROM ragdb.chunks AS chunks
        JOIN node_sources ON node_sources.source_id = chunks.source_id
        JOIN sourcesdb.sources AS sources ON sources.source_id = chunks.source_id
        WHERE sources.exists_status = 'active'
          AND sources.graph_status = 'ready'
          AND sources.rag_status = 'ready'
          AND sources.graph_hash = sources.rag_hash
          AND node_sources.content_hash = sources.graph_hash
        """
    )
    return int(connection.execute("SELECT COUNT(*) FROM ragdb.chunk_nodes").fetchone()[0])


def _json_list(value: Any, node_id: str, field: str) -> list[str]:
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise GraphOrganizerError(f"nodes.{field} 非法：{node_id}") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise GraphOrganizerError(f"nodes.{field} 必须是字符串数组：{node_id}")
    return parsed


def _unique_texts(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(unicodedata.normalize("NFKC", str(value)).strip().split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphOrganizerError(f"{name} 必须是非空字符串")
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _key(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "GraphOrganizer",
    "GraphOrganizerError",
    "MergeCandidate",
    "ORGANIZER_TOOL_SCHEMAS",
]
