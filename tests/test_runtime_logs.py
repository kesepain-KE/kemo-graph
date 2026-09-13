import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core.config import AppConfig
from core.logger import DailyTSVLogger, redact_log_text
from core.log_viewer import read_logs
from core.query_logging import trace_query
from core.terminal_logging import TerminalLogHandler, capture_terminal_logs


@pytest.fixture
def settings(tmp_path):
    return AppConfig(log_dir=str(tmp_path / "log"), kemo={"api_key": "configured-secret", "api_key_env": "KEMO_TEST_LOG_KEY"})


def test_reads_separate_categories_and_redacts_historical_logs(settings, monkeypatch):
    monkeypatch.setenv("KEMO_TEST_LOG_KEY", "environment-secret")
    logger = DailyTSVLogger(settings.resolve_log_dir())
    logger.log("kb", "query_complete", 'configured-secret environment-secret "api_key": "old secret with spaces"')
    logger.log("ingestor", "document_import", "done")
    logger.log("rag_engine", "embedding_request", "purpose=query, count=3")
    logger.log("kb", "search_cache_clear", "deleted=2")
    query = read_logs(settings, category="query")
    assert [entry["action"] for entry in query["entries"]] == ["query_complete", "embedding_request"]
    text = json.dumps(query)
    assert "configured-secret" not in text
    assert "environment-secret" not in text
    assert "old secret" not in text
    assert "REDACTED" in text
    internal = read_logs(settings, category="internal")
    assert [entry["action"] for entry in internal["entries"]] == ["document_import", "search_cache_clear"]
    assert read_logs(settings, category="terminal")["available"] is False


def test_limit_tail_and_partial_lines(settings, monkeypatch):
    logger = DailyTSVLogger(settings.resolve_log_dir())
    for i in range(30):
        path = logger.log("kb", "query_complete", "x" * 50 + str(i))
    with path.open("ab") as stream:
        stream.write(b"12:30:00\tINFO\tkb\tquery_complete\tincomplete")
    monkeypatch.setattr("core.log_viewer.MAX_READ_BYTES", 600)
    result = read_logs(settings, category="query", limit=3)
    assert len(result["entries"]) == 3
    assert result["entries"][-1]["detail"].endswith("29")
    assert result["truncated"] is True
    assert "incomplete" not in str(result)


@pytest.mark.parametrize("date", ["../config/config", "2026-02-30", "2026-1-01"])
def test_rejects_non_date_paths(settings, date):
    with pytest.raises(ValueError):
        read_logs(settings, log_date=date)


def test_terminal_mirror_is_restored_and_does_not_duplicate_or_capture_viewer_poll(settings):
    stdout, stderr = sys.stdout, sys.stderr
    logger = logging.getLogger("uvicorn")
    previous_level = logger.level
    previous_handlers = list(logger.handlers)
    logger.setLevel(logging.INFO)
    try:
        with capture_terminal_logs(settings):
            with capture_terminal_logs(settings):
                logger.info("service ready configured-secret")
                logger.info("GET /api/v1/system/logs?category=terminal")
        result = read_logs(settings, category="terminal")
        assert len(result["entries"]) == 1
        assert "configured-secret" not in str(result)
        assert logger.handlers == previous_handlers
        assert sys.stdout is stdout and sys.stderr is stderr
    finally:
        logger.setLevel(previous_level)


def test_terminal_handler_follows_runtime_config_and_environment(tmp_path, monkeypatch):
    first_log = tmp_path / "first-log"
    second_log = tmp_path / "second-log"
    config = tmp_path / "config.json"
    initial = AppConfig(
        log_dir=str(first_log),
        log_level="INFO",
        kemo={"api_key": "first-secret", "api_key_env": "KEMO_DYNAMIC_LOG_KEY"},
    )
    config.write_text(initial.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("KEMO_DYNAMIC_LOG_KEY", "first-environment-secret")
    handler = TerminalLogHandler(initial, config_path=config)
    handler.emit(logging.makeLogRecord({"name": "test", "levelno": logging.INFO, "msg": "first-secret first-environment-secret"}))

    updated = initial.model_copy(
        update={
            "log_dir": str(second_log),
            "log_level": "DEBUG",
            "kemo": initial.kemo.model_copy(update={"api_key": "second-secret"}),
        }
    )
    config.write_text(updated.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("KEMO_DYNAMIC_LOG_KEY", "second-environment-secret")
    handler.emit(logging.makeLogRecord({"name": "test", "levelno": logging.DEBUG, "msg": "second-secret second-environment-secret"}))

    second = read_logs(updated, category="terminal")
    assert second["entries"][-1]["level"] == "DEBUG"
    assert "second-secret" not in str(second)
    assert "second-environment-secret" not in str(second)
    assert handler.sink.log_dir == (second_log / "terminal").resolve()


def test_terminal_handler_keeps_last_good_sink_when_config_is_invalid(tmp_path):
    settings = AppConfig(log_dir=str(tmp_path / "stable-log"))
    config = tmp_path / "config.json"
    config.write_text(settings.model_dump_json(), encoding="utf-8")
    handler = TerminalLogHandler(settings, config_path=config)
    stable_sink = handler.sink.log_dir
    config.write_text("{not-json", encoding="utf-8")

    handler.emit(logging.makeLogRecord({"name": "test", "levelno": logging.INFO, "msg": "still works"}))

    assert handler.sink.log_dir == stable_sink
    assert read_logs(settings, category="terminal")["entries"][-1]["detail"] == "still works"


def test_query_lifecycle_preserves_results_errors_and_omits_question():
    owner = SimpleNamespace(_log_event=Mock())
    service = SimpleNamespace(owner=owner)
    @trace_query
    def execute(self, mode, query, params, callback, *, force):
        return callback()
    assert execute(service, "hybrid", "private question", {}, lambda: {"value": 1}, force=False) == {"value": 1}
    assert [call.args[0] for call in owner._log_event.call_args_list] == ["query_start", "query_complete"]
    assert "private question" not in str(owner._log_event.call_args_list)
    def fail():
        raise RuntimeError("private failure body")
    with pytest.raises(RuntimeError):
        execute(service, "rag", "private question", {}, fail, force=False)
    assert owner._log_event.call_args.args[0] == "query_failed"
    assert "private failure body" not in str(owner._log_event.call_args_list)


def test_api_uses_configured_log_dir_and_validates_inputs(settings, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(settings.model_dump_json(), encoding="utf-8")
    app = create_app(config_path=config, data_dir=tmp_path / "data", external_dir=tmp_path / "markdown")
    DailyTSVLogger(settings.resolve_log_dir()).log("kb", "query_complete", "done")
    with TestClient(app) as client:
        response = client.get("/api/v1/system/logs?category=query&limit=1")
        assert response.status_code == 200
        assert response.json()["data"]["entries"][0]["action"] == "query_complete"
        assert client.get("/api/v1/system/logs?category=unknown").status_code == 422
        assert client.get("/api/v1/system/logs?date=../config.json").status_code == 422
        assert client.get("/api/v1/system/logs?limit=100000").status_code == 422
        assert client.get("/api/v1/system/logs?date=2026-02-30").status_code in (400, 422)
        assert client.get("/api/v1/system/logs?date=2000-01-01").json()["data"]["entries"] == []


def test_secret_formats_are_sanitized():
    text = redact_log_text('Authorization: Bearer abcdef; password="long password" token=12345 {"api_key":"secret"}')
    for secret in ("abcdef", "long password", "12345", '"secret"'):
        assert secret not in text
