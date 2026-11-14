"""KnowledgeBaseService 领域服务委托契约测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from core.service.documents import DocumentService
from core.service.graph import GraphService
from core.service.maintenance import MaintenanceService
from core.service.retrieval import RetrievalService

class DomainServiceDelegationTests(unittest.TestCase):
    def test_retrieval_query_keeps_keyword_arguments(self) -> None:
        owner = Mock()
        owner.settings.default_top_k = 10
        owner.settings.default_confidence = 0.7
        owner.settings.rag_similarity_threshold = 0.6
        owner.settings.search_cache_enabled = False
        owner._new_hybrid_engine.return_value.query.return_value = {"ok": True}

        result = RetrievalService(owner).query_hybrid(
            "alpha",
            graph_depth=4,
            rag_top_k=7,
            graph_confidence=0.8,
            rag_threshold=0.2,
            direction="out",
        )

        self.assertEqual(result, {"ok": True})
        owner._new_hybrid_engine.return_value.query.assert_called_once_with(
            "alpha",
            graph_depth=4,
            rag_top_k=7,
            graph_confidence=0.8,
            rag_threshold=0.2,
            direction="out",
        )

    def test_domain_services_expose_separate_areas(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from core.settings.schema import AppConfig
        from core.storage.schema import initialize_databases
        from core.service.facade import KnowledgeBaseService
        from core.storage.connections import connect_graph
        from core.graph.tool_store import add_entity, add_relation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppConfig(log_dir=str(root / "logs"))
            initialize_databases(root / "data", settings)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data", external_dir=root / "markdown")
            original = root / "source.txt"
            original.write_text("A shared document.", encoding="utf-8")
            source = service.import_document(original, ingest_after_import=False)
            documents = service.list_documents()
            self.assertEqual(service.documents.list_documents(), documents)
            self.assertEqual(service.retrieval.list_cached_queries(), service.list_cached_queries())
            self.assertEqual(service.maintenance.status(), service.status())
            conn = connect_graph(service.paths)
            try:
                left = add_entity(conn, keyword="alpha", summary="alpha", source_id=source["source_id"], content_hash="hash")
                right = add_entity(conn, keyword="beta", summary="beta", source_id=source["source_id"], content_hash="hash")
                relation = add_relation(conn, source_node_id=left, target_node_id=right, relation="relates", evidence_weight=1.0, source_id=source["source_id"], content_hash="hash")
            finally:
                conn.close()
            service.graph.delete_relation(relation)
            self.assertEqual(service.documents.list_documents(), documents)
