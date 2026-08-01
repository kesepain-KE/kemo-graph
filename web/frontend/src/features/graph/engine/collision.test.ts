import { describe, expect, it } from "vitest";

import { resolvePositionCollisions } from "./collision";
import {
  calculateLayoutWorld,
  expandLayoutWorldToFit,
} from "./layoutTypes";

describe("graph collision constraint", () => {
  it("separates overlapping nodes with a visible safety gap", () => {
    const positions = new Float32Array([500, 350, 500, 350, 506, 350]);
    const radii = new Float32Array([36, 36, 36]);
    const world = calculateLayoutWorld(radii);
    const result = resolvePositionCollisions(positions, radii, world, 12);

    expect(result.overlaps).toBe(0);
    for (let left = 0; left < radii.length; left += 1) {
      for (let right = left + 1; right < radii.length; right += 1) {
        const distance = Math.hypot(
          positions[left * 2] - positions[right * 2],
          positions[left * 2 + 1] - positions[right * 2 + 1],
        );
        expect(distance).toBeGreaterThanOrEqual(radii[left] + radii[right] - 0.01);
      }
    }
  });

  it("keeps a pinned node fixed while moving its neighbor", () => {
    const positions = new Float32Array([500, 350, 510, 350]);
    const radii = new Float32Array([36, 36]);
    const world = calculateLayoutWorld(radii);
    resolvePositionCollisions(positions, radii, world, 4, new Set([0]));
    expect(positions[0]).toBe(500);
    expect(positions[1]).toBe(350);
    expect(Math.hypot(positions[2] - 500, positions[3] - 350)).toBeGreaterThanOrEqual(71.99);
  });

  it("does not clamp nodes back into the initial packing envelope", () => {
    const radii = new Float32Array([36]);
    const world = calculateLayoutWorld(radii);
    const positions = new Float32Array([world.maxX + 420, world.minY - 260]);

    resolvePositionCollisions(positions, radii, world, 2);

    expect(positions[0]).toBeGreaterThan(world.maxX);
    expect(positions[1]).toBeLessThan(world.minY);
  });

  it("expands the WebGPU spatial grid around unbounded positions", () => {
    const radii = new Float32Array([36, 42]);
    const world = calculateLayoutWorld(radii);
    const positions = new Float32Array([
      world.minX - 300,
      world.centerY,
      world.maxX + 520,
      world.maxY + 240,
    ]);

    const expanded = expandLayoutWorldToFit(world, positions, radii);

    expect(expanded.minX).toBeLessThan(positions[0] - radii[0]);
    expect(expanded.maxX).toBeGreaterThan(positions[2] + radii[1]);
    expect(expanded.maxY).toBeGreaterThan(positions[3] + radii[1]);
    expect(expanded.centerX).toBe(world.centerX);
    expect(expanded.centerY).toBe(world.centerY);
  });
});
