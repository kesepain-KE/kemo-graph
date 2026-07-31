"""项目配置模型、默认值和加载逻辑。"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class ConfigLoadError(RuntimeError):
    """配置文件无法解析时抛出的错误。"""


class EntityExtractionConfig(BaseModel):
    """实体抽取配置。"""

    model_config = ConfigDict(extra="ignore")

    method: str = "llm"
    max_entities: int = Field(default=10, ge=1)

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        if value not in {"llm", "rule"}:
            raise ValueError("method 必须为 llm 或 rule")
        return value


class KemoConfig(BaseModel):
    """kemo-adapter-api 网关协议配置。"""

    model_config = ConfigDict(extra="ignore")

    base_url: str = "http://127.0.0.1:7531"
    api_key_env: str = "KEMO_API_KEY"
    protocol_version: str = "1.0"
    request_timeout: int = Field(default=900, ge=60, le=3600)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("kemo.protocol_version 仅支持 1.0")
        return value


class ModelSelectionConfig(BaseModel):
    """统一 Kemo 网关下各项能力使用的模型。"""

    model_config = ConfigDict(extra="ignore")

    llm: str = "deepseek-deepseek-v4-flash"
    embedding: str = "siliconflow-Qwen-Qwen3-VL-Embedding-8B"
    embedding_dimensions: int = Field(default=4096, ge=1)
    rerank: str = "siliconflow-Qwen-Qwen3-VL-Reranker-8B"

    @field_validator("llm", "embedding", "rerank")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("模型名称不能为空")
        return normalized


class AppConfig(BaseModel):
    """config/config.json 的权威配置模型。"""

    model_config = ConfigDict(extra="ignore")

    max_query_depth: int = Field(default=5, ge=1, le=10)
    default_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    max_documents: int = Field(default=1000, ge=1, le=100000)
    web_node_load_depth: int = Field(default=2, ge=1, le=5)
    web_node_label_threshold: int = Field(default=3, ge=1, le=100)
    summary_trigger_file_count: int = Field(default=5, ge=1, le=1000)
    summary_trigger_time: str = "03:00"
    recycle_life_days: int = Field(default=30, ge=1, le=365)

    rag_similarity_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    default_top_k: int = Field(default=10, ge=1, le=100)
    chunking_mode: str = "fixed"
    chunk_small_size: int = Field(default=128, ge=64, le=2048)
    chunk_size: int = Field(default=512, ge=128, le=4096)
    chunk_large_size: int = Field(default=1024, ge=256, le=8192)
    chunk_overlap: int = Field(default=64, ge=0, le=512)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    graph_tool_max_iterations: int = Field(default=40, ge=1, le=200)
    log_dir: str = "log"
    log_level: str = "INFO"
    kemo: KemoConfig = Field(default_factory=KemoConfig)
    models: ModelSelectionConfig = Field(default_factory=ModelSelectionConfig)

    hybrid_enhancement_factor: float = Field(default=1.2, ge=1.0, le=2.0)
    entity_extraction: EntityExtractionConfig = Field(
        default_factory=EntityExtractionConfig
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_api_sections(cls, value: Any) -> Any:
        """兼容旧三段 API 配置，但不把旧结构保留到配置模型中。"""

        if not isinstance(value, dict):
            return value
        return _migrate_legacy_provider_config(value)

    @field_validator("summary_trigger_time")
    @classmethod
    def validate_summary_time(cls, value: str) -> str:
        try:
            hour_text, minute_text = value.split(":", maxsplit=1)
            hour = int(hour_text)
            minute = int(minute_text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("summary_trigger_time 必须为 HH:MM") from exc
        if len(value) != 5 or not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("summary_trigger_time 必须为有效的 HH:MM")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level 必须为 DEBUG、INFO、WARNING 或 ERROR")
        return value

    @field_validator("chunking_mode")
    @classmethod
    def validate_chunking_mode(cls, value: str) -> str:
        if value not in {"fixed", "hierarchical"}:
            raise ValueError("chunking_mode 必须为 fixed 或 hierarchical")
        return value

    def resolve_data_dir(self, project_root: Path = PROJECT_ROOT) -> Path:
        """解析数据目录；相对环境变量以项目根目录为基准。"""

        return _resolve_runtime_path(
            os.getenv("KEMO_GRAPH_DATA_DIR", "data"), project_root
        )

    def resolve_external_dir(self, project_root: Path = PROJECT_ROOT) -> Path:
        """解析 Markdown 文档目录；相对环境变量以项目根目录为基准。"""

        return _resolve_runtime_path(
            os.getenv("KEMO_GRAPH_EXTERNAL_DIR", "external/markdown"), project_root
        )

    def resolve_log_dir(self, project_root: Path = PROJECT_ROOT) -> Path:
        """解析日志目录；相对路径以项目根目录为基准。"""

        return _resolve_runtime_path(self.log_dir, project_root)


def default_config_dict() -> dict[str, Any]:
    """返回一份可安全修改的默认配置字典。"""

    return AppConfig().model_dump(mode="json")


def write_default_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Path:
    """将完整默认配置写入指定路径。"""

    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, default_config_dict())
    return path


def load_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    env_path: Path | str | None = DEFAULT_ENV_PATH,
) -> AppConfig:
    """读取配置和 .env，缺失字段兜底，非法字段回退并记录警告。"""

    if env_path is not None:
        load_dotenv(dotenv_path=Path(env_path), override=False)

    path = Path(config_path)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        write_default_config(path)
        LOGGER.warning("已创建默认配置，请检查并修改 %s", path)

    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"配置文件不是合法 JSON：{path}: {exc}") from exc

    if not isinstance(raw_config, dict):
        LOGGER.warning("配置文件根节点必须是对象，已使用默认配置：%s", path)
        raw_config = {}

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


def _migrate_legacy_provider_config(values: dict[str, Any]) -> dict[str, Any]:
    """将三套旧 Provider 配置折叠为统一网关和模型选择配置。"""

    migrated = deepcopy(values)
    raw_models = migrated.get("models")
    models = deepcopy(raw_models) if isinstance(raw_models, dict) else {}
    mappings = (
        ("llm_api", "model", "llm"),
        ("embedding_api", "model", "embedding"),
        ("embedding_api", "dimensions", "embedding_dimensions"),
        ("rerank_api", "model", "rerank"),
    )
    for section_name, old_key, new_key in mappings:
        section = migrated.get(section_name)
        if new_key not in models and isinstance(section, dict) and old_key in section:
            models[new_key] = section[old_key]
    if models:
        migrated["models"] = models
    return migrated


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


def _resolve_runtime_path(value: str, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
