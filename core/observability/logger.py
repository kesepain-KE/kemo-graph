from __future__ import annotations
import csv
import io
import logging
import re
import shutil
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LEVEL_RANK = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}

LOG_COLUMNS = ("time", "level", "module", "action", "detail", "elapsed_ms")

_MAX_DETAIL_LENGTH = 1000

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(api[_ -]?key|authorization|access[_ -]?token|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)

_QUOTED_SECRET = re.compile(
    r'''(?i)(["']?\b(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|token|secret|password)["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&]+)'''
)

def redact_log_text(value: Any, secrets: tuple[str, ...] = (), limit: int = 2000) -> str:
    """Safe plain-text output for persisted console logs and the log viewer."""
    text = str(value)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = _SENSITIVE_PATTERNS[0].sub("Bearer [REDACTED]", text)
    text = _QUOTED_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", text)
    return _single_line(text, limit)

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
                        writer.writerow(LOG_COLUMNS)
                    writer.writerow(
                        [
                            now.strftime("%H:%M:%S"),
                            record_level,
                            _single_line(module, 80),
                            _single_line(action, 80),
                            _sanitize_detail(detail),
                            _single_line(elapsed_ms, 80),
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
    value = redact_log_text(detail, limit=_MAX_DETAIL_LENGTH * 2)
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

class TSVFormatter(logging.Formatter):
    """与持久化日志使用相同的 TSV 字段和脱敏规则。"""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def _fields(self, record: logging.LogRecord) -> tuple[str, ...]:
        return (
            datetime.fromtimestamp(record.created, timezone.utc).strftime("%H:%M:%S"),
            "ERROR" if record.levelno >= logging.ERROR else record.levelname,
            _single_line(record.name, 80),
            _single_line(getattr(record, "action", "console"), 80),
            _sanitize_detail(redact_log_text(super().format(record), self.secrets)),
            _single_line(getattr(record, "elapsed_ms", "-"), 80),
        )

    def format(self, record: logging.LogRecord) -> str:
        output = io.StringIO(newline="")
        csv.writer(output, delimiter="\t", lineterminator="\n").writerow(self._fields(record))
        return output.getvalue().rstrip("\n")


def _display_width(text: str) -> int:
    return sum(
        0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in text
    )


def _wrap_console_text(text: str, width: int) -> list[str]:
    lines = []
    line = ""
    line_width = 0
    for char in text:
        char_width = _display_width(char)
        if line and line_width + char_width > width:
            lines.append(line)
            line = ""
            line_width = 0
        line += char
        line_width += char_width
    return [*lines, line]


def _console_row(fields: tuple[str, ...] | list[str], widths: tuple[int, ...]) -> str:
    cells = []
    for text, width in zip(fields, widths):
        if _display_width(text) > width:
            text = _wrap_console_text(text, width - 1)[0] + "…"
        cells.append(text + " " * (width - _display_width(text)))
    return " │ ".join(cells)


class ConsoleTableFormatter(TSVFormatter):
    """终端使用对齐、折行的可读表格；持久化 TSV 格式保持不变。"""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__(secrets)
        self._last_widths: tuple[int, ...] | None = None

    def format(self, record: logging.LogRecord) -> str:
        terminal_width = shutil.get_terminal_size(fallback=(140, 24)).columns
        widths = (8, 7, 22, max(20, terminal_width - 46))
        timestamp, level, module, action, detail, elapsed = self._fields(record)
        module = module.removeprefix("core.")
        if module == "uvicorn.error":
            module = "uvicorn"
        if action != "console":
            detail = f"[{action}] {detail}"
        if elapsed != "-":
            detail = f"{detail} ({elapsed} ms)"
        fields = [timestamp, level, module, detail]

        rows = []
        if widths != self._last_widths:
            rows.append(_console_row(("时间 UTC", "级别", "来源", "详情"), widths))
            rows.append("─┼─".join("─" * width for width in widths))
            self._last_widths = widths
        for index, detail in enumerate(_wrap_console_text(detail, widths[3])):
            cells = fields.copy() if index == 0 else [""] * len(fields)
            cells[3] = detail
            rows.append(_console_row(cells, widths))
        return "\n".join(rows)


class ConsoleAccessFilter(logging.Filter):
    """仅在控制台隐藏成功的轮询和静态资源请求，不影响落盘日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name != "uvicorn.access"
            or record.levelno >= logging.WARNING
            or not isinstance(record.args, tuple)
            or len(record.args) != 5
        ):
            return True
        _, method, target, _, status = record.args
        if method not in ("GET", "HEAD") or not isinstance(status, int):
            return True
        if not (200 <= status < 300 or status == 304):
            return True
        path = str(target).partition("?")[0]
        return not (
            path in (
                "/api/v1/jobs",
                "/api/v1/system/runtime",
                "/api/v1/system/logs",
                "/kemo-graph-logo.png",
            )
            or path.startswith(("/api/v1/query/progress/", "/assets/"))
        )


class TSVStreamHandler(logging.StreamHandler):
    """每个终端流写入一次表头，后续逐条写入表格行。"""

    def __init__(self, stream=None) -> None:
        super().__init__(stream)
        self._needs_header = True

    def emit(self, record: logging.LogRecord) -> None:
        if self._needs_header:
            self.stream.write("\t".join(LOG_COLUMNS) + "\n")
            self._needs_header = False
        super().emit(record)
