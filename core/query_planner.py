"""查询理解、受控扩展与语义漂移防护。"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from provider.engine import chat_structured

from .config import AppConfig, load_config


LOGGER = logging.getLogger(__name__)
QUERY_PLANNER_VERSION = "query-planner-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "config" / "query_planning_prompt.md"
_TYPE_WEIGHTS = {
    "synonym": 0.85,
    "paraphrase": 0.80,
    "narrower": 0.75,
    "related": 0.65,
    "broader": 0.55,
    "subquery": 0.80,
}
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "rewrites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["synonym", "paraphrase", "narrower", "related", "broader"],
                    },
                },
                "required": ["text", "type"],
                "additionalProperties": False,
            },
        },
        "subqueries": {
            "type": "array",
            "items": {"type": "string"},
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "normalized": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "type": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["text", "normalized", "aliases", "type", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intent", "rewrites", "subqueries", "entities"],
    "additionalProperties": False,
}


class QueryPlanningError(RuntimeError):
    """查询规划响应无法验证。"""


@dataclass(frozen=True)
class QueryVariant:
    text: str
    kind: str
    weight: float
    similarity: float | None = None


@dataclass(frozen=True)
class PlannedEntity:
    text: str
    normalized: str
    aliases: tuple[str, ...]
    type: str
    confidence: float


@dataclass(frozen=True)
class QueryPlan:
    original: str
    intent: str
    variants: tuple[QueryVariant, ...]
    entities: tuple[PlannedEntity, ...]
    mode: str
    degraded: bool = False

    @property
    def texts(self) -> list[str]:
        return [item.text for item in self.variants]

    @property
    def weights(self) -> list[float]:
        return [item.weight for item in self.variants]


StructuredPlanner = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def plan_query(
    query: str,
    *,
    settings: AppConfig | None = None,
    structured_planner: StructuredPlanner | None = None,
) -> QueryPlan:
    """生成受控查询计划；任何模型故障都安全退化为原始查询。"""

    original = _normalized_text(query, "query")
    active_settings = settings or load_config()
    mode = active_settings.query_planning.mode
    if mode == "off":
        return _original_only(original, mode="off")
    if mode == "rule":
        return _rule_plan(original, mode="rule")

    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    user = json.dumps({"query": original}, ensure_ascii=False)
    planner = structured_planner or (
        lambda system, payload, schema: chat_structured(
            system,
            payload,
            schema,
            settings=active_settings,
            tool_name="submit_query_plan",
        )
    )
    try:
        payload = planner(prompt, user, _PLAN_SCHEMA)
        return _plan_from_payload(original, payload, active_settings, mode)
    except Exception as exc:
        LOGGER.warning("查询规划失败，已退化为原始查询：%s", exc)
        return replace(_rule_plan(original, mode=mode), degraded=True)


def filter_semantic_drift(
    plan: QueryPlan,
    vectors: Sequence[Sequence[float]],
    threshold: float,
) -> tuple[QueryPlan, list[int]]:
    """以原始查询向量为锚点过滤语义漂移，并返回保留的向量下标。"""

    if len(vectors) != len(plan.variants):
        raise QueryPlanningError("查询向量数量与查询计划不一致")
    if not vectors:
        raise QueryPlanningError("查询计划没有向量")
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise QueryPlanningError("查询计划向量必须是有限二维数组")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 0):
        raise QueryPlanningError("查询计划向量不能是零向量")
    similarities = (matrix @ matrix[0]) / (norms * norms[0])

    kept: list[QueryVariant] = []
    indexes: list[int] = []
    for index, (variant, similarity) in enumerate(zip(plan.variants, similarities)):
        value = float(similarity)
        if index == 0 or value >= threshold:
            kept.append(replace(variant, similarity=value))
            indexes.append(index)
    return replace(plan, variants=tuple(kept)), indexes


def query_planner_signature(settings: AppConfig) -> str:
    """返回缓存键可用的查询规划配置与 Prompt 摘要。"""

    payload = {
        "version": QUERY_PLANNER_VERSION,
        "config": settings.query_planning.model_dump(mode="json"),
        "prompt_sha256": hashlib.sha256(_PROMPT_PATH.read_bytes()).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _plan_from_payload(
    original: str,
    payload: Any,
    settings: AppConfig,
    mode: str,
) -> QueryPlan:
    if not isinstance(payload, dict):
        raise QueryPlanningError("查询规划响应必须是对象")
    intent = _optional_text(payload.get("intent")) or original
    rewrites = payload.get("rewrites")
    subqueries = payload.get("subqueries")
    entities = payload.get("entities")
    if not isinstance(rewrites, list) or not isinstance(subqueries, list):
        raise QueryPlanningError("rewrites 和 subqueries 必须是数组")
    if not isinstance(entities, list):
        raise QueryPlanningError("entities 必须是数组")

    variants = [QueryVariant(original, "original", 1.0)]
    seen = {_match_key(original)}
    for item in rewrites[: settings.query_planning.max_rewrites]:
        if not isinstance(item, dict):
            raise QueryPlanningError("rewrite 必须是对象")
        kind = item.get("type")
        if kind not in _TYPE_WEIGHTS:
            raise QueryPlanningError(f"不支持的查询扩展类型：{kind}")
        _append_variant(variants, seen, item.get("text"), kind, _TYPE_WEIGHTS[kind])
    for value in subqueries[: settings.query_planning.max_subqueries]:
        _append_variant(variants, seen, value, "subquery", _TYPE_WEIGHTS["subquery"])
    variants = variants[: settings.query_planning.max_total_queries]

    planned_entities: list[PlannedEntity] = []
    entity_seen: set[str] = set()
    for item in entities[: settings.entity_extraction.max_entities]:
        entity = _entity_from_payload(item)
        key = _match_key(entity.normalized)
        if key not in entity_seen:
            entity_seen.add(key)
            planned_entities.append(entity)
    return QueryPlan(
        original=original,
        intent=intent,
        variants=tuple(variants),
        entities=tuple(planned_entities),
        mode=mode,
    )


def _entity_from_payload(payload: Any) -> PlannedEntity:
    if not isinstance(payload, dict):
        raise QueryPlanningError("entity 必须是对象")
    text = _normalized_text(payload.get("text"), "entity.text")
    normalized = _normalized_text(payload.get("normalized"), "entity.normalized")
    entity_type = _normalized_text(payload.get("type"), "entity.type")
    raw_aliases = payload.get("aliases")
    confidence = payload.get("confidence")
    if not isinstance(raw_aliases, list):
        raise QueryPlanningError("entity.aliases 必须是数组")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise QueryPlanningError("entity.confidence 必须是数字")
    if not 0.0 <= float(confidence) <= 1.0:
        raise QueryPlanningError("entity.confidence 必须在 0~1")
    aliases: list[str] = []
    seen = {_match_key(normalized)}
    for alias in raw_aliases:
        value = _normalized_text(alias, "entity.aliases[]")
        key = _match_key(value)
        if key not in seen:
            seen.add(key)
            aliases.append(value)
    return PlannedEntity(text, normalized, tuple(aliases), entity_type, float(confidence))


def _append_variant(
    variants: list[QueryVariant],
    seen: set[str],
    raw_text: Any,
    kind: str,
    weight: float,
) -> None:
    text = _normalized_text(raw_text, f"{kind}.text")
    if len(text) > 500:
        raise QueryPlanningError("查询扩展文本不能超过 500 个字符")
    key = _match_key(text)
    if key not in seen:
        seen.add(key)
        variants.append(QueryVariant(text, kind, weight))


def _original_only(original: str, *, mode: str, degraded: bool = False) -> QueryPlan:
    return QueryPlan(
        original=original,
        intent=original,
        variants=(QueryVariant(original, "original", 1.0),),
        entities=(),
        mode=mode,
        degraded=degraded,
    )


def _rule_plan(original: str, *, mode: str) -> QueryPlan:
    # 规则模式只做安全规范化；不使用静态同义词表猜测用户意图。
    return _original_only(original, mode=mode)


def _normalized_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise QueryPlanningError(f"{field} 必须是字符串")
    normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not normalized:
        raise QueryPlanningError(f"{field} 不能为空")
    return normalized


def _optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _match_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()
