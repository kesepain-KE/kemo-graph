"""Embedding 身份契约及失败后已有索引的完整性。"""

import pytest

from core.settings.schema import AppConfig
from core.storage.connections import connect_rag, connect_sources
from core.ingest.pipeline import Ingestor
from core.rag.engine import RAGEngine
from core.rag.errors import IndexIntegrityError
from provider.embedding import EmbeddingResult


@pytest.fixture
def indexed_document(tmp_path):
    settings = AppConfig(
        log_dir=str(tmp_path / "log"),
        models={"embedding_dimensions": 3},
    )
    external = tmp_path / "external" / "markdown"
    external.mkdir(parents=True)
    document = external / "sample.md"
    document.write_text("Original document body.", encoding="utf-8")
    ingestor = Ingestor(
        tmp_path / "data", external, settings=settings,
        embedder=lambda texts: EmbeddingResult([[1.0, 0.0, 0.0] for _ in texts], "test-space"),
    )
    assert ingestor.ingest(mode="rag")["rag_updated"] == 1
    return ingestor, document


@pytest.mark.parametrize("invalid_kind", ["bare_list", "empty_space"])
def test_rebuild_rejects_missing_embedding_identity_without_replacing_old_vectors(indexed_document, invalid_kind):
    ingestor, document = indexed_document
    with connect_rag(ingestor.paths) as connection:
        previous = [tuple(row) for row in connection.execute("SELECT * FROM embeddings")]
    previous_ids = ingestor._get_rag_engine().index.ids.copy()
    document.write_text("Updated document body.", encoding="utf-8")

    def invalid_embedder(texts):
        vectors = [[1.0, 0.0, 0.0] for _ in texts]
        return vectors if invalid_kind == "bare_list" else EmbeddingResult(vectors, " ")

    failing = Ingestor(
        ingestor.paths.data_dir, ingestor.external_dir, settings=ingestor.settings,
        embedder=invalid_embedder,
    )
    result = failing.ingest(mode="rag")
    assert result["failed"] == 1 and result["rag_updated"] == 0
    expected_message = "EmbeddingResult" if invalid_kind == "bare_list" else "vector_space_id"
    assert expected_message in result["details"][0]["rag_error"]
    with connect_rag(ingestor.paths) as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM embeddings")] == previous
    assert failing._get_rag_engine().index.ids == previous_ids
    with connect_sources(ingestor.paths) as connection:
        source = connection.execute("SELECT rag_status, rag_hash, content_hash FROM sources").fetchone()
    assert source["rag_status"] == "failed"
    assert source["rag_hash"] != source["content_hash"]


@pytest.mark.parametrize("invalid_kind", ["bare_list", "empty_space"])
def test_query_rejects_missing_embedding_identity(indexed_document, invalid_kind):
    ingestor, _ = indexed_document

    def invalid_embedder(texts):
        vectors = [[1.0, 0.0, 0.0] for _ in texts]
        return vectors if invalid_kind == "bare_list" else EmbeddingResult(vectors, " ")

    engine = RAGEngine(
        ingestor.paths.data_dir, settings=ingestor.settings, embedder=invalid_embedder,
    )
    expected_error = TypeError if invalid_kind == "bare_list" else IndexIntegrityError
    with pytest.raises(expected_error, match="EmbeddingResult|vector_space_id"):
        engine.query("document")
    assert engine.index.ids == ingestor._get_rag_engine().index.ids
