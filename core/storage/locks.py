"""同一进程内按知识库目录共享的写锁。"""

from __future__ import annotations

import threading
from pathlib import Path


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def get_knowledge_base_lock(data_dir: Path | str) -> threading.RLock:
    resolved = Path(data_dir).expanduser().resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


__all__ = ["get_knowledge_base_lock"]
