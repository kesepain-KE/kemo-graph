import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "../types/api";
import { computeForceLayout, selectNeighborhood } from "./forceLayout";

const nodes = ["a", "b", "c", "d"].map(
  (node_id): GraphNode => ({
    node_id,
    keyword: node_id.toUpperCase(),
    summary: node_id,
    aliases: [],
    tags: [],
    ref_count: 1,
  }),
);

const edges: GraphEdge[] = [
  ["a", "b"],
  ["b", "c"],
  ["c", "d"],
].map(([source_node_id, target_node_id], index) => ({
  edge_id: String(index),
  source_node_id,
  target_node_id,
  relation: "关联",
  weight: 0.8,
}));

describe("force layout", () => {
  it("produces deterministic finite positions", () => {
    const first = computeForceLayout(nodes, edges, 40);
    const second = computeForceLayout(nodes, edges, 40);
    expect(first).toEqual(second);
    expect(first.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true);
  });

  it("selects bidirectional neighbors up to the requested depth", () => {
    expect(selectNeighborhood(nodes, edges, "a", 1)).toEqual(new Set(["a", "b"]));
    expect(selectNeighborhood(nodes, edges, "b", 2)).toEqual(
      new Set(["a", "b", "c", "d"]),
    );
  });
});
