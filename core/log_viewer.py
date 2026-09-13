"""Bounded, read-only access to known daily logs; no arbitrary file paths."""

import csv
import hashlib
from datetime import date, datetime, timezone
import io
import os

from .logger import redact_log_text
from .read_cache import READ_CACHE, file_revision

MAX_READ_BYTES = 2 * 1024 * 1024
CATEGORIES = {"terminal", "query", "internal"}


def is_query_log(module: str, action: str, detail: str) -> bool:
    return ("query" in action or "search" in action and action != "search_cache_clear"
            or module in {"query_planner", "hybrid"} or "purpose=query" in detail
            or module == "api" and "/query/" in detail)


def read_logs(settings, *, category: str = "internal", log_date: str | None = None, limit: int = 200) -> dict:
    if category not in CATEGORIES:
        raise ValueError("category 必须是 terminal、query 或 internal")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit 必须为 1 到 500")
    selected = log_date or datetime.now(timezone.utc).date().isoformat()
    if len(selected) != 10 or date.fromisoformat(selected).isoformat() != selected:
        raise ValueError("date 必须是 YYYY-MM-DD")
    root = settings.resolve_log_dir().resolve()
    folder = root / "terminal" if category == "terminal" else root
    path = folder / f"{selected}.tsv"
    if folder.is_symlink() or path.is_symlink() or path.resolve().parent != folder.resolve():
        raise ValueError("日志路径不安全")
    secrets = (settings.kemo.api_key, os.getenv(settings.kemo.api_key_env, ""))
    secret_revision = hashlib.sha256(repr(secrets).encode("utf-8")).hexdigest()
    return READ_CACHE.get_or_load(
        ("runtime-logs", str(path), category, limit, MAX_READ_BYTES, secret_revision),
        lambda: file_revision(path),
        lambda: _read_logs_uncached(path, category, selected, limit, secrets),
    )


def _read_logs_uncached(path, category, selected, limit, secrets) -> dict:
    result = {"category": category, "date": selected, "timezone": "UTC", "entries": [], "truncated": False, "limit": limit, "available": False}
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            offset = max(0, size - MAX_READ_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_READ_BYTES)
    except FileNotFoundError:
        return result
    result["available"] = True
    if offset:
        raw = raw.partition(b"\n")[2]
    # Ignore an incomplete final record while another thread is appending.
    if raw and not raw.endswith(b"\n"):
        raw = raw.rpartition(b"\n")[0] + b"\n"
    entries = []
    for index, line in enumerate(raw.decode("utf-8", errors="replace").splitlines()):
        try:
            fields = next(csv.reader(io.StringIO(line), delimiter="\t"))
        except (csv.Error, StopIteration):
            continue
        if len(fields) != 6 or fields[0] == "time" or fields[1] not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            continue
        timestamp, level, module, action, detail, elapsed = fields
        query = is_query_log(module, action, detail)
        if category != "terminal" and query != (category == "query"):
            continue
        safe = [redact_log_text(value, secrets) for value in (module, action, detail, elapsed)]
        entries.append({"id": f"{selected}:{offset}:{index}", "time": redact_log_text(timestamp, secrets), "level": level,
                        "module": safe[0], "action": safe[1], "detail": safe[2], "elapsed_ms": safe[3]})
    result["truncated"] = bool(offset or len(entries) > limit)
    result["entries"] = entries[-limit:]
    return result
