"""Gracefully restart a running ``python start_web.py`` process.

The old server asks a detached helper to wait for its PID to disappear.  The
helper then launches a fresh Python interpreter with the exact original Web
command.  This is a process-level restart, not a module reload.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

import httpx

from update import migrate_legacy_runtime


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_STATE_PATH = PROJECT_ROOT / "update" / "runtime" / "web-runtime.json"
RESTART_LOG_PATH = PROJECT_ROOT / "update" / "runtime" / "restart.log"


class RestartError(RuntimeError):
    """Base process restart error."""


class RestartPermissionError(RestartError):
    """A non-local caller attempted to restart the process."""


class RestartUnavailableError(RestartError):
    """The Web process was not started in restartable supervisor mode."""


def write_runtime_state(
    *,
    pid: int,
    command: Sequence[str],
    cwd: Path | str,
    host: str,
    port: int,
    path: Path = RUNTIME_STATE_PATH,
) -> dict[str, Any]:
    payload = {
        "pid": int(pid),
        "command": [str(item) for item in command],
        "cwd": str(Path(cwd).resolve()),
        "host": str(host),
        "port": int(port),
        "started_at": _now_iso(),
    }
    _write_json_atomic(path, payload)
    return payload


def read_runtime_state(path: Path = RUNTIME_STATE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        command = payload["command"]
        cwd = Path(payload["cwd"]).resolve()
        port = int(payload["port"])
        if pid <= 0 or not isinstance(command, list) or not command:
            raise ValueError("运行信息不完整")
        if any(not isinstance(item, str) or not item for item in command):
            raise ValueError("启动命令无效")
        if not cwd.is_dir() or not 1 <= port <= 65535:
            raise ValueError("工作目录或端口无效")
        return {
            **payload,
            "pid": pid,
            "command": command,
            "cwd": str(cwd),
            "port": port,
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RestartUnavailableError(
            "没有可重启的 Web 运行实例；请使用 python start_web.py 启动"
        ) from exc


def remove_runtime_state(pid: int, path: Path = RUNTIME_STATE_PATH) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("pid", -1)) == int(pid):
            path.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


def schedule_restart(
    *,
    target_pid: int,
    command: Sequence[str],
    cwd: Path | str,
) -> dict[str, Any]:
    """Start a detached helper which relaunches only after ``target_pid`` exits."""

    if int(target_pid) <= 0 or not command:
        raise RestartError("重启目标 PID 或启动命令无效")
    resolved_cwd = Path(cwd).resolve()
    if not resolved_cwd.is_dir():
        raise RestartError(f"重启工作目录不存在：{resolved_cwd}")
    restart_id = str(uuid4())
    helper_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--helper",
        "--pid",
        str(int(target_pid)),
        "--cwd",
        str(resolved_cwd),
        "--command-json",
        json.dumps([str(item) for item in command], ensure_ascii=False),
        "--restart-id",
        restart_id,
    ]
    try:
        subprocess.Popen(
            helper_command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **_detached_options(),
        )
    except OSError as exc:
        raise RestartError(f"无法启动重启守护进程：{exc}") from exc
    return {
        "restart_id": restart_id,
        "old_pid": int(target_pid),
        "status": "scheduled",
    }


def request_running_server_restart(
    *,
    state_path: Path = RUNTIME_STATE_PATH,
    timeout: float = 10.0,
) -> dict[str, Any]:
    runtime = read_runtime_state(state_path)
    if not _pid_exists(runtime["pid"]):
        raise RestartUnavailableError("记录的 Web 进程已不存在，请重新启动服务")
    host = str(runtime.get("host") or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"http://{host}:{runtime['port']}/api/v1/system/restart"
    try:
        response = httpx.post(
            url,
            json={"confirm": "restart"},
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "kemo-graph-restart"},
        )
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RestartUnavailableError(f"无法连接正在运行的 Web 服务：{exc}") from exc
    if not response.is_success or not payload.get("ok"):
        message = (payload.get("error") or {}).get("message") or f"HTTP {response.status_code}"
        raise RestartError(str(message))
    return payload["data"]


def _run_helper(
    *,
    target_pid: int,
    command: Sequence[str],
    cwd: Path,
    restart_id: str,
    timeout: float = 90.0,
) -> int:
    _append_log(restart_id, f"等待旧进程退出 pid={target_pid}")
    deadline = time.monotonic() + timeout
    while _pid_exists(target_pid):
        if time.monotonic() >= deadline:
            _append_log(restart_id, "等待旧进程退出超时")
            return 2
        time.sleep(0.2)

    # Windows occasionally releases the listening socket a fraction later than the PID.
    time.sleep(0.35)
    RESTART_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with RESTART_LOG_PATH.open("ab") as log_file:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **_detached_options(),
            )
        _append_log(restart_id, f"新进程已启动 pid={process.pid}")
        return 0
    except OSError as exc:
        _append_log(restart_id, f"新进程启动失败：{exc}")
        return 3


def _detached_options() -> dict[str, Any]:
    if os.name == "nt":
        return {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        }
    return {"start_new_session": True}


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(pid),
        )
        if not handle:
            # Access denied still means the PID exists; invalid/not-found means it does not.
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _append_log(restart_id: str, message: str) -> None:
    RESTART_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_now_iso()}\t{restart_id}\t{message}\n"
    with RESTART_LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(line)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="底层重启 kemo-graph Web 服务")
    parser.add_argument("--helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--cwd", help=argparse.SUPPRESS)
    parser.add_argument("--command-json", help=argparse.SUPPRESS)
    parser.add_argument("--restart-id", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    migrate_legacy_runtime(PROJECT_ROOT)
    args = build_parser().parse_args(argv)
    try:
        if args.helper:
            if not all((args.pid, args.cwd, args.command_json, args.restart_id)):
                raise RestartError("重启守护进程参数不完整")
            command = json.loads(args.command_json)
            if not isinstance(command, list) or not all(
                isinstance(item, str) and item for item in command
            ):
                raise RestartError("重启启动命令无效")
            return _run_helper(
                target_pid=args.pid,
                command=command,
                cwd=Path(args.cwd).resolve(),
                restart_id=args.restart_id,
            )
        result = request_running_server_restart()
        print(json.dumps({"ok": True, "data": result, "error": None}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "data": None,
                    "error": {"code": "RESTART_FAILED", "message": str(exc)},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RestartError",
    "RestartPermissionError",
    "RestartUnavailableError",
    "read_runtime_state",
    "remove_runtime_state",
    "request_running_server_restart",
    "schedule_restart",
    "write_runtime_state",
]
