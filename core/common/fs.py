from __future__ import annotations

import hashlib
import os
from pathlib import Path


def validated_path(value: Path | str, field_name: str) -> str:
    text = os.fspath(value).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def is_within(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def sha256_of(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()
