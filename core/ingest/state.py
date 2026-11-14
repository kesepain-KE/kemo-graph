from __future__ import annotations

from core.storage import connect_graph, connect_sources
from core.storage import read_graph_meta, write_graph_meta
from core.common import now_iso as _now_iso

def _refresh_graph_meta(self, *, changed: bool) -> None:
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
            "extraction_signature": self.settings.graph_extraction_signature(),
        }
    )
    if changed:
        meta["changed_since_summary"] = (
            int(meta.get("changed_since_summary", 0)) + 1
        )
    write_graph_meta(self.paths, meta)

def _set_source_status(self, source_id: str, target: str, status: str) -> None:
    if target not in {"graph", "rag"} or status not in {
        "pending",
        "processing",
        "ready",
        "failed",
    }:
        raise ValueError("无效的 source 状态更新")
    connection = connect_sources(self.paths)
    try:
        connection.execute(
            f"UPDATE sources SET {target}_status = ?, updated_at = ? WHERE source_id = ?",
            (status, _now_iso(), source_id),
        )
        connection.commit()
    finally:
        connection.close()

def _mark_source_ready(
    self,
    source_id: str,
    target: str,
    content_hash: str,
) -> None:
    if target not in {"graph", "rag"}:
        raise ValueError("target 必须是 graph 或 rag")
    connection = connect_sources(self.paths)
    try:
        connection.execute(
            f"""
                UPDATE sources
                SET {target}_hash = ?, {target}_status = 'ready', updated_at = ?
                WHERE source_id = ?
                """,
            (content_hash, _now_iso(), source_id),
        )
        connection.commit()
    finally:
        connection.close()
