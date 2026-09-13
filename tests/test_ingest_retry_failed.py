"""失败来源的重试开关：默认只处理 pending，retry_failed=True 才重新整理 failed。

默认行为是有意保守的：图谱与 RAG 构建会消耗模型额度，因此失败的来源必须由
调用方显式要求才会重跑，不能被下一次 ingest 自动带上。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from api.deps import get_job_manager, get_service
from core.config import AppConfig
from core.db import connect_sources
from core.ingestor import Ingestor
from provider.embedding import EmbeddingResult


def _settings(root: Path) -> AppConfig:
    return AppConfig(
        graph_build_mode="structured",
        graph_extract_granularity="medium",
        graph_extract_chunk_size=2000,
        graph_extract_concurrency=3,
        log_dir=str(root / "log"),
        models={
            "llm": "test-llm",
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
            "rerank": "test-rerank",
        },
        kemo={"api_key": "test-key"},
    )


def _embedding(texts: list[str], **_: object) -> EmbeddingResult:
    return EmbeddingResult(
        vectors=[[0.5, 0.25, 0.75] for _ in texts],
        vector_space_id="test-space",
    )


def _draft(left: str = "Alpha", right: str = "Beta") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "entities": [
            {
                "local_id": "a",
                "keyword": left,
                "summary": f"{left} summary",
                "aliases": [],
                "tags": ["concept"],
                "evidence_weight": 0.7,
                "evidence": left,
            },
            {
                "local_id": "b",
                "keyword": right,
                "summary": f"{right} summary",
                "aliases": [],
                "tags": ["concept"],
                "evidence_weight": 0.6,
                "evidence": right,
            },
        ],
        "relations": [
            {
                "source": "a",
                "relation": "关联",
                "target": "b",
                "evidence_weight": 0.8,
                "evidence": f"{left} relates {right}",
            }
        ],
    }


@pytest.fixture
def ingestor(tmp_path: Path) -> Ingestor:
    external = tmp_path / "external" / "markdown"
    external.mkdir(parents=True)
    (external / "doc.md").write_text("Alpha relates Beta", encoding="utf-8")
    return Ingestor(tmp_path / "data", external, settings=_settings(tmp_path))


def _statuses(ingestor: Ingestor) -> tuple[str, str]:
    connection = connect_sources(ingestor.paths)
    try:
        row = connection.execute(
            "SELECT graph_status, rag_status FROM sources"
        ).fetchone()
        return row["graph_status"], row["rag_status"]
    finally:
        connection.close()


def test_failed_graph_is_skipped_by_default_and_retried_on_request(ingestor: Ingestor) -> None:
    with patch("core.ingestor.chat_structured", side_effect=RuntimeError("gateway")):
        failed = ingestor.ingest(mode="graph")
    assert failed["failed"] == 1
    assert _statuses(ingestor)[0] == "failed"

    # 默认：失败来源根本不被选中，既不算 processed/skipped，也不会调用模型。
    with patch("core.ingestor.chat_structured", return_value=_draft()) as chat:
        skipped = ingestor.ingest(mode="graph")
    assert skipped["processed"] == 0
    assert skipped["skipped"] == 0
    assert skipped["details"] == []
    assert chat.call_count == 0

    # 显式要求后重跑同一来源，且不重复计入失败。
    with patch("core.ingestor.chat_structured", return_value=_draft()) as chat:
        retried = ingestor.ingest(mode="graph", retry_failed=True)
    assert retried["graph_updated"] == 1
    assert retried["failed"] == 0
    assert chat.call_count == 1
    assert _statuses(ingestor)[0] == "ready"


def test_failed_rag_is_retried_only_with_the_flag(ingestor: Ingestor) -> None:
    with patch("core.ingestor.embed", side_effect=RuntimeError("gateway")):
        failed = ingestor.ingest(mode="rag")
    assert failed["failed"] == 1
    assert _statuses(ingestor)[1] == "failed"

    with patch("core.ingestor.embed", side_effect=_embedding) as embed:
        skipped = ingestor.ingest(mode="rag")
    assert skipped["processed"] == 0
    assert skipped["skipped"] == 0
    assert skipped["details"] == []
    assert embed.call_count == 0

    with patch("core.ingestor.embed", side_effect=_embedding) as embed:
        retried = ingestor.ingest(mode="rag", retry_failed=True)
    assert retried["rag_updated"] == 1
    assert retried["failed"] == 0
    assert embed.call_count >= 1
    assert _statuses(ingestor)[1] == "ready"


def test_retry_only_touches_failed_sources_and_leaves_ready_alone(ingestor: Ingestor) -> None:
    with patch("core.ingestor.chat_structured", return_value=_draft()):
        assert ingestor.ingest(mode="graph")["graph_updated"] == 1

    # 已经 ready 的来源既不是 pending 也不是 failed，开了开关也不会被重新构建。
    with patch("core.ingestor.chat_structured", return_value=_draft()) as chat:
        again = ingestor.ingest(mode="graph", retry_failed=True)
    assert again["processed"] == 0
    assert again["skipped"] == 0
    assert chat.call_count == 0


def test_api_ingest_passes_retry_failed_only_when_requested() -> None:
    calls: list[dict[str, object]] = []

    def ingest(**options: object) -> dict[str, object]:
        calls.append(options)
        return {"processed": 0}

    app = create_app()
    app.dependency_overrides[get_service] = lambda: SimpleNamespace(ingest=ingest)
    with TestClient(app) as client:
        assert client.post("/api/v1/ingest", json={"mode": "graph"}).status_code == 200
        assert (
            client.post(
                "/api/v1/ingest", json={"mode": "graph", "retry_failed": True}
            ).status_code
            == 200
        )
    assert "retry_failed" not in calls[0]
    assert calls[1]["retry_failed"] is True


def test_ingest_job_passes_retry_failed_only_when_requested() -> None:
    submitted: list[dict[str, object]] = []

    def submit(kind: str, **options: object) -> dict[str, object]:
        submitted.append({"kind": kind, **options})
        return {"job_id": "job-1"}

    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: SimpleNamespace(submit=submit)
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/jobs/ingest", json={"mode": "both"}).status_code == 200
        )
        assert (
            client.post(
                "/api/v1/jobs/ingest", json={"mode": "both", "retry_failed": True}
            ).status_code
            == 200
        )
    assert submitted[0]["kind"] == "ingest"
    assert "retry_failed" not in submitted[0]
    assert submitted[1]["retry_failed"] is True
