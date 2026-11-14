from __future__ import annotations

from .paths import _assert_initialized_layout

from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence
from core.config import AppConfig, DEFAULT_CONFIG_PATH, load_config


from core.knowledge_base import KnowledgeBaseService
from .models import PortableStoreError
from .paths import resolve_store_paths, _unique_roots
from .manifest import load_store_manifest

def create_store_service(
    store_root: Path | str,
    *,
    settings: AppConfig | None = None,
    config_path: Path | str | None = None,
) -> KnowledgeBaseService:
    """创建严格绑定到指定分布式 Store 的核心服务。"""

    active_settings = settings or load_config(config_path or DEFAULT_CONFIG_PATH)
    paths = resolve_store_paths(
        store_root,
        settings=active_settings,
        require_initialized=True,
    )
    load_store_manifest(paths.store_root, settings=active_settings)
    _assert_initialized_layout(paths)
    return KnowledgeBaseService(
        settings=active_settings,
        data_dir=paths.storage_root,
        external_dir=paths.external_dir,
        config_path=config_path or DEFAULT_CONFIG_PATH,
    )


def describe_store(
    store_root: Path | str,
    *,
    settings: AppConfig | None = None,
    config_path: Path | str | None = None,
    initialized_now: bool = False,
) -> dict[str, Any]:
    active_settings = settings or load_config(config_path or DEFAULT_CONFIG_PATH)
    paths = resolve_store_paths(
        store_root,
        settings=active_settings,
        require_initialized=True,
    )
    manifest = load_store_manifest(paths.store_root, settings=active_settings)
    status = create_store_service(
        paths.store_root,
        settings=active_settings,
        config_path=config_path,
    ).status()
    return {
        "manifest": asdict(manifest),
        "store_root": str(paths.store_root),
        "storage_root": str(paths.storage_root),
        "data_dir": str(paths.storage_root),
        "external_dir": str(paths.external_dir),
        "originals_dir": str(paths.external_dir.parent / "originals"),
        "initialized_now": initialized_now,
        "status": status,
    }


def federated_query(
    store_roots: Sequence[Path | str],
    query: str,
    *,
    mode: str = "hybrid",
    settings: AppConfig | None = None,
    config_path: Path | str | None = None,
    force: bool = False,
    top_k: int | None = None,
    graph_depth: int = 3,
) -> dict[str, Any]:
    """独立查询多个位置，在内存中融合并隔离单 Store 故障。"""

    if mode not in {"graph", "rag", "hybrid", "global", "answer"}:
        raise PortableStoreError("联合查询 mode 必须为 graph、rag、hybrid、global 或 answer")
    if not isinstance(query, str) or not query.strip():
        raise PortableStoreError("query 不能为空")
    roots = _unique_roots(store_roots)
    if not roots:
        raise PortableStoreError("联合查询至少需要一个 store_root")

    active_settings = settings or load_config(config_path or DEFAULT_CONFIG_PATH)
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    merged_items: list[dict[str, Any]] = []
    for root in roots:
        try:
            manifest = load_store_manifest(root, settings=active_settings)
            service = create_store_service(
                root,
                settings=active_settings,
                config_path=config_path,
            )
            result = _query_store(
                service,
                query.strip(),
                mode=mode,
                force=force,
                top_k=top_k,
                graph_depth=graph_depth,
            )
            store_meta = {
                "store_id": manifest.store_id,
                "scope": manifest.scope,
                "owner_id": manifest.owner_id,
                "store_root": manifest.root_path,
            }
            successes.append({"store": store_meta, "result": result})
            merged_items.extend(_mergeable_items(mode, result, store_meta))
        except Exception as exc:
            failures.append(
                {
                    "store_root": str(root),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    merged_items.sort(
        key=lambda item: float(item.get("federated_score", 0.0)),
        reverse=True,
    )
    if top_k is not None:
        merged_items = merged_items[:top_k]
    return {
        "query": query.strip(),
        "mode": mode,
        "stores_requested": len(roots),
        "stores_succeeded": len(successes),
        "stores_failed": failures,
        "stores": successes,
        "merged_results": merged_items,
    }


def _query_store(
    service: KnowledgeBaseService,
    query: str,
    *,
    mode: str,
    force: bool,
    top_k: int | None,
    graph_depth: int,
) -> dict[str, Any]:
    if mode == "graph":
        return service.query_graph(query, depth=graph_depth, force=force)
    if mode == "rag":
        return service.query_rag(query, top_k=top_k, force=force)
    if mode == "hybrid":
        return service.query_hybrid(
            query,
            graph_depth=graph_depth,
            rag_top_k=top_k,
            force=force,
        )
    if mode == "global":
        return service.query_global(query, top_k=top_k or 5, force=force)
    return service.query_answer(
        query,
        graph_depth=graph_depth,
        rag_top_k=top_k,
        force=force,
    )


def _mergeable_items(
    mode: str,
    result: dict[str, Any],
    store: dict[str, Any],
) -> list[dict[str, Any]]:
    if mode == "rag":
        values = result.get("results", [])
    elif mode == "hybrid":
        rag = result.get("rag", {})
        values = rag.get("results", []) if isinstance(rag, dict) else []
    elif mode == "answer":
        retrieval = result.get("retrieval", {})
        rag = retrieval.get("rag", {}) if isinstance(retrieval, dict) else {}
        values = rag.get("results", []) if isinstance(rag, dict) else []
    elif mode == "global":
        values = result.get("communities", [])
    else:
        values = result.get("hit_nodes", [])
    items: list[dict[str, Any]] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict):
            item = value.copy()
            item["store"] = store
            item["federated_score"] = float(
                item.get("score", item.get("match_score", 0.0)) or 0.0
            )
            items.append(item)
    return items
