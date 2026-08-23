"""KnowledgeBaseService 领域服务委托契约测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from core.services import (
    DocumentService,
    GraphService,
    MaintenanceService,
    RetrievalService,
)


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
        owner = Mock()
        owner._list_documents_impl.return_value = {"documents": []}
        owner._delete_relation_impl.return_value = {"edge_id": "e1"}
        owner._rebuild_all_impl.return_value = {"validated": True}

        self.assertEqual(DocumentService(owner).list_documents(), {"documents": []})
        self.assertEqual(GraphService(owner).delete_relation("e1"), {"edge_id": "e1"})
        self.assertEqual(
            MaintenanceService(owner).rebuild_all(),
            {"validated": True},
        )
