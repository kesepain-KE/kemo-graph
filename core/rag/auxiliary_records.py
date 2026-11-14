from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any
import numpy as np
from core.settings import AppConfig

def _normalize_auxiliary_records(
    records: Sequence[tuple[str, str]],
    id_column: str,
) -> dict[str, str]:
    if isinstance(records, (str, bytes)):
        raise TypeError("records 必须是 (id, summary) 序列")
    normalized: dict[str, str] = {}
    for item in records:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("records 中每一项必须是 (id, summary) 二元组")
        object_id, summary = item
        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError(f"{id_column} 必须是非空字符串")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary 必须是非空字符串")
        normalized_id = object_id.strip()
        normalized_summary = summary.strip()
        previous = normalized.get(normalized_id)
        if previous is not None and previous != normalized_summary:
            raise ValueError(f"同一 {id_column} 对应了不同 summary：{normalized_id}")
        normalized[normalized_id] = normalized_summary
    return normalized

def _normalize_object_ids(values: Sequence[str], field_name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} 列表不能是字符串")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是非空字符串")
        item = value.strip()
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized

def _summary_hash(summary: str) -> str:
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()

def _auxiliary_row_is_fresh(
    row: Any,
    summary: str,
    settings: AppConfig,
) -> bool:
    return bool(
        row is not None
        and str(row["summary_hash"]) == _summary_hash(summary)
        and int(row["dimensions"]) == settings.models.embedding_dimensions
        and str(row["model_name"]) == settings.models.embedding
        and isinstance(row["vector_space_id"], str)
        and bool(str(row["vector_space_id"]).strip())
    )

def _auxiliary_row_matches_graph(
    row: Any,
    expected_summary_hash: str | None,
    settings: AppConfig,
) -> bool:
    if (
        expected_summary_hash is None
        or str(row["summary_hash"]) != expected_summary_hash
        or int(row["dimensions"]) != settings.models.embedding_dimensions
        or str(row["model_name"]) != settings.models.embedding
    ):
        return False
    vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
    return bool(
        len(vector) == settings.models.embedding_dimensions
        and np.isfinite(vector).all()
    )
