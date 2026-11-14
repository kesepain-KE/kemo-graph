"""The original-file library and its source-to-Markdown index."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from core.storage.connections import connect_sources
from core.storage.schema import initialize_databases
from core.ingest.errors import DocumentNotFoundError
from core.ingest.file_map import FileMapStore, FileMapping
from core.ingest.paths import _path_key
from core.documents.errors import DocumentContentConflictError
from core.documents.formats import MAX_IMPORT_BYTES, SUPPORTED_IMPORT_SUFFIXES
from core.common.jsonio import write_bytes_atomic as _write_bytes_atomic
from core.storage.locks import get_knowledge_base_lock
from markitdown._utils import read_text

TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".log", ".rst", ".csv", ".tsv",
    ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml", ".html", ".htm",
}

class LibraryNameConflictError(DocumentContentConflictError):
    """A paste destination already exists; nothing has been overwritten."""


@contextmanager
def _write_lock(owner: Any):
    lock = get_knowledge_base_lock(owner.data_dir)
    if not lock.acquire(blocking=False):
        raise DocumentContentConflictError("知识库正在构建或修改，请稍后再执行文件操作")
    try:
        yield
    finally:
        lock.release()


def _is_link(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()


def original_root(owner: Any) -> Path:
    root = owner.external_dir.parent / "originals"
    if root.exists() and _is_link(root):
        raise ValueError("原文件根目录不能是符号链接或目录联接")
    return root.absolute()


def resolve_path(owner: Any, path: str = "", *, must_exist: bool = True) -> Path:
    """Validate paths at the filesystem boundary, including junctions/symlinks."""
    if not isinstance(path, str) or "\\" in path:
        raise ValueError("目录路径必须使用 / 分隔")
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or ":" in path:
        raise ValueError("知识库路径必须是库内相对路径")
    if path and relative.as_posix() != path:
        raise ValueError("请使用规范的库内相对路径")
    root = original_root(owner)
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("路径超出原文件目录")
    if any(part.startswith(".") for part in relative.parts):
        raise ValueError("不允许访问隐藏文件或目录")
    current = candidate
    while current != root:
        if current.exists() and _is_link(current):
            raise ValueError("知识库不支持符号链接或目录联接")
        current = current.parent
    if must_exist and not candidate.exists():
        raise DocumentNotFoundError(f"文件或目录不存在：{path}")
    return candidate


def validate_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    if not name or len(name) > 160 or name.startswith(".") or name.endswith((".", " ")):
        raise ValueError("文件名不能为空、过长或以点开头/结尾")
    if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name):
        raise ValueError("文件名包含不允许的字符")
    if name.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("文件名是系统保留名称")
    return name


def source_rows(owner: Any) -> list[Any]:
    if not owner.paths.sources_db.is_file():
        return []
    connection = connect_sources(owner.paths)
    try:
        return connection.execute(
            "SELECT * FROM sources WHERE library_path IS NOT NULL AND exists_status = 'active'"
        ).fetchall()
    finally:
        connection.close()


def entry(owner: Any, path: Path, indexed: dict[str, Any]) -> dict[str, Any]:
    relative = path.relative_to(original_root(owner)).as_posix()
    row = indexed.get(relative)
    info = path.stat()
    return {
        "name": path.name,
        "path": relative,
        "kind": "directory" if path.is_dir() else "file",
        "size": info.st_size if path.is_file() else None,
        "modified_at": info.st_mtime,
        "source_id": row["source_id"] if row else None,
        "supported": path.suffix.casefold() in SUPPORTED_IMPORT_SUFFIXES,
    }


def list_library(owner: Any, directory: str = "", search: str = "") -> dict[str, Any]:
    # Browsing originals does not need the Graph/RAG write lock, which may be
    # held for minutes by a model request. SQLite supplies the source snapshot.
    root = original_root(owner)
    root.mkdir(parents=True, exist_ok=True)
    folder = resolve_path(owner, directory)
    if not folder.is_dir():
        raise ValueError("所选路径不是目录")
    indexed = {row["library_path"]: row for row in source_rows(owner)}

    def children(parent: Path) -> list[Path]:
        return sorted(
            (path for path in parent.iterdir()
             if not path.name.startswith(".") and not _is_link(path)),
            key=lambda path: (not path.is_dir(), path.name.casefold()),
        )

    def tree(parent: Path) -> list[dict[str, Any]]:
        return [
            {**entry(owner, path, indexed), **({"children": tree(path)} if path.is_dir() else {})}
            for path in children(parent)
        ]

    nodes = tree(root)
    if search.strip():
        term = search.strip().casefold()

        def matches(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                found
                for item in items
                for found in (
                    ([item] if term in item["path"].casefold() else [])
                    + matches(item.get("children", []))
                )
            ]
        entries = matches(nodes)
    else:
        entries = [entry(owner, path, indexed) for path in children(folder)]
    return {
        "root": str(root),
        "directory": directory,
        "tree": nodes,
        "entries": entries,
    }


def create_entry(owner: Any, directory: str, name: str, kind: str) -> dict[str, Any]:
    with _write_lock(owner):
        original_root(owner).mkdir(parents=True, exist_ok=True)
        parent = resolve_path(owner, directory)
        if not parent.is_dir():
            raise ValueError("目标路径不是目录")
        name = validate_name(name)
        destination = parent / name
        if destination.exists():
            raise DocumentContentConflictError("同名文件或目录已存在")
        if kind == "directory":
            destination.mkdir()
        elif kind == "file":
            if destination.suffix.casefold() not in {".md", ".markdown", ".txt"}:
                raise ValueError("新建文档请使用 .md、.markdown 或 .txt 扩展名")
            heading = f"# {destination.stem}\n" if destination.suffix.casefold() in {".md", ".markdown"} else f"{destination.stem}\n"
            _write_bytes_atomic(destination, heading.encode("utf-8"))
            try:
                owner.import_document(destination, ingest_after_import=False)
            except Exception:
                destination.unlink()
                raise
        else:
            raise ValueError("kind 必须是 file 或 directory")
        return entry(owner, destination, {})


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


def original_for_source(owner: Any, source_id: str) -> Path:
    owner._require_initialized()
    connection = connect_sources(owner.paths)
    try:
        row = connection.execute(
            "SELECT library_path FROM sources WHERE source_id = ? AND exists_status = 'active'",
            (source_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["library_path"] is None:
        raise DocumentNotFoundError("没有可用的原文件，请重新导入原始资料")
    path = resolve_path(owner, row["library_path"])
    if not path.is_file():
        raise DocumentNotFoundError("原文件不存在")
    return path


def read_content(owner: Any, source_id: str) -> dict[str, Any]:
    path = original_for_source(owner, source_id)
    if path.suffix.casefold() not in TEXT_SUFFIXES:
        raise ValueError("该文件为二进制文档，请使用原文件预览")
    connection = connect_sources(owner.paths)
    try:
        row = connection.execute(
            "SELECT content_hash AS processed_content_hash, graph_hash, rag_hash, graph_status, rag_status FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    finally:
        connection.close()
    return {
        **dict(row),
        "source_id": source_id,
        "relative_path": path.relative_to(original_root(owner)).as_posix(),
        "original_path": str(path),
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content": read_text(path),
    }


def update_content(owner: Any, source_id: str, content: str, expected_hash: str | None) -> dict[str, Any]:
    from core.documents.formats import MAX_IMPORT_BYTES
    from core.documents.errors import DocumentTooLargeError
    with _write_lock(owner):
        path = original_for_source(owner, source_id)
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            raise ValueError("只能编辑原始文本文件")
        previous = path.read_bytes()
        current_hash = hashlib.sha256(previous).hexdigest()
        if expected_hash is not None and expected_hash.casefold() != current_hash:
            raise DocumentContentConflictError("原文件已变化，请重新读取后再编辑")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_IMPORT_BYTES:
            raise DocumentTooLargeError("文件超过 50 MB 上限")
        new_hash = hashlib.sha256(encoded).hexdigest()
        if encoded != previous:
            _write_bytes_atomic(path, encoded)
            try:
                owner.import_document(path, ingest_after_import=False)
            except Exception:
                _write_bytes_atomic(path, previous)
                raise
        return {
            "source_id": source_id,
            "relative_path": path.relative_to(original_root(owner)).as_posix(),
            "changed": encoded != previous,
            "previous_content_hash": current_hash,
            "content_hash": new_hash,
            "graph_status": "pending",
            "rag_status": "pending",
        }


def relocate_entry(owner: Any, path: str, destination: str) -> dict[str, Any]:
    """Move originals while retaining source IDs and processed artifact paths."""
    with _write_lock(owner):
        source = resolve_path(owner, path)
        if source == original_root(owner):
            raise ValueError("不能移动知识库根目录")
        target = resolve_path(owner, destination, must_exist=False)
        name = validate_name(target.name)
        if name != target.name:
            destination = (PurePosixPath(destination).parent / name).as_posix()
            target = resolve_path(owner, destination, must_exist=False)
        if target.exists():
            raise DocumentContentConflictError("目标文件或目录已存在")
        if source.is_file() and source.suffix.casefold() != target.suffix.casefold():
            raise ValueError("重命名请保留原文件扩展名")
        if not target.parent.is_dir() or target.is_relative_to(source):
            raise ValueError("目标目录不存在或位于源目录内部")
        initialize_databases(owner.data_dir, owner.settings)
        connection = connect_sources(owner.paths)
        file_map = FileMapStore(owner.external_dir / "file_map.json")
        previous = file_map.list()
        moved = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM maintenance_jobs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone():
                raise DocumentContentConflictError("请等待后台构建任务结束后再移动文件")
            rows = connection.execute(
                "SELECT * FROM sources WHERE library_path IS NOT NULL AND exists_status = 'active'"
            ).fetchall()
            changes = {}
            for row in rows:
                old = row["library_path"]
                if old == path or old.startswith(path + "/"):
                    new = destination + old[len(path):]
                    changes[row["original_path"]] = str(original_root(owner) / new)
                    connection.execute(
                        "UPDATE sources SET library_path = NULL WHERE library_path = ? AND exists_status = 'deleted'",
                        (new,),
                    )
                    connection.execute(
                        "UPDATE sources SET original_path = ?, library_path = ? WHERE source_id = ?",
                        (changes[row["original_path"]], new, row["source_id"]),
                    )
            source.rename(target)
            moved = True
            destinations = {_path_key(value) for value in changes.values()}
            file_map._write([
                FileMapping(changes.get(mapping.original_path, mapping.original_path), mapping.markdown_path)
                for mapping in previous
                if mapping.original_path in changes or _path_key(mapping.original_path) not in destinations
            ])
            connection.commit()
        except Exception:
            connection.rollback()
            if moved:
                target.rename(source)
            # A failed atomic map write leaves the original map intact.
            if file_map.list() != previous:
                file_map._write(previous)
            raise
        finally:
            connection.close()
        owner.clear_search_cache()
        return {"path": destination}


def _copy_file(source: Path | str, destination: Path | str) -> None:
    # Exclusive creation is essential: a concurrent filesystem writer must
    # never turn a failed paste into an overwrite.
    with Path(source).open("rb") as reader, Path(destination).open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    shutil.copystat(source, destination)


def transfer_entry(
    owner: Any, path: str, directory: str, mode: str, *, conflict: str = "error",
) -> dict[str, Any]:
    """Paste an original into a directory; moves retain the existing mapping."""
    with _write_lock(owner):
        source = resolve_path(owner, path)
        root = original_root(owner)
        if source == root:
            raise ValueError("不能复制或移动知识库根目录")
        folder = resolve_path(owner, directory)
        if not folder.is_dir():
            raise ValueError("粘贴目标不是目录")
        if source.is_dir() and folder.is_relative_to(source):
            raise ValueError("不能将目录复制或移动到自身及其子目录")
        if mode == "cut" and folder == source.parent:
            raise ValueError("所选对象已在目标目录中")
        target = folder / source.name
        if target.exists():
            if conflict != "keep_both":
                raise LibraryNameConflictError(f"目标目录已存在“{source.name}”，请选择保留两者或取消")
            suffix = source.suffix if source.is_file() else ""
            stem = source.stem if suffix else source.name
            number = 1
            while target.exists():
                addition = " - 副本" + (f" ({number})" if number > 1 else "")
                name = stem[:160 - len(addition) - len(suffix)] + addition + suffix
                target = folder / validate_name(name)
                number += 1
        destination = target.relative_to(root).as_posix()
        resolve_path(owner, destination, must_exist=False)
        if mode == "cut":
            return relocate_entry(owner, path, destination)
        if mode != "copy":
            raise ValueError("文件操作必须是 copy 或 cut")
        # Validate nested reparse points before copytree can follow any of
        # them. Hidden regular files stay with their containing directory.
        if source.is_dir():
            for current, directories, files in os.walk(source, followlinks=False):
                for name in directories + files:
                    if _is_link(Path(current) / name):
                        raise ValueError("目录包含符号链接或目录联接，不能复制")
        created = False
        try:
            if source.is_dir():
                target.mkdir()
                created = True
                shutil.copytree(source, target, dirs_exist_ok=True, copy_function=_copy_file)
            else:
                # Mark ownership only after the exclusive open succeeds.
                with source.open("rb") as reader, target.open("xb") as writer:
                    created = True
                    shutil.copyfileobj(reader, writer)
                shutil.copystat(source, target)
        except Exception as exc:
            if created:
                verified = resolve_path(owner, destination)
                if verified.is_dir():
                    shutil.rmtree(verified)
                else:
                    verified.unlink()
            if isinstance(exc, FileExistsError):
                raise LibraryNameConflictError("粘贴时出现同名项目，请选择保留两者或取消") from exc
            raise
        # Copies are new originals, not aliases of the old source. Their own
        # source/Markdown mapping is created by refresh or the next build.
        return {"path": destination}


def delete_entry(owner: Any, path: str) -> dict[str, Any]:
    with _write_lock(owner):
        source = resolve_path(owner, path, must_exist=False)
        if source == original_root(owner):
            raise ValueError("不能删除知识库根目录")
        rows = [
            row for row in source_rows(owner)
            if row["library_path"] == path or row["library_path"].startswith(path + "/")
        ]
        if not source.exists() and not rows:
            raise DocumentNotFoundError(f"文件或目录不存在：{path}")
        for row in rows:
            owner.delete_document(row["source_id"])
        # resolve_path verifies the target is inside the original file root
        # and does not traverse symbolic links or directory junctions.
        if source.exists():
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        return {"deleted": path, "sources_deleted": len(rows)}


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
