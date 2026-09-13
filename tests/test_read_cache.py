"""Read-cache bounds, concurrency, cross-store isolation and disk invalidation."""

from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import threading
from unittest.mock import Mock, patch

import pytest

from core.config import AppConfig, load_config
from core.db import connect_sources, initialize_databases
from core.knowledge_base import KnowledgeBaseService
from core.logger import DailyTSVLogger
from core.log_viewer import read_logs
from core.read_cache import MemoryReadCache, READ_CACHE, database_revision, file_revision
from core.search_cache import cache_key_lock, compute_state_hash
import core.search_cache as search_cache


@pytest.fixture(autouse=True)
def clean_cache():
    READ_CACHE.clear()
    yield
    READ_CACHE.clear()


def test_cache_returns_isolated_values_and_expires_without_sliding_ttl():
    now = [0.0]
    cache = MemoryReadCache(clock=lambda: now[0])
    loader = Mock(return_value={"nested": [1]})
    first = cache.get_or_load("x", lambda: 1, loader, ttl=10)
    first["nested"].append(2)
    now[0] = 9
    assert cache.get_or_load("x", lambda: 1, loader, ttl=10) == {"nested": [1]}
    assert loader.call_count == 1
    now[0] = 10
    cache.get_or_load("x", lambda: 1, loader, ttl=10)
    assert loader.call_count == 2


def test_cache_enforces_lru_bytes_entries_and_oversized_bypass():
    cache = MemoryReadCache(max_bytes=20, max_entries=2, max_item_bytes=15)
    for key in ("a", "b"):
        cache.get_or_load(key, lambda: 1, lambda: "12345")
    cache.get_or_load("a", lambda: 1, lambda: "unexpected")
    cache.get_or_load("c", lambda: 1, lambda: "12345")
    assert list(cache._entries) == ["a", "c"]
    assert cache._bytes <= 20
    large = Mock(return_value="x" * 100)
    for _ in range(2):
        cache.get_or_load("large", lambda: 1, large)
    assert large.call_count == 2
    cache.clear()
    assert cache._bytes == 0 and not cache._entries


def test_concurrent_readers_share_one_load():
    cache = MemoryReadCache()
    started, release = threading.Event(), threading.Event()
    def load():
        started.set()
        assert release.wait(5)
        return {"value": 1}
    loader = Mock(side_effect=load)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.get_or_load, "x", lambda: 1, loader) for _ in range(8)]
        assert started.wait(5)
        release.set()
        assert all(future.result(timeout=5) == {"value": 1} for future in futures)
    assert loader.call_count == 1


def test_changes_during_read_and_errors_are_not_cached():
    revision = [0]
    cache = MemoryReadCache()
    def changed():
        revision[0] += 1
        return "unstable"
    loader = Mock(side_effect=changed)
    for _ in range(2):
        cache.get_or_load("x", lambda: revision[0], loader)
    assert loader.call_count == 2 and not cache._entries
    loader = Mock(side_effect=[RuntimeError("failed"), "ok"])
    with pytest.raises(RuntimeError):
        cache.get_or_load("error", lambda: 1, loader)
    assert cache.get_or_load("error", lambda: 1, loader) == "ok"


def test_clear_during_load_does_not_repopulate():
    cache = MemoryReadCache()
    def load():
        cache.clear()
        return "value"
    assert cache.get_or_load("x", lambda: 1, load) == "value"
    assert not cache._entries


def test_sqlite_commit_with_restored_mtime_invalidates_and_handles_do_not_block_replace(tmp_path):
    path = tmp_path / "data.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE example (value TEXT)")
    connection.execute("INSERT INTO example VALUES ('before')")
    connection.commit()
    previous = database_revision(path)
    stat = path.stat()
    connection.execute("UPDATE example SET value='after!'")
    connection.commit()
    connection.close()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert database_revision(path) != previous
    moved = tmp_path / "replaced.db"
    os.replace(path, moved)
    assert database_revision(path) != previous


def test_wal_bypasses_cache_instead_of_serving_stale_data(tmp_path):
    path = tmp_path / "wal.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE example (value INTEGER)")
        connection.execute("INSERT INTO example VALUES (1)")
        connection.commit()
        cache = MemoryReadCache()
        assert database_revision(path) is None
        read = lambda: connection.execute("SELECT value FROM example").fetchone()[0]
        assert cache.get_or_load("wal", lambda: database_revision(path), read) == 1
        connection.execute("UPDATE example SET value=2")
        connection.commit()
        assert cache.get_or_load("wal", lambda: database_revision(path), read) == 2
        assert not cache._entries
    finally:
        connection.close()


