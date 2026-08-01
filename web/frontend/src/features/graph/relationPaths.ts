import type { GraphEdge, GraphNode } from "../../types/api";

export type ReadableRelationPath = {
  id: string;
  text: string;
  nodeIds: string[];
  edgeIds: string[];
  weight: number;
};

export function buildReadableRelationPaths(
  anchorId: string | null,
  edges: GraphEdge[],
  nodesById: Map<string, GraphNode>,
  limit = 20,
): ReadableRelationPath[] {
  if (!anchorId || limit <= 0) return [];
  const paths: ReadableRelationPath[] = [];
  const direct = edges.filter((edge) => (
    edge.source_node_id === anchorId || edge.target_node_id === anchorId
  ));
  for (const edge of direct) {
    paths.push(toReadablePath([edge], nodesById));
    if (paths.length >= limit) return paths;
  }

  const outgoing = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    const bucket = outgoing.get(edge.source_node_id) ?? [];
    bucket.push(edge);
    outgoing.set(edge.source_node_id, bucket);
  }
  const seen = new Set(paths.map((path) => path.edgeIds.join("|")));
  for (const first of edges) {
    for (const second of outgoing.get(first.target_node_id) ?? []) {
      if (first.edge_id === second.edge_id) continue;
      const nodeIds = [
        first.source_node_id,
        first.target_node_id,
        second.target_node_id,
      ];
      if (!nodeIds.includes(anchorId) || new Set(nodeIds).size < 3) continue;
      const key = `${first.edge_id}|${second.edge_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      paths.push(toReadablePath([first, second], nodesById));
      if (paths.length >= limit) return paths;
    }
  }
  return paths;
}

export function formatRelationPath(
  edges: GraphEdge[],
  nodesById: Map<string, GraphNode>,
): string {
  if (!edges.length) return "";
  const first = edges[0];
  let text = nodeName(first.source_node_id, nodesById);
  for (const edge of edges) {
    text += ` →[${edge.relation}]→ ${nodeName(edge.target_node_id, nodesById)}`;
  }
  return text;
}

function toReadablePath(
  edges: GraphEdge[],
  nodesById: Map<string, GraphNode>,
): ReadableRelationPath {
  return {
    id: edges.map((edge) => edge.edge_id).join("|"),
    text: formatRelationPath(edges, nodesById),
    nodeIds: [edges[0].source_node_id, ...edges.map((edge) => edge.target_node_id)],
    edgeIds: edges.map((edge) => edge.edge_id),
    weight: Math.min(...edges.map((edge) => edge.weight)),
  };
}

function nodeName(nodeId: string, nodesById: Map<string, GraphNode>): string {
  return nodesById.get(nodeId)?.keyword ?? nodeId;
}
