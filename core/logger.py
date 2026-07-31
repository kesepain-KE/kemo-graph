"""按 UTC 日期滚动写入 TSV 文件的轻量日志器。"""

from __future__ import annotations

import csv
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LEVEL_RANK = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}
_HEADER = ["time", "level", "module", "action", "detail", "elapsed_ms"]
_MAX_DETAIL_LENGTH = 1000
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(api[_ -]?key|authorization|access[_ -]?token|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)


class DailyTSVLogger:
    """线程安全地写入 ``log/YYYY-MM-DD.tsv``。"""

    def __init__(self, log_dir: Path | str, level: str = "INFO") -> None:
        self.log_dir = Path(log_dir).expanduser().resolve()
        self.level = _normalize_level(level)
        self._lock = threading.RLock()

    def log(
        self,
        module: str,
        action: str,
        detail: Any = "-",
        elapsed_ms: str | int | float = "-",
        level: str = "INFO",
    ) -> Path | None:
        """写入一条日志；低于配置级别时跳过并返回 ``None``。"""

        record_level = _normalize_level(level)
        if _LEVEL_RANK[record_level] < _LEVEL_RANK[self.level]:
            return None

        now = datetime.now(timezone.utc)
        path = self._get_path(now)
        try:
            with self._lock:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                needs_header = not path.exists() or path.stat().st_size == 0
                with path.open("a", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream, delimiter="\t")
                    if needs_header:
                        writer.writerow(_HEADER)
                    writer.writerow(
                        [
                            now.strftime("%H:%M:%S"),
                            record_level,
                            _single_line(module, 80),
                            _single_line(action, 80),
                            _sanitize_detail(detail),
                            str(elapsed_ms),
                        ]
                    )
        except OSError:
            return None
        return path

    def _get_path(self, now: datetime | None = None) -> Path:
        timestamp = now or datetime.now(timezone.utc)
        return self.log_dir / f"{timestamp.strftime('%Y-%m-%d')}.tsv"


def _normalize_level(level: str) -> str:
    if not isinstance(level, str):
        raise TypeError("日志级别必须是字符串")
    normalized = level.upper()
    if normalized not in _LEVEL_RANK:
        raise ValueError("日志级别必须为 DEBUG、INFO、WARNING 或 ERROR")
    return normalized


def _sanitize_detail(detail: Any) -> str:
    value = _single_line(detail, _MAX_DETAIL_LENGTH * 2)
    value = _SENSITIVE_PATTERNS[0].sub("Bearer [REDACTED]", value)
    value = _SENSITIVE_PATTERNS[1].sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    if len(value) > _MAX_DETAIL_LENGTH:
        value = value[: _MAX_DETAIL_LENGTH - 1] + "…"
    return value or "-"


def _single_line(value: Any, limit: int) -> str:
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")[:limit]
