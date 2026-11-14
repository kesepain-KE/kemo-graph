from __future__ import annotations

import os
from pathlib import Path
import yaml


from .schema import AppConfig
from .errors import ConfigLoadError

def write_config(config_path: Path | str, settings: AppConfig) -> None:
    """以 UTF-8 YAML 原子保存已经校验的配置。"""
    path = Path(config_path)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ConfigLoadError(f"配置文件必须使用 .yaml 或 .yml：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
