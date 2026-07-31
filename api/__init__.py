"""可独立启动或嵌入 Web 入口的 kemo-graph 外部 API。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .deps import create_context
from .errors import install_exception_handlers
from .routes import router


def create_app(
    *,
    config_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> FastAPI:
    app = FastAPI(title="kemo-graph API", version="1.0.0")
    app.state.kemo_context = create_context(
        config_path=config_path,
        data_dir=data_dir,
        external_dir=external_dir,
    )
    install_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]
