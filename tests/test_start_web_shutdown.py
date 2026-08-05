"""Web 入口信号关闭行为测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import start_web


def _run_main_with(exception: BaseException, *, should_exit: bool) -> tuple[int, Mock]:
    application = SimpleNamespace(state=SimpleNamespace())
    server = Mock()
    server.should_exit = should_exit
    server.run.side_effect = exception
    with (
        patch("start_web.create_app", return_value=application),
        patch("start_web.uvicorn.Config", return_value=object()),
        patch("start_web.uvicorn.Server", return_value=server),
        patch("start_web.write_runtime_state"),
        patch("start_web.remove_runtime_state") as remove_runtime_state,
    ):
        result = start_web.main(["--host", "127.0.0.1", "--port", "8765"])
    return result, remove_runtime_state


def test_ctrl_c_exits_cleanly_and_removes_runtime_state() -> None:
    with patch.object(start_web.UVICORN_LOGGER, "info") as log_info:
        result, remove_runtime_state = _run_main_with(
            KeyboardInterrupt(), should_exit=False
        )

    assert result == 0
    remove_runtime_state.assert_called_once()
    log_info.assert_called_once_with(
        "已收到 Ctrl+C，kemo-graph Web 服务已安全停止。"
    )


def test_cancelled_error_is_clean_only_after_server_started_exiting() -> None:
    result, remove_runtime_state = _run_main_with(
        asyncio.CancelledError(), should_exit=True
    )
    assert result == 0
    remove_runtime_state.assert_called_once()


def test_unexpected_cancelled_error_is_not_hidden() -> None:
    application = SimpleNamespace(state=SimpleNamespace())
    server = Mock(should_exit=False)
    server.run.side_effect = asyncio.CancelledError()
    with (
        patch("start_web.create_app", return_value=application),
        patch("start_web.uvicorn.Config", return_value=object()),
        patch("start_web.uvicorn.Server", return_value=server),
        patch("start_web.write_runtime_state"),
        patch("start_web.remove_runtime_state") as remove_runtime_state,
        pytest.raises(asyncio.CancelledError),
    ):
        start_web.main([])
    remove_runtime_state.assert_called_once()


def test_real_server_error_is_not_hidden() -> None:
    application = SimpleNamespace(state=SimpleNamespace())
    server = Mock(should_exit=False)
    server.run.side_effect = RuntimeError("port unavailable")
    with (
        patch("start_web.create_app", return_value=application),
        patch("start_web.uvicorn.Config", return_value=object()),
        patch("start_web.uvicorn.Server", return_value=server),
        patch("start_web.write_runtime_state"),
        patch("start_web.remove_runtime_state") as remove_runtime_state,
        pytest.raises(RuntimeError, match="port unavailable"),
    ):
        start_web.main([])
    remove_runtime_state.assert_called_once()
