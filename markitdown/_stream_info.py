"""Converter 调度所需的输入文件元数据。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """本地输入流的稳定描述；不包含文档正文。"""

    path: Path
    filename: str
    extension: str
    mime_type: str | None = None
    size: int = 0


__all__ = ["StreamInfo"]
