"""Real FTS5/simple search and graph-inspector persistence boundaries."""

import yaml
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core.settings.schema import AppConfig
from core.storage.connections import connect_graph
from core.storage.schema import initialize_databases
from core.service.facade import KnowledgeBaseService
from core.documents.errors import DocumentContentConflictError
from core.retrieval.cache.store import SearchCache
from core.retrieval.cache.errors import SearchCacheCleanupError, SearchCacheError


@pytest.fixture
def service(tmp_path):
    settings = AppConfig(log_dir=str(tmp_path / "log"), models={"embedding_dimensions": 3})
    service = KnowledgeBaseService(
        settings=settings, data_dir=tmp_path / "data", external_dir=tmp_path / "external/markdown",
    )
    initialize_databases(service.data_dir, settings)
    connection = connect_graph(service.paths)
    try:
        with connection:
            connection.executemany(
                "INSERT INTO nodes(node_id,keyword,summary,aliases,tags,ref_count) VALUES(?,?,?,?,?,?)",
                [
                    ("a", "知识图谱", "SQLite 支持本地全文搜索，处理实体关系。", '["语义网络"]', '["检索"]', 4),
                    ("b", "构建流程", "知识图谱与 SQLite 索引。", "[]", "[]", 1),
                    ("c", "向量服务", "独立模型部署。", "[]", '["语义检索"]', 1),
                ],
            )
            connection.execute("INSERT INTO groups(group_id,summary,node_count,edge_count) VALUES('g','图谱检索总结',2,1)")
            connection.execute("INSERT INTO group_nodes(group_id,node_id) VALUES('g','a')")
    finally:
        connection.close()
    return service


def test_search_backfills_existing_nodes_and_ranks_name_summary_aliases_and_tags(service):
    page = service.graph.search_nodes("知识 图谱", limit=1)
    assert page["engine"] == "sqlite-fts5/simple" and page["total"] == 2
    assert page["nodes"][0]["node_id"] == "a"
    assert service.graph.search_nodes("知识 图谱", limit=1, offset=1)["nodes"][0]["node_id"] == "b"
    for query in ("语义 网络", "SQLite 搜索", "实体 关系", "SQLITE 全文"):
        assert [node["node_id"] for node in service.graph.search_nodes(query)["nodes"]] == ["a"]
    assert service.graph.search_nodes("语义检索")["nodes"][0]["node_id"] == "c"
    assert service.graph.search_nodes('" OR * NOT')["nodes"] == []
    assert service.graph.search_nodes("  ")["total"] == 0
    with pytest.raises(ValueError, match="1000"):
        service.graph.search_nodes("坏\0输入")


@pytest.mark.parametrize("operation", ["node", "relation"])
def test_graph_deletion_cache_failure_preserves_committed_result(service, operation):
    service.graph.search_nodes("知识图谱")
    connection = connect_graph(service.paths)
    try:
        with connection:
            connection.execute(
                "INSERT INTO edges(edge_id,source_node_id,relation,target_node_id) VALUES('e','a','包含','b')"
            )
    finally:
        connection.close()
    with patch.object(SearchCache, "clear", side_effect=SearchCacheError("database locked")):
        with pytest.raises(SearchCacheCleanupError) as caught:
            if operation == "node":
                service.delete_node("a")
            else:
                service.delete_relation("e")
    assert caught.value.result["search_cache_deleted"] is None
    connection = connect_graph(service.paths)
    try:
        assert connection.execute("SELECT 1 FROM edges WHERE edge_id='e'").fetchone() is None
        if operation == "node":
            assert connection.execute("SELECT 1 FROM nodes WHERE node_id='a'").fetchone() is None
        else:
            assert connection.execute("SELECT 1 FROM nodes WHERE node_id='a'").fetchone() is not None
    finally:
        connection.close()


def test_index_follows_committed_mutations_rollback_delete_and_concurrent_readers(service):
    service.graph.search_nodes("知识图谱")
    connection = connect_graph(service.paths)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with connection:
                connection.execute("UPDATE nodes SET summary='不应提交的正文' WHERE node_id='a'")
                raise RuntimeError("rollback")
        with connection:
            connection.execute("UPDATE nodes SET summary='离线数据库工程',aliases='[\"本地检索\"]' WHERE node_id='a'")
        with connection:
            connection.execute("DELETE FROM nodes WHERE node_id='c'")
    finally:
        connection.close()
    assert service.graph.search_nodes("全文 搜索")["total"] == 0
    assert service.graph.search_nodes("语义检索")["total"] == 0
    assert service.graph.search_nodes("不应提交")["total"] == 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda _: service.graph.search_nodes("离线 数据库"), range(8)))
    assert all(page["nodes"][0]["node_id"] == "a" for page in pages)


def test_summary_edit_preserves_originals_and_mappings_checks_conflict_and_invalidates_group(service, tmp_path):
    original = tmp_path / "原始文件.md"
    original.write_text("# 独立样本\n\n原始知识内容", encoding="utf-8")
    imported = service.import_document(original, ingest_after_import=False)
    source_id = imported["source_id"]
    connection = connect_graph(service.paths)
    try:
        with connection:
            connection.execute(
                "INSERT INTO node_sources(node_id,source_id,content_hash,evidence) VALUES('a',?,?,'文档证据')",
                (source_id, imported["content_hash"]),
            )
    finally:
        connection.close()
    detail = service.get_node("a")
    assert detail["sources"][0]["source_path"].endswith("原始文件.md")
    assert detail["sources"][0]["relative_path"] == imported["markdown_relative_path"]
    assert detail["groups"][0]["summary"] == "图谱检索总结"
    original_bytes = original.read_bytes()
    mapping = (service.external_dir / "file_map.json").read_bytes()
    updated = service.graph.update_node_summary("a", "修订后的离线索引方案。", detail["summary"])
    assert updated["summary"] == "修订后的离线索引方案。" and updated["groups"] == []
    assert service.graph.search_nodes("离线 索引")["nodes"][0]["node_id"] == "a"
    with pytest.raises(DocumentContentConflictError):
        service.graph.update_node_summary("a", "错误覆盖", detail["summary"])
    assert service.get_node("a")["summary"] == updated["summary"]
    assert original.read_bytes() == original_bytes
    assert (service.external_dir / "file_map.json").read_bytes() == mapping


def test_http_search_limits_and_summary_conflicts(service, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    client = TestClient(create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir))
    response = client.get("/api/v1/graph/search", params={"query": "知识 图谱", "limit": 1})
    assert response.status_code == 200 and response.json()["data"]["total"] == 2
    for params in ({"query": "x" * 1001}, {"query": "x", "limit": 101}, {"query": "x", "offset": -1}):
        assert client.get("/api/v1/graph/search", params=params).status_code == 422
    previous = service.get_node("a")["summary"]
    assert client.patch("/api/v1/nodes/a", json={"summary": "新的搜索摘要", "expected_summary": previous}).status_code == 200
    assert client.patch("/api/v1/nodes/a", json={"summary": "覆盖", "expected_summary": previous}).status_code == 409
    assert client.patch("/api/v1/nodes/missing", json={"summary": "", "expected_summary": ""}).status_code == 404
