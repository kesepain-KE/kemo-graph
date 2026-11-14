"""读取当前网关密钥可访问的 Kemo 模型目录。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from core.config import AppConfig
from . import (
    ProviderResponseError,
    build_endpoint_url,
    get_api_key,
    kemo_headers,
    request_json,
)


class CatalogModel(BaseModel):
    id: str = Field(min_length=1)
    provider_id: str
    task: Literal["llm", "embedding", "rerank", "unknown"]


class ModelCatalog(BaseModel):
    data: list[CatalogModel]


def list_models(settings: AppConfig) -> list[dict[str, str]]:
    """只发现模型，不发送推理请求；凭据始终留在服务端。"""

    payload = request_json(
        "GET",
        build_endpoint_url(settings.kemo.base_url, "model/models"),
        provider="kemo",
        headers=kemo_headers(get_api_key(settings.kemo)),
        timeout=min(settings.kemo.request_timeout, 20),
    )
    try:
        catalog = ModelCatalog.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError("Kemo 模型目录格式无效", provider="kemo") from exc
    return [model.model_dump() for model in catalog.data]
