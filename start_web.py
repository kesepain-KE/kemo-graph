"""kemo-graph FastAPI Web 入口。"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from collections.abc import Sequence
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.deps import create_context, create_service
from api.errors import install_exception_handlers
from api.routes import router
from core.scheduler import MaintenanceScheduler


LOGGER = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parent / "web" / "frontend" / "dist"


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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    application = FastAPI(
        title="kemo-graph Web",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.kemo_context = context
    application.state.kemo_scheduler = scheduler
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
    _mount_frontend(application)
    return application


app = create_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 kemo-graph Web API")
    parser.add_argument("--host", default=os.getenv("KEMO_GRAPH_WEB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("KEMO_GRAPH_WEB_PORT", "8000")),
    )
    parser.add_argument("--data-dir")
    parser.add_argument("--external-dir")
    parser.add_argument("--config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    application = create_app(
        config_path=args.config,
        data_dir=args.data_dir,
        external_dir=args.external_dir,
    )
    uvicorn.run(application, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
