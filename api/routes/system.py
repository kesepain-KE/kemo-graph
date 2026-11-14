from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from provider import ProviderAuthenticationError, ProviderConfigurationError, ProviderError, ProviderTimeoutError
from provider.model_catalog import list_models
from update import UpdateBlockedError, UpdatePermissionError
from api.errors import error_payload, success_response
from api.schemas import APIResponse, ConfigSaveRequest, UpdateApplyRequest

from api.routes.dependencies import Service, Jobs, Updater

router = APIRouter()

status_router = APIRouter()

@status_router.get("/status", response_model=APIResponse)
def get_status(service: Service) -> dict:
    return success_response(service.status())

@router.get("/config", response_model=APIResponse)
def get_config(service: Service) -> dict:
    return success_response(service.get_config())

@router.put("/config", response_model=APIResponse)
def put_config(payload: ConfigSaveRequest, service: Service) -> dict:
    return success_response(service.save_config(payload.model_dump()))

@router.get("/config/models", response_model=APIResponse)
def get_model_catalog(service: Service, response: Response) -> dict | JSONResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        models = list_models(service.settings)
    except ProviderError as exc:
        status, code, message = (
            502,
            "MODEL_CATALOG_UNAVAILABLE",
            "无法读取上游模型列表，请检查网关地址和模型目录接口后重试。",
        )
        if isinstance(exc, ProviderConfigurationError):
            status, code, message = (
                400,
                "MODEL_CATALOG_NOT_CONFIGURED",
                "请先在「网关连接」中配置并保存 API Key，或设置对应的密钥环境变量。",
            )
        elif isinstance(exc, ProviderAuthenticationError):
            code = "MODEL_CATALOG_AUTH_FAILED"
            message = "网关密钥无效或没有读取模型列表的权限，请检查「网关连接」配置。"
        elif isinstance(exc, ProviderTimeoutError):
            status, code, message = (
                504,
                "MODEL_CATALOG_TIMEOUT",
                "读取上游模型列表超时，请稍后重试。",
            )
        return JSONResponse(
            status_code=status,
            content=error_payload(code, message),
            headers={"Cache-Control": "no-store"},
        )
    return success_response({"models": models})

@router.get("/update/status", response_model=APIResponse)
def get_update_status(updater: Updater) -> dict:
    return success_response(updater.status())

@router.post("/update/check", response_model=APIResponse)
def post_update_check(updater: Updater) -> dict:
    return success_response(updater.check())

@router.post("/update/apply", response_model=APIResponse)
def post_update_apply(
    request: Request,
    updater: Updater,
    jobs: Jobs,
    payload: UpdateApplyRequest | None = None,
    force: Annotated[bool, Query(description="允许同版本强制重新安装")] = False,
) -> dict:
    host = request.client.host if request.client is not None else ""
    if not _is_loopback_host(host):
        raise UpdatePermissionError("应用更新只允许从本机访问")
    force_requested = bool(force or (payload and payload.force))
    status = updater.status()
    same_version_force = bool(force_requested and status.get("force_update_available"))
    if not status.get("update_available") and not same_version_force:
        raise UpdateBlockedError("请先检查更新；当前没有可安装的新版本")
    can_apply = bool(status.get("can_apply")) or bool(
        same_version_force and status.get("can_force_apply")
    )
    if not can_apply:
        reasons = "；".join(status.get("blocking_reasons") or [])
        raise UpdateBlockedError(reasons or "当前安装状态不允许更新")
    if force_requested:
        return success_response(jobs.submit("update", force=True))
    # Preserve the original no-options call shape for existing API adapters
    # and test doubles; MaintenanceJobManager defaults force to False.
    return success_response(jobs.submit("update"))

def _is_loopback_host(host: str) -> bool:
    import ipaddress

    normalized = host.strip().casefold()
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
