from __future__ import annotations
from core.ingest.errors import IngestError, GraphExtractionError
import json
import unicodedata
from collections.abc import Sequence
from typing import Any
from core.ingest.errors import IngestError


def _semantic_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphExtractionError(f"{field_name} 必须是非空字符串")
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())

def _validate_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GraphExtractionError(f"{field_name} 必须是字符串数组")
    return _unique_texts(value)

def _unique_texts(values: Sequence[str]) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            results.append(normalized)
    return results

def _load_json_string_list(value: Any, node_id: str, field_name: str) -> list[str]:
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        raise IngestError(f"nodes.{field_name} 不是 JSON 文本：node_id={node_id}")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise IngestError(
            f"nodes.{field_name} 不是合法 JSON：node_id={node_id}"
        ) from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise IngestError(f"nodes.{field_name} 必须是字符串数组：node_id={node_id}")
    return parsed
