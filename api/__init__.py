"""可独立启动或嵌入 Web 入口的 kemo-graph 外部 API。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from core.jobs import MaintenanceJobManager

from .deps import create_context, create_service
from .errors import install_exception_handlers
from .routes import router


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
    jobs = MaintenanceJobManager(
        lambda: create_service(context),
        data_dir=context.data_dir,
        settings=context.settings,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        try:
            yield
        finally:
            jobs.stop()

    app = FastAPI(title="kemo-graph API", version="1.0.0", lifespan=lifespan)
    app.state.kemo_context = context
    app.state.kemo_job_manager = jobs
    install_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]
