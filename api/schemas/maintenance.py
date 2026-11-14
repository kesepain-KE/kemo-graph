from __future__ import annotations
from typing import Literal
from pydantic import ConfigDict

from api.schemas.common import StrictRequest
class ConfigSaveRequest(StrictRequest):
    """接受完整配置 JSON 的请求体，字段由 AppConfig 模型校验。"""
    model_config = ConfigDict(extra="allow")

class OrganizeGraphRequest(StrictRequest):
    use_llm: bool = True
    summarize: bool = True

class RestartRequest(StrictRequest):
    confirm: Literal["restart"]

class UpdateApplyRequest(StrictRequest):
    """应用更新请求；``force`` 允许同版本重新安装当前远端提交。"""

    force: bool = False
