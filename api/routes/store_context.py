from __future__ import annotations
from dataclasses import asdict
from typing import Any, Callable
from core.service.facade import KnowledgeBaseService
from core.settings.loader import load_config
from core.service.federation import create_store_service
from core.storage.portable.manifest import load_store_manifest
from api.deps import RuntimeContext


def _store_operation(
    store_root: str,
    context: RuntimeContext,
    operation: Callable[[KnowledgeBaseService], Any],
) -> dict[str, Any]:
    """创建严格绑定的 Store 服务，并让每次响应都携带稳定身份。"""

    settings = load_config(context.config_path)
    manifest = load_store_manifest(store_root, settings=settings)
    service = create_store_service(
        store_root,
        settings=settings,
        config_path=context.config_path,
    )
    return {
        "store": asdict(manifest),
        "result": operation(service),
    }
