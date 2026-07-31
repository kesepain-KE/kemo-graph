"""kemo-graph 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.config import DEFAULT_CONFIG_PATH, load_config
from core.graph_engine import GraphQueryError
from core.ingestor import DocumentNotFoundError, IngestError, RecycleConflictError
from core.knowledge_base import (
    DocumentImportError,
    DocumentImportPathError,
    DocumentTooLargeError,
    KnowledgeBaseNotInitializedError,
    KnowledgeBaseProcessingError,
    KnowledgeBaseService,
    UnsupportedDocumentFormatError,
)
from core.rag_engine import RAGQueryError
from provider.tools.document_tools import DocumentConversionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kemo-graph 知识库命令行工具")
    parser.add_argument("--data-dir", help="知识库数据目录")
    parser.add_argument("--external-dir", help="Markdown 文档目录")
    parser.add_argument("--config", help="config.json 路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="整理 Markdown 文档")
    ingest_parser.add_argument("paths", nargs="*", help="要处理的 Markdown 路径")
    ingest_parser.add_argument(
        "--mode",
        choices=("graph", "rag", "both"),
        default="both",
    )

    import_parser = subparsers.add_parser("import", help="转换并导入本地文档")
    import_parser.add_argument("path", help="PDF、DOCX、Markdown、TXT、HTML、RST 或 CSV 路径")
    import_parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="只转换和注册，不立即构建 Graph/RAG",
    )

    graph_parser = subparsers.add_parser("query-graph", help="执行图谱检索")
    graph_parser.add_argument("query")
    graph_parser.add_argument("--depth", type=int, default=3)
    graph_parser.add_argument(
        "--direction",
        choices=("forward", "backward", "both"),
        default="both",
    )
    graph_parser.add_argument("--confidence", type=float)

    rag_parser = subparsers.add_parser("query-rag", help="执行向量检索")
    rag_parser.add_argument("query")
    rag_parser.add_argument("--top-k", type=int)
    rag_parser.add_argument("--threshold", type=float)

    hybrid_parser = subparsers.add_parser("query-hybrid", help="执行混合检索")
    hybrid_parser.add_argument("query")
    hybrid_parser.add_argument("--graph-depth", type=int, default=3)
    hybrid_parser.add_argument("--rag-top-k", type=int)
    hybrid_parser.add_argument("--graph-confidence", type=float)
    hybrid_parser.add_argument("--rag-threshold", type=float)
    hybrid_parser.add_argument(
        "--direction",
        choices=("forward", "backward", "both"),
        default="both",
    )

    subparsers.add_parser("status", help="查看知识库状态")
    delete_parser = subparsers.add_parser("delete-doc", help="删除文档")
    delete_parser.add_argument("source_id")
    subparsers.add_parser("list-docs", help="列出所有文档")
    content_parser = subparsers.add_parser("doc-content", help="查看文档内容")
    content_parser.add_argument("source_id")
    delete_node_parser = subparsers.add_parser("delete-node", help="删除图谱节点")
    delete_node_parser.add_argument("node_id")
    subparsers.add_parser("summarize", help="生成节点群总结")
    subparsers.add_parser("cleanup-recycle", help="清理过期回收站文件")
    subparsers.add_parser("config-get", help="查看当前配置")
    config_set_parser = subparsers.add_parser("config-set", help="更新配置")
    config_set_parser.add_argument("key", help="配置键（如 rag.chunk_size）")
    config_set_parser.add_argument("value", help="配置值")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = (
        Path(args.config or os.getenv("KEMO_GRAPH_CONFIG", DEFAULT_CONFIG_PATH))
        .expanduser()
        .resolve()
    )
    try:
        settings = load_config(config_path)
        service = KnowledgeBaseService(
            settings=settings,
            data_dir=args.data_dir,
            external_dir=args.external_dir,
            config_path=config_path,
        )
        data = _dispatch(args, service)
        _print_json({"ok": True, "data": data, "error": None})
        return 0
    except Exception as exc:
        code, message, exit_code = _map_error(exc)
        _print_json(
            {
                "ok": False,
                "data": None,
                "error": {"code": code, "message": message},
            }
        )
        return exit_code


def _dispatch(
    args: argparse.Namespace, service: KnowledgeBaseService
) -> dict[str, Any]:
    if args.command == "ingest":
        return service.ingest(paths=args.paths or None, mode=args.mode)
    if args.command == "import":
        return service.import_document(
            args.path,
            ingest_after_import=not args.no_ingest,
        )
    if args.command == "query-graph":
        return service.query_graph(
            args.query,
            depth=args.depth,
            direction=args.direction,
            confidence=args.confidence,
        )
    if args.command == "query-rag":
        return service.query_rag(
            args.query,
            top_k=args.top_k,
            threshold=args.threshold,
        )
    if args.command == "query-hybrid":
        return service.query_hybrid(
            args.query,
            graph_depth=args.graph_depth,
            rag_top_k=args.rag_top_k,
            graph_confidence=args.graph_confidence,
            rag_threshold=args.rag_threshold,
            direction=args.direction,
        )
    if args.command == "status":
        return service.status()
    if args.command == "delete-doc":
        return service.delete_document(args.source_id)
    if args.command == "list-docs":
        return {"documents": service.list_documents()}
    if args.command == "doc-content":
        return service.get_document_content(args.source_id)
    if args.command == "delete-node":
        return service.delete_node(args.node_id)
    if args.command == "summarize":
        return service.generate_group_summaries()
    if args.command == "cleanup-recycle":
        return service.cleanup_recycle()
    if args.command == "config-get":
        return service.get_config()
    if args.command == "config-set":
        return _set_config(service, args.key, args.value)
    raise ValueError(f"未知命令：{args.command}")


def _set_config(
    service: KnowledgeBaseService, key: str, value: str
) -> dict[str, Any]:
    import json as _json

    current = service.get_config()
    keys = key.split(".")
    target = current
    for part in keys[:-1]:
        if part not in target:
            raise ValueError(f"配置键不存在：{part}")
        target = target[part]
    last = keys[-1]
    if last not in target:
        raise ValueError(f"配置键不存在：{last}")
    try:
        parsed = _json.loads(value)
    except _json.JSONDecodeError:
        parsed = value
    target[last] = parsed
    return service.save_config(current)


def _map_error(exc: Exception) -> tuple[str, str, int]:
    if isinstance(exc, KnowledgeBaseNotInitializedError):
        return "NOT_INITIALIZED", str(exc), 2
    if isinstance(exc, KnowledgeBaseProcessingError):
        return "PROCESSING", str(exc), 3
    if isinstance(exc, DocumentNotFoundError):
        return "NOT_FOUND", str(exc), 2
    if isinstance(exc, UnsupportedDocumentFormatError):
        return "UNSUPPORTED_FORMAT", str(exc), 2
    if isinstance(exc, DocumentTooLargeError):
        return "FILE_TOO_LARGE", str(exc), 2
    if isinstance(exc, DocumentImportPathError):
        return "INVALID_PATH", str(exc), 2
    if isinstance(exc, DocumentConversionError):
        return "CONVERSION_FAILED", str(exc), 2
    if isinstance(exc, DocumentImportError):
        return "IMPORT_FAILED", str(exc), 2
    if isinstance(
        exc,
        (
            GraphQueryError,
            RAGQueryError,
            RecycleConflictError,
            IngestError,
            ValueError,
            TypeError,
        ),
    ):
        return "INVALID_PARAM", str(exc), 2
    return "INTERNAL", "内部错误", 1


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
