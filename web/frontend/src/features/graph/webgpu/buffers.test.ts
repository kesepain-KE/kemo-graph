import { describe, expect, it } from "vitest";

import {
  alignedBufferSize,
  buildWebGpuAdjacency,
  createWebGpuParamsBuffer,
} from "./buffers";
import { calculateLayoutWorld } from "../engine/layoutTypes";

describe("WebGPU graph buffers", () => {
  it("builds a bidirectional CSR adjacency table", () => {
    const adjacency = buildWebGpuAdjacency(
      3,
      new Uint32Array([0, 1]),
      new Uint32Array([1, 2]),
      new Float32Array([0.8, 0.6]),
    );
    expect([...adjacency.offsets]).toEqual([0, 1, 3, 4]);
    expect([...adjacency.targets]).toEqual([1, 0, 2, 1]);
    expect([...adjacency.weights]).toEqual([
      expect.closeTo(0.8),
      expect.closeTo(0.8),
      expect.closeTo(0.6),
      expect.closeTo(0.6),
    ]);
  });

  it("packs dynamic world and force uniforms into 64 bytes", () => {
    const world = calculateLayoutWorld(new Float32Array(384).fill(36));
    const buffer = createWebGpuParamsBuffer(12, 32, 24, 128, world, {
      centerStrength: 0.5,
      repulsionStrength: 10,
      linkStrength: 1,
      linkDistance: 118,
      damping: 0.82,
      alpha: 1,
    });
    const view = new DataView(buffer);
    expect(buffer.byteLength).toBe(64);
    expect(view.getUint32(0, true)).toBe(12);
    expect(view.getFloat32(16, true)).toBeCloseTo(world.minX);
    expect(view.getFloat32(52, true)).toBe(118);
    expect(alignedBufferSize(0)).toBe(4);
    expect(alignedBufferSize(7)).toBe(8);
  });
});
