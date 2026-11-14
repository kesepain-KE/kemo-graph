from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from core.storage.paths import DatabasePaths


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
    databases: DatabasePaths
