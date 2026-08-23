"""kemo-graph FastAPI Web 入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from collections.abc import Sequence
from pathlib import Path
from typing import AsyncIterator

try:
    import uvicorn
    from dotenv import load_dotenv
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from api.deps import create_context, create_service
    from api.errors import install_exception_handlers, success_response
    from api.routes import _is_loopback_host, router
    from api.schemas import RestartRequest
    from core.scheduler import MaintenanceScheduler
    from core.jobs import MaintenanceJobManager
    from update import ApplicationUpdater, read_local_version
    from restart import (
        RestartPermissionError,
        RestartUnavailableError,
        remove_runtime_state,
        schedule_restart,
        write_runtime_state,
    )
except ModuleNotFoundError as exc:
    if __name__ == "__main__":
        missing = exc.name or "未知模块"
        print(f"kemo-graph Web 启动失败：缺少 Python 依赖 {missing!r}。", file=sys.stderr)
        print(
            f"请运行：{sys.executable} -m pip install -r "
            f'"{Path(__file__).resolve().parent / "requirements.txt"}"',
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    raise

PROJECT_ROOT = Path(__file__).resolve().parent
# 使用脚本所在项目的环境文件；显式 shell 环境变量仍保持更高优先级。
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


LOGGER = logging.getLogger(__name__)
UVICORN_LOGGER = logging.getLogger("uvicorn.error")
FRONTEND_DIST = PROJECT_ROOT / "web" / "frontend" / "dist"
RUNTIME_HEALTH_PATH = "/api/v1/system/runtime"


def _port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1 到 65535 之间")
    return port


def _probe_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _listener_is_active(host: str, port: int) -> bool:
    try:
        with socket.create_connection((_probe_host(host), port), timeout=0.5):
            return True
    except OSError:
        return False


def _is_kemo_graph_service(host: str, port: int) -> bool:
    probe_host = _probe_host(host)
    display_host = f"[{probe_host}]" if ":" in probe_host else probe_host
    request = urllib.request.Request(
        f"http://{display_host}:{port}{RUNTIME_HEALTH_PATH}",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False
    data = payload.get("data")
    return isinstance(data, dict) and isinstance(data.get("version"), str)


def _service_url(host: str, port: int) -> str:
    display_host = _probe_host(host)
    if ":" in display_host:
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}"


def _mount_frontend(
    application: FastAPI,
    frontend_dist: Path | None = None,
) -> None:
    """挂载构建产物，并为 React Router 提供 SPA 回退。"""

    frontend_dist = frontend_dist or FRONTEND_DIST
    index_path = frontend_dist / "index.html"
    assets_path = frontend_dist / "assets"
    if not frontend_dist.is_dir() or not index_path.is_file():
        LOGGER.warning(
            "前端构建目录不存在，Web API 将继续启动：%s；请先运行 npm run build",
            frontend_dist,
        )
        return

    if assets_path.is_dir():
        # Windows 的注册表可能把 .js 映射成 text/plain，ES Module 会因此被浏览器拒绝。
        mimetypes.add_type("application/javascript", ".js")
        mimetypes.add_type("text/css", ".css")
        mimetypes.add_type("application/wasm", ".wasm")
        application.mount(
            "/assets",
            StaticFiles(directory=assets_path),
            name="frontend-assets",
        )
    else:
        LOGGER.warning("前端 assets 目录不存在：%s", assets_path)

    @application.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        requested_path = (frontend_dist / full_path).resolve()
        try:
            requested_path.relative_to(frontend_dist.resolve())
        except ValueError:
            requested_path = index_path
        if requested_path.is_file():
            return FileResponse(requested_path)
        return FileResponse(index_path)


def create_app(
    *,
    config_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> FastAPI:
    context = create_context(
        config_path=config_path,
        data_dir=data_dir,
        external_dir=external_dir,
    )
    scheduler = MaintenanceScheduler(lambda: create_service(context))
    updater = ApplicationUpdater()
    job_manager = MaintenanceJobManager(
        lambda: create_service(context),
        data_dir=context.data_dir,
        settings=context.settings,
        updater_factory=lambda: updater,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        job_manager.start()
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()
            job_manager.stop()

    application = FastAPI(
        title="kemo-graph Web",
        version=read_local_version(),
        lifespan=lifespan,
    )
    application.state.kemo_context = context
    application.state.kemo_scheduler = scheduler
    application.state.kemo_job_manager = job_manager
    application.state.kemo_updater = updater
    application.state.kemo_restart_pending = False
    install_exception_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://127.0.0.1",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router, prefix="/api/v1")

    @application.get("/api/v1/system/runtime", include_in_schema=True)
    def get_web_runtime() -> dict:
        return success_response(
            {
                "pid": os.getpid(),
                "restart_available": callable(
                    getattr(application.state, "kemo_request_shutdown", None)
                ),
                "restart_pending": bool(application.state.kemo_restart_pending),
                "version": read_local_version(),
            }
        )

    @application.post("/api/v1/system/restart", include_in_schema=True)
    def restart_web_service(payload: RestartRequest, request: Request) -> dict:
        del payload  # Literal["restart"] 已由 Pydantic 完成二次确认校验。
        host = request.client.host if request.client is not None else ""
        if not _is_loopback_host(host):
            raise RestartPermissionError("底层重启只允许从本机访问")
        if application.state.kemo_restart_pending:
            raise RestartUnavailableError("重启已在执行，请等待服务重新上线")
        active_jobs = job_manager.list(limit=1, status="running") + job_manager.list(
            limit=1, status="queued"
        )
        if active_jobs:
            raise RestartUnavailableError(
                "仍有后台任务正在运行或排队，请等待任务结束后再重启"
            )
        if scheduler.is_executing:
            raise RestartUnavailableError("每日维护任务正在执行，请稍后再重启")
        command = getattr(application.state, "kemo_restart_command", None)
        cwd = getattr(application.state, "kemo_restart_cwd", None)
        shutdown = getattr(application.state, "kemo_request_shutdown", None)
        if not command or cwd is None or not callable(shutdown):
            raise RestartUnavailableError(
                "当前实例不受重启守护器管理；请使用 python start_web.py 启动"
            )

        result = schedule_restart(
            target_pid=os.getpid(),
            command=command,
            cwd=cwd,
        )
        application.state.kemo_restart_pending = True
        timer = threading.Timer(0.35, shutdown)
        timer.daemon = True
        timer.start()
        return success_response(
            {
                **result,
                "message": "旧进程将完整退出，新 Python 进程会在端口释放后启动",
            }
        )

    _mount_frontend(application)
    return application


app = create_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 kemo-graph Web API")
    parser.add_argument("--host", default=os.getenv("KEMO_GRAPH_WEB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=_port_number,
        default=os.getenv("KEMO_GRAPH_WEB_PORT", "8000"),
    )
    parser.add_argument("--data-dir")
    parser.add_argument("--external-dir")
    parser.add_argument("--config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_arguments)
    service_url = _service_url(args.host, args.port)
    if _listener_is_active(args.host, args.port):
        if _is_kemo_graph_service(args.host, args.port):
            print(f"kemo-graph Web 已在运行：{service_url}", flush=True)
            return 0
        print(
            f"kemo-graph Web 启动失败：{args.host}:{args.port} 已被其他程序占用。",
            file=sys.stderr,
            flush=True,
        )
        return 2

    application = create_app(
        config_path=args.config,
        data_dir=args.data_dir,
        external_dir=args.external_dir,
    )
    command = [sys.executable, str(Path(__file__).resolve()), *raw_arguments]
    application.state.kemo_restart_command = command
    application.state.kemo_restart_cwd = Path(__file__).resolve().parent
    server = uvicorn.Server(
        uvicorn.Config(application, host=args.host, port=args.port)
    )
    application.state.kemo_request_shutdown = lambda: setattr(
        server, "should_exit", True
    )
    write_runtime_state(
        pid=os.getpid(),
        command=command,
        cwd=Path(__file__).resolve().parent,
        host=args.host,
        port=args.port,
    )
    print(f"kemo-graph Web 正在启动：{service_url}", flush=True)
    try:
        try:
            server.run()
        except KeyboardInterrupt:
            UVICORN_LOGGER.info("已收到 Ctrl+C，kemo-graph Web 服务已安全停止。")
        except asyncio.CancelledError:
            # 部分 Python/uvicorn 组合会在正常信号关闭后直接抛出
            # CancelledError；仅当服务器已进入退出状态时将其视为正常关闭。
            if not server.should_exit:
                raise
            UVICORN_LOGGER.info("kemo-graph Web 服务已安全停止。")
        return 0
    finally:
        remove_runtime_state(os.getpid())


def _run_cli() -> int:
    try:
        return main()
    except Exception as exc:
        print(
            f"kemo-graph Web 启动失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        print(
            "请检查配置、文件权限和依赖；依赖可使用 "
            f'`{sys.executable} -m pip check` 验证。',
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run_cli())
