from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any
from core.read_cache import READ_CACHE, file_revision
import yaml
from pydantic import ValidationError


from .schema import AppConfig, DEFAULT_CONFIG_PATH, _migrate_legacy_provider_config
from .errors import ConfigLoadError
from .defaults import default_config_dict, write_default_config
from .writer import write_config as write_config

LOGGER = logging.getLogger(__name__)

def load_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> AppConfig:
    """安全读取 YAML 配置，缺失字段兜底，非法字段回退并记录警告。"""

    path = Path(config_path)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ConfigLoadError(f"配置文件必须使用 .yaml 或 .yml：{path}")
    def read_text():
        return READ_CACHE.get_or_load(
            ("config-text", str(path.resolve())), lambda: file_revision(path),
            lambda: path.read_text(encoding="utf-8"),
        )

    text = read_text() if path.exists() else ""
    if not text.strip():
        write_default_config(path)
        LOGGER.warning("已创建默认配置，请检查并修改 %s", path)
        text = read_text()

    try:
        raw_config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
        raise ConfigLoadError(f"配置文件不是合法 YAML：{path}{position}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigLoadError(f"配置文件根节点必须是 YAML 映射：{path}")

    defaults = default_config_dict()
    merged = _deep_merge(defaults, _migrate_legacy_provider_config(raw_config))
    return _validate_with_fallback(merged, defaults)


def _validate_with_fallback(
    values: dict[str, Any], defaults: dict[str, Any]
) -> AppConfig:
    candidate = deepcopy(values)
    while True:
        try:
            return AppConfig.model_validate(candidate)
        except ValidationError as exc:
            changed = False
            for error in exc.errors():
                location = tuple(part for part in error["loc"] if isinstance(part, str))
                if not location:
                    continue
                default_value = _get_nested(defaults, location)
                _set_nested(candidate, location, deepcopy(default_value))
                LOGGER.warning(
                    "配置项 %s 非法，已使用默认值 %r",
                    ".".join(location),
                    default_value,
                )
                changed = True
            if not changed:
                raise ConfigLoadError(f"配置校验失败：{exc}") from exc


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_nested(values: dict[str, Any], location: tuple[str, ...]) -> Any:
    current: Any = values
    for part in location:
        current = current[part]
    return current


def _set_nested(values: dict[str, Any], location: tuple[str, ...], value: Any) -> None:
    current: dict[str, Any] = values
    for part in location[:-1]:
        current = current.setdefault(part, {})
    current[location[-1]] = value
