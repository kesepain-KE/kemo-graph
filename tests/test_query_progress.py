from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from api import create_app
from api.deps import get_service
from core.config import AppConfig
from core.query_planner import plan_query
from core.query_progress import QueryProgressStore, QueryTrace, bind_trace, query_step


def test_query_plan_reports_real_steps_and_fallback():
    settings = AppConfig(query_planning={"mode": "llm"})
    trace = QueryTrace()
    with bind_trace(trace):
        plan_query("private question", settings=settings, structured_planner=lambda *_: {
            "intent": "read", "rewrites": [], "subqueries": ["read"], "entities": [],
        })
    assert [item["id"] for item in trace.snapshot()["steps"]] == ["llm", "split"]
    assert "private question" not in str(trace.snapshot())
    trace = QueryTrace()
    with bind_trace(trace):
        plan_query("private question", settings=settings, structured_planner=Mock(side_effect=RuntimeError("test")))
    assert trace.snapshot()["steps"][0]["status"] == "fallback"
    assert trace.snapshot()["steps"][1]["id"] == "normalize"


def test_live_progress_is_available_during_query_and_does_not_change_response(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(AppConfig().model_dump_json(), encoding="utf-8")
    app = create_app(config_path=config, data_dir=tmp_path / "data", external_dir=tmp_path / "docs")
    entered, release = threading.Event(), threading.Event()
    def query(*_, **__):
        with query_step("rerank"):
            entered.set()
            assert release.wait(5)
        return {"query": "test", "results": []}
    service = Mock()
    service.query_rag.side_effect = query
    app.dependency_overrides[get_service] = lambda: service
    key = str(uuid4())
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/v1/query/rag", json={"query": "test"}, headers={"X-Kemo-Progress-Id": key})
        try:
            assert entered.wait(5)
            current = client.get(f"/api/v1/query/progress/{key}").json()["data"]
            assert current["status"] == "running"
            assert current["steps"] == [{"id": "rerank", "label": "重排序", "status": "running"}]
            assert client.get(f"/api/v1/query/progress/{uuid4()}").json()["data"]["available"] is False
        finally:
            release.set()
        assert pending.result(timeout=5).json()["data"] == {"query": "test", "results": []}
        finished = client.get(f"/api/v1/query/progress/{key}").json()["data"]
        assert finished["status"] == "completed"
        assert finished["steps"][0]["status"] == "completed"
        def fail(*_, **__):
            with query_step("embedding"):
                raise ValueError("failed")
        service.query_rag.side_effect = fail
        failed_key = str(uuid4())
        assert client.post("/api/v1/query/rag", json={"query": "test"}, headers={"X-Kemo-Progress-Id": failed_key}).status_code == 422
        failed = client.get(f"/api/v1/query/progress/{failed_key}").json()["data"]
        assert failed["status"] == "failed"
        assert failed["steps"][0]["status"] == "failed"


def test_store_is_bounded_and_traces_are_isolated():
    store = QueryProgressStore(max_entries=2, ttl=10)
    a, b = store.start("a"), store.start("b")
    assert store.start("a") is None
    assert store.start("c") is None
    with bind_trace(a):
        with query_step("vector"):
            pass
    assert not b.snapshot()["steps"]
    a.finish()
    assert store.start("c") is not None
    assert not store.get("a")["available"]
    b.created -= 11
    assert not store.get("b")["available"]
