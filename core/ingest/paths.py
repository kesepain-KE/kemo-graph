from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path
from core.settings.schema import AppConfig, PROJECT_ROOT
from core.ingest.errors import IngestError

def _resolve_external_dir(
    value: Path | str | None,
    settings: AppConfig,
) -> Path:
    if value is None:
        return settings.resolve_external_dir()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()

def _safe_source_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IngestError(f"非法 Markdown 相对路径：{relative_path}") from exc
    if candidate.suffix.casefold() != ".md":
        raise IngestError(f"源文件不是 Markdown：{relative_path}")
    return candidate

def _hash_relative_path(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path.replace("\\", "/"))
    normalized = normalized.strip("/").casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _path_key(value: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(value).strip()))
