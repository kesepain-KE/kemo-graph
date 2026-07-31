"""FastAPI 运行时配置与依赖注入。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, Request

from core.config import AppConfig, DEFAULT_CONFIG_PATH, load_config
from core.knowledge_base import KnowledgeBaseService


@dataclass(frozen=True)
class RuntimeContext:
    settings: AppConfig
    data_dir: Path
    external_dir: Path
    config_path: Path


def create_context(
    *,
    config_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> RuntimeContext:
    selected_config = (
        Path(config_path or os.getenv("KEMO_GRAPH_CONFIG", DEFAULT_CONFIG_PATH))
        .expanduser()
        .resolve()
    )
    settings = load_config(selected_config)
    selected_data_dir = (
        Path(data_dir).expanduser().resolve()
        if data_dir is not None
        else settings.resolve_data_dir()
    )
    selected_external_dir = (
        Path(external_dir).expanduser().resolve()
        if external_dir is not None
        else settings.resolve_external_dir()
    )
    return RuntimeContext(
        settings=settings,
        data_dir=selected_data_dir,
        external_dir=selected_external_dir,
        config_path=selected_config,
    )


def get_context(request: Request) -> RuntimeContext:
    return request.app.state.kemo_context


def create_service(context: RuntimeContext) -> KnowledgeBaseService:
    """使用运行时目录和最新配置创建知识库服务。"""

    return KnowledgeBaseService(
        settings=load_config(context.config_path),
        data_dir=context.data_dir,
        external_dir=context.external_dir,
        config_path=context.config_path,
    )


def get_service(
    context: RuntimeContext = Depends(get_context),
) -> KnowledgeBaseService:
    return create_service(context)
