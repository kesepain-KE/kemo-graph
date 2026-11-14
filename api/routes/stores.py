from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, File, Form, Query, UploadFile
from core.settings import load_config
from core.service.federation import describe_store
from core.service.federation import initialize_store
from api.errors import success_response
from api.routes.store_context import _store_operation
from api.schemas import APIResponse, StoreImportPathRequest, StoreInitializeRequest, StoreRootRequest, StoreUploadRequest

from api.routes.dependencies import Context
from api.routes.uploads import _stage_uploaded_document, _raise_import_failure
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/initialize", response_model=APIResponse)
def post_store_initialize(
    payload: StoreInitializeRequest,
    context: Context,
) -> dict:
    settings = load_config(context.config_path)
    return success_response(
        initialize_store(
            payload.store_root,
            scope=payload.scope,
            owner_id=payload.owner_id,
            display_name=payload.display_name,
            settings=settings,
        )
    )

@router.post("/stores/info", response_model=APIResponse)
def post_store_info(payload: StoreRootRequest, context: Context) -> dict:
    return success_response(
        describe_store(
            payload.store_root,
            settings=load_config(context.config_path),
            config_path=context.config_path,
        )
    )

@router.post("/stores/status", response_model=APIResponse)
def post_store_status(payload: StoreRootRequest, context: Context) -> dict:
    return success_response(
        _store_operation(payload.store_root, context, lambda service: service.status())
    )

@router.post("/stores/import-path", response_model=APIResponse)
def post_store_import_path(
    payload: StoreImportPathRequest,
    context: Context,
) -> dict:
    if not Path(payload.path).expanduser().is_absolute():
        raise ValueError("Store import-path 只接受绝对文件路径")
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.import_document(
                payload.path,
                ingest_after_import=payload.ingest_after_import,
                expected_origin_hash=payload.expected_origin_hash,
                directory=payload.directory,
            ),
        )
    )

@router.post("/stores/import", response_model=APIResponse)
async def post_store_import(
    context: Context,
    file: Annotated[UploadFile, File(description="要转换并导入的本地文档")],
    store_root: Annotated[
        str,
        Form(description="portable Store 绝对路径；使用表单字段避免进入 URL 日志"),
    ],
    ingest_after_import: Annotated[
        bool,
        Query(alias="ingest", description="转换后是否立即整理图谱与 RAG"),
    ] = False,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="kemo-graph-store-import-") as temporary_dir:
        filename, staged = await _stage_uploaded_document(file, Path(temporary_dir))
        data = _store_operation(
            store_root,
            context,
            lambda service: service.import_document(
                staged,
                ingest_after_import=ingest_after_import,
            ),
        )
        result = data.get("result")
        if isinstance(result, dict):
            _raise_import_failure(result)
    return success_response(data)

@router.post("/stores/upload", response_model=APIResponse)
def post_store_upload(payload: StoreUploadRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.upload_file(payload.content, payload.filename),
        )
    )
