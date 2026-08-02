from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from restart import _pid_exists, read_runtime_state, remove_runtime_state, write_runtime_state
from start_web import create_app


def _app(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    return create_app(
        config_path=config,
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "markdown",
    )


def test_runtime_state_round_trip_and_pid_safe_cleanup(tmp_path: Path) -> None:
    state_path = tmp_path / "update" / "runtime" / "web-runtime.json"
    write_runtime_state(
        pid=123,
        command=["python", "start_web.py", "--port", "8000"],
        cwd=tmp_path,
        host="127.0.0.1",
        port=8000,
        path=state_path,
    )
    state = read_runtime_state(state_path)
    assert state["pid"] == 123
    assert state["command"][-1] == "8000"

    remove_runtime_state(999, state_path)
    assert state_path.exists()
    remove_runtime_state(123, state_path)
    assert not state_path.exists()


def test_pid_probe_distinguishes_live_and_missing_processes() -> None:
    assert _pid_exists(os.getpid()) is True
    assert _pid_exists(2_147_483_647) is False


def test_restart_endpoint_requires_supervised_start(tmp_path: Path) -> None:
    application = _app(tmp_path)
    with TestClient(application) as client:
        runtime = client.get("/api/v1/system/runtime")
        restart = client.post(
            "/api/v1/system/restart", json={"confirm": "restart"}
        )
    assert runtime.status_code == 200
    assert runtime.json()["data"]["restart_available"] is False
    assert restart.status_code == 503
    assert restart.json()["error"]["code"] == "RESTART_UNAVAILABLE"


def test_restart_endpoint_schedules_helper_then_requests_graceful_exit(
    tmp_path: Path,
) -> None:
    application = _app(tmp_path)
    shutdown_requested = threading.Event()
    application.state.kemo_restart_command = ["python", "start_web.py", "--port", "8000"]
    application.state.kemo_restart_cwd = tmp_path
    application.state.kemo_request_shutdown = shutdown_requested.set
    scheduled = {
        "restart_id": "restart-test",
        "old_pid": os.getpid(),
        "status": "scheduled",
    }

    with patch("start_web.schedule_restart", return_value=scheduled) as restart_helper:
        with TestClient(application) as client:
            response = client.post(
                "/api/v1/system/restart", json={"confirm": "restart"}
            )
            assert shutdown_requested.wait(timeout=2)

    assert response.status_code == 200
    assert response.json()["data"]["old_pid"] == os.getpid()
    restart_helper.assert_called_once_with(
        target_pid=os.getpid(),
        command=application.state.kemo_restart_command,
        cwd=tmp_path,
    )
