from __future__ import annotations

from pathlib import Path
from typing import Any


from core.settings.schema import AppConfig, DEFAULT_CONFIG_PATH
from core.settings.writer import write_config

def default_config_dict() -> dict[str, Any]:
    """返回一份可安全修改的默认配置字典。"""

    return AppConfig().model_dump(mode="json")


def write_default_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Path:
    """将完整默认配置写入指定路径。"""

    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_config(path, AppConfig())
    return path
