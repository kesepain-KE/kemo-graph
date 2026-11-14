from __future__ import annotations

from core.common import write_json_atomic as _write_json_atomic
import json
from pathlib import Path
from typing import Any
from core.settings import AppConfig
from core.settings import load_config


from core.storage.paths import DatabasePaths

def default_graph_meta() -> dict[str, Any]:
    return {
        "total_nodes": 0,
        "total_edges": 0,
        "total_groups": 0,
        "last_summary_at": None,
        "changed_since_summary": 0,
        "extraction_signature": None,
    }


def default_rag_meta(settings: AppConfig | None = None) -> dict[str, Any]:
    active_settings = settings or load_config()
    return {
        "total_chunks": 0,
        "total_vectors": 0,
        "vector_dimensions": active_settings.models.embedding_dimensions,
        "embedding_model": active_settings.models.embedding,
        "vector_space_id": None,
        "chunking_signature": None,
        "faiss_index_type": "IndexIDMap2+IndexFlatIP",
        "last_built_at": None,
    }


def read_graph_meta(paths: DatabasePaths) -> dict[str, Any]:
    return _read_meta(paths.graph_meta, default_graph_meta())


def write_graph_meta(paths: DatabasePaths, meta: dict[str, Any]) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(paths.graph_meta, meta)


def read_rag_meta(
    paths: DatabasePaths, settings: AppConfig | None = None
) -> dict[str, Any]:
    return _read_meta(paths.rag_meta, default_rag_meta(settings))


def write_rag_meta(paths: DatabasePaths, meta: dict[str, Any]) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(paths.rag_meta, meta)


def _read_meta(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        _write_json_atomic(path, defaults)
        return deepcopy_dict(defaults)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"元数据文件不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"元数据文件根节点必须是对象：{path}")
    return {**defaults, **loaded}


def deepcopy_dict(value: dict[str, Any]) -> dict[str, Any]:
    """通过 JSON 往返复制仅含 JSON 值的元数据。"""

    return json.loads(json.dumps(value))
