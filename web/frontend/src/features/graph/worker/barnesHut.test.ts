import { describe, expect, it } from "vitest";

import {
  buildBarnesHutTree,
  calculateBarnesHutCollision,
  calculateBarnesHutRepulsion,
} from "./barnesHut";

describe("Barnes-Hut repulsion", () => {
  it("keeps a single particle free of self-repulsion", () => {
    const tree = buildBarnesHutTree(
      new Float32Array([500, 350]),
      new Float32Array([2]),
    );
    expect(calculateBarnesHutRepulsion(tree, 0, 10, 1)).toEqual({ x: 0, y: 0 });
  });

  it("produces finite opposite forces for overlapping particles", () => {
    const tree = buildBarnesHutTree(
      new Float32Array([500, 350, 500, 350]),
      new Float32Array([1, 1]),
    );
    const first = calculateBarnesHutRepulsion(tree, 0, 10, 1);
    const second = calculateBarnesHutRepulsion(tree, 1, 10, 1);
    expect(Number.isFinite(first.x) && Number.isFinite(first.y)).toBe(true);
    expect(Number.isFinite(second.x) && Number.isFinite(second.y)).toBe(true);
    expect(first.x * second.x).toBeLessThan(0);
  });

  it("applies an alpha-independent collision force before circles touch", () => {
    const tree = buildBarnesHutTree(
      new Float32Array([500, 350, 520, 350]),
      new Float32Array([1, 1]),
      new Float32Array([36, 36]),
    );
    const first = calculateBarnesHutCollision(tree, 0);
    const second = calculateBarnesHutCollision(tree, 1);
    expect(first.x).toBeLessThan(0);
    expect(second.x).toBeGreaterThan(0);
    expect(first.x).toBeCloseTo(-second.x, 5);
  });

  it("rejects mismatched typed-array dimensions", () => {
    expect(() => buildBarnesHutTree(
      new Float32Array([10, 20, 30, 40]),
      new Float32Array([1]),
    )).toThrow("输入维度不一致");
  });
});
