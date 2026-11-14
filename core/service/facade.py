from __future__ import annotations
from pathlib import Path
from typing import Any, Sequence
from core.settings.schema import AppConfig
from core.graph.visualization import GraphDirection
from core.maintenance.rebuild import ProgressCallback
from core.service.context import ServiceContext
from core.service.documents import DocumentService
from core.service.graph import GraphService
from core.service.retrieval import RetrievalService
from core.service.maintenance import MaintenanceService

class KnowledgeBaseService:
    def __init__(self, *, settings: AppConfig | None = None, data_dir: Path | str | None = None, external_dir: Path | str | None = None, config_path: Path | str | None = None) -> None:
        self.context = ServiceContext(settings=settings, data_dir=data_dir, external_dir=external_dir, config_path=config_path)
        self.documents = DocumentService(self.context)
        self.graph = GraphService(self.context)
        self.retrieval = RetrievalService(self.context)
        self.maintenance = MaintenanceService(self.context)
        self.context.documents = self.documents
        self.context.retrieval = self.retrieval


    @property
    def settings(self):
        return self.context.settings

    @property
    def paths(self):
        return self.context.paths

    @property
    def data_dir(self):
        return self.context.data_dir

    @property
    def external_dir(self):
        return self.context.external_dir

    @property
    def config_path(self):
        return self.context.config_path

    def list_documents(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        *,
        directory: str | None = None,
        search: str | None = None,
        graph_status: str | None = None,
        rag_status: str | None = None,
        include_summary: bool = False,
    ) -> dict[str, Any]:
        return self.documents.list_documents(
            status,
            page,
            page_size,
            directory=directory,
            search=search,
            graph_status=graph_status,
            rag_status=rag_status,
            include_summary=include_summary,
        )

    def get_document_content(self, source_id: str) -> dict[str, Any]:
        return self.documents.get_document_content(source_id)

    def update_document_content(
        self,
        source_id: str,
        content: str,
        *,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        return self.documents.update_document_content(
            source_id,
            content,
            expected_content_hash=expected_content_hash,
        )

    def import_document(
        self,
        source_path: Path | str,
        *,
        ingest_after_import: bool = True,
        expected_origin_hash: str | None = None,
        directory: str | None = None,
    ) -> dict[str, Any]:
        return self.documents.import_document(
            source_path,
            ingest_after_import=ingest_after_import,
            expected_origin_hash=expected_origin_hash,
            **({"directory": directory} if directory is not None else {}),
        )

    def upload_file(self, content: str, filename: str) -> dict[str, Any]:
        return self.documents.upload_file(content, filename)

    def sync_sources(
        self,
        records: Sequence[dict[str, Any]],
        *,
        ingest_after_sync: bool = False,
    ) -> dict[str, Any]:
        return self.documents.sync_sources(
            records,
            ingest_after_sync=ingest_after_sync,
        )

    def list_synced_sources(
        self,
        *,
        source_type: str | None = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self.documents.list_synced_sources(
            source_type=source_type,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
        )

    def delete_synced_sources(self, source_uris: Sequence[str]) -> dict[str, Any]:
        return self.documents.delete_synced_sources(source_uris)

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self.graph.get_node(node_id)

    def get_relation(self, edge_id: str) -> dict[str, Any]:
        return self.graph.get_relation(edge_id)

    def delete_relation(self, edge_id: str) -> dict[str, Any]:
        return self.graph.delete_relation(edge_id)

    def delete_node(self, node_id: str) -> dict[str, Any]:
        return self.graph.delete_node(node_id)

    def get_full_graph(
        self,
        nodes_page: int | None = None,
        nodes_page_size: int = 100,
    ) -> dict[str, Any]:
        return self.graph.get_full_graph(nodes_page, nodes_page_size)

    def get_graph_visualization_meta(self) -> dict[str, Any]:
        return self.graph.get_visualization_meta()

    def list_graph_visualization_nodes(
        self,
        *,
        page: int = 1,
        page_size: int = 1000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return self.graph.list_visualization_nodes(
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def list_graph_visualization_edges(
        self,
        *,
        page: int = 1,
        page_size: int = 2000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return self.graph.list_visualization_edges(
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def get_graph_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 2,
        direction: GraphDirection = "both",
        limit: int = 2000,
        edge_limit: int = 10000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return self.graph.get_neighborhood(
            node_id,
            depth=depth,
            direction=direction,
            limit=limit,
            edge_limit=edge_limit,
            expected_revision=expected_revision,
        )

    def organize_graph(
        self,
        *,
        use_llm: bool = True,
        summarize: bool = True,
    ) -> dict[str, Any]:
        return self.maintenance.organize_graph(
            use_llm=use_llm,
            summarize=summarize,
        )

    def rebuild_knowledge_base(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        return self.maintenance.rebuild_knowledge_base(progress=progress)

    def rebuild_all(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        return self.maintenance.rebuild_all(progress=progress)

    def list_jobs(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return self.maintenance.list_jobs(limit=limit)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.maintenance.get_job(job_id)

    def generate_group_summaries(self, *, force: bool = False) -> dict[str, Any]:
        return self.maintenance.generate_group_summaries(force=force)

    def query_graph(
        self,
        query: str,
        *,
        depth: int = 3,
        direction: str = "both",
        confidence: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_graph(
            query,
            depth=depth,
            direction=direction,
            confidence=confidence,
            force=force,
        )

    def query_rag(
        self,
        query: str,
        *,
        top_k: int | None = None,
        threshold: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_rag(
            query,
            top_k=top_k,
            threshold=threshold,
            force=force,
        )

    def query_hybrid(
        self,
        query: str,
        *,
        graph_depth: int = 3,
        rag_top_k: int | None = None,
        graph_confidence: float | None = None,
        rag_threshold: float | None = None,
        direction: str = "both",
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_hybrid(
            query,
            graph_depth=graph_depth,
            rag_top_k=rag_top_k,
            graph_confidence=graph_confidence,
            rag_threshold=rag_threshold,
            direction=direction,
            force=force,
        )

    def query_answer(
        self,
        query: str,
        *,
        graph_depth: int = 3,
        rag_top_k: int | None = None,
        graph_confidence: float | None = None,
        rag_threshold: float | None = None,
        direction: str = "both",
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_answer(
            query,
            graph_depth=graph_depth,
            rag_top_k=rag_top_k,
            graph_confidence=graph_confidence,
            rag_threshold=rag_threshold,
            direction=direction,
            force=force,
        )

    def query_global(
        self,
        query: str,
        top_k: int = 5,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_global(query, top_k=top_k, force=force)

    def list_cached_queries(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        return self.retrieval.list_cached_queries(page, page_size)

    def get_cached_query(self, cache_key: str) -> dict[str, Any]:
        return self.retrieval.get_cached_query(cache_key)

    def clear_search_cache(self, stale_only: bool = False) -> dict[str, Any]:
        return self.retrieval.clear_search_cache(stale_only)

    def delete_document(self, source_id: str) -> dict[str, Any]:
        return self.documents.delete_document(source_id)

    def delete_documents(self, source_ids: Sequence[str]) -> dict[str, Any]:
        return self.documents.delete_documents(source_ids)

    def delete_all_documents(self, directory: str | None = None) -> dict[str, Any]:
        return self.documents.delete_all_documents(directory)

    def status(self) -> dict[str, Any]:
        return self.maintenance.status()

    def get_config(self) -> dict[str, Any]:
        return self.maintenance.get_config()

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.maintenance.save_config(data)

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
        *,
        retry_failed: bool = False,
        force: bool = False,
        failed_only: bool = False,
    ) -> dict[str, Any]:
        return self.documents.ingest(paths, mode, retry_failed=retry_failed, force=force, failed_only=failed_only)


    def _require_initialized(self) -> None:
        self.context._require_initialized()

    def _log_event(self, *args, **kwargs) -> None:
        self.context._log_event(*args, **kwargs)
