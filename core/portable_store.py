"""分布在绝对路径中的可移植 kemo-graph 知识库。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence
from uuid import UUID, uuid4

from .config import AppConfig, DEFAULT_CONFIG_PATH, load_config
from .db import DatabasePaths, get_database_paths, initialize_databases
from .knowledge_base import KnowledgeBaseService


STORAGE_DIRECTORY_NAME = "kemo-graph-storage"
STORE_MANIFEST_VERSION = 1
StoreScope = Literal[
    "knowledge.global",
    "knowledge.shared",
    "knowledge.user",
    "memory.temporary",
    "memory.important",
    "memory.permanent",
    "memory.user",
]
STORE_SCOPES = {
    "knowledge.global",
    "knowledge.shared",
    "knowledge.user",
    "memory.temporary",
    "memory.important",
    "memory.permanent",
    "memory.user",
}


class PortableStoreError(RuntimeError):
    """分布式知识库路径、清单或操作不符合契约。"""


class PortableStoreNotInitializedError(PortableStoreError):
    """目标目录中不存在完整的 kemo-graph-storage。"""


class PortableStoreAccessError(PortableStoreError):
    """绝对路径不在允许访问的根目录中。"""


@dataclass(frozen=True)
class PortableStoreManifest:
    schema_version: int
    store_id: str
    display_name: str
    scope: str
    owner_id: str | None
    root_path: str
    storage_directory: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PortableStorePaths:
    store_root: Path
    storage_root: Path
    manifest_path: Path
    external_dir: Path
    recycle_dir: Path
    databases: DatabasePaths


def resolve_store_paths(
    store_root: Path | str,
    *,
    settings: AppConfig | None = None,
    require_initialized: bool = False,
) -> PortableStorePaths:
    """从一个绝对知识位置推导所有存储路径，不允许默认目录回退。"""

    active_settings = settings or load_config()
    if not active_settings.portable_stores.enabled:
        raise PortableStoreAccessError("绝对路径知识库功能已在配置中禁用")
    root = _absolute_root(store_root)
    _ensure_allowed(root, active_settings)
    storage_root = (root / STORAGE_DIRECTORY_NAME).resolve(strict=False)
    if storage_root.parent != root:
        raise PortableStoreAccessError("kemo-graph-storage 必须直接位于 store_root 下")
    databases = get_database_paths(storage_root)
    external_dir = storage_root / "content" / "markdown"
    recycle_dir = storage_root / "content" / "recycle"
    paths = PortableStorePaths(
        store_root=root,
        storage_root=storage_root,
        manifest_path=storage_root / "manifest.json",
        external_dir=external_dir,
        recycle_dir=recycle_dir,
        databases=databases,
    )
    _assert_contained_paths(paths)
    if require_initialized and not paths.manifest_path.is_file():
        raise PortableStoreNotInitializedError(
            f"知识位置尚未初始化：{root}"
        )
    return paths


def initialize_store(
    store_root: Path | str,
    *,
    scope: str,
    owner_id: str | None = None,
    display_name: str | None = None,
    settings: AppConfig | None = None,
) -> dict[str, Any]:
    """初始化独立数据库、索引、规范 Markdown 目录和稳定清单。"""

    active_settings = settings or load_config()
    normalized_scope = _validated_scope(scope)
    normalized_owner = _optional_text(owner_id)
    paths = resolve_store_paths(store_root, settings=active_settings)
    if paths.store_root.exists() and not paths.store_root.is_dir():
        raise PortableStoreError(f"store_root 不是目录：{paths.store_root}")
    paths.store_root.mkdir(parents=True, exist_ok=True)

    if paths.manifest_path.exists():
        manifest = load_store_manifest(paths.store_root, settings=active_settings)
        if manifest.scope != normalized_scope:
            raise PortableStoreError(
                f"知识位置已按 {manifest.scope} 初始化，不能改为 {normalized_scope}"
            )
        if normalized_owner is not None and manifest.owner_id != normalized_owner:
            raise PortableStoreError("知识位置 owner_id 与现有清单不一致")
        paths.external_dir.mkdir(parents=True, exist_ok=True)
        paths.recycle_dir.mkdir(parents=True, exist_ok=True)
        initialize_databases(paths.storage_root, active_settings)
        return describe_store(
            paths.store_root,
            settings=active_settings,
            initialized_now=False,
        )

    if paths.storage_root.exists() and not paths.storage_root.is_dir():
        raise PortableStoreError(f"存储路径不是目录：{paths.storage_root}")
    if paths.storage_root.exists() and any(paths.storage_root.iterdir()):
        raise PortableStoreError(
            f"存储目录非空但缺少 manifest.json：{paths.storage_root}"
        )

    paths.external_dir.mkdir(parents=True, exist_ok=True)
    paths.recycle_dir.mkdir(parents=True, exist_ok=True)
    initialize_databases(paths.storage_root, active_settings)
    now = _now_iso()
    manifest = PortableStoreManifest(
        schema_version=STORE_MANIFEST_VERSION,
        store_id=str(uuid4()),
        display_name=(
            _optional_text(display_name)
            or f"{normalized_scope} @ {paths.store_root.name}"
        ),
        scope=normalized_scope,
        owner_id=normalized_owner,
        root_path=str(paths.store_root),
        storage_directory=STORAGE_DIRECTORY_NAME,
        created_at=now,
        updated_at=now,
    )
    _write_json_atomic(paths.manifest_path, asdict(manifest))
    return describe_store(
        paths.store_root,
        settings=active_settings,
        initialized_now=True,
    )


def load_store_manifest(
    store_root: Path | str,
    *,
    settings: AppConfig | None = None,
) -> PortableStoreManifest:
    paths = resolve_store_paths(
        store_root,
        settings=settings,
        require_initialized=True,
    )
    try:
        payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableStoreError(f"无法读取知识库清单：{paths.manifest_path}") from exc
    if not isinstance(payload, dict):
        raise PortableStoreError("manifest.json 根节点必须是对象")
    required = {
        "schema_version",
        "store_id",
        "display_name",
        "scope",
        "owner_id",
        "root_path",
        "storage_directory",
        "created_at",
        "updated_at",
    }
    if set(payload) != required:
        raise PortableStoreError("manifest.json 字段不完整或包含未知字段")
    if payload["schema_version"] != STORE_MANIFEST_VERSION:
        raise PortableStoreError(
            f"不支持的知识库清单版本：{payload['schema_version']!r}"
        )
    if payload["storage_directory"] != STORAGE_DIRECTORY_NAME:
        raise PortableStoreError("manifest.json 的存储目录名不符合固定契约")
    _validated_scope(payload["scope"])
    if not isinstance(payload["store_id"], str) or not payload["store_id"].strip():
        raise PortableStoreError("manifest.json 缺少 store_id")
    try:
        UUID(payload["store_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise PortableStoreError("manifest.json 的 store_id 不是有效 UUID") from exc
    if not isinstance(payload["display_name"], str) or not payload[
        "display_name"
    ].strip():
        raise PortableStoreError("manifest.json 缺少 display_name")
    if payload["owner_id"] is not None and (
        not isinstance(payload["owner_id"], str) or not payload["owner_id"].strip()
    ):
        raise PortableStoreError("manifest.json 的 owner_id 类型无效")
    for field in ("created_at", "updated_at"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise PortableStoreError(f"manifest.json 缺少 {field}")
    manifest_root = _absolute_root(payload["root_path"])
    if os.path.normcase(str(manifest_root)) != os.path.normcase(str(paths.store_root)):
        raise PortableStoreError(
            "manifest.json 的 root_path 与当前绝对位置不一致"
        )
    try:
        return PortableStoreManifest(**payload)
    except TypeError as exc:
        raise PortableStoreError("manifest.json 字段类型无效") from exc


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


def _absolute_root(value: Path | str) -> Path:
    if not isinstance(value, (str, Path)):
        raise PortableStoreAccessError("store_root 必须是绝对路径字符串")
    raw = os.fspath(value).strip()
    if not raw:
        raise PortableStoreAccessError("store_root 不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise PortableStoreAccessError("store_root 必须使用绝对路径")
    if ".." in path.parts:
        raise PortableStoreAccessError("store_root 不能包含 '..'")
    resolved = path.resolve(strict=False)
    if resolved.name.casefold() == STORAGE_DIRECTORY_NAME.casefold():
        raise PortableStoreAccessError(
            f"store_root 应指向知识位置本身，不能直接指向 {STORAGE_DIRECTORY_NAME}"
        )
    return resolved


def _ensure_allowed(root: Path, settings: AppConfig) -> None:
    allowed = settings.portable_stores.allowed_roots
    if not allowed:
        return
    for value in allowed:
        allowed_root = Path(value).resolve(strict=False)
        try:
            root.relative_to(allowed_root)
            return
        except ValueError:
            continue
    raise PortableStoreAccessError(f"store_root 不在允许访问的根目录中：{root}")


def _assert_contained_paths(paths: PortableStorePaths) -> None:
    candidates = [
        paths.manifest_path,
        paths.external_dir,
        paths.recycle_dir,
        *(
            value
            for value in vars(paths.databases).values()
            if isinstance(value, Path)
        ),
    ]
    for candidate in candidates:
        try:
            candidate.resolve(strict=False).relative_to(paths.storage_root)
        except ValueError as exc:
            raise PortableStoreAccessError(
                f"派生存储路径越出 kemo-graph-storage：{candidate}"
            ) from exc


def _assert_initialized_layout(paths: PortableStorePaths) -> None:
    required_files = (
        paths.databases.sources_db,
        paths.databases.search_cache_db,
        paths.databases.graph_db,
        paths.databases.rag_db,
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    required_directories = (
        paths.external_dir,
        paths.recycle_dir,
        paths.databases.vector_index_dir,
    )
    missing.extend(
        str(path) for path in required_directories if not path.is_dir()
    )
    if missing:
        raise PortableStoreNotInitializedError(
            "知识位置结构不完整，请重新执行 store-init：" + ", ".join(missing)
        )


def _validated_scope(value: Any) -> str:
    if not isinstance(value, str) or value not in STORE_SCOPES:
        raise PortableStoreError(
            "scope 必须为 knowledge.global/shared/user 或 "
            "memory.temporary/important/permanent/user"
        )
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableStoreError("可选文本字段必须是字符串")
    normalized = value.strip()
    return normalized or None


def _unique_roots(values: Sequence[Path | str]) -> list[Path]:
    if isinstance(values, (str, bytes, Path)):
        raise PortableStoreError("store_roots 必须是路径数组")
    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        root = _absolute_root(value)
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "STORAGE_DIRECTORY_NAME",
    "STORE_MANIFEST_VERSION",
    "STORE_SCOPES",
    "PortableStoreAccessError",
    "PortableStoreError",
    "PortableStoreManifest",
    "PortableStoreNotInitializedError",
    "PortableStorePaths",
    "create_store_service",
    "describe_store",
    "federated_query",
    "initialize_store",
    "load_store_manifest",
    "resolve_store_paths",
]
