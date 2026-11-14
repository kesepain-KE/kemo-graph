from __future__ import annotations

from core.storage.portable.paths import _absolute_root

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID
from core.settings import AppConfig


from core.storage.portable.models import STORAGE_DIRECTORY_NAME, STORE_MANIFEST_VERSION, STORE_SCOPES, PortableStoreError, PortableStoreManifest
from core.storage.portable.paths import resolve_store_paths

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