def seed_source(paths):
    connection = connect_sources(paths)
    try:
        connection.execute("""INSERT INTO sources (source_id,original_path,relative_path,path_hash,content_hash)
                              VALUES ('s1','note.md','note.md','path','hash')""")
        connection.commit()
    finally:
        connection.close()


def test_state_fingerprint_scans_once_until_database_changes(tmp_path):
    settings = AppConfig()
    paths = initialize_databases(tmp_path / "data", settings)
    seed_source(paths)
    with patch.object(search_cache, "_compute_state_hash_uncached", wraps=search_cache._compute_state_hash_uncached) as scan:
        before = compute_state_hash(paths, settings)
        for _ in range(9):
            assert compute_state_hash(paths, settings) == before
        assert scan.call_count == 1
        connection = connect_sources(paths)
        connection.execute("UPDATE sources SET relative_path='renamed.md'")
        connection.commit()
        connection.close()
        assert compute_state_hash(paths, settings) != before
        assert scan.call_count == 2


def test_documents_and_health_share_reads_across_service_instances_and_invalidate(tmp_path):
    settings = AppConfig()
    data_dir = tmp_path / "data"
    paths = initialize_databases(data_dir, settings)
    seed_source(paths)
    service = KnowledgeBaseService(settings=settings, data_dir=data_dir, external_dir=tmp_path / "docs")
    another = KnowledgeBaseService(settings=settings, data_dir=data_dir, external_dir=tmp_path / "docs")
    assert service.list_documents()["pagination"]["total"] == 1
    assert service.status()["sources"]["active"] == 1
    with patch.object(another, "_read_document_page", side_effect=AssertionError("disk reread")), patch.object(another, "_read_status", side_effect=AssertionError("health reread")):
        assert another.list_documents()["pagination"]["total"] == 1
        assert another.status()["sources"]["active"] == 1
    connection = connect_sources(paths)
    connection.execute("UPDATE sources SET exists_status='deleted'")
    connection.commit()
    connection.close()
    assert service.list_documents("active")["documents"] == []
    assert another.status()["sources"]["active"] == 0
    other_paths = initialize_databases(tmp_path / "other", settings)
    other = KnowledgeBaseService(settings=settings, data_dir=other_paths.data_dir, external_dir=tmp_path / "other-docs")
    assert other.list_documents()["pagination"]["total"] == 0
    with pytest.raises(ValueError):
        another.list_documents(page=True)


def test_logs_reuse_reads_and_invalidate_append_and_secret_changes(tmp_path, monkeypatch):
    import core.log_viewer as log_viewer
    settings = AppConfig(log_dir=str(tmp_path / "log"), kemo={"api_key_env": "KEMO_CACHE_TEST_KEY"})
    logger = DailyTSVLogger(settings.resolve_log_dir())
    logger.log("test", "event", "sensitive-value")
    with patch.object(log_viewer, "_read_logs_uncached", wraps=log_viewer._read_logs_uncached) as read:
        for _ in range(10):
            assert len(read_logs(settings)["entries"]) == 1
        assert read.call_count == 1
        monkeypatch.setenv("KEMO_CACHE_TEST_KEY", "sensitive-value")
        assert "sensitive-value" not in str(read_logs(settings))
        assert read.call_count == 2
        logger.log("test", "event", "next")
        assert len(read_logs(settings)["entries"]) == 2
        assert read.call_count == 3


def test_config_and_file_replacement_invalidate_without_mutable_sharing(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"default_top_k": 7}', encoding="utf-8")
    first = load_config(path, env_path=None)
    first.default_top_k = 9
    assert load_config(path, env_path=None).default_top_k == 7
    previous = file_revision(path)
    replacement = tmp_path / "new.json"
    replacement.write_text('{"default_top_k": 8}', encoding="utf-8")
    os.replace(replacement, path)
    assert file_revision(path) != previous
    assert load_config(path, env_path=None).default_top_k == 8


def test_query_locks_are_reclaimed_including_nested_and_error_paths(tmp_path):
    from core.db import get_database_paths
    paths = get_database_paths(tmp_path)
    for index in range(100):
        with cache_key_lock(paths, str(index)):
            with cache_key_lock(paths, str(index)):
                assert len(search_cache._KEY_LOCKS) == 1
    assert not search_cache._KEY_LOCKS
    with pytest.raises(RuntimeError):
        with cache_key_lock(paths, "failure"):
            raise RuntimeError("failure")
    assert not search_cache._KEY_LOCKS
