import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "../../types/api";
import { buildReadableRelationPaths, formatRelationPath } from "./relationPaths";

const nodes = new Map<string, GraphNode>(["a", "b", "c"].map((nodeId) => [
  nodeId,
  {
    node_id: nodeId,
    keyword: nodeId.toLocaleUpperCase(),
    summary: "",
    aliases: [],
    tags: [],
    ref_count: 1,
  },
]));
const edges: GraphEdge[] = [
  {
    edge_id: "ab",
    source_node_id: "a",
    relation: "依赖",
    target_node_id: "b",
    weight: 0.9,
  },
  {
    edge_id: "bc",
    source_node_id: "b",
    relation: "产生",
    target_node_id: "c",
    weight: 0.7,
  },
];

describe("readable graph paths", () => {
  it("formats directed one-hop and two-hop paths", () => {
    expect(formatRelationPath(edges.slice(0, 1), nodes)).toBe("A →[依赖]→ B");
    expect(formatRelationPath(edges, nodes)).toBe("A →[依赖]→ B →[产生]→ C");
  });

  it("includes direct and chained paths around the selected anchor", () => {
    const paths = buildReadableRelationPaths("b", edges, nodes);
    expect(paths.map((path) => path.text)).toContain("A →[依赖]→ B →[产生]→ C");
  });
});
