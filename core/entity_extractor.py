"""查询实体抽取，支持 LLM 与无外部依赖的规则模式。"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from provider.engine import chat

from .config import AppConfig, load_config
from .logger import DailyTSVLogger


_QUOTED_PATTERN = re.compile(r"[\"'“”‘’《》]([^\"'“”‘’《》]{1,80})[\"'“”‘’《》]")
_SPLIT_PATTERN = re.compile(
    r"(?:和|与|及|或者|或|的|是|什么|如何|区别|联系|关系|"
    r"\b(?:and|or|vs|versus|with|between|of|the|is|are|what|how)\b|"
    r"[,，;；:：!?！？()（）/\\])",
    re.IGNORECASE,
)
_EDGE_PUNCTUATION = " \t\r\n,，;；:：.!！?？()（）[]【】{}<>《》\"'“”‘’"
_RULE_STOP_WORDS = {
    "what",
    "how",
    "why",
    "difference",
    "relation",
    "relationship",
    "什么",
    "如何",
    "为什么",
    "区别",
    "联系",
    "关系",
}

_LLM_SYSTEM_PROMPT = """你是查询实体抽取器。请从用户查询切片中抽取用于知识图谱检索的实体。
只返回严格 JSON，不要 Markdown 或解释。格式：
{"entities":[{"text":"原文","type":"concept","normalized":"规范名","aliases":["别名"],"confidence":0.95}]}
要求：normalized 简短稳定；aliases 只放真实可能别名；confidence 范围 0~1。"""


class EntityExtractionError(RuntimeError):
    """实体抽取响应无法解析或不符合结构。"""


@dataclass(frozen=True)
class Entity:
    text: str
    type: str
    normalized: str
    aliases: list[str]
    confidence: float


LLMCallable = Callable[[str, str], str]


def extract(
    chunks: list[str],
    *,
    settings: AppConfig | None = None,
    llm: LLMCallable | None = None,
) -> list[Entity]:
    """按配置方法从查询切片中抽取实体并去重。"""

    if not isinstance(chunks, list) or any(
        not isinstance(value, str) for value in chunks
    ):
        raise TypeError("chunks 必须是字符串列表")
    non_empty_chunks = [value.strip() for value in chunks if value.strip()]
    if not non_empty_chunks:
        return []

    active_settings = settings or load_config()
    if active_settings.entity_extraction.method == "rule":
        entities = _extract_by_rule(non_empty_chunks)
    else:
        active_llm = llm or (
            lambda system, user: chat(system, user, settings=active_settings)
        )
        started_at = time.perf_counter()
        try:
            entities = _extract_by_llm(non_empty_chunks, active_llm)
        except Exception:
            _log_llm_request(active_settings, started_at, "failed")
            raise
        _log_llm_request(active_settings, started_at, "completed")
    return _deduplicate(entities)[: active_settings.entity_extraction.max_entities]


def _extract_by_rule(chunks: list[str]) -> list[Entity]:
    candidates: list[str] = []
    for value in chunks:
        candidates.extend(match.group(1) for match in _QUOTED_PATTERN.finditer(value))
        candidates.extend(_SPLIT_PATTERN.split(value))

    entities: list[Entity] = []
    for candidate in candidates:
        normalized = _normalize(candidate.strip(_EDGE_PUNCTUATION))
        if not normalized or normalized.casefold() in _RULE_STOP_WORDS:
            continue
        if len(normalized) == 1 and not normalized.isascii():
            continue
        entities.append(
            Entity(
                text=normalized,
                type="concept",
                normalized=normalized,
                aliases=[],
                confidence=1.0,
            )
        )
    return entities


def _extract_by_llm(chunks: list[str], llm: LLMCallable) -> list[Entity]:
    user_payload = json.dumps({"chunks": chunks}, ensure_ascii=False)
    raw_response = llm(_LLM_SYSTEM_PROMPT, user_payload)
    payload = _parse_json_response(raw_response)
    raw_entities = payload.get("entities") if isinstance(payload, dict) else payload
    if not isinstance(raw_entities, list):
        raise EntityExtractionError("LLM 响应必须包含 entities 数组")
    return [_entity_from_payload(item, index) for index, item in enumerate(raw_entities)]


def _parse_json_response(response: str) -> Any:
    if not isinstance(response, str) or not response.strip():
        raise EntityExtractionError("LLM 返回了空响应")
    candidate = response.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise EntityExtractionError(f"LLM 实体响应不是合法 JSON：{exc}") from exc


def _entity_from_payload(payload: Any, index: int) -> Entity:
    if not isinstance(payload, dict):
        raise EntityExtractionError(f"第 {index} 个实体必须是对象")
    text = payload.get("text")
    normalized = payload.get("normalized", text)
    entity_type = payload.get("type", "concept")
    aliases = payload.get("aliases", [])
    confidence = payload.get("confidence", 1.0)
    if not isinstance(text, str) or not text.strip():
        raise EntityExtractionError(f"第 {index} 个实体缺少有效 text")
    if not isinstance(normalized, str) or not normalized.strip():
        raise EntityExtractionError(f"第 {index} 个实体缺少有效 normalized")
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise EntityExtractionError(f"第 {index} 个实体缺少有效 type")
    if not isinstance(aliases, list) or any(
        not isinstance(alias, str) for alias in aliases
    ):
        raise EntityExtractionError(f"第 {index} 个实体 aliases 必须是字符串数组")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise EntityExtractionError(f"第 {index} 个实体 confidence 必须在 0~1")

    normalized_value = _normalize(normalized)
    clean_aliases = _unique_strings(
        alias for alias in aliases if _normalize(alias).casefold() != normalized_value.casefold()
    )
    return Entity(
        text=_normalize(text),
        type=_normalize(entity_type),
        normalized=normalized_value,
        aliases=clean_aliases,
        confidence=float(confidence),
    )


def _deduplicate(entities: list[Entity]) -> list[Entity]:
    merged: dict[str, Entity] = {}
    order: list[str] = []
    for entity in entities:
        key = entity.normalized.casefold()
        previous = merged.get(key)
        if previous is None:
            merged[key] = entity
            order.append(key)
            continue
        preferred = entity if entity.confidence > previous.confidence else previous
        merged[key] = Entity(
            text=preferred.text,
            type=preferred.type,
            normalized=preferred.normalized,
            aliases=_unique_strings([*previous.aliases, *entity.aliases]),
            confidence=max(previous.confidence, entity.confidence),
        )
    return [merged[key] for key in order]


def _unique_strings(values: Any) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            results.append(normalized)
    return results


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _log_llm_request(settings: AppConfig, started_at: float, status: str) -> None:
    try:
        DailyTSVLogger(
            settings.resolve_log_dir(),
            settings.log_level,
        ).log(
            "entity_extractor",
            "llm_request",
            f"purpose=entity_extraction, model={settings.models.llm}, status={status}",
            round((time.perf_counter() - started_at) * 1000),
            "ERROR" if status == "failed" else "INFO",
        )
    except Exception:
        pass
