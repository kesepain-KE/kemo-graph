from __future__ import annotations
import json
import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from core.storage.connections import connect_sources
from core.documents.errors import DocumentImportConflictError, DocumentImportError, DocumentImportPathError, DocumentTooLargeError
from core.documents.formats import MAX_IMPORT_BYTES, _ImportSnapshot

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

def _normalize_expected_origin_hash(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected_origin_hash 必须是 SHA-256 字符串")
    normalized = value.strip().casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("expected_origin_hash 必须是 64 位 SHA-256 十六进制字符串")
    return normalized

def _sha256_snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise DocumentImportConflictError("无法复核导入快照") from exc
    return digest.hexdigest()

def _same_import_source_state(
    before: os.stat_result,
    after: os.stat_result,
    *,
    compare_change_time: bool = False,
) -> bool:
    try:
        same_object = os.path.samestat(before, after)
    except (AttributeError, OSError):
        same_object = (
            getattr(before, "st_dev", None) == getattr(after, "st_dev", None)
            and getattr(before, "st_ino", None) == getattr(after, "st_ino", None)
        )
    unchanged = bool(
        same_object
        and stat.S_IFMT(before.st_mode) == stat.S_IFMT(after.st_mode)
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )
    if compare_change_time:
        unchanged = bool(
            unchanged
            and getattr(before, "st_ctime_ns", None)
            == getattr(after, "st_ctime_ns", None)
        )
    return unchanged

@contextmanager
def _stable_import_snapshot(
    source: Path,
    *,
    expected_origin_hash: str | None = None,
) -> Iterator[_ImportSnapshot]:
    """复制并锁定一次导入内容，使哈希与转换消费同一份字节。"""

    expected_hash = _normalize_expected_origin_hash(expected_origin_hash)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(source, flags)
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = None
    except (OSError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise DocumentImportPathError(f"无法读取文件内容：{source.name}") from exc

    with stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise DocumentImportPathError(f"路径不是普通文件：{source.name}")
        if before.st_size > MAX_IMPORT_BYTES:
            raise DocumentTooLargeError(
                f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限：{source.name}"
            )

        with tempfile.TemporaryDirectory(
            prefix="kemo-graph-import-snapshot-"
        ) as temporary_dir:
            snapshot_path = Path(temporary_dir) / _snapshot_filename(source)
            digest = hashlib.sha256()
            total = 0
            snapshot_descriptor: int | None = None
            try:
                snapshot_descriptor = os.open(
                    snapshot_path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                snapshot_stream = os.fdopen(
                    snapshot_descriptor,
                    "wb",
                    closefd=True,
                )
                snapshot_descriptor = None
                with snapshot_stream:
                    while block := stream.read(1024 * 1024):
                        total += len(block)
                        if total > MAX_IMPORT_BYTES:
                            raise DocumentTooLargeError(
                                "文件在读取期间超过 "
                                f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限："
                                f"{source.name}"
                            )
                        digest.update(block)
                        snapshot_stream.write(block)
                after = os.fstat(stream.fileno())
            except (DocumentImportError, OSError, ValueError):
                if snapshot_descriptor is not None:
                    os.close(snapshot_descriptor)
                raise
            try:
                current = source.stat()
            except OSError as exc:
                raise DocumentImportConflictError(
                    f"源文件在读取期间不可用，请重新扫描后重试：{source.name}"
                ) from exc

            if (
                total != before.st_size
                or not _same_import_source_state(
                    before,
                    after,
                    compare_change_time=True,
                )
                or not _same_import_source_state(before, current)
            ):
                raise DocumentImportConflictError(
                    f"源文件在读取期间发生变化，请重新扫描后重试：{source.name}"
                )

            origin_hash = digest.hexdigest()
            if expected_hash is not None and origin_hash != expected_hash:
                raise DocumentImportConflictError(
                    f"源文件内容与调用方确认的哈希不一致，请重新扫描后重试：{source.name}"
                )

            yield _ImportSnapshot(
                path=snapshot_path,
                size=total,
                sha256=origin_hash,
                modified_at=datetime.fromtimestamp(
                    before.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
            )

def _snapshot_filename(source: Path) -> str:
    """保留转换所需的原始 basename，同时生成跨平台安全的快照名。"""

    sanitized = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", source.name).rstrip(" .")
    suffix = source.suffix
    if not sanitized or sanitized in {".", ".."}:
        sanitized = f"source{suffix.casefold()}"
    stem = Path(sanitized).stem
    if stem.casefold() in {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }:
        sanitized = f"_{sanitized}"
    if len(sanitized) > 180:
        safe_suffix = Path(sanitized).suffix
        stem_limit = max(1, 180 - len(safe_suffix))
        sanitized = f"{Path(sanitized).stem[:stem_limit]}{safe_suffix}"
    return sanitized

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

def _load_source_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
