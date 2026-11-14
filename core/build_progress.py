"""Persist the latest real build steps for each source and pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.storage.connections import connect_sources


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_build(paths, source_id: str, pipeline: str, content_hash: str) -> None:
    connection = connect_sources(paths)
    try:
        with connection:
            connection.execute(
                "DELETE FROM source_build_steps WHERE source_id = ? AND pipeline = ?",
                (source_id, pipeline),
            )
            connection.execute(
                """
                INSERT INTO source_build_steps
                    (source_id, pipeline, position, step, label, content_hash, status, started_at)
                VALUES (?, ?, 0, 'read', '读取处理稿', ?, 'processing', ?)
                """,
                (source_id, pipeline, content_hash, _now()),
            )
    finally:
        connection.close()


def advance_build(paths, source_id: str, pipeline: str, step: str, label: str) -> None:
    """Called only after the previous stage has returned successfully."""
    connection = connect_sources(paths)
    try:
        with connection:
            now = _now()
            connection.execute(
                """
                UPDATE source_build_steps SET status = 'completed', finished_at = ?
                WHERE source_id = ? AND pipeline = ? AND status = 'processing'
                """,
                (now, source_id, pipeline),
            )
            connection.execute(
                """
                INSERT INTO source_build_steps
                    (source_id, pipeline, position, step, label, content_hash, status, started_at)
                SELECT source_id, pipeline, position + 1, ?, ?, content_hash, 'processing', ?
                FROM source_build_steps WHERE source_id = ? AND pipeline = ?
                ORDER BY position DESC LIMIT 1
                """,
                (step, label, now, source_id, pipeline),
            )
    finally:
        connection.close()


def finish_build(paths, source_id: str, pipeline: str, error: str | None = None) -> None:
    connection = connect_sources(paths)
    try:
        with connection:
            connection.execute(
                """
                UPDATE source_build_steps SET status = ?, error = ?, finished_at = ?
                WHERE source_id = ? AND pipeline = ? AND status = 'processing'
                """,
                ("failed" if error is not None else "completed", error, _now(), source_id, pipeline),
            )
    finally:
        connection.close()


def read_builds(connection, source_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not source_ids:
        return {}
    rows = connection.execute(
        f"""
        SELECT * FROM source_build_steps
        WHERE source_id IN ({','.join('?' for _ in source_ids)})
        ORDER BY source_id, pipeline, position
        """,
        source_ids,
    ).fetchall()
    builds: dict[str, dict[str, Any]] = {}
    for row in rows:
        build = builds.setdefault(row["source_id"], {}).setdefault(
            row["pipeline"], {"content_hash": row["content_hash"], "steps": []}
        )
        build["steps"].append({
            key: row[key] for key in
            ("step", "label", "status", "error", "started_at", "finished_at")
        })
    return builds
