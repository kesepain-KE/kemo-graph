from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict

class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ErrorDetail(BaseModel):
    code: str
    message: str
    result: dict[str, Any] | None = None

class APIResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: ErrorDetail | None = None
