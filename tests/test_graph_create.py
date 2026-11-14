"""Graph creation persists evidence and preserves the existing HTTP contract."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from api import create_app
from core.graph.organizer import GraphOrganizer
from core.retrieval import SearchCache, SearchCacheError
from core.service import KnowledgeBaseService
from core.settings import AppConfig
from core.storage import (
    connect_graph, connect_sources, get_knowledge_base_lock,
    initialize_databases, read_graph_meta,
)


@pytest.fixture
def service(tmp_path):
    settings = AppConfig(
        log_dir=str(tmp_path / "log"),
        entity_extraction={"method": "rule"},
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8",
    )
    service = KnowledgeBaseService(
        settings=settings, config_path=config,
        data_dir=tmp_path / "data", external_dir=tmp_path / "markdown",
    )
    initialize_databases(service.data_dir, settings)
    return service


@pytest.fixture
def client(service):
    with TestClient(create_app(
        config_path=service.config_path,
        data_dir=service.data_dir,
        external_dir=service.external_dir,
    )) as client:
        yield client


def test_create_node_persists_evidence_without_merging_same_names(service):
    first = service.graph.create_node(
        "知识图谱", "完整摘要", aliases=["KG", "KG"], tags=["AI"],
    )
    second = service.graph.create_node("知识图谱", "另一个语境")
    connection = connect_graph(service.paths)
    try:
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2
        bindings = connection.execute(
            "SELECT node_id, source_id, content_hash FROM node_sources ORDER BY node_id"
        ).fetchall()
        assert {row["node_id"] for row in bindings} == {first["node_id"], second["node_id"]}
        assert len(bindings) == 2
        assert all(row["source_id"] == row["content_hash"] == "manual" for row in bindings)
    finally:
        connection.close()
    assert first["node_id"] != second["node_id"]
    assert first["summary"] == "完整摘要"
    assert first["aliases"] == ["KG"] and first["tags"] == ["AI"]
    assert first["ref_count"] == first["weight"] == 1


def test_http_node_roundtrip_revision_and_organization_keep_manual_source(client, service):
    before = client.get("/api/v1/graph/visualization/meta").json()["data"]
    response = client.post("/api/v1/nodes", json={
        "keyword": "  星海导航  ", "summary": "完整的手工节点摘要",
        "aliases": ["星海"], "tags": ["导航"],
    })
    assert response.status_code == 200
    node = response.json()["data"]
    assert "node" not in node and node["keyword"] == "星海导航"
    assert node["created_at"] and node["updated_at"]
    assert node["search_cache_deleted"] == 0
    detail = client.get(f"/api/v1/nodes/{node['node_id']}").json()["data"]
    assert detail["summary"] == node["summary"]
    assert detail["sources"][0]["source_id"] == "manual"
    assert detail["sources"][0]["source_path"] is None
    graph = client.get("/api/v1/graph").json()["data"]
    assert [entry["node_id"] for entry in graph["nodes"]] == [node["node_id"]]
    after = client.get("/api/v1/graph/visualization/meta").json()["data"]
    assert after["node_count"] == before["node_count"] + 1
    assert after["revision"] != before["revision"]
    stale_page = client.get(
        "/api/v1/graph/visualization/nodes",
        params={"expected_revision": before["revision"]},
    )
    assert stale_page.status_code == 409
    assert stale_page.json()["error"]["code"] == "GRAPH_CHANGED"
    organized = GraphOrganizer(service.data_dir, settings=service.settings).organize(use_llm=False)
    assert organized["removed_nodes"] == 0
    assert service.get_node(node["node_id"])["ref_count"] == 1


def test_relation_upserts_same_source_and_takes_current_sources_maximum(client, service):
    left = service.graph.create_node("起点节点", "起点")
    right = service.graph.create_node("终点节点", "终点")
    payload = {
        "source_node_id": left["node_id"], "relation": "关联",
        "target_node_id": right["node_id"], "evidence_weight": 0.9,
    }
    responses = []
    for update, count, weight in (
        ({}, 1, 0.9),
        ({"evidence_weight": 0.2, "content_hash": "v2"}, 1, 0.2),
        ({"source_id": "another", "evidence_weight": 0.7}, 2, 0.7),
        ({"source_id": "another", "evidence_weight": 0.7}, 2, 0.7),
    ):
        response = client.post("/api/v1/relations", json={**payload, **update})
        assert response.status_code == 200
        edge = response.json()["data"]
        assert "edge" not in edge
        assert edge["support_count"] == count and edge["weight"] == weight
        responses.append(edge)
    assert len({edge["edge_id"] for edge in responses}) == 1
    detail = client.get(f"/api/v1/relations/{edge['edge_id']}").json()["data"]
    assert detail["path"] == "起点节点->[关联]->终点节点"
    assert len(detail["sources"]) == 2
    assert next(row for row in detail["sources"] if row["source_id"] == "manual")["content_hash"] == "v2"
    connection = connect_graph(service.paths)
    try:
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM edge_sources").fetchone()[0] == 2
    finally:
        connection.close()
    meta = read_graph_meta(service.paths)
    assert (meta["total_nodes"], meta["total_edges"]) == (2, 1)
    assert meta["changed_since_summary"] == 6


def test_missing_endpoint_is_404_and_does_not_leave_partial_evidence(client, service):
    node = service.graph.create_node("有效节点", "摘要")
    before = service.graph.get_visualization_meta()
    response = client.post("/api/v1/relations", json={
        "source_node_id": node["node_id"], "relation": "关联",
        "target_node_id": "missing", "evidence_weight": 0.5,
    })
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    assert service.graph.get_visualization_meta() == before
    connection = connect_graph(service.paths)
    try:
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM edge_sources").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("kind,invalid", [
    ("nodes", {"keyword": "短"}),
    ("nodes", {"keyword": "长" * 21}),
    ("nodes", {"summary": " \n "}),
    ("nodes", {"source_id": " "}),
    ("nodes", {"source_id": "s" * 201}),
    ("nodes", {"content_hash": ""}),
    ("nodes", {"extra": "forbidden"}),
    ("relations", {"relation": " "}),
    ("relations", {"evidence_weight": -0.1}),
    ("relations", {"evidence_weight": 1.1}),
    ("relations", {"evidence_weight": float("nan")}),
    ("relations", {"evidence_weight": float("inf")}),
    ("relations", {"evidence_weight": True}),
    ("relations", {"extra": "forbidden"}),
])
def test_invalid_requests_are_rejected_before_writing(client, service, kind, invalid):
    payload = (
        {"keyword": "有效节点", "summary": "摘要"}
        if kind == "nodes"
        else {"source_node_id": "a", "relation": "关联", "target_node_id": "b", "evidence_weight": 0.5}
    )
    response = client.post(
        f"/api/v1/{kind}", content=json.dumps({**payload, **invalid}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_PARAM"
    assert service.get_full_graph()["nodes"] == []


@pytest.mark.parametrize("kind", ["nodes", "relations"])
def test_creation_refuses_concurrent_writers(client, service, kind):
    acquired, release = threading.Event(), threading.Event()

    def hold_lock():
        with get_knowledge_base_lock(service.data_dir):
            acquired.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_lock)
        assert acquired.wait(5)
        try:
            payload = (
                {"keyword": "有效节点", "summary": "摘要"}
                if kind == "nodes"
                else {"source_node_id": "a", "relation": "关联", "target_node_id": "b", "evidence_weight": 0.5}
            )
            response = client.post(f"/api/v1/{kind}", json=payload)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "CONTENT_CONFLICT"
            assert service.get_full_graph()["nodes"] == []
        finally:
            release.set()
            holder.result(timeout=5)


def test_create_clears_actual_cached_queries_and_returns_new_nodes_and_edges(service):
    before = service.query_graph("Nebula")
    assert before["hit_nodes"] == []
    assert service.query_graph("Nebula") == before
    assert service.list_cached_queries()["items"][0]["hit_count"] == 1
    left = service.graph.create_node("Nebula", "新创建的星云")
    assert left["search_cache_deleted"] == 1
    assert service.list_cached_queries()["total"] == 0
    assert service.query_graph("Nebula")["hit_nodes"][0]["node_id"] == left["node_id"]
    right = service.graph.create_node("Orbit", "轨道")
    assert service.query_graph("Nebula")["edges"] == []
    edge = service.graph.create_relation(left["node_id"], "关联", right["node_id"], 0.8)
    assert edge["search_cache_deleted"] == 1
    assert service.list_cached_queries()["total"] == 0
    assert service.query_graph("Nebula")["edges"][0]["edge_id"] == edge["edge_id"]


@pytest.mark.parametrize("kind", ["nodes", "relations"])
def test_cache_cleanup_failure_reports_the_committed_record(client, service, kind):
    left = service.graph.create_node("起点节点", "起点")
    right = service.graph.create_node("终点节点", "终点")
    payload = (
        {"keyword": "保留节点", "summary": "清缓存失败也已提交"}
        if kind == "nodes"
        else {
            "source_node_id": left["node_id"], "relation": "关联",
            "target_node_id": right["node_id"], "evidence_weight": 0.5,
        }
    )
    with patch.object(SearchCache, "clear", side_effect=SearchCacheError("database locked")):
        response = client.post(f"/api/v1/{kind}", json=payload)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SEARCH_CACHE_CLEANUP_FAILED"
    committed = response.json()["error"]["result"]
    assert committed["search_cache_deleted"] is None
    if kind == "nodes":
        assert service.get_node(committed["node_id"])["summary"] == payload["summary"]
    else:
        assert service.get_relation(committed["edge_id"])["relation"] == payload["relation"]


@pytest.mark.parametrize("state", ["uninitialized", "processing"])
@pytest.mark.parametrize("kind", ["nodes", "relations"])
def test_creation_preserves_initialization_and_processing_guards(client, service, tmp_path, state, kind):
    if state == "uninitialized":
        service.paths.sources_db.unlink()
        expected = (503, "NOT_INITIALIZED")
    else:
        original = tmp_path / "input.txt"
        original.write_text("正在处理的原文件", encoding="utf-8")
        source = service.import_document(original, ingest_after_import=False)
        connection = connect_sources(service.paths)
        try:
            with connection:
                connection.execute(
                    "UPDATE sources SET graph_status='processing' WHERE source_id=?",
                    (source["source_id"],),
                )
        finally:
            connection.close()
        expected = (409, "PROCESSING")
    payload = (
        {"keyword": "有效节点", "summary": "摘要"}
        if kind == "nodes"
        else {"source_node_id": "a", "relation": "关联", "target_node_id": "b", "evidence_weight": 0.5}
    )
    response = client.post(f"/api/v1/{kind}", json=payload)
    assert (response.status_code, response.json()["error"]["code"]) == expected
