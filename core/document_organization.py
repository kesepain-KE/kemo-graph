"""Physical project folders and identity-preserving Markdown relocation.

This module changes paths, not document contents or graph/vector identities.
All mutations share the knowledge-base write lock with import and ingest.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from .db import connect_sources
from .ingestor import DocumentNotFoundError, FileMapStore
from .ingestor._utils import _hash_relative_path, _now_iso, _safe_source_path
from .knowledge_models import DocumentContentConflictError
from .locks import get_knowledge_base_lock


def validate_name(name: str, *, document: bool = False) -> str:
    if not isinstance(name, str):
        raise ValueError("名称必须是字符串")
    name = unicodedata.normalize("NFKC", name).strip()
    if not name or len(name) > 160 or name.startswith(".") or name.endswith((".", " ")):
        raise ValueError("名称不能为空、过长或以点开头/结尾")
    if re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name) or ".." in name:
        raise ValueError("名称不能包含路径分隔符、路径遍历或特殊控制字符")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("名称是系统保留名称")
    if document and not name.casefold().endswith(".md"):
        name += ".md"
    return name


def project_directory(owner: Any, project: str) -> Path:
    root = owner.external_dir.resolve()
    if project == "":
        return root
    name = validate_name(project)
    candidate = root / name
    if candidate.is_symlink() or candidate.resolve().parent != root:
        raise ValueError("项目路径不安全")
    if not candidate.is_dir():
        raise ValueError("项目文件夹不存在，请先创建项目")
    return candidate


def list_projects(owner: Any) -> dict[str, Any]:
    with get_knowledge_base_lock(owner.data_dir):
        root = owner.external_dir.resolve()
        counts: dict[str, int] = {"": 0}
        if root.exists():
            for item in root.iterdir():
                if item.is_dir() and not item.is_symlink() and not item.name.startswith("."):
                    counts[item.name] = 0
        if owner.paths.sources_db.exists():
            connection = connect_sources(owner.paths)
            try:
                for row in connection.execute("SELECT relative_path FROM sources WHERE exists_status = 'active'"):
                    parts = Path(row["relative_path"]).parts
                    key = parts[0] if len(parts) > 1 else ""
                    counts[key] = counts.get(key, 0) + 1
            finally:
                connection.close()
        return {"projects": [{"name": name, "document_count": counts[name]} for name in sorted(counts, key=str.casefold)]}


def create_project(owner: Any, name: str) -> dict[str, Any]:
    name = validate_name(name)
    with get_knowledge_base_lock(owner.data_dir):
        root = owner.external_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if any(unicodedata.normalize("NFKC", entry.name).casefold() == name.casefold() for entry in root.iterdir()):
            raise DocumentContentConflictError("同名项目或文件已存在")
        (root / name).mkdir()
    return {"name": name, "document_count": 0}


def relocate_documents(owner: Any, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Preflight the whole batch; restore paths/map if the transaction fails."""
    if not changes or len(changes) > 1000:
        raise ValueError("每次需选择 1 到 1000 篇文档")
    if len({item["source_id"] for item in changes}) != len(changes):
        raise ValueError("source_id 不能重复")
    owner._require_initialized()
    with get_knowledge_base_lock(owner.data_dir):
        connection = connect_sources(owner.paths)
        moved: list[tuple[Path, Path]] = []
        file_map = FileMapStore(owner.external_dir / "file_map.json")
        previous_mappings = file_map.list()
        map_written = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Pending jobs carry paths, so do not move files out from under them.
            if connection.execute("SELECT 1 FROM maintenance_jobs WHERE status IN ('queued', 'running') LIMIT 1").fetchone():
                raise DocumentContentConflictError("有后台任务排队或运行中，请完成后再改名或移动")
            rows = connection.execute("SELECT * FROM sources").fetchall()
            by_id = {row["source_id"]: row for row in rows}
            occupied = {row["path_hash"]: row["source_id"] for row in rows}
            targets: set[str] = set()
            planned = []
            for change in changes:
                source_id = change["source_id"]
                row = by_id.get(source_id)
                if row is None or row["exists_status"] != "active":
                    raise DocumentNotFoundError(f"活动文档不存在：{source_id}")
                if row["source_uri"]:
                    raise DocumentContentConflictError("外部同步文档由上游管理，请在来源系统中重命名或移动")
                if "processing" in (row["graph_status"], row["rag_status"]):
                    raise DocumentContentConflictError("文档正在处理中，请稍后重试")
                old = row["relative_path"]
                if change.get("expected_relative_path") is not None and change["expected_relative_path"] != old:
                    raise DocumentContentConflictError("文档位置已变化，请刷新后重试")
                source = _safe_source_path(owner.external_dir, old)
                if not source.is_file():
                    raise DocumentNotFoundError(f"文档文件不存在：{old}")
                parent = project_directory(owner, change["project"]) if change.get("project") is not None else source.parent
                filename = validate_name(change["filename"], document=True) if change.get("filename") is not None else source.name
                target = parent / filename
                new = target.relative_to(owner.external_dir).as_posix()
                path_hash = _hash_relative_path(new)
                if path_hash in targets:
                    raise DocumentContentConflictError("批量移动的目标文件名重复，请先重命名")
                targets.add(path_hash)
                if occupied.get(path_hash, source_id) != source_id:
                    raise DocumentContentConflictError(f"目标路径已被其他文档或历史记录占用：{new}")
                if target.is_symlink() or (target.exists() and source.resolve() != target.resolve()):
                    raise DocumentContentConflictError(f"目标文件已存在：{new}")
                if any(_hash_relative_path(m.markdown_path) == path_hash and m.markdown_path != old for m in previous_mappings):
                    raise DocumentContentConflictError(f"目标路径已被来源映射占用：{new}")
                planned.append((row, source, target, old, new, path_hash))
            # Write all paths under one SQLite transaction, retaining source IDs.
            from .ingestor import FileMapping
            mapping_paths = {old: new for _, _, _, old, new, _ in planned}
            origins = {}
            for row, _, _, old, new, _ in planned:
                original = row["original_path"]
                if original.startswith("upload://"):
                    # Browser-upload identity is scoped by project, while the
                    # original filename (not the renamed Markdown name) stays.
                    project = Path(new).parts[0] if len(Path(new).parts) > 1 else ""
                    original = "upload://" + (project + "/" if project else "") + original.removeprefix("upload://").split("/")[-1]
                origins[old] = original
            next_mappings = [FileMapping(origins.get(m.markdown_path, m.original_path), mapping_paths.get(m.markdown_path, m.markdown_path)) for m in previous_mappings]
            if len({m.original_path.casefold() for m in next_mappings}) != len(next_mappings):
                raise DocumentContentConflictError("目标项目已有同名上传来源，请先处理该来源，避免重复导入覆盖")
            for row, source, target, old, new, path_hash in planned:
                if old == new:
                    continue
                os.rename(source, target)
                moved.append((source, target))
                # A raw Markdown file may not yet have a map; preserve its origin.
                if not any(m.markdown_path == old for m in previous_mappings):
                    next_mappings.append(FileMapping(origins[old], new))
                connection.execute("UPDATE sources SET relative_path = ?, path_hash = ?, original_path = ?, updated_at = ? WHERE source_id = ?", (new, path_hash, origins[old], _now_iso(), row["source_id"]))
            if moved:
                file_map._write(next_mappings)
                map_written = True
            connection.commit()
        except Exception:
            connection.rollback()
            for source, target in reversed(moved):
                os.rename(target, source)
            if map_written:
                file_map._write(previous_mappings)
            raise
        finally:
            connection.close()
        owner._log_event("document_relocate", f"moved={len(moved)}")
        return {"documents": [{"source_id": row["source_id"], "previous_relative_path": old, "relative_path": new, "changed": old != new} for row, _, _, old, new, _ in planned]}
