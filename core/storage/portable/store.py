from __future__ import annotations

from .federation import describe_store

from core.common import now_iso as _now_iso, write_json_atomic as _write_json_atomic
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4
from core.config import AppConfig, load_config
from core.db import initialize_databases


from .models import STORAGE_DIRECTORY_NAME, STORE_MANIFEST_VERSION, PortableStoreError, PortableStoreManifest
from .paths import resolve_store_paths
from .manifest import load_store_manifest, _validated_scope, _optional_text

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
        (paths.external_dir.parent / "originals").mkdir(parents=True, exist_ok=True)
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
    (paths.external_dir.parent / "originals").mkdir(parents=True, exist_ok=True)
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
