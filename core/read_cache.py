"""Bounded process-local read-through cache. No database/file handles are retained."""

from collections import OrderedDict
import json
from pathlib import Path
import threading
from time import monotonic
from typing import Any, Callable, Hashable


def file_revision(path: Path) -> tuple:
    """Cheap identity check including replacement, truncation and nanosecond times."""
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return (str(path.absolute()), "missing")


def files_revision(*paths: Path) -> tuple:
    return tuple(file_revision(path) for path in paths)


def database_revision(path: Path) -> tuple | None:
    """Read only the 100-byte SQLite header, never scan tables to validate a hit.

    WAL commits may not change the main file. Conservatively bypass caching
    while a nonempty WAL/journal exists; do not hold SQLite connections open
    because rebuilds must be able to replace databases on Windows as well.
    """
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-journal")]
    before = files_revision(path, *sidecars)
    if any(len(item) > 2 and item[3] > 0 for item in before[1:]):
        return None
    try:
        with path.open("rb") as stream:
            header = stream.read(100)
    except FileNotFoundError:
        header = b""
    after = files_revision(path, *sidecars)
    if before != after:
        return None
    # The change counter also catches normal commits with restored mtimes.
    return (after, header)


def databases_revision(*paths: Path) -> tuple | None:
    revisions = tuple(database_revision(path) for path in paths)
    return None if any(item is None for item in revisions) else revisions


class MemoryReadCache:
    """LRU with byte/entry limits, hard TTL and striped single-flight locking.

    JSON bytes give predictable payload accounting and a fresh return object
    on every hit, so callers cannot mutate the shared cached value.
    """

    def __init__(self, *, max_bytes: int = 32 * 1024 * 1024, max_entries: int = 256,
                 max_item_bytes: int = 2 * 1024 * 1024, clock=monotonic):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.max_item_bytes = max_item_bytes
        self.clock = clock
        self._entries: OrderedDict = OrderedDict()
        self._bytes = 0
        self._guard = threading.RLock()
        self._stripes = [threading.RLock() for _ in range(32)]
        self._generation = 0

    def clear(self) -> None:
        with self._guard:
            self._generation += 1
            self._entries.clear()
            self._bytes = 0

    def _remove(self, key) -> None:
        item = self._entries.pop(key, None)
        if item is not None:
            self._bytes -= len(item[2])

    def get_or_load(self, key: Hashable, revision: Callable[[], Any],
                    loader: Callable[[], Any], *, ttl: float = 30) -> Any:
        with self._stripes[hash(key) % len(self._stripes)]:
            try:
                before = revision()
            except OSError:
                before = None
            with self._guard:
                generation = self._generation
                now = self.clock()
                for expired in [name for name, value in self._entries.items() if value[1] <= now]:
                    self._remove(expired)
                item = self._entries.get(key)
                if before is not None and item is not None and item[0] == before:
                    self._entries.move_to_end(key)
                    return json.loads(item[2])
                self._remove(key)
            value = loader()  # Exceptions never become cached successes.
            if before is None or ttl <= 0:
                return value
            try:
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")
                after = revision()
            except (OSError, TypeError, ValueError):
                return value
            if before != after or len(encoded) > min(self.max_bytes, self.max_item_bytes):
                return value
            with self._guard:
                if generation != self._generation:
                    return value
                self._entries[key] = (after, self.clock() + ttl, encoded)
                self._bytes += len(encoded)
                while self._entries and (len(self._entries) > self.max_entries or self._bytes > self.max_bytes):
                    self._remove(next(iter(self._entries)))
            return value


# Shared across short-lived API service objects, isolated by absolute store paths.
READ_CACHE = MemoryReadCache()
