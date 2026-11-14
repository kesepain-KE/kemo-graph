from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from markitdown import supported_suffixes

SUPPORTED_IMPORT_SUFFIXES = supported_suffixes() | {""}

MAX_IMPORT_BYTES = 50 * 1024 * 1024

@dataclass(frozen=True)
class _ImportSnapshot:
    """一次导入使用的私有不可变文件快照及其来源元数据。"""

    path: Path
    size: int
    sha256: str
    modified_at: str
