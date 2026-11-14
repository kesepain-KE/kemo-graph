from __future__ import annotations

import hashlib
from typing import Any
from core.documents.errors import DocumentNotFoundError
from core.documents.formats import MAX_IMPORT_BYTES, SUPPORTED_IMPORT_SUFFIXES
from core.documents.library import _write_lock, original_root, resolve_path, source_rows

def refresh_library(owner: Any, path: str = "", *, force: bool = False) -> dict[str, Any]:
    """Read actual files and refresh derived Markdown without model requests."""
    with _write_lock(owner):
        original_root(owner).mkdir(parents=True, exist_ok=True)
        deleted = []
        if not path:
            from core.ingest.pipeline import Ingestor
            from core.ingest.scan import cleanup_orphaned_sources

            ingestor = Ingestor(owner.data_dir, owner.external_dir, settings=owner.settings)
            _, deleted = cleanup_orphaned_sources(ingestor, remove_unmapped=True)
            if deleted:
                owner.clear_search_cache()
        root = resolve_path(owner, path, must_exist=False)
        indexed = {row["library_path"]: row for row in source_rows(owner)}
        if not root.exists() and not any(
            relative == path or relative.startswith(path + "/") for relative in indexed
        ):
            raise DocumentNotFoundError(f"文件或目录不存在：{path}")
        files = [root] if root.is_file() else [
            item for item in root.rglob("*")
            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(root).parts)
        ]
        # Clear missing originals before imports run their own source scan.
        # Otherwise that scan may delete the same source ahead of this pass.
        for relative, row in indexed.items():
            if path and relative != path and not relative.startswith(path + "/"):
                continue
            if not resolve_path(owner, relative, must_exist=False).is_file():
                owner.delete_document(row["source_id"])
                deleted.append(relative)
        updated: list[str] = []
        errors: list[dict[str, str]] = []
        for file in files:
            relative = file.relative_to(original_root(owner)).as_posix()
            # Reject links even if they point at another file inside the library.
            resolve_path(owner, relative)
            if file.suffix.casefold() not in SUPPORTED_IMPORT_SUFFIXES:
                continue
            if file.stat().st_size > MAX_IMPORT_BYTES:
                errors.append({"path": relative, "message": "文件超过 50 MB 上限"})
                continue
            row = indexed.get(relative)
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            if not force and row and row["origin_hash"] == digest and (owner.external_dir / row["relative_path"]).is_file():
                continue
            try:
                owner.import_document(file, ingest_after_import=False)
                updated.append(relative)
            except Exception as exc:
                errors.append({"path": relative, "message": str(exc)})
        return {"updated": updated, "deleted": deleted, "errors": errors}

def build_paths(owner: Any, path: str, *, reprocess: bool = False) -> list[str]:
    result = refresh_library(owner, path, force=reprocess)
    if result["errors"]:
        raise ValueError("原文件处理失败：" + "；".join(
            f"{item['path']}: {item['message']}" for item in result["errors"]
        ))
    return [
        row["relative_path"] for row in source_rows(owner)
        if not path or row["library_path"] == path or row["library_path"].startswith(path + "/")
    ]
