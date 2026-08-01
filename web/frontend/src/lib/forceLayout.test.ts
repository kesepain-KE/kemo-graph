import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "../types/api";
import {
  computeForceLayout,
  computeInitialLayout,
  selectNeighborhood,
} from "./forceLayout";
import { graphNodeCollisionRadius } from "../features/graph/engine/layoutTypes";

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

  it("creates a deterministic non-iterative starting layout", () => {
    expect(computeInitialLayout(nodes)).toEqual(computeInitialLayout(nodes));
    expect(computeInitialLayout([])).toEqual([]);
  });

  it("keeps rendered node circles from touching after layout", () => {
    const positioned = computeForceLayout(nodes, edges, 40);
    for (let left = 0; left < positioned.length; left += 1) {
      for (let right = left + 1; right < positioned.length; right += 1) {
        const distance = Math.hypot(
          positioned[left].x - positioned[right].x,
          positioned[left].y - positioned[right].y,
        );
        expect(distance).toBeGreaterThanOrEqual(
          graphNodeCollisionRadius(positioned[left].ref_count)
          + graphNodeCollisionRadius(positioned[right].ref_count)
          - 0.02,
        );
      }
    }
  });

  it("selects bidirectional neighbors up to the requested depth", () => {
    expect(selectNeighborhood(nodes, edges, "a", 1)).toEqual(new Set(["a", "b"]));
    expect(selectNeighborhood(nodes, edges, "b", 2)).toEqual(
      new Set(["a", "b", "c", "d"]),
    );
  });
});
