"""高速图谱抽取的结构化合同、校验、分段和确定性合并。"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


GRAPH_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "entities": {
            "type": "array",
            "maxItems": 1000,
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "keyword": {"type": "string", "minLength": 1, "maxLength": 100},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        "maxItems": 50,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        "maxItems": 50,
                    },
                    "evidence_weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "maxLength": 4000},
                },
                "required": [
                    "local_id", "keyword", "summary", "aliases", "tags",
                    "evidence_weight", "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "maxItems": 2000,
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "minLength": 1, "maxLength": 80},
                    "relation": {"type": "string", "minLength": 1, "maxLength": 100},
                    "target": {"type": "string", "minLength": 1, "maxLength": 80},
                    "evidence_weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "maxLength": 4000},
                },
                "required": [
                    "source", "relation", "target", "evidence_weight", "evidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "entities", "relations"],
    "additionalProperties": False,
}


class GraphDraftError(ValueError):
    """结构化图谱草稿不符合本地完整性约束。"""


@dataclass(frozen=True)
class DraftEntity:
    local_id: str
    keyword: str
    summary: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    evidence_weight: float
    evidence: str


@dataclass(frozen=True)
class DraftRelation:
    source: str
    relation: str
    target: str
    evidence_weight: float
    evidence: str


@dataclass(frozen=True)
class GraphDraft:
    entities: tuple[DraftEntity, ...]
    relations: tuple[DraftRelation, ...]


def validate_graph_draft(payload: Any) -> GraphDraft:
    if not isinstance(payload, dict):
        raise GraphDraftError("图谱结构化输出必须是对象")
    if set(payload) != {"schema_version", "entities", "relations"}:
        raise GraphDraftError("图谱结构化输出字段必须严格为 schema_version/entities/relations")
    if payload.get("schema_version") != "1.0":
        raise GraphDraftError("图谱结构化输出 schema_version 必须为 1.0")
    raw_entities = payload.get("entities")
    raw_relations = payload.get("relations")
    if not isinstance(raw_entities, list) or len(raw_entities) > 1000:
        raise GraphDraftError("entities 必须是最多 1000 项的数组")
    if not isinstance(raw_relations, list) or len(raw_relations) > 2000:
        raise GraphDraftError("relations 必须是最多 2000 项的数组")

    entities: list[DraftEntity] = []
    local_ids: set[str] = set()
    for index, item in enumerate(raw_entities):
        if not isinstance(item, dict):
            raise GraphDraftError(f"entities[{index}] 必须是对象")
        expected = {
            "local_id", "keyword", "summary", "aliases", "tags",
            "evidence_weight", "evidence",
        }
        if set(item) != expected:
            raise GraphDraftError(f"entities[{index}] 字段不符合严格 Schema")
        local_id = _text(item["local_id"], f"entities[{index}].local_id", 80)
        if local_id in local_ids:
            raise GraphDraftError(f"实体 local_id 重复：{local_id}")
        local_ids.add(local_id)
        entities.append(
            DraftEntity(
                local_id=local_id,
                keyword=_text(item["keyword"], f"entities[{index}].keyword", 100),
                summary=_text(item["summary"], f"entities[{index}].summary", 10000),
                aliases=tuple(_string_list(item["aliases"], f"entities[{index}].aliases", 50)),
                tags=tuple(_string_list(item["tags"], f"entities[{index}].tags", 50)),
                evidence_weight=_weight(item["evidence_weight"], f"entities[{index}].evidence_weight"),
                evidence=_optional_text(item["evidence"], f"entities[{index}].evidence", 4000),
            )
        )

    relations: list[DraftRelation] = []
    for index, item in enumerate(raw_relations):
        if not isinstance(item, dict):
            raise GraphDraftError(f"relations[{index}] 必须是对象")
        expected = {"source", "relation", "target", "evidence_weight", "evidence"}
        if set(item) != expected:
            raise GraphDraftError(f"relations[{index}] 字段不符合严格 Schema")
        source = _text(item["source"], f"relations[{index}].source", 80)
        target = _text(item["target"], f"relations[{index}].target", 80)
        if source not in local_ids or target not in local_ids:
            raise GraphDraftError(f"relations[{index}] 的端点未在 entities 中声明")
        if source == target:
            raise GraphDraftError(f"relations[{index}] 不允许自环关系")
        relations.append(
            DraftRelation(
                source=source,
                relation=_text(item["relation"], f"relations[{index}].relation", 100),
                target=target,
                evidence_weight=_weight(item["evidence_weight"], f"relations[{index}].evidence_weight"),
                evidence=_optional_text(item["evidence"], f"relations[{index}].evidence", 4000),
            )
        )
    return GraphDraft(tuple(entities), tuple(relations))


def merge_graph_drafts(drafts: Iterable[GraphDraft]) -> GraphDraft:
    """按 keyword/alias 完全规范化相等合并分段草稿，关系保留最高证据。"""

    merged: list[DraftEntity] = []
    term_owner: dict[str, int] = {}
    local_map: dict[tuple[int, str], str] = {}
    canonical_local_ids: set[str] = set()
    for draft_index, draft in enumerate(drafts):
        for entity in draft.entities:
            source_local_id = entity.local_id
            terms = {_key(entity.keyword), *(_key(alias) for alias in entity.aliases)}
            owners = {term_owner[term] for term in terms if term in term_owner}
            created = False
            if len(owners) > 1:
                selected_index = min(owners)
                for duplicate_index in sorted(owners - {selected_index}, reverse=True):
                    merged[selected_index] = _merge_entity(merged[selected_index], merged[duplicate_index])
                    removed = merged.pop(duplicate_index)
                    for term, owner in list(term_owner.items()):
                        if owner == duplicate_index:
                            term_owner[term] = selected_index
                        elif owner > duplicate_index:
                            term_owner[term] = owner - 1
                    for key, value in list(local_map.items()):
                        if value == removed.local_id:
                            local_map[key] = merged[selected_index].local_id
            elif owners:
                selected_index = next(iter(owners))
            else:
                selected_index = len(merged)
                canonical_id = _available_local_id(
                    entity.local_id,
                    canonical_local_ids,
                    draft_index,
                )
                if canonical_id != entity.local_id:
                    entity = DraftEntity(
                        local_id=canonical_id,
                        keyword=entity.keyword,
                        summary=entity.summary,
                        aliases=entity.aliases,
                        tags=entity.tags,
                        evidence_weight=entity.evidence_weight,
                        evidence=entity.evidence,
                    )
                canonical_local_ids.add(canonical_id)
                merged.append(entity)
                created = True

            # local_id 仅在单个分段内唯一，跨分段可能重复，不能拿它判断
            # 两个实体是否相同；只要命中了已有术语拥有者就必须合并证据。
            if not created:
                merged[selected_index] = _merge_entity(merged[selected_index], entity)
            selected = merged[selected_index]
            local_map[(draft_index, source_local_id)] = selected.local_id
            for term in {_key(selected.keyword), *(_key(alias) for alias in selected.aliases)}:
                term_owner[term] = selected_index

    relations_by_key: dict[tuple[str, str, str], DraftRelation] = {}
    for draft_index, draft in enumerate(drafts):
        for relation in draft.relations:
            source = local_map[(draft_index, relation.source)]
            target = local_map[(draft_index, relation.target)]
            if source == target:
                continue
            normalized = DraftRelation(
                source=source,
                relation=relation.relation,
                target=target,
                evidence_weight=relation.evidence_weight,
                evidence=relation.evidence,
            )
            key = (source, _key(relation.relation), target)
            previous = relations_by_key.get(key)
            if previous is None or normalized.evidence_weight > previous.evidence_weight:
                relations_by_key[key] = normalized
    return GraphDraft(tuple(merged), tuple(relations_by_key.values()))


def split_markdown_for_graph(text: str, max_chars: int) -> list[str]:
    """优先在 Markdown 标题前切分，再以段落和硬字符上限兜底。"""

    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]
    blocks = re.split(r"(?=^#{1,6}\s+)", text, flags=re.MULTILINE)
    sections: list[str] = []
    current = ""
    for block in blocks:
        if not block:
            continue
        if len(current) + len(block) <= max_chars:
            current += block
            continue
        if current.strip():
            sections.extend(_split_oversized(current, max_chars))
        current = block
    if current.strip():
        sections.extend(_split_oversized(current, max_chars))
    return [section for section in sections if section.strip()]


def _split_oversized(value: str, max_chars: int) -> list[str]:
    if len(value) <= max_chars:
        return [value]
    paragraphs = re.split(r"(\n\s*\n)", value)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) <= max_chars:
            current += paragraph
            continue
        if current.strip():
            chunks.append(current)
        current = paragraph
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current.strip():
        chunks.append(current)
    return chunks


def _merge_entity(left: DraftEntity, right: DraftEntity) -> DraftEntity:
    aliases = _unique([*left.aliases, *right.aliases])
    if _key(left.keyword) != _key(right.keyword):
        aliases = _unique([*aliases, right.keyword])
    return DraftEntity(
        local_id=left.local_id,
        keyword=left.keyword,
        summary=right.summary if len(right.summary) > len(left.summary) else left.summary,
        aliases=tuple(aliases),
        tags=tuple(_unique([*left.tags, *right.tags])),
        evidence_weight=max(left.evidence_weight, right.evidence_weight),
        evidence=right.evidence if len(right.evidence) > len(left.evidence) else left.evidence,
    )


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphDraftError(f"{name} 必须是非空字符串")
    result = value.strip()
    if len(result) > maximum:
        raise GraphDraftError(f"{name} 超过 {maximum} 字符")
    return result


def _optional_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise GraphDraftError(f"{name} 必须是字符串")
    result = value.strip()
    if len(result) > maximum:
        raise GraphDraftError(f"{name} 超过 {maximum} 字符")
    return result


def _string_list(value: Any, name: str, maximum_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise GraphDraftError(f"{name} 必须是最多 {maximum_items} 项的字符串数组")
    return _unique([_text(item, f"{name}[]", 100) for item in value])


def _weight(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphDraftError(f"{name} 必须是数值")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise GraphDraftError(f"{name} 必须是 0~1 的有限数值")
    return result


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _available_local_id(preferred: str, used: set[str], draft_index: int) -> str:
    if preferred not in used:
        return preferred
    counter = 1
    while True:
        prefix = f"d{draft_index + 1}_{counter}_"
        candidate = f"{prefix}{preferred}"[:80]
        if candidate not in used:
            return candidate
        counter += 1


__all__ = [
    "GRAPH_DRAFT_SCHEMA",
    "DraftEntity",
    "DraftRelation",
    "GraphDraft",
    "GraphDraftError",
    "merge_graph_drafts",
    "split_markdown_for_graph",
    "validate_graph_draft",
]
