import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "../types/api";
import { computeForceLayout } from "./forceLayout";

type BaselineCase = {
  nodes: number;
  iterations: number;
};

const CASES: BaselineCase[] = [
  { nodes: 384, iterations: 20 },
  { nodes: 1000, iterations: 5 },
  { nodes: 5000, iterations: 1 },
];

describe("SVG force-layout performance baseline", () => {
  it("records normalized costs for 384, 1000 and 5000 nodes", () => {
    const measurements = CASES.map(({ nodes: nodeCount, iterations }) => {
      const graph = syntheticGraph(nodeCount);
      const startedAt = performance.now();
      const positioned = computeForceLayout(graph.nodes, graph.edges, iterations);
      const elapsedMs = performance.now() - startedAt;
      expect(positioned).toHaveLength(nodeCount);
      expect(positioned.every((node) => Number.isFinite(node.x + node.y))).toBe(true);
      return {
        nodes: nodeCount,
        edges: graph.edges.length,
        sampled_iterations: iterations,
        elapsed_ms: Number(elapsedMs.toFixed(2)),
        ms_per_iteration: Number((elapsedMs / iterations).toFixed(2)),
        projected_180_iterations_ms: Number(
          ((elapsedMs / iterations) * 180).toFixed(2),
        ),
      };
    });

    console.info("[force-layout-baseline]", JSON.stringify(measurements));
  });
});

function syntheticGraph(nodeCount: number): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const nodes = Array.from(
    { length: nodeCount },
    (_, index): GraphNode => ({
      node_id: `node-${index}`,
      keyword: `Node ${index}`,
      summary: "baseline",
      aliases: [],
      tags: [],
      ref_count: (index % 12) + 1,
    }),
  );
  const edges: GraphEdge[] = [];
  for (let index = 0; index < nodeCount; index += 1) {
    edges.push({
      edge_id: `ring-${index}`,
      source_node_id: `node-${index}`,
      relation: "ring",
      target_node_id: `node-${(index + 1) % nodeCount}`,
      weight: 0.8,
    });
    if (index + 7 < nodeCount) {
      edges.push({
        edge_id: `skip-${index}`,
        source_node_id: `node-${index}`,
        relation: "skip",
        target_node_id: `node-${index + 7}`,
        weight: 0.6,
      });
    }
  }
  return { nodes, edges };
}
