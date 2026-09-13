"""Mirror Python/Uvicorn logs without redirecting process stdout or stderr.

The Web settings page can change the log directory, level, or explicit Kemo
key while a process is running.  The terminal handler therefore treats the
configuration file as a small, revisioned source of truth instead of freezing
the startup values forever.
"""

from contextlib import contextmanager
import logging
import os
from pathlib import Path
from threading import RLock

from .config import load_config
from .logger import DailyTSVLogger, redact_log_text
from .read_cache import file_revision


class TerminalLogHandler(logging.Handler):
    """A resilient terminal mirror that follows runtime configuration changes."""

    def __init__(self, settings, *, config_path: Path | str | None = None):
        # Keep the handler open to all records.  DailyTSVLogger applies the
        # current configured level, so a DEBUG record can trigger a refresh
        # after the level is changed from INFO to DEBUG.
        super().__init__(logging.NOTSET)
        self._state_lock = RLock()
        self._refreshing = False
        self._settings = settings
        self._config_path = (
            Path(config_path).expanduser().resolve() if config_path is not None else None
        )
        self._config_revision = self._safe_revision(self._config_path)
        self._environment_name = ""
        self._environment_value = ""
        self.sink = None
        self.sink_key = ""
        self.secrets: tuple[str, ...] = ()
        self._apply_settings(settings)

    @staticmethod
    def _safe_revision(path: Path | None) -> tuple | None:
        if path is None:
            return None
        try:
            return file_revision(path)
        except OSError:
            return None

    @staticmethod
    def _secret_values(settings) -> tuple[str, ...]:
        configured = getattr(getattr(settings, "kemo", None), "api_key", "") or ""
        environment_name = getattr(getattr(settings, "kemo", None), "api_key_env", "") or ""
        environment = os.getenv(environment_name, "") if environment_name else ""
        return tuple(dict.fromkeys(value for value in (configured, environment) if value))

    def _apply_settings(self, settings) -> None:
        with self._state_lock:
            self._settings = settings
            self.sink = DailyTSVLogger(
                settings.resolve_log_dir() / "terminal",
                settings.log_level,
            )
            self.sink_key = str(self.sink.log_dir)
            self._environment_name = getattr(
                getattr(settings, "kemo", None), "api_key_env", ""
            ) or ""
            self._environment_value = os.getenv(self._environment_name, "")
            self.secrets = self._secret_values(settings)

    def _refresh_if_needed(self) -> None:
        """Refresh sink/config cheaply and never let config errors break logging."""

        with self._state_lock:
            if self._refreshing:
                return
            revision = self._safe_revision(self._config_path)
            current_environment_name = getattr(
                getattr(self._settings, "kemo", None), "api_key_env", ""
            ) or ""
            current_environment_value = os.getenv(current_environment_name, "")
            config_changed = (
                self._config_path is not None and revision != self._config_revision
            )
            environment_changed = (
                current_environment_name != self._environment_name
                or current_environment_value != self._environment_value
            )
            if not config_changed and not environment_changed:
                return

            if config_changed:
                self._refreshing = True
                try:
                    # load_config may emit validation warnings through Python
                    # logging.  The _refreshing guard prevents recursion.
                    refreshed = load_config(self._config_path)
                except Exception:
                    # Keep the last known-good sink and secrets.  Record the
                    # revision so a malformed file is not reparsed for every
                    # incoming log record; a later edit gets a new revision.
                    refreshed = None
                finally:
                    self._config_revision = revision
                    self._refreshing = False
                if refreshed is not None:
                    self._apply_settings(refreshed)
                    return

            # Environment variables can change independently of the JSON
            # file.  Update only the redaction set when no valid new config was
            # loaded, preserving the active log destination and level.
            self._environment_name = current_environment_name
            self._environment_value = current_environment_value
            self.secrets = self._secret_values(self._settings)

    def log(
        self,
        module: str,
        action: str,
        detail="-",
        elapsed_ms="-",
        level: str = "INFO",
    ):
        """Compatibility proxy for the DailyTSVLogger yielded by the context."""

        try:
            self._refresh_if_needed()
            with self._state_lock:
                sink = self.sink
                secrets = self.secrets
            return sink.log(
                module,
                action,
                redact_log_text(detail, secrets),
                elapsed_ms,
                level,
            )
        except Exception:
            return None

    def emit(self, record):
        try:
            self._refresh_if_needed()
            with self._state_lock:
                sink = self.sink
                sink_key = self.sink_key
                secrets = self.secrets
            # Uvicorn propagation differs by launcher. Deduplicate records for
            # the same sink even when root and child loggers both see them.
            seen = getattr(record, "_kemo_terminal_sinks", set())
            if sink_key in seen:
                return
            seen.add(sink_key)
            record._kemo_terminal_sinks = seen
            # Polling the viewer must not create an endless log feedback loop.
            if any(
                path in record.getMessage()
                for path in ("/api/v1/system/logs", "/api/v1/query/progress/")
            ):
                return
            detail = self.format(record)
            level = (
                "ERROR"
                if record.levelno >= logging.ERROR
                else "WARNING"
                if record.levelno >= logging.WARNING
                else "INFO"
                if record.levelno >= logging.INFO
                else "DEBUG"
            )
            sink.log(record.name, "console", redact_log_text(detail, secrets), level=level)
        except Exception:
            # Disk/formatter/config failures must never break the server or
            # recurse through logging's own error handler.
            return


@contextmanager
def capture_terminal_logs(settings, *, config_path: Path | str | None = None):
    handler = TerminalLogHandler(settings, config_path=config_path)
    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
    ]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        # Yield the handler proxy rather than a frozen sink so shutdown and
        # explicit lifecycle messages use the latest runtime configuration.
        yield handler
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        handler.close()
