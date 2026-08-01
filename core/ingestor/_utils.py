"""文档整理流程共享的无状态工具函数。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig, PROJECT_ROOT
from ..locks import get_knowledge_base_lock
from . import IngestError


def _graph_extraction_error(message: str) -> RuntimeError:
    from ._graph_build import GraphExtractionError

    return GraphExtractionError(message)


def _resolve_external_dir(
    value: Path | str | None,
    settings: AppConfig,
) -> Path:
    if value is None:
        return settings.resolve_external_dir()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _safe_source_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IngestError(f"非法 Markdown 相对路径：{relative_path}") from exc
    if candidate.suffix.casefold() != ".md":
        raise IngestError(f"源文件不是 Markdown：{relative_path}")
    return candidate


def _hash_relative_path(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path.replace("\\", "/"))
    normalized = normalized.strip("/").casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _semantic_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _graph_extraction_error(f"{field_name} 必须是非空字符串")
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _validate_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _graph_extraction_error(f"{field_name} 必须是字符串数组")
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


def _get_write_lock(data_dir: Path) -> threading.RLock:
    """Deprecated 内部别名；新维护流程与 Ingestor 共享同一把目录写锁。"""

    return get_knowledge_base_lock(data_dir)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _validated_path(value: Path | str, field_name: str) -> str:
    text = os.fspath(value).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _path_key(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(value).strip()))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
