from core.storage import connect_graph
from core.storage import read_graph_meta, write_graph_meta

def refresh_graph_meta(context, *, changed: bool) -> None:
    """刷新 graph_meta.json，并在有变更时递增 changed_since_summary。"""
    connection = connect_graph(context.paths)
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
    meta = read_graph_meta(context.paths)
    meta.update(
        {
            "total_nodes": int(counts["total_nodes"]),
            "total_edges": int(counts["total_edges"]),
            "total_groups": int(counts["total_groups"]),
        }
    )
    if changed:
        meta["changed_since_summary"] = (
            int(meta.get("changed_since_summary", 0)) + 1
        )
    write_graph_meta(context.paths, meta)
