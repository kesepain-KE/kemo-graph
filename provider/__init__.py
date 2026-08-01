"""Provider 公共 HTTP 调用和统一错误类型。"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import httpx

from core.config import KemoConfig


DEFAULT_TIMEOUT_SECONDS = 60.0
KEMO_PROTOCOL_VERSION = "1.0"


class ProviderError(RuntimeError):
    """所有外部模型服务错误的基类。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderConfigurationError(ProviderError):
    """Provider 配置或密钥缺失。"""


class ProviderTimeoutError(ProviderError):
    """Provider 请求超时。"""


class ProviderAuthenticationError(ProviderError):
    """Provider 鉴权失败。"""


class ProviderRequestError(ProviderError):
    """Provider 网络请求失败。"""


class ProviderResponseError(ProviderError):
    """Provider 返回 HTTP 错误或响应格式无效。"""


def kemo_headers(api_key: str) -> dict[str, str]:
    """生成使用同一请求 ID 的 kemo 1.0 标准请求头。"""

    if not isinstance(api_key, str) or not api_key.strip():
        raise ProviderConfigurationError("kemo API 密钥不能为空", provider="kemo")
    request_id = str(uuid4())
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "X-Kemo-Protocol-Version": KEMO_PROTOCOL_VERSION,
        "X-Request-ID": request_id,
        "Idempotency-Key": request_id,
        "Content-Type": "application/json",
    }


def get_api_key(api_config: KemoConfig) -> str:
    """优先读取配置文件密钥，否则回退到配置指定的环境变量。"""

    explicit_api_key = api_config.api_key.strip()
    if explicit_api_key:
        return explicit_api_key

    environment_name = api_config.api_key_env.strip()
    api_key = os.getenv(environment_name, "").strip() if environment_name else ""
    if not api_key:
        raise ProviderConfigurationError(
            f"未配置 kemo.api_key 或 API 密钥环境变量：{environment_name or '<空>'}",
            provider="kemo",
        )
    return api_key


def build_endpoint_url(base_url: str, endpoint: str) -> str:
    """兼容传入 API 根地址或已经包含端点的完整地址。"""

    normalized_base = base_url.rstrip("/")
    normalized_endpoint = endpoint.strip("/")
    if normalized_base.lower().endswith(f"/{normalized_endpoint.lower()}"):
        return normalized_base
    return f"{normalized_base}/{normalized_endpoint}"


def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> Any:
    """执行 JSON HTTP 请求，并转换为统一的 Provider 错误。"""

    owns_client = client is None
    active_client = client or httpx.Client(timeout=timeout)
    try:
        try:
            response = active_client.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{provider} 请求超时",
                provider=provider,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderRequestError(
                f"{provider} 网络请求失败：{exc}",
                provider=provider,
            ) from exc

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                f"{provider} API 鉴权失败",
                provider=provider,
                status_code=response.status_code,
            )
        if response.is_error:
            detail = response.text.strip().replace("\n", " ")[:500]
            suffix = f"：{detail}" if detail else ""
            raise ProviderResponseError(
                f"{provider} API 返回 HTTP {response.status_code}{suffix}",
                provider=provider,
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                f"{provider} API 返回的内容不是合法 JSON",
                provider=provider,
                status_code=response.status_code,
            ) from exc
    finally:
        if owns_client:
            active_client.close()


__all__ = [
    "KEMO_PROTOCOL_VERSION",
    "ProviderAuthenticationError",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "build_endpoint_url",
    "get_api_key",
    "kemo_headers",
    "request_json",
]
