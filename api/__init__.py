"""可独立启动或嵌入 Web 入口的 kemo-graph 外部 API。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from core.jobs import MaintenanceJobManager
from update import ApplicationUpdater, read_local_version

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
    updater = ApplicationUpdater()
    jobs = MaintenanceJobManager(
        lambda: create_service(context),
        data_dir=context.data_dir,
        settings=context.settings,
        updater_factory=lambda: updater,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        try:
            yield
        finally:
            jobs.stop()

    app = FastAPI(
        title="kemo-graph API",
        version=read_local_version(),
        lifespan=lifespan,
    )
    app.state.kemo_context = context
    app.state.kemo_job_manager = jobs
    app.state.kemo_updater = updater
    install_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]
