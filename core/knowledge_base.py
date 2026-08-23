"""供 CLI、外部 API 和 Web 入口共用的知识库门面。"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from provider.engine import chat
from provider.tools.document_tools import (
    DocumentConversionError,
    SUPPORTED_DOCUMENT_SUFFIXES,
    import_document as convert_document,
)

from .config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from .db import (
    connect_graph,
    connect_rag,
    connect_sources,
    get_database_paths,
    read_graph_meta,
    write_graph_meta,
)
from .graph_engine import GraphEngine
from .graph_organizer import GraphOrganizer
from .graph_visualization import (
    GraphDirection,
    get_neighborhood,
    get_visualization_meta,
    list_visualization_edges,
    list_visualization_nodes,
)
from .hybrid import HybridEngine
from .ingestor import (
    DocumentNotFoundError,
    FileMapError,
    IngestError,
    Ingestor,
    RecycleConflictError,
)
from .logger import DailyTSVLogger
from .rag_engine import FaissIndexManager, RAGEngine
from .rebuilder import KnowledgeBaseRebuilder, ProgressCallback
from .services import (
    DocumentService,
    GraphService,
    MaintenanceService,
    RetrievalService,
)


class KnowledgeBaseNotInitializedError(RuntimeError):
    """知识库的 sources.db 尚不存在。"""


class KnowledgeBaseProcessingError(RuntimeError):
    """所请求的数据当前正在整理。"""


class DocumentImportError(RuntimeError):
    """文档导入入口错误基类。"""


class UnsupportedDocumentFormatError(DocumentImportError):
    """用户上传了不受支持的文件格式。"""


class DocumentImportPathError(DocumentImportError):
    """导入路径不安全、缺失或不是普通文件。"""


class DocumentTooLargeError(DocumentImportError):
    """导入文件超过服务端大小上限。"""


class DocumentIngestError(DocumentImportError):
    """转换已完成，但后续知识库整理无法启动。"""


class DocumentContentConflictError(DocumentImportError):
    """文档在编辑期间已变化，拒绝覆盖较新的内容。"""


SUPPORTED_IMPORT_SUFFIXES = SUPPORTED_DOCUMENT_SUFFIXES
MAX_IMPORT_BYTES = 50 * 1024 * 1024
CONFIG_API_KEY_MASK = "************"


class KnowledgeBaseService:
    """将入口层请求转换为稳定的 core 调用。"""

    def __init__(
        self,
        *,
        settings: AppConfig | None = None,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.config_path = (
            Path(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
        )
        self._data_dir_override = (
            Path(data_dir).expanduser().resolve() if data_dir is not None else None
        )
        self._external_dir_override = (
            Path(external_dir).expanduser().resolve()
            if external_dir is not None
            else None
        )
        self.data_dir = self._data_dir_override or self.settings.resolve_data_dir()
        self.external_dir = (
            self._external_dir_override or self.settings.resolve_external_dir()
        )
        self.paths = get_database_paths(self.data_dir)
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        # 对外仍由本类提供稳定门面；领域服务只持有 owner 引用，
        # 因而 save_config() 更新配置/路径后无需重建服务对象。
        self.documents = DocumentService(self)
        self.graph = GraphService(self)
        self.retrieval = RetrievalService(self)
        self.maintenance = MaintenanceService(self)

    # ------------------------------------------------------------------
    # 兼容门面：公开方法签名保持不变，具体入口按领域委托到 services。
    # ------------------------------------------------------------------
    def list_documents(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        return self.documents.list_documents(status, page, page_size)

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
        _original_identity: str | None = None,
    ) -> dict[str, Any]:
        return self.documents.import_document(
            source_path,
            ingest_after_import=ingest_after_import,
            _original_identity=_original_identity,
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

    def cleanup_recycle(self, *, force: bool = False) -> dict[str, Any]:
        return self.maintenance.cleanup_recycle(force=force)

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

    def delete_all_documents(self) -> dict[str, Any]:
        return self.documents.delete_all_documents()

    def status(self) -> dict[str, Any]:
        return self.maintenance.status()

    def get_config(self) -> dict[str, Any]:
        return self.maintenance.get_config()

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.maintenance.save_config(data)

    def _status_impl(self) -> dict[str, Any]:
        """返回 03 文档约定的知识库状态，不隐式初始化数据库。"""

        initialized = self.paths.sources_db.exists()
        result: dict[str, Any] = {
            "initialized": initialized,
            "sources": {
                "total": 0,
                "active": 0,
                "pending_graph": 0,
                "pending_rag": 0,
            },
            "graph": {"total_nodes": 0, "total_edges": 0, "total_groups": 0},
            "rag": {
                "total_chunks": 0,
                "total_vectors": 0,
                "faiss_healthy": False,
            },
        }
        if not initialized:
            return result

        source_connection = connect_sources(self.paths)
        try:
            source_counts = source_connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN exists_status = 'active' THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN exists_status = 'active' AND graph_status = 'pending'
                                THEN 1 ELSE 0 END) AS pending_graph,
                       SUM(CASE WHEN exists_status = 'active' AND rag_status = 'pending'
                                THEN 1 ELSE 0 END) AS pending_rag
                FROM sources
                """
            ).fetchone()
        finally:
            source_connection.close()
        result["sources"] = {
            "total": int(source_counts["total"] or 0),
            "active": int(source_counts["active"] or 0),
            "pending_graph": int(source_counts["pending_graph"] or 0),
            "pending_rag": int(source_counts["pending_rag"] or 0),
        }

        if self.paths.graph_db.exists():
            graph_connection = connect_graph(self.paths)
            try:
                graph_counts = graph_connection.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM nodes) AS total_nodes,
                           (SELECT COUNT(*) FROM edges) AS total_edges,
                           (SELECT COUNT(*) FROM groups) AS total_groups
                    """
                ).fetchone()
            finally:
                graph_connection.close()
            result["graph"] = {
                "total_nodes": int(graph_counts["total_nodes"]),
                "total_edges": int(graph_counts["total_edges"]),
                "total_groups": int(graph_counts["total_groups"]),
            }

        vector_ids: set[int] = set()
        if self.paths.rag_db.exists():
            rag_connection = connect_rag(self.paths)
            try:
                rag_counts = rag_connection.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM chunks) AS total_chunks,
                           (SELECT COUNT(*) FROM embeddings) AS total_vectors
                    """
                ).fetchone()
                vector_ids = {
                    int(row["vector_id"])
                    for row in rag_connection.execute(
                        "SELECT vector_id FROM embeddings"
                    ).fetchall()
                }
            finally:
                rag_connection.close()
            result["rag"] = {
                "total_chunks": int(rag_counts["total_chunks"]),
                "total_vectors": int(rag_counts["total_vectors"]),
                "faiss_healthy": self._faiss_is_healthy(vector_ids),
            }
        return result

    def _list_documents_impl(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """分页返回文档基本信息，并支持按活动或待处理状态筛选。"""

        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page 必须是大于等于 1 的整数")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size 必须是 1 到 100 之间的整数")

        pagination = {
            "page": page,
            "page_size": page_size,
            "total": 0,
            "total_pages": 0,
        }
        if not self.paths.sources_db.exists():
            return {"documents": [], "pagination": pagination}

        where_sql = ""
        order_sql = "ORDER BY exists_status, relative_path"
        if status == "pending":
            where_sql = (
                "WHERE exists_status = 'active' "
                "AND (graph_status = 'pending' OR rag_status = 'pending')"
            )
            order_sql = "ORDER BY relative_path"
        elif status == "active":
            where_sql = "WHERE exists_status = 'active'"
            order_sql = "ORDER BY relative_path"

        connection = connect_sources(self.paths)
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM sources {where_sql}"
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT source_id, relative_path, original_path,
                       content_hash, graph_hash, rag_hash,
                       origin_hash, origin_size, origin_modified_at,
                       source_uri, source_type, source_revision,
                       source_updated_at, source_metadata_json,
                       external_content_hash, last_synced_at,
                       graph_status, rag_status, exists_status,
                       created_at, updated_at
                FROM sources
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
        finally:
            connection.close()

        pagination["total"] = total
        pagination["total_pages"] = (total + page_size - 1) // page_size
        return {
            "documents": [
                {
                    "source_id": row["source_id"],
                    "relative_path": row["relative_path"],
                    "original_path": row["original_path"],
                    "content_hash": row["content_hash"],
                    "graph_hash": row["graph_hash"],
                    "rag_hash": row["rag_hash"],
                    "origin_hash": row["origin_hash"],
                    "origin_size": row["origin_size"],
                    "origin_modified_at": row["origin_modified_at"],
                    "source_uri": row["source_uri"],
                    "source_type": row["source_type"],
                    "source_revision": row["source_revision"],
                    "source_updated_at": row["source_updated_at"],
                    "source_metadata": _load_source_metadata(
                        row["source_metadata_json"]
                    ),
                    "external_content_hash": row["external_content_hash"],
                    "last_synced_at": row["last_synced_at"],
                    "graph_status": row["graph_status"],
                    "rag_status": row["rag_status"],
                    "exists_status": row["exists_status"],
                    "updated_at": row["updated_at"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
            "pagination": pagination,
        }

    def _get_document_content_impl(self, source_id: str) -> dict[str, Any]:
        """返回指定文档的 Markdown 文本内容。"""
        self._require_initialized()
        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                """
                SELECT source_id, original_path, relative_path, content_hash,
                       graph_hash, rag_hash, origin_hash, origin_size,
                       origin_modified_at, source_uri, source_type,
                       source_revision, source_updated_at, source_metadata_json,
                       external_content_hash, last_synced_at,
                       graph_status, rag_status, exists_status
                FROM sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise DocumentNotFoundError(f"文档不存在：{source_id}")
        if row["exists_status"] != "active":
            raise DocumentNotFoundError(f"文档已删除：{source_id}")

        file_path = self.external_dir / row["relative_path"]
        if not file_path.exists():
            raise DocumentNotFoundError(f"文档文件缺失：{row['relative_path']}")

        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DocumentNotFoundError(
                f"无法读取文档：{row['relative_path']}: {exc}"
            ) from exc

        return {
            "source_id": row["source_id"],
            "original_path": row["original_path"],
            "relative_path": row["relative_path"],
            "content_hash": row["content_hash"],
            "graph_hash": row["graph_hash"],
            "rag_hash": row["rag_hash"],
            "origin_hash": row["origin_hash"],
            "origin_size": row["origin_size"],
            "origin_modified_at": row["origin_modified_at"],
            "source_uri": row["source_uri"],
            "source_type": row["source_type"],
            "source_revision": row["source_revision"],
            "source_updated_at": row["source_updated_at"],
            "source_metadata": _load_source_metadata(row["source_metadata_json"]),
            "external_content_hash": row["external_content_hash"],
            "last_synced_at": row["last_synced_at"],
            "graph_status": row["graph_status"],
            "rag_status": row["rag_status"],
            "exists_status": row["exists_status"],
            "content": content,
        }

    def _update_document_content_impl(
        self,
        source_id: str,
        content: str,
        *,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        """原子保存 Markdown，并仅将派生 Graph/RAG 标记为待重建。"""

        self._require_initialized()
        normalized_source_id = str(source_id).strip()
        if not normalized_source_id:
            raise ValueError("source_id 必须是非空字符串")
        if not isinstance(content, str):
            raise TypeError("content 必须是字符串")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_IMPORT_BYTES:
            raise DocumentTooLargeError(
                f"Markdown 内容超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限"
            )
        expected_hash = (
            str(expected_content_hash).strip().casefold()
            if expected_content_hash is not None
            else None
        )
        if expected_hash is not None and (
            len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise ValueError("expected_content_hash 必须是 64 位 SHA-256")

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        with ingestor._write_lock:
            connection = connect_sources(self.paths)
            try:
                row = connection.execute(
                    """
                    SELECT source_id, relative_path, content_hash,
                           graph_hash, rag_hash, graph_status, rag_status,
                           exists_status
                    FROM sources WHERE source_id = ?
                    """,
                    (normalized_source_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is None or row["exists_status"] != "active":
                raise DocumentNotFoundError(
                    f"活动文档不存在：{normalized_source_id}"
                )

            current_hash = str(row["content_hash"])
            if expected_hash is not None and expected_hash != current_hash.casefold():
                raise DocumentContentConflictError(
                    "文档已被其他操作更新，请重新加载后再编辑"
                )
            destination = _safe_markdown_destination(
                self.external_dir,
                str(row["relative_path"]),
            )
            if not destination.is_file():
                raise DocumentNotFoundError(
                    f"文档文件缺失：{row['relative_path']}"
                )
            try:
                previous_bytes = destination.read_bytes()
            except OSError as exc:
                raise DocumentNotFoundError(
                    f"无法读取文档：{row['relative_path']}"
                ) from exc
            disk_hash = hashlib.sha256(previous_bytes).hexdigest()
            if disk_hash != current_hash:
                raise DocumentContentConflictError(
                    "磁盘文档已在数据库外发生变化，请刷新文档列表后再编辑"
                )

            new_hash = hashlib.sha256(encoded).hexdigest()
            if new_hash == current_hash:
                return {
                    "source_id": normalized_source_id,
                    "relative_path": str(row["relative_path"]),
                    "changed": False,
                    "previous_content_hash": current_hash,
                    "content_hash": current_hash,
                    "graph_status": str(row["graph_status"]),
                    "rag_status": str(row["rag_status"]),
                }

            _write_bytes_atomic(destination, encoded)
            now = _now_iso()
            connection = connect_sources(self.paths)
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE sources
                    SET content_hash = ?, graph_status = 'pending',
                        rag_status = 'pending', updated_at = ?
                    WHERE source_id = ? AND exists_status = 'active'
                      AND content_hash = ?
                    """,
                    (new_hash, now, normalized_source_id, current_hash),
                )
                if cursor.rowcount != 1:
                    raise DocumentContentConflictError(
                        "文档状态已变化，请重新加载后再编辑"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                _write_bytes_atomic(destination, previous_bytes)
                raise
            finally:
                connection.close()

        self._log_event(
            "document_content_update",
            f"source_id={normalized_source_id}, path={row['relative_path']}",
        )
        return {
            "source_id": normalized_source_id,
            "relative_path": str(row["relative_path"]),
            "changed": True,
            "previous_content_hash": current_hash,
            "content_hash": new_hash,
            "graph_status": "pending",
            "rag_status": "pending",
            "updated_at": now,
        }

    def _get_node_impl(self, node_id: str) -> dict[str, Any]:
        """旧私有入口兼容层；读取实现位于 GraphService。"""
        return self.graph.get_node(node_id)
    def _get_relation_impl(self, edge_id: str) -> dict[str, Any]:
        """旧私有入口兼容层；读取实现位于 GraphService。"""
        return self.graph.get_relation(edge_id)

    def _delete_relation_impl(self, edge_id: str) -> dict[str, Any]:
        """删除关系及其全部证据，并使群组总结和搜索缓存失效。"""

        self._require_available("graph")
        graph_connection = connect_graph(self.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            edge = graph_connection.execute(
                """
                SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                       e.relation, e.target_node_id, tn.keyword AS target_keyword,
                       e.weight, e.support_count, e.created_at
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                WHERE e.edge_id = ?
                """,
                (edge_id,),
            ).fetchone()
            if edge is None:
                raise DocumentNotFoundError(f"关系不存在：{edge_id}")
            evidence_count = int(
                graph_connection.execute(
                    "SELECT COUNT(*) FROM edge_sources WHERE edge_id = ?",
                    (edge_id,),
                ).fetchone()[0]
            )
            mention_rows = graph_connection.execute(
                """
                SELECT rm.mention_id
                FROM relation_mentions rm
                JOIN mention_nodes sm ON sm.mention_id = rm.source_mention_id
                JOIN mention_nodes tm ON tm.mention_id = rm.target_mention_id
                WHERE sm.node_id = ? AND rm.relation = ? AND tm.node_id = ?
                """,
                (
                    edge["source_node_id"],
                    edge["relation"],
                    edge["target_node_id"],
                ),
            ).fetchall()
            if mention_rows:
                placeholders = ",".join("?" for _ in mention_rows)
                graph_connection.execute(
                    f"DELETE FROM relation_mentions WHERE mention_id IN ({placeholders})",
                    tuple(row["mention_id"] for row in mention_rows),
                )
            graph_connection.execute(
                "DELETE FROM edge_sources WHERE edge_id = ?", (edge_id,)
            )
            graph_connection.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        self._refresh_graph_meta(changed=True)
        RAGEngine(self.data_dir, settings=self.settings).refresh_auxiliary_consistency()
        try:
            cache_deleted = self.clear_search_cache()["deleted"]
        except Exception:
            cache_deleted = 0
        result = {
            "deleted": True,
            "edge": _relation_row(edge),
            "deleted_evidence_count": evidence_count,
            "deleted_mention_count": len(mention_rows),
            "groups_invalidated": True,
            "search_cache_deleted": cache_deleted,
        }
        self._log_event(
            "delete_relation",
            f"edge_id={edge_id}, evidence={evidence_count}",
        )
        return result

    def _delete_node_impl(self, node_id: str) -> dict[str, Any]:
        """删除指定节点，按 06 文档规则执行级联操作。"""
        self._require_available("graph")
        with Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )._write_lock:
            result = self._execute_node_deletion(node_id)
        try:
            result["search_cache_deleted"] = self.clear_search_cache()["deleted"]
        except Exception:
            result["search_cache_deleted"] = 0
        self._log_event(
            "delete_node",
            f"node_id={node_id}, edges={result['cascade_deleted_edges']}",
        )
        return result

    def _execute_node_deletion(self, node_id: str) -> dict[str, Any]:
        graph_connection = connect_graph(self.paths)
        try:
            node_row = graph_connection.execute(
                "SELECT node_id, keyword FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if node_row is None:
                raise DocumentNotFoundError(f"节点不存在：{node_id}")
            bindings = graph_connection.execute(
                """
                SELECT ns.source_id,
                       (SELECT COUNT(*) FROM node_sources other
                        WHERE other.source_id = ns.source_id
                          AND other.node_id != ns.node_id) AS other_node_count
                FROM node_sources ns WHERE ns.node_id = ?
                ORDER BY ns.source_id
                """,
                (node_id,),
            ).fetchall()
        finally:
            graph_connection.close()

        source_ids = [str(binding["source_id"]) for binding in bindings]
        source_by_id: dict[str, Any] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_connection = connect_sources(self.paths)
            try:
                source_by_id = {
                    str(row["source_id"]): row
                    for row in source_connection.execute(
                        f"""
                        SELECT source_id, relative_path, exists_status
                        FROM sources WHERE source_id IN ({placeholders})
                        """,
                        tuple(source_ids),
                    ).fetchall()
                }
            finally:
                source_connection.close()

        isolated_source_ids = [
            str(binding["source_id"])
            for binding in bindings
            if int(binding["other_node_count"] or 0) == 0
            and str(binding["source_id"]) in source_by_id
            and source_by_id[str(binding["source_id"])]["exists_status"] == "active"
        ]
        unlinked_files = [
            str(source_by_id[str(binding["source_id"])]["relative_path"])
            for binding in bindings
            if int(binding["other_node_count"] or 0) > 0
            and str(binding["source_id"]) in source_by_id
        ]

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        recycled_files: list[str] = []
        recycled_paths: list[str] = []
        for source_id in isolated_source_ids:
            deletion = ingestor.delete_document(source_id)
            recycled_files.append(str(deletion["relative_path"]))
            if deletion.get("recycled_path"):
                recycled_paths.append(str(deletion["recycled_path"]))

        graph_connection = connect_graph(self.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            edge_ids = {
                row["edge_id"]
                for row in graph_connection.execute(
                    """
                    SELECT edge_id FROM edges
                    WHERE source_node_id = ? OR target_node_id = ?
                    """,
                    (node_id, node_id),
                ).fetchall()
            }
            mention_ids = [
                str(row["mention_id"])
                for row in graph_connection.execute(
                    "SELECT mention_id FROM mention_nodes WHERE node_id = ?",
                    (node_id,),
                ).fetchall()
            ]
            deleted_relation_mentions = 0
            if mention_ids:
                placeholders = ",".join("?" for _ in mention_ids)
                deleted_relation_mentions = int(
                    graph_connection.execute(
                        f"""
                        SELECT COUNT(*) FROM relation_mentions
                        WHERE source_mention_id IN ({placeholders})
                           OR target_mention_id IN ({placeholders})
                        """,
                        tuple(mention_ids + mention_ids),
                    ).fetchone()[0]
                )
                graph_connection.execute(
                    f"""
                    DELETE FROM relation_mentions
                    WHERE source_mention_id IN ({placeholders})
                       OR target_mention_id IN ({placeholders})
                    """,
                    tuple(mention_ids + mention_ids),
                )
                graph_connection.execute(
                    f"DELETE FROM entity_mentions WHERE mention_id IN ({placeholders})",
                    tuple(mention_ids),
                )
            if edge_ids:
                placeholders = ",".join("?" for _ in edge_ids)
                parameters = tuple(sorted(edge_ids))
                graph_connection.execute(
                    f"DELETE FROM edge_sources WHERE edge_id IN ({placeholders})",
                    parameters,
                )
                graph_connection.execute(
                    f"DELETE FROM edges WHERE edge_id IN ({placeholders})",
                    parameters,
                )

            graph_connection.execute(
                "DELETE FROM node_sources WHERE node_id = ?", (node_id,)
            )
            graph_connection.execute(
                "DELETE FROM mention_nodes WHERE node_id = ?", (node_id,)
            )
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            graph_connection.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        # 清理 chunk_nodes 中的孤立引用
        rag_connection = connect_rag(self.paths)
        try:
            rag_connection.execute(
                "DELETE FROM chunk_nodes WHERE node_id = ?", (node_id,)
            )
            rag_connection.commit()
        finally:
            rag_connection.close()

        self._refresh_graph_meta(changed=True)
        RAGEngine(self.data_dir, settings=self.settings).refresh_auxiliary_consistency()
        return {
            "deleted_node_id": node_id,
            "deleted_keyword": node_row["keyword"],
            "cascade_deleted_edges": len(edge_ids),
            "deleted_mention_count": len(mention_ids),
            "deleted_relation_mention_count": deleted_relation_mentions,
            "deleted_source_ids": isolated_source_ids,
            "recycled_files": recycled_files,
            "recycled_paths": recycled_paths,
            "unlinked_files": unlinked_files,
        }

    def _move_source_to_recycle(self, relative_path: str) -> str | None:
        """将 Markdown 文件移入回收站。回滚保证原子性。"""
        source_path = (self.external_dir / relative_path).resolve()
        try:
            source_path.relative_to(self.external_dir)
        except ValueError as exc:
            raise IngestError(f"非法 Markdown 相对路径：{relative_path}") from exc
        if not source_path.exists():
            return None

        recycle_root = (self.external_dir.parent / "recycle").resolve()
        destination = (recycle_root / relative_path).resolve()
        try:
            destination.relative_to(recycle_root)
        except ValueError as exc:
            raise IngestError(f"非法回收站相对路径：{relative_path}") from exc
        meta_path = destination.with_name(destination.name + ".meta.json")
        if destination.exists() or meta_path.exists():
            return str(destination.relative_to(recycle_root.parent))

        destination.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        metadata = {
            "original_path": relative_path,
            "recycled_at": now.isoformat(),
            "expires_at": (
                now + timedelta(days=self.settings.recycle_life_days)
            ).isoformat(),
        }
        shutil.move(str(source_path), str(destination))
        try:
            _write_json_atomic(meta_path, metadata)
        except Exception:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source_path))
            raise
        return destination.relative_to(recycle_root.parent).as_posix()

    def _cleanup_recycle_impl(self, *, force: bool = False) -> dict[str, Any]:
        """清理回收站文件；``force`` 为真时永久清空全部内容。"""
        started_at = time.perf_counter()
        external_parent = self.external_dir.parent.resolve()
        recycle_root = (external_parent / "recycle").resolve()
        if recycle_root.parent != external_parent or recycle_root.name != "recycle":
            raise IngestError("回收站目录不安全，拒绝执行清理")

        if not recycle_root.exists():
            if force:
                recycle_root.mkdir(parents=True, exist_ok=True)
            result = {"deleted": 0, "forced": force}
            self._log_event(
                "recycle_cleanup",
                f"force={str(force).lower()}, deleted=0",
                _elapsed_ms(started_at),
            )
            return result

        if not recycle_root.is_dir():
            raise IngestError("回收站路径不是目录，拒绝执行清理")

        if force:
            deleted = _clear_directory_contents(recycle_root)
            result = {"deleted": deleted, "forced": True}
            self._log_event(
                "recycle_cleanup",
                f"force=true, deleted={deleted}",
                _elapsed_ms(started_at),
            )
            return result

        now = datetime.now(timezone.utc)
        deleted = 0
        for meta_path in sorted(recycle_root.rglob("*.meta.json")):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(payload["expires_at"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

            if expires_at > now:
                continue

            base = meta_path.with_name(meta_path.name.replace(".meta.json", ""))
            meta_path.unlink(missing_ok=True)
            if base.exists():
                base.unlink()
                deleted += 1

        result = {"deleted": deleted, "forced": False}
        self._log_event(
            "recycle_cleanup",
            f"force=false, deleted={deleted}",
            _elapsed_ms(started_at),
        )
        return result

    def _generate_group_summaries_impl(self, *, force: bool = False) -> dict[str, Any]:
        """按 06 文档规则执行一次节点群总结。"""
        started_at = time.perf_counter()
        self._log_event("group_summary_start", "status=started")
        self._require_available("graph")
        meta = read_graph_meta(self.paths)
        changed = int(meta.get("changed_since_summary", 0))
        if not force and changed < self.settings.summary_trigger_file_count:
            result = {
                "generated": 0,
                "changed_files": changed,
                "threshold": self.settings.summary_trigger_file_count,
                "skipped_reason": "改动数量未达阈值",
            }
            self._log_event(
                "group_summary_done",
                f"generated=0, changed={changed}, skipped=threshold",
                _elapsed_ms(started_at),
            )
            return result

        components = self._find_connected_components()
        qualified = [
            comp
            for comp in components
            if len(comp) >= 3 and self._component_depth(comp) >= 3
        ]
        if not qualified:
            graph_connection = connect_graph(self.paths)
            try:
                graph_connection.execute("BEGIN IMMEDIATE")
                graph_connection.execute("DELETE FROM group_nodes")
                graph_connection.execute("DELETE FROM groups")
                graph_connection.commit()
            except Exception:
                graph_connection.rollback()
                raise
            finally:
                graph_connection.close()
            RAGEngine(self.data_dir, settings=self.settings).refresh_auxiliary_consistency()
            write_graph_meta(self.paths, {**meta, "changed_since_summary": 0})
            result = {
                "generated": 0,
                "qualified_components": 0,
                "total_components": len(components),
                "changed_files": changed,
            }
            self._log_event(
                "group_summary_done",
                f"generated=0, changed={changed}, skipped=no_component",
                _elapsed_ms(started_at),
            )
            return result

        community_records: list[tuple[str, str]] = []
        graph_connection = connect_graph(self.paths)
        try:
            graph_connection.execute("BEGIN IMMEDIATE")
            graph_connection.execute("DELETE FROM group_nodes")
            graph_connection.execute("DELETE FROM groups")
            generated = 0
            for component in qualified:
                summary = self._summarize_component(component)
                placeholders = ",".join("?" for _ in component)
                edge_count = int(
                    graph_connection.execute(
                        f"""
                        SELECT COUNT(*) FROM edges
                        WHERE source_node_id IN ({placeholders})
                          AND target_node_id IN ({placeholders})
                        """,
                        tuple(sorted(component)) + tuple(sorted(component)),
                    ).fetchone()[0]
                )
                group_id = str(uuid4())
                now_str = _now_iso()
                graph_connection.execute(
                    """
                    INSERT INTO groups (group_id, summary, node_count, edge_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (group_id, summary, len(component), edge_count, now_str, now_str),
                )
                graph_connection.executemany(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES (?, ?)",
                    [(group_id, node_id) for node_id in sorted(component)],
                )
                community_records.append((group_id, summary))
                generated += 1
            graph_connection.commit()
        except Exception:
            graph_connection.rollback()
            raise
        finally:
            graph_connection.close()

        community_vectors = RAGEngine(
            self.data_dir,
            settings=self.settings,
        ).sync_community_vectors(community_records)

        write_graph_meta(
            self.paths,
            {**meta, "changed_since_summary": 0, "last_summary_at": _now_iso()},
        )
        self._refresh_graph_meta(changed=False)
        result = {
            "generated": generated,
            "qualified_components": len(qualified),
            "total_components": len(components),
            "changed_files": changed,
            "community_vectors": community_vectors,
        }
        self._log_event(
            "group_summary_done",
            f"generated={generated}, changed={changed}",
            _elapsed_ms(started_at),
        )
        return result

    def _find_connected_components(self) -> list[set[str]]:
        """从 graph.db 中查找所有连通分量。"""
        connection = connect_graph(self.paths)
        try:
            node_ids = {
                row["node_id"]
                for row in connection.execute("SELECT node_id FROM nodes").fetchall()
            }
            adjacency: dict[str, set[str]] = {nid: set() for nid in node_ids}
            for row in connection.execute(
                "SELECT source_node_id, target_node_id FROM edges"
            ).fetchall():
                a, b = row["source_node_id"], row["target_node_id"]
                if a in adjacency and b in adjacency:
                    adjacency[a].add(b)
                    adjacency[b].add(a)
        finally:
            connection.close()

        visited: set[str] = set()
        components: list[set[str]] = []
        for node_id in sorted(node_ids):
            if node_id in visited:
                continue
            component: set[str] = set()
            frontier = {node_id}
            while frontier:
                current = frontier.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                frontier.update(adjacency.get(current, set()) - visited)
            components.append(component)
        return components

    def _component_depth(self, component: set[str]) -> int:
        """计算连通分量中任意节点能到达的最大 BFS 深度。"""
        connection = connect_graph(self.paths)
        try:
            adjacency: dict[str, set[str]] = {nid: set() for nid in component}
            for row in connection.execute(
                """
                SELECT source_node_id, target_node_id FROM edges
                WHERE source_node_id IN ({}) AND target_node_id IN ({})
                """.format(
                    ",".join("?" * len(component)),
                    ",".join("?" * len(component)),
                ),
                tuple(sorted(component)) + tuple(sorted(component)),
            ).fetchall():
                a, b = row["source_node_id"], row["target_node_id"]
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)
        finally:
            connection.close()

        max_depth = 0
        for start in component:
            visited = {start}
            frontier = {start}
            depth = 0
            while frontier and depth <= 5:
                next_frontier: set[str] = set()
                for nid in frontier:
                    for neighbor in adjacency.get(nid, set()):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_frontier.add(neighbor)
                if next_frontier:
                    depth += 1
                frontier = next_frontier
            max_depth = max(max_depth, depth)
        return max_depth

    def _summarize_component(self, component: set[str]) -> str:
        """用 LLM 为一个节点群生成总结。"""
        connection = connect_graph(self.paths)
        try:
            nodes_info: list[str] = []
            placeholders = ",".join("?" for _ in component)
            for row in connection.execute(
                f"SELECT keyword, summary FROM nodes WHERE node_id IN ({placeholders})",
                tuple(sorted(component)),
            ).fetchall():
                nodes_info.append(f"- {row['keyword']}: {row['summary']}")
            edges_info: list[str] = []
            for row in connection.execute(
                f"""
                SELECT n1.keyword AS src_kw, e.relation, n2.keyword AS tgt_kw
                FROM edges e
                JOIN nodes n1 ON n1.node_id = e.source_node_id
                JOIN nodes n2 ON n2.node_id = e.target_node_id
                WHERE e.source_node_id IN ({placeholders})
                  AND e.target_node_id IN ({placeholders})
                """,
                tuple(sorted(component)) + tuple(sorted(component)),
            ).fetchall():
                edges_info.append(
                    f"- {row['src_kw']} -[{row['relation']}]-> {row['tgt_kw']}"
                )
        finally:
            connection.close()

        system_prompt = (
            "你是知识图谱群组总结助手。根据给出的节点摘要和关系，"
            "用 2~3 句话总结该群组的核心主题和关键关系。"
            "要求：简洁、专业，只输出一段中文总结，不要额外解释。"
        )
        user_prompt = (
            f"节点（{len(nodes_info)} 个）：\n"
            + "\n".join(nodes_info[:20])
            + "\n\n关系（{len(edges_info)} 条）：\n"
            + "\n".join(edges_info[:30])
            + "\n\n请为该群组生成一段总结。"
        )

        started_at = time.perf_counter()
        try:
            summary = chat(system_prompt, user_prompt, settings=self.settings).strip()
            self._log_event(
                "llm_request",
                f"purpose=group_summary, model={self.settings.models.llm}",
                _elapsed_ms(started_at),
            )
            return summary
        except Exception:
            self._log_event(
                "llm_request",
                f"purpose=group_summary, model={self.settings.models.llm}, status=failed",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise

    def _refresh_graph_meta(self, *, changed: bool) -> None:
        """刷新 graph_meta.json，并在有变更时递增 changed_since_summary。"""
        connection = connect_graph(self.paths)
        try:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM nodes) AS total_nodes,
                    (SELECT COUNT(*) FROM edges) AS total_edges,
                    (SELECT COUNT(*) FROM groups) AS total_groups
                """
            ).fetchone()
        finally:
            connection.close()
        meta = read_graph_meta(self.paths)
        meta.update(
            {
                "total_nodes": int(counts["total_nodes"]),
                "total_edges": int(counts["total_edges"]),
                "total_groups": int(counts["total_groups"]),
            }
        )
        if changed:
            meta["changed_since_summary"] = (
                int(meta.get("changed_since_summary", 0)) + 1
            )
        write_graph_meta(self.paths, meta)

    def _get_config_impl(self) -> dict[str, Any]:
        """返回可安全回写的配置，不向客户端暴露显式 API 密钥。"""

        payload = self.settings.model_dump(mode="json")
        kemo = payload["kemo"]
        has_explicit_key = bool(self.settings.kemo.api_key)
        environment_name = self.settings.kemo.api_key_env.strip()
        has_environment_key = bool(
            os.getenv(environment_name, "").strip() if environment_name else ""
        )
        kemo["api_key"] = CONFIG_API_KEY_MASK if has_explicit_key else ""
        kemo["api_key_source"] = (
            "config"
            if has_explicit_key
            else "environment" if has_environment_key else "none"
        )
        return payload

    def _save_config_impl(self, data: dict[str, Any]) -> dict[str, Any]:
        """保存配置并校验；密钥掩码表示保留当前显式密钥。"""
        from .config import AppConfig

        candidate = deepcopy(data)
        raw_kemo = candidate.get("kemo")
        if isinstance(raw_kemo, dict):
            submitted_key = raw_kemo.get("api_key", CONFIG_API_KEY_MASK)
            if submitted_key == CONFIG_API_KEY_MASK:
                raw_kemo["api_key"] = self.settings.kemo.api_key
            raw_kemo.pop("api_key_source", None)

        new_config = AppConfig.model_validate(candidate)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary.write_text(
            new_config.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.config_path)
        self.settings = new_config
        self.data_dir = self._data_dir_override or new_config.resolve_data_dir()
        self.external_dir = (
            self._external_dir_override or new_config.resolve_external_dir()
        )
        self.paths = get_database_paths(self.data_dir)
        self._logger = DailyTSVLogger(
            new_config.resolve_log_dir(),
            new_config.log_level,
        )
        return self.get_config()

    def _import_document_impl(
        self,
        source_path: Path | str,
        *,
        ingest_after_import: bool = True,
        _original_identity: str | None = None,
    ) -> dict[str, Any]:
        """安全转换任意受支持文件、注册来源，并可立即执行整理。"""

        started_at = time.perf_counter()
        try:
            source = _resolve_import_source(source_path)
        except DocumentImportPathError as exc:
            self._log_event(
                "document_import_failed",
                f"error={type(exc).__name__}",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise
        suffix = source.suffix.casefold()
        if suffix not in SUPPORTED_IMPORT_SUFFIXES:
            self._log_event(
                "document_import_failed",
                f"file={source.name}, error=UnsupportedDocumentFormatError",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise UnsupportedDocumentFormatError(
                f"不支持的文档格式：{suffix or '<无扩展名>'}"
            )
        try:
            source_stat = source.stat()
            size = source_stat.st_size
        except OSError as exc:
            raise DocumentImportPathError(f"无法读取文件信息：{source.name}") from exc
        if size > MAX_IMPORT_BYTES:
            self._log_event(
                "document_import_failed",
                f"file={source.name}, error=DocumentTooLargeError",
                _elapsed_ms(started_at),
                level="ERROR",
            )
            raise DocumentTooLargeError(
                f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限：{source.name}"
            )
        origin_hash = _sha256_file(source)
        origin_modified_at = datetime.fromtimestamp(
            source_stat.st_mtime,
            tz=timezone.utc,
        ).isoformat()

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        identity = _original_identity or str(source)
        existing_mapping = ingestor.file_map.get_by_original(identity)
        markdown_relative_path = (
            existing_mapping.markdown_path
            if existing_mapping is not None
            else _stable_markdown_name(source.name, identity)
        )
        destination = _safe_markdown_destination(
            self.external_dir,
            markdown_relative_path,
        )
        previous_bytes = destination.read_bytes() if destination.exists() else None
        self._log_event(
            "document_import_start",
            f"file={source.name}, format={suffix.removeprefix('.')}, size={size}",
        )

        with ingestor._write_lock:
            try:
                conversion = convert_document(
                    source,
                    self.external_dir,
                    destination_name=markdown_relative_path,
                )
                self._log_event(
                    "document_convert",
                    f"file={source.name}, format={conversion['format']}",
                    _elapsed_ms(started_at),
                )
                try:
                    ingestor.file_map.upsert(identity, markdown_relative_path)
                except Exception:
                    _restore_import_destination(destination, previous_bytes)
                    raise
                ingestor.scan_sources()
            except (DocumentConversionError, FileMapError) as exc:
                self._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise
            except Exception as exc:
                self._log_event(
                    "document_import_failed",
                    f"file={source.name}, error={type(exc).__name__}",
                    _elapsed_ms(started_at),
                    level="ERROR",
                )
                raise DocumentImportError(
                    f"文档注册失败：{source.name}: {type(exc).__name__}"
                ) from exc

            source_record = _source_record_for_relative_path(
                self.paths,
                markdown_relative_path,
            )
            source_id = str(source_record["source_id"])
            content_hash = str(source_record["content_hash"])
            source_connection = connect_sources(self.paths)
            try:
                source_connection.execute(
                    """
                    UPDATE sources
                    SET origin_hash = ?, origin_size = ?, origin_modified_at = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        origin_hash,
                        size,
                        origin_modified_at,
                        _now_iso(),
                        source_id,
                    ),
                )
                source_connection.commit()
            finally:
                source_connection.close()
            ingest_status = "pending"
            ingest_result: dict[str, Any] | None = None
            ingest_error: str | None = None
            if ingest_after_import:
                try:
                    ingest_result = ingestor.ingest(
                        paths=[markdown_relative_path],
                        mode="both",
                    )
                    ingest_status = (
                        "failed" if int(ingest_result.get("failed", 0)) else "completed"
                    )
                    if ingest_status == "failed":
                        ingest_error = _ingest_error_summary(ingest_result)
                except Exception as exc:
                    ingest_status = "failed"
                    ingest_error = f"{type(exc).__name__}: {str(exc)[:240]}"

        result: dict[str, Any] = {
            "source_id": source_id,
            "original_filename": source.name,
            "detected_format": _detected_format(suffix),
            "markdown_relative_path": markdown_relative_path,
            "conversion_status": "completed",
            "ingest_status": ingest_status,
            "size": size,
            "origin_hash": origin_hash,
            "content_hash": content_hash,
            "origin_modified_at": origin_modified_at,
        }
        if ingest_result is not None:
            result["ingest"] = ingest_result
        if ingest_error:
            result["ingest_error"] = ingest_error
        self._log_event(
            "document_import_done",
            f"file={source.name}, source_id={source_id}, ingest={ingest_status}",
            _elapsed_ms(started_at),
            level="ERROR" if ingest_status == "failed" else "INFO",
        )
        return result

    def _upload_file_impl(self, content: str, filename: str) -> dict[str, Any]:
        """将文本内容保存为 Markdown 文件，并注册到 sources。"""
        safe_filename = filename.strip()
        if not safe_filename.endswith(".md"):
            safe_filename += ".md"
        safe_filename = safe_filename.replace("\\", "/").split("/")[-1]

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        dest_path = self.external_dir / safe_filename
        with ingestor._write_lock:
            if dest_path.exists():
                raise RecycleConflictError(f"文件已存在：{safe_filename}")
            dest_path.write_text(content, encoding="utf-8")
            try:
                ingestor.scan_sources()
            except Exception:
                dest_path.unlink(missing_ok=True)
                raise

        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                "SELECT source_id, content_hash FROM sources WHERE relative_path = ?",
                (safe_filename,),
            ).fetchone()
            if row is not None:
                encoded = content.encode("utf-8")
                origin_hash = hashlib.sha256(encoded).hexdigest()
                origin_modified_at = _now_iso()
                connection.execute(
                    """
                    UPDATE sources
                    SET origin_hash = ?, origin_size = ?, origin_modified_at = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        origin_hash,
                        len(encoded),
                        origin_modified_at,
                        origin_modified_at,
                        row["source_id"],
                    ),
                )
                connection.commit()
            else:
                origin_hash = None
                origin_modified_at = None
        finally:
            connection.close()
        result = {
            "source_id": row["source_id"] if row is not None else None,
            "filename": safe_filename,
            "path": dest_path.relative_to(self.external_dir).as_posix(),
            "size": len(content),
            "origin_hash": origin_hash,
            "content_hash": row["content_hash"] if row is not None else None,
            "origin_modified_at": origin_modified_at,
        }
        self._log_event(
            "document_import_done",
            f"file={safe_filename}, source_id={result['source_id']}, ingest=pending",
        )
        return result

    def ingest(
        self,
        paths: Sequence[Path | str] | None = None,
        mode: str = "both",
    ) -> dict[str, Any]:
        return Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        ).ingest(paths=paths, mode=mode)

    def _sync_sources_impl(
        self,
        records: Sequence[dict[str, Any]],
        *,
        ingest_after_sync: bool = False,
    ) -> dict[str, Any]:
        """同步上游表记录；kemo-graph 只维护派生 Markdown、Graph 与 RAG。"""

        from .source_sync import sync_external_sources

        return sync_external_sources(
            self,
            records,
            ingest_after_sync=ingest_after_sync,
        )

    def _list_synced_sources_impl(
        self,
        *,
        source_type: str | None = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """分页读取由外部权威数据源同步而来的记录。"""

        from .source_sync import list_external_sources

        return list_external_sources(
            self,
            source_type=source_type,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
        )

    def _delete_synced_sources_impl(self, source_uris: Sequence[str]) -> dict[str, Any]:
        """按稳定 URI 删除外部派生数据，不把派生 Markdown 放入回收站。"""

        from .source_sync import delete_external_sources

        return delete_external_sources(self, source_uris)

    def _organize_graph_impl(
        self,
        *,
        use_llm: bool = True,
        summarize: bool = True,
    ) -> dict[str, Any]:
        """只整理既有图谱投影，不重读文档、不调用 Embedding。"""

        self._require_available("graph", "rag")
        result = GraphOrganizer(
            self.data_dir,
            settings=self.settings,
        ).organize(use_llm=use_llm)
        summary: dict[str, Any] | None = None
        if summarize and result.get("groups_invalidated"):
            summary = self.generate_group_summaries(force=True)
        return {**result, "group_summary": summary}

    def _rebuild_knowledge_base_impl(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """重读新增、变化、删除和失败文档；未变化文档保持跳过。"""

        return KnowledgeBaseRebuilder(
            self.data_dir,
            self.external_dir,
            settings=self.settings,
        ).rebuild_changed(progress=progress)

    def _rebuild_all_impl(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """在影子目录重建 Graph、RAG、FAISS，验证后切换正式目录。"""

        return KnowledgeBaseRebuilder(
            self.data_dir,
            self.external_dir,
            settings=self.settings,
        ).rebuild_all(progress=progress)

    def _list_jobs_impl(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        from .jobs import list_jobs

        return list_jobs(self.data_dir, settings=self.settings, limit=limit)

    def _get_job_impl(self, job_id: str) -> dict[str, Any]:
        from .jobs import get_job

        return get_job(job_id, self.data_dir, settings=self.settings)

    def _query_graph_impl(
        self,
        query: str,
        *,
        depth: int = 3,
        direction: str = "both",
        confidence: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """旧私有入口兼容层；检索实现位于 :class:`RetrievalService`。"""
        return self.retrieval.query_graph(
            query,
            depth=depth,
            direction=direction,
            confidence=confidence,
            force=force,
        )

    def _query_rag_impl(
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

    def _query_hybrid_impl(
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

    def _query_answer_impl(
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

    def _query_answer_uncached(
        self,
        normalized_query: str,
        *,
        graph_depth: int,
        rag_top_k: int | None,
        graph_confidence: float | None,
        rag_threshold: float | None,
        direction: str,
    ) -> dict[str, Any]:
        return self.retrieval.query_answer_uncached(
            normalized_query,
            graph_depth=graph_depth,
            rag_top_k=rag_top_k,
            graph_confidence=graph_confidence,
            rag_threshold=rag_threshold,
            direction=direction,
        )

    def _query_global_impl(
        self,
        query: str,
        top_k: int = 5,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.retrieval.query_global(query, top_k=top_k, force=force)

    def _query_global_uncached(
        self,
        normalized_query: str,
        top_k: int,
    ) -> dict[str, Any]:
        return self.retrieval.query_global_uncached(normalized_query, top_k)

    def _cached_query(
        self,
        query_mode: str,
        query: str,
        params: dict[str, Any],
        execute: Callable[[], dict[str, Any]],
        *,
        force: bool,
    ) -> dict[str, Any]:
        """兼容私有入口；缓存实现已迁移到 RetrievalService。"""

        return self.retrieval.cached_query(
            query_mode,
            query,
            params,
            execute,
            force=force,
        )

    def _list_cached_queries_impl(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self.retrieval.list_cached_queries(page, page_size)

    def _get_cached_query_impl(self, cache_key: str) -> dict[str, Any]:
        return self.retrieval.get_cached_query(cache_key)

    def _clear_search_cache_impl(self, stale_only: bool = False) -> dict[str, Any]:
        return self.retrieval.clear_search_cache(stale_only)

    def _delete_document_impl(self, source_id: str) -> dict[str, Any]:
        self._require_initialized()
        result = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        ).delete_document(source_id)
        # 删除属于隐私边界：旧缓存可能包含已删除正文，因此不保留结果历史。
        try:
            result["search_cache_deleted"] = self.clear_search_cache()["deleted"]
        except Exception:
            result["search_cache_deleted"] = 0
        return result

    def _delete_documents_impl(self, source_ids: Sequence[str]) -> dict[str, Any]:
        """逐一精确删除活动文档，并以结构化结果报告部分失败。"""

        self._require_initialized()
        if isinstance(source_ids, (str, bytes)):
            raise TypeError("source_ids 必须是字符串数组")
        normalized: list[str] = []
        seen: set[str] = set()
        for value in source_ids:
            source_id = str(value).strip()
            if not source_id:
                raise ValueError("source_ids 中不能包含空值")
            if source_id not in seen:
                seen.add(source_id)
                normalized.append(source_id)
        if not normalized:
            raise ValueError("至少选择一篇文档")
        if len(normalized) > 1000:
            raise ValueError("单次最多删除 1000 篇文档")

        ingestor = Ingestor(
            data_dir=self.data_dir,
            external_dir=self.external_dir,
            settings=self.settings,
        )
        deleted: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with ingestor._write_lock:
            for source_id in normalized:
                try:
                    deleted.append(ingestor.delete_document(source_id))
                except Exception as exc:
                    failures.append(
                        {
                            "source_id": source_id,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
        cache_deleted = 0
        if deleted:
            try:
                cache_deleted = int(self.clear_search_cache()["deleted"])
            except Exception:
                cache_deleted = 0
        self._log_event(
            "delete_documents",
            f"requested={len(normalized)}, deleted={len(deleted)}, failed={len(failures)}",
            level="WARNING" if failures else "INFO",
        )
        return {
            "requested": len(normalized),
            "deleted": len(deleted),
            "failed": len(failures),
            "documents": deleted,
            "failures": failures,
            "search_cache_deleted": cache_deleted,
        }

    def _delete_all_documents_impl(self) -> dict[str, Any]:
        """删除当前知识库内的全部活动文档，不跨越 Store 边界。"""

        self._require_initialized()
        connection = connect_sources(self.paths)
        try:
            source_ids = [
                str(row["source_id"])
                for row in connection.execute(
                    """
                    SELECT source_id FROM sources
                    WHERE exists_status = 'active'
                    ORDER BY relative_path, source_id
                    """
                ).fetchall()
            ]
        finally:
            connection.close()
        if not source_ids:
            return {
                "requested": 0,
                "deleted": 0,
                "failed": 0,
                "documents": [],
                "failures": [],
                "search_cache_deleted": 0,
            }
        return self.delete_documents(source_ids)

    def _get_full_graph_impl(
        self,
        nodes_page: int | None = None,
        nodes_page_size: int = 100,
    ) -> dict[str, Any]:
        """旧私有入口兼容层；读取实现位于 GraphService。"""
        return self.graph.get_full_graph(nodes_page, nodes_page_size)

    def _get_graph_visualization_meta_impl(self) -> dict[str, Any]:
        """返回 GPU 图谱分页加载所需的稳定 revision 与数量。"""

        self._require_available("graph")
        return get_visualization_meta(self.paths)

    def _list_graph_visualization_nodes_impl(
        self,
        *,
        page: int = 1,
        page_size: int = 1000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """独立分页返回可视化节点及来源、群组绑定。"""

        self._require_available("graph")
        return list_visualization_nodes(
            self.paths,
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def _list_graph_visualization_edges_impl(
        self,
        *,
        page: int = 1,
        page_size: int = 2000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """独立分页返回可视化关系，不随节点页重复传输。"""

        self._require_available("graph")
        return list_visualization_edges(
            self.paths,
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )

    def _get_graph_neighborhood_impl(
        self,
        node_id: str,
        *,
        depth: int = 2,
        direction: GraphDirection = "both",
        limit: int = 2000,
        edge_limit: int = 10000,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """按节点 ID 返回确定性局部子图，不触发任何模型调用。"""

        self._require_available("graph")
        return get_neighborhood(
            self.paths,
            node_id,
            depth=depth,
            direction=direction,
            limit=limit,
            edge_limit=edge_limit,
            expected_revision=expected_revision,
        )

    # Provider 工厂是领域服务的最小可替换上下文。它们保留旧的
    # ``patch("core.knowledge_base.*Engine")`` 测试/集成注入点，同时不把
    # provider 依赖重新扩散到 API、CLI 或 Web。
    def _new_graph_engine(self) -> GraphEngine:
        return GraphEngine(self.data_dir, settings=self.settings)

    def _new_rag_engine(self) -> RAGEngine:
        return RAGEngine(self.data_dir, settings=self.settings)

    def _new_hybrid_engine(self) -> HybridEngine:
        return HybridEngine(self.data_dir, settings=self.settings)

    def _chat(self, system: str, user: str) -> str:
        return chat(system, user, settings=self.settings)

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log(
                "knowledge_base",
                action,
                detail,
                elapsed_ms,
                level,
            )
        except Exception:
            pass

    def _require_initialized(self) -> None:
        if not self.paths.sources_db.exists():
            raise KnowledgeBaseNotInitializedError("知识库尚未初始化")

    def _require_available(self, *targets: str) -> None:
        self._require_initialized()
        conditions: list[str] = []
        if "graph" in targets:
            conditions.append("graph_status = 'processing'")
        if "rag" in targets:
            conditions.append("rag_status = 'processing'")
        connection = connect_sources(self.paths)
        try:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sources "
                    "WHERE exists_status = 'active' AND ("
                    + " OR ".join(conditions)
                    + ")"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        if count:
            raise KnowledgeBaseProcessingError("知识库正在处理中，请稍后重试")

    def _faiss_is_healthy(self, vector_ids: set[int]) -> bool:
        if not self.paths.faiss_index.exists():
            return not vector_ids
        try:
            manager = FaissIndexManager(
                self.paths.faiss_index,
                self.settings.models.embedding_dimensions,
            )
            return manager.ids == vector_ids
        except Exception:
            return False


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise ValueError("图谱中的 JSON 数组字段格式错误")
    return parsed


def _relation_row(row: Any) -> dict[str, Any]:
    source_keyword = str(row["source_keyword"])
    relation = str(row["relation"])
    target_keyword = str(row["target_keyword"])
    return {
        "edge_id": row["edge_id"],
        "source_node_id": row["source_node_id"],
        "source_keyword": source_keyword,
        "relation": relation,
        "target_node_id": row["target_node_id"],
        "target_keyword": target_keyword,
        "path": f"{source_keyword}->[{relation}]->{target_keyword}",
        "weight": float(row["weight"] or 0.0),
        "support_count": int(row["support_count"] or 0),
        "created_at": row["created_at"],
    }


def _clear_directory_contents(root: Path) -> int:
    """删除目录中的全部内容且不跟随符号链接，返回永久删除的文件数。"""

    deleted = 0
    for child in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        junction_check = getattr(child, "is_junction", None)
        is_junction = bool(junction_check and junction_check())
        if child.is_symlink() or is_junction:
            if not child.name.endswith(".meta.json"):
                deleted += 1
            if is_junction and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
            continue
        if child.is_dir():
            deleted += _clear_directory_contents(child)
            child.rmdir()
            continue
        child.unlink()
        if not child.name.endswith(".meta.json"):
            deleted += 1
    return deleted


def _resolve_import_source(value: Path | str) -> Path:
    if not isinstance(value, (str, Path)):
        raise DocumentImportPathError("source_path 必须是路径字符串")
    raw = os.fspath(value).strip()
    if not raw:
        raise DocumentImportPathError("source_path 不能为空")
    candidate = Path(raw).expanduser()
    if ".." in candidate.parts:
        raise DocumentImportPathError("导入路径不能包含 '..'")
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.exists():
        raise DocumentImportPathError(f"文件不存在：{resolved.name}")
    if not resolved.is_file():
        raise DocumentImportPathError(f"路径不是普通文件：{resolved.name}")
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise DocumentImportPathError(f"文件不可读：{resolved.name}") from exc
    return resolved


def _stable_markdown_name(filename: str, identity: str) -> str:
    stem = Path(filename).stem
    safe_stem = (
        "".join(
            "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
            for character in stem
        ).strip(" .")
        or "document"
    )
    digest = hashlib.sha256(
        os.path.normcase(os.path.normpath(identity)).encode("utf-8")
    ).hexdigest()[:10]
    return f"{safe_stem}-{digest}.md"


def _safe_markdown_destination(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DocumentImportPathError("Markdown 映射路径不安全")
    destination = (root / relative).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise DocumentImportPathError("Markdown 映射路径超出目标目录") from exc
    if destination.suffix.casefold() != ".md":
        raise DocumentImportPathError("Markdown 映射路径必须以 .md 结尾")
    return destination


def _restore_import_destination(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.restore")
    try:
        temporary.write_bytes(previous)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_record_for_relative_path(
    paths: Any,
    relative_path: str,
) -> dict[str, Any]:
    connection = connect_sources(paths)
    try:
        row = connection.execute(
            """
            SELECT source_id, content_hash FROM sources
            WHERE relative_path = ? AND exists_status = 'active'
            """,
            (relative_path,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise DocumentImportError(f"导入后未注册来源：{relative_path}")
    return {
        "source_id": str(row["source_id"]),
        "content_hash": str(row["content_hash"]),
    }


def _source_id_for_relative_path(paths: Any, relative_path: str) -> str:
    """兼容内部旧调用；新代码应读取完整来源记录。"""

    return str(_source_record_for_relative_path(paths, relative_path)["source_id"])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise DocumentImportPathError(f"无法读取文件内容：{path.name}") from exc
    return digest.hexdigest()


def _detected_format(suffix: str) -> str:
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    return suffix.removeprefix(".")


def _ingest_error_summary(result: dict[str, Any]) -> str:
    messages: list[str] = []
    for detail in result.get("details", []):
        if not isinstance(detail, dict):
            continue
        for key in ("error", "graph_error", "rag_error"):
            value = detail.get(key)
            if value:
                messages.append(str(value)[:160])
    return "; ".join(messages[:3]) or "文档整理返回失败状态"


def _build_answer_context(retrieval: Any) -> dict[str, Any]:
    """将混合检索结果压缩为可控、可读且不丢失来源的 LLM 上下文。"""

    if not isinstance(retrieval, dict):
        retrieval = {}
    graph = retrieval.get("graph")
    rag = retrieval.get("rag")
    graph = graph if isinstance(graph, dict) else {}
    rag = rag if isinstance(rag, dict) else {}

    raw_nodes = _dict_items(graph.get("hit_nodes")) + _dict_items(
        graph.get("expanded_nodes")
    )
    nodes: list[dict[str, Any]] = []
    node_names: dict[str, str] = {}
    seen_node_ids: set[str] = set()
    for item in raw_nodes:
        node_id = str(item.get("node_id") or "").strip()
        keyword = str(item.get("keyword") or node_id).strip()
        identity = node_id or keyword.casefold()
        if not identity or identity in seen_node_ids:
            continue
        seen_node_ids.add(identity)
        if node_id:
            node_names[node_id] = keyword or node_id
        nodes.append(
            {
                "node_id": node_id,
                "keyword": keyword,
                "summary": _clip_context_text(item.get("summary"), 800),
                "aliases": item.get("aliases") if isinstance(item.get("aliases"), list) else [],
                "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
                "match_score": item.get("match_score"),
                "depth": item.get("depth"),
            }
        )
        if len(nodes) >= 30:
            break

    relationships: list[dict[str, Any]] = []
    for edge in _dict_items(graph.get("edges"))[:40]:
        source_id = str(edge.get("source_node_id") or "")
        target_id = str(edge.get("target_node_id") or "")
        source = node_names.get(source_id, source_id)
        target = node_names.get(target_id, target_id)
        relation = _clip_context_text(edge.get("relation"), 160)
        relationships.append(
            {
                "text": f"{source} →[{relation}]→ {target}",
                "weight": edge.get("weight"),
            }
        )

    relationship_paths = [
        _clip_context_text(path.get("text"), 700)
        for path in _dict_items(graph.get("paths"))[:20]
        if str(path.get("text") or "").strip()
    ]
    groups = [
        {
            "group_id": str(item.get("group_id") or ""),
            "summary": _clip_context_text(item.get("summary"), 1200),
            "node_ids": item.get("node_ids") if isinstance(item.get("node_ids"), list) else [],
        }
        for item in _dict_items(graph.get("groups"))[:8]
    ]

    rag_passages: list[dict[str, Any]] = []
    for item in _dict_items(rag.get("results"))[:12]:
        source = item.get("source")
        source = source if isinstance(source, dict) else {}
        parent = item.get("context")
        parent = parent if isinstance(parent, dict) else {}
        matched_content = _clip_context_text(item.get("content"), 1600)
        parent_content = _clip_context_text(parent.get("content"), 3200)
        rag_passages.append(
            {
                "chunk_id": str(item.get("chunk_id") or ""),
                "content": parent_content or matched_content,
                "matched_content": (
                    matched_content
                    if parent_content and matched_content != parent_content
                    else ""
                ),
                "score": item.get("score"),
                "granularity": item.get("granularity"),
                "context_granularity": parent.get("granularity"),
                "source": str(
                    source.get("relative_path") or source.get("source_id") or "未知来源"
                ),
            }
        )

    semantic_entities = [
        {
            "node_id": str(item.get("node_id") or ""),
            "keyword": str(item.get("keyword") or ""),
            "summary": _clip_context_text(item.get("summary"), 800),
            "score": item.get("score"),
        }
        for item in _dict_items(retrieval.get("entities"))[:10]
    ]
    semantic_communities = [
        {
            "group_id": str(item.get("group_id") or ""),
            "summary": _clip_context_text(item.get("summary"), 1200),
            "score": item.get("score"),
        }
        for item in _dict_items(retrieval.get("communities"))[:6]
    ]
    return {
        "graph_nodes": nodes,
        "relationships": relationships,
        "relationship_paths": relationship_paths,
        "groups": groups,
        "rag_passages": rag_passages,
        "semantic_entities": semantic_entities,
        "semantic_communities": semantic_communities,
    }


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _load_source_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip_context_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
