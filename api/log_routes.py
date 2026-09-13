"""Read-only application logs for the runtime status page."""

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from core.config import load_config
from core.log_viewer import read_logs
from .deps import RuntimeContext, get_context
from .errors import success_response
from .schemas import APIResponse

router = APIRouter()


@router.get("/system/logs", response_model=APIResponse)
def get_system_logs(
    context: Annotated[RuntimeContext, Depends(get_context)],
    category: Literal["terminal", "query", "internal"] = "internal",
    date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    return success_response(read_logs(load_config(context.config_path), category=category, log_date=date, limit=limit))
