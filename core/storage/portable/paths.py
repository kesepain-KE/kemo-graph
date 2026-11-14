from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence
from core.settings.schema import AppConfig
from core.settings.loader import load_config
from core.storage.paths import get_database_paths


from core.storage.portable.models import STORAGE_DIRECTORY_NAME, PortableStoreError, PortableStoreNotInitializedError, PortableStoreAccessError, PortableStorePaths

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
    paths = PortableStorePaths(
        store_root=root,
        storage_root=storage_root,
        manifest_path=storage_root / "manifest.json",
        external_dir=external_dir,
        databases=databases,
    )
    _assert_contained_paths(paths)
    if require_initialized and not paths.manifest_path.is_file():
        raise PortableStoreNotInitializedError(
            f"知识位置尚未初始化：{root}"
        )
    return paths


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
        paths.databases.vector_index_dir,
    )
    missing.extend(
        str(path) for path in required_directories if not path.is_dir()
    )
    if missing:
        raise PortableStoreNotInitializedError(
            "知识位置结构不完整，请重新执行 store-init：" + ", ".join(missing)
        )


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
