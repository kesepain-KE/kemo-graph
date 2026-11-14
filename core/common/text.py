import json
from typing import Any


def required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    return text


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise ValueError("图谱中的 JSON 数组字段格式错误")
    return parsed


def clip_context_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"
