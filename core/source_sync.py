"""外部权威数据源到 kemo-graph 派生 Markdown 的同步协议。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from .db import connect_sources, initialize_databases
from .ingestor import Ingestor
from .ingestor._utils import _hash_relative_path


MAX_SOURCE_CONTENT_BYTES = 50 * 1024 * 1024
MAX_SOURCE_METADATA_BYTES = 64 * 1024
_SOURCE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SAFE_STEM_PATTERN = re.compile(r"[^0-9A-Za-z._-]+")


class ExternalSourceSyncError(ValueError):
    """外部来源批次不符合稳定同步契约。"""


def sync_external_sources(
    service: Any,
    records: Sequence[Mapping[str, Any]],
    *,
    ingest_after_sync: bool = False,
) -> dict[str, Any]:
    """幂等同步外部表记录，并按稳定 URI 维护派生 Markdown。"""

    normalized = _validate_batch(records)
    initialize_databases(service.data_dir, service.settings)
    ingestor = Ingestor(
        data_dir=service.data_dir,
        external_dir=service.external_dir,
        settings=service.settings,
    )
    result: dict[str, Any] = {
        "received": len(normalized),
        "created": 0,
        "updated": 0,
        "metadata_updated": 0,
        "unchanged": 0,
        "deleted": 0,
        "stale": 0,
        "conflicts": 0,
        "failed": 0,
        "details": [],
        "ingest": None,
    }
    changed_paths_by_mode: dict[str, list[str]] = {
        "graph": [],
        "rag": [],
        "both": [],
    }
    mutated = False

    with ingestor._write_lock:
        existing = _load_existing(service.paths)
        actions: list[tuple[str, dict[str, Any], Any]] = []
        for record in normalized:
            row = existing.get(record["source_uri"])
            action = _classify(record, row)
            if action in {"stale", "conflict", "unchanged"}:
                counter = "conflicts" if action == "conflict" else action
                result[counter] += 1
                result["details"].append(
                    _detail(record, action, row["source_id"] if row else None)
                )
                continue
            actions.append((action, record, row))

        file_actions = [item for item in actions if item[0] != "delete"]
        delete_actions = [item for item in actions if item[0] == "delete"]
        file_backups: dict[Path, bytes | None] = {}
        file_map_path = ingestor.file_map.file_path
        file_map_backup = file_map_path.read_bytes() if file_map_path.exists() else None
        try:
            for action, record, row in file_actions:
                if action == "metadata":
                    _update_external_columns(
                        service.paths,
                        row["source_id"],
                        record,
                    )
                    result["metadata_updated"] += 1
                    result["details"].append(
                        _detail(record, "metadata_updated", row["source_id"])
                    )
                    mutated = True
                    continue

                relative_path = (
                    str(row["relative_path"])
                    if row is not None
                    else _managed_relative_path(record)
                )
                destination = _contained_markdown_path(
                    service.external_dir, relative_path
                )
                if destination not in file_backups:
                    file_backups[destination] = (
                        destination.read_bytes() if destination.exists() else None
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes_atomic(destination, record["content_bytes"])
                ingestor.file_map.upsert(record["source_uri"], relative_path)
                record["relative_path"] = relative_path

            if any(action != "metadata" for action, _, _ in file_actions):
                ingestor.scan_sources()

            for action, record, row in file_actions:
                if action == "metadata":
                    continue
                source_row = _find_by_relative_path(
                    service.paths, record["relative_path"]
                )
                _update_external_columns(
                    service.paths,
                    source_row["source_id"],
                    record,
                )
                result[action] += 1
                result["details"].append(
                    _detail(record, action, source_row["source_id"])
                )
                changed_paths_by_mode[record["ingest_mode"]].append(
                    record["relative_path"]
                )
                mutated = True
        except Exception:
            for path, previous in file_backups.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _write_bytes_atomic(path, previous)
            if file_map_backup is None:
                file_map_path.unlink(missing_ok=True)
            else:
                _write_bytes_atomic(file_map_path, file_map_backup)
            ingestor.scan_sources()
            raise

        for _, record, row in delete_actions:
            if row is None or row["exists_status"] != "active":
                result["unchanged"] += 1
                result["details"].append(
                    _detail(record, "unchanged", row["source_id"] if row else None)
                )
                continue
            relative_path = str(row["relative_path"])
            path = _contained_markdown_path(service.external_dir, relative_path)
            path.unlink(missing_ok=True)
            ingestor.file_map.delete_by_original(record["source_uri"])
            _mark_external_deleted(service.paths, row["source_id"], record)
            graph_changed, rag_changed = ingestor._delete_source_data(row["source_id"])
            result["deleted"] += 1
            result["details"].append(
                {
                    **_detail(record, "deleted", row["source_id"]),
                    "graph_deleted": graph_changed,
                    "rag_deleted": rag_changed,
                }
            )
            mutated = True

    if mutated:
        try:
            result["search_cache_deleted"] = service.clear_search_cache()["deleted"]
        except Exception:
            result["search_cache_deleted"] = 0
    else:
        result["search_cache_deleted"] = 0

    if ingest_after_sync:
        result["ingest"] = _ingest_changed(service, changed_paths_by_mode)
    return result


def list_external_sources(
    service: Any,
    *,
    source_type: str | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """分页列出具备 source_uri 的同步来源。"""

    initialize_databases(service.data_dir, service.settings)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ExternalSourceSyncError("page 必须是大于等于 1 的整数")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 1000
    ):
        raise ExternalSourceSyncError("page_size 必须是 1 到 1000 之间的整数")
    normalized_type = _validate_source_type(source_type) if source_type else None
    conditions = ["source_uri IS NOT NULL"]
    parameters: list[Any] = []
    if not include_deleted:
        conditions.append("exists_status = 'active'")
    if normalized_type:
        conditions.append("source_type = ?")
        parameters.append(normalized_type)
    where = " AND ".join(conditions)
    connection = connect_sources(service.paths)
    try:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM sources WHERE {where}", parameters
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT source_id, source_uri, source_type, source_revision,
                   source_updated_at, source_metadata_json,
                   external_content_hash, last_synced_at, relative_path,
                   content_hash, graph_status, rag_status, exists_status,
                   created_at, updated_at
            FROM sources
            WHERE {where}
            ORDER BY source_updated_at DESC, source_uri
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()
    finally:
        connection.close()
    return {
        "sources": [_serialize_source_row(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


def delete_external_sources(
    service: Any,
    source_uris: Sequence[str],
) -> dict[str, Any]:
    """以 tombstone 语义批量删除外部来源。"""

    if isinstance(source_uris, (str, bytes)) or not source_uris:
        raise ExternalSourceSyncError("source_uris 必须是非空数组")
    initialize_databases(service.data_dir, service.settings)
    validated_uris = list(
        dict.fromkeys(_validate_source_uri(uri) for uri in source_uris)
    )
    connection = connect_sources(service.paths)
    try:
        placeholders = ",".join("?" for _ in validated_uris)
        rows = connection.execute(
            f"SELECT * FROM sources WHERE source_uri IN ({placeholders})",
            validated_uris,
        ).fetchall()
        existing = {str(row["source_uri"]): row for row in rows}
    finally:
        connection.close()
    records = []
    for uri in validated_uris:
        row = existing.get(uri)
        previous = _parse_timestamp(row["source_updated_at"]) if row else None
        now = datetime.now(timezone.utc)
        if previous is not None and now <= previous:
            now = previous + timedelta(microseconds=1)
        records.append(
            {
                "source_uri": uri,
                "source_type": (
                    str(row["source_type"] or "external.deleted")
                    if row
                    else "external.deleted"
                ),
                "display_name": (
                    Path(str(row["relative_path"])).name
                    if row
                    else "deleted.md"
                ),
                "revision": f"delete:{uuid4()}",
                "updated_at": now.isoformat(),
                "metadata": (
                    json.loads(row["source_metadata_json"] or "{}") if row else {}
                ),
                "deleted": True,
                "ingest_mode": "both",
            }
        )
    return sync_external_sources(service, records)


def _validate_batch(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(
        records, Sequence
    ):
        raise ExternalSourceSyncError("records 必须是来源对象数组")
    if not records:
        raise ExternalSourceSyncError("records 不能为空")
    if len(records) > 1000:
        raise ExternalSourceSyncError("单批 records 不能超过 1000 条")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(records):
        if not isinstance(value, Mapping):
            raise ExternalSourceSyncError(f"records[{index}] 必须是对象")
        unknown = set(value) - {
            "source_uri",
            "source_type",
            "display_name",
            "content",
            "content_hash",
            "revision",
            "updated_at",
            "metadata",
            "deleted",
            "ingest_mode",
        }
        if unknown:
            raise ExternalSourceSyncError(
                f"records[{index}] 包含未知字段：{', '.join(sorted(unknown))}"
            )
        uri = _validate_source_uri(value.get("source_uri"))
        if uri in seen:
            raise ExternalSourceSyncError(f"批次中 source_uri 重复：{uri}")
        seen.add(uri)
        source_type = _validate_source_type(value.get("source_type"))
        deleted = value.get("deleted", False)
        if not isinstance(deleted, bool):
            raise ExternalSourceSyncError(f"records[{index}].deleted 必须是布尔值")
        display_name = _required_text(value.get("display_name"), "display_name", 255)
        if Path(display_name).name != display_name or ".." in display_name:
            raise ExternalSourceSyncError("display_name 必须是不含路径的逻辑文件名")
        revision = _required_text(value.get("revision"), "revision", 255)
        updated_at = _normalize_timestamp(value.get("updated_at"))
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ExternalSourceSyncError("metadata 必须是 JSON 对象")
        metadata_json = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(metadata_json.encode("utf-8")) > MAX_SOURCE_METADATA_BYTES:
            raise ExternalSourceSyncError("metadata 超过 64 KiB 上限")
        ingest_mode = value.get("ingest_mode", "both")
        if ingest_mode not in {"graph", "rag", "both"}:
            raise ExternalSourceSyncError("ingest_mode 必须是 graph、rag 或 both")

        content = value.get("content", "")
        if not isinstance(content, str):
            raise ExternalSourceSyncError("content 必须是字符串")
        if not deleted and not content.strip():
            raise ExternalSourceSyncError("非删除来源的 content 不能为空")
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        if normalized_content and not normalized_content.endswith("\n"):
            normalized_content += "\n"
        content_bytes = normalized_content.encode("utf-8")
        if len(content_bytes) > MAX_SOURCE_CONTENT_BYTES:
            raise ExternalSourceSyncError("content 超过 50 MiB 上限")
        computed_hash = hashlib.sha256(content_bytes).hexdigest()
        external_hash = value.get("content_hash")
        if external_hash is not None:
            external_hash = _validate_sha256(external_hash, "content_hash")
        normalized.append(
            {
                "source_uri": uri,
                "source_type": source_type,
                "display_name": display_name,
                "content_bytes": content_bytes,
                "computed_hash": computed_hash,
                "external_content_hash": (
                    external_hash
                    if external_hash is not None
                    else (None if deleted else computed_hash)
                ),
                "revision": revision,
                "updated_at": updated_at,
                "metadata": metadata,
                "metadata_json": metadata_json,
                "deleted": deleted,
                "ingest_mode": ingest_mode,
            }
        )
    return normalized


def _classify(record: dict[str, Any], row: Any | None) -> str:
    if row is None:
        return "unchanged" if record["deleted"] else "created"
    previous_time = _parse_timestamp(row["source_updated_at"])
    incoming_time = _parse_timestamp(record["updated_at"])
    if previous_time is not None and incoming_time < previous_time:
        return "stale"
    if (
        row["source_revision"] == record["revision"]
        and row["content_hash"] != record["computed_hash"]
        and not record["deleted"]
        and row["exists_status"] == "active"
    ):
        return "conflict"
    if record["deleted"]:
        return "delete" if row["exists_status"] == "active" else "metadata"
    if (
        row["exists_status"] != "active"
        or row["content_hash"] != record["computed_hash"]
    ):
        return "updated"
    metadata_changed = any(
        (
            row["source_type"] != record["source_type"],
            row["source_revision"] != record["revision"],
            row["source_updated_at"] != record["updated_at"],
            (row["source_metadata_json"] or "{}") != record["metadata_json"],
            row["external_content_hash"] != record["external_content_hash"],
        )
    )
    return "metadata" if metadata_changed else "unchanged"


def _load_existing(paths: Any) -> dict[str, Any]:
    connection = connect_sources(paths)
    try:
        rows = connection.execute(
            "SELECT * FROM sources WHERE source_uri IS NOT NULL"
        ).fetchall()
        return {str(row["source_uri"]): row for row in rows}
    finally:
        connection.close()


def _update_external_columns(
    paths: Any,
    source_id: str,
    record: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect_sources(paths)
    try:
        connection.execute(
            """
            UPDATE sources
            SET source_uri = ?, source_type = ?, source_revision = ?,
                source_updated_at = ?, source_metadata_json = ?,
                external_content_hash = COALESCE(?, external_content_hash),
                last_synced_at = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (
                record["source_uri"],
                record["source_type"],
                record["revision"],
                record["updated_at"],
                record["metadata_json"],
                record["external_content_hash"],
                now,
                now,
                source_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _mark_external_deleted(
    paths: Any,
    source_id: str,
    record: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect_sources(paths)
    try:
        connection.execute(
            """
            UPDATE sources
            SET exists_status = 'deleted', source_type = ?, source_revision = ?,
                source_updated_at = ?, source_metadata_json = ?,
                external_content_hash = COALESCE(?, external_content_hash),
                last_synced_at = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (
                record["source_type"],
                record["revision"],
                record["updated_at"],
                record["metadata_json"],
                record["external_content_hash"],
                now,
                now,
                source_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _find_by_relative_path(paths: Any, relative_path: str) -> Any:
    connection = connect_sources(paths)
    try:
        row = connection.execute(
            "SELECT * FROM sources WHERE path_hash = ?",
            (_hash_relative_path(relative_path),),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ExternalSourceSyncError(f"同步后未找到来源记录：{relative_path}")
    return row


def _managed_relative_path(record: dict[str, Any]) -> str:
    type_path = "/".join(record["source_type"].split("."))
    stem = Path(record["display_name"]).stem
    safe_stem = _SAFE_STEM_PATTERN.sub("-", stem).strip("-._")[:80] or "source"
    identity = hashlib.sha256(record["source_uri"].encode("utf-8")).hexdigest()[:12]
    return f"_managed/{type_path}/{safe_stem}-{identity}.md"


def _contained_markdown_path(root: Path, relative_path: str) -> Path:
    resolved_root = Path(root).resolve()
    candidate = (resolved_root / relative_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ExternalSourceSyncError("派生 Markdown 路径越出 Store") from exc
    if candidate.suffix.casefold() != ".md":
        raise ExternalSourceSyncError("派生来源必须使用 .md 文件")
    return candidate


def _ingest_changed(
    service: Any, paths_by_mode: dict[str, list[str]]
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for mode in ("both", "graph", "rag"):
        paths = list(dict.fromkeys(paths_by_mode[mode]))
        if paths:
            results.append(
                {"mode": mode, "paths": paths, "result": service.ingest(paths, mode)}
            )
    return {"runs": results}


def _detail(
    record: dict[str, Any], status: str, source_id: str | None
) -> dict[str, Any]:
    return {
        "source_uri": record["source_uri"],
        "source_id": source_id,
        "status": status,
    }


def _serialize_source_row(row: Any) -> dict[str, Any]:
    try:
        metadata = json.loads(row["source_metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "source_id": row["source_id"],
        "source_uri": row["source_uri"],
        "source_type": row["source_type"],
        "revision": row["source_revision"],
        "source_updated_at": row["source_updated_at"],
        "metadata": metadata,
        "external_content_hash": row["external_content_hash"],
        "last_synced_at": row["last_synced_at"],
        "relative_path": row["relative_path"],
        "content_hash": row["content_hash"],
        "graph_status": row["graph_status"],
        "rag_status": row["rag_status"],
        "exists_status": row["exists_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_source_uri(value: Any) -> str:
    text = _required_text(value, "source_uri", 2048)
    parsed = urlsplit(text)
    if not parsed.scheme or not re.fullmatch(r"[a-z][a-z0-9+.-]*", parsed.scheme):
        raise ExternalSourceSyncError("source_uri 必须包含小写 URI scheme")
    if not parsed.netloc and not parsed.path:
        raise ExternalSourceSyncError("source_uri 缺少资源身份")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalSourceSyncError("source_uri 不能包含认证信息")
    return text


def _validate_source_type(value: Any) -> str:
    text = _required_text(value, "source_type", 128).casefold()
    if not _SOURCE_TYPE_PATTERN.fullmatch(text):
        raise ExternalSourceSyncError(
            "source_type 必须是小写字母开头的点分类型"
        )
    return text


def _normalize_timestamp(value: Any) -> str:
    text = _required_text(value, "updated_at", 64)
    parsed = _parse_timestamp(text)
    if parsed is None:
        raise ExternalSourceSyncError("updated_at 必须是带时区的 ISO 8601 时间")
    return parsed.isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ExternalSourceSyncError("updated_at 不是合法 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise ExternalSourceSyncError("updated_at 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _validate_sha256(value: Any, field: str) -> str:
    text = _required_text(value, field, 64).casefold()
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ExternalSourceSyncError(f"{field} 必须是 SHA-256 十六进制字符串")
    return text


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalSourceSyncError(f"{field} 必须是非空字符串")
    text = value.strip()
    if len(text) > maximum:
        raise ExternalSourceSyncError(f"{field} 超过 {maximum} 字符上限")
    return text


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "ExternalSourceSyncError",
    "MAX_SOURCE_CONTENT_BYTES",
    "MAX_SOURCE_METADATA_BYTES",
    "delete_external_sources",
    "list_external_sources",
    "sync_external_sources",
]
