"""文档域服务。

文档导入、编辑、同步和删除的实现暂时留在兼容门面中，以便保持复杂的
事务边界；公开域入口在此集中，并通过 owner 的显式 ``*_impl`` 方法
委托。后续可逐步把实现下沉而不改 HTTP/CLI 合约。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .protocols import ServiceOwner


class DocumentService:
    """文档生命周期与外部来源同步入口。"""

    def __init__(self, owner: ServiceOwner) -> None:
        self.owner = owner

    def list_projects(self) -> dict[str, Any]:
        from ..document_organization import list_projects
        return list_projects(self.owner)

    def create_project(self, name: str) -> dict[str, Any]:
        from ..document_organization import create_project
        return create_project(self.owner, name)

    def relocate_documents(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        from ..document_organization import relocate_documents
        return relocate_documents(self.owner, changes)

    def list_documents(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        *,
        project: str | None = None,
        search: str | None = None,
        graph_status: str | None = None,
        rag_status: str | None = None,
        include_summary: bool = False,
    ) -> dict[str, Any]:
        if (
            project is None
            and search is None
            and graph_status is None
            and rag_status is None
            and not include_summary
        ):
            # Preserve the original three-argument call shape for embedders
            # that provide a lightweight owner/mock implementation.
            return self.owner._list_documents_impl(status, page, page_size)
        return self.owner._list_documents_impl(
            status,
            page,
            page_size,
            project=project,
            search=search,
            graph_status=graph_status,
            rag_status=rag_status,
            include_summary=include_summary,
        )

    def get_document_content(self, source_id: str) -> dict[str, Any]:
        return self.owner._get_document_content_impl(source_id)

    def update_document_content(
        self,
        source_id: str,
        content: str,
        *,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        return self.owner._update_document_content_impl(
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
        _original_identity: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        return self.owner._import_document_impl(
            source_path,
            ingest_after_import=ingest_after_import,
            expected_origin_hash=expected_origin_hash,
            _original_identity=_original_identity,
            **({"project": project} if project is not None else {}),
        )

    def upload_file(self, content: str, filename: str) -> dict[str, Any]:
        return self.owner._upload_file_impl(content, filename)

    def sync_sources(
        self,
        records: Sequence[dict[str, Any]],
        *,
        ingest_after_sync: bool = False,
    ) -> dict[str, Any]:
        return self.owner._sync_sources_impl(
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
        return self.owner._list_synced_sources_impl(
            source_type=source_type,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
        )

    def delete_synced_sources(self, source_uris: Sequence[str]) -> dict[str, Any]:
        return self.owner._delete_synced_sources_impl(source_uris)

    def delete_document(self, source_id: str) -> dict[str, Any]:
        return self.owner._delete_document_impl(source_id)

    def delete_documents(self, source_ids: Sequence[str]) -> dict[str, Any]:
        return self.owner._delete_documents_impl(source_ids)

    def delete_all_documents(self, project: str | None = None) -> dict[str, Any]:
        if project is None:
            return self.owner._delete_all_documents_impl()
        return self.owner._delete_all_documents_impl(project=project)
