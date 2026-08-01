export type WebGpuAdjacency = {
  offsets: Uint32Array<ArrayBuffer>;
  targets: Uint32Array<ArrayBuffer>;
  weights: Float32Array<ArrayBuffer>;
};

export function buildWebGpuAdjacency(
  nodeCount: number,
  edgeSources: Uint32Array<ArrayBufferLike>,
  edgeTargets: Uint32Array<ArrayBufferLike>,
  edgeWeights: Float32Array<ArrayBufferLike>,
): WebGpuAdjacency {
  if (
    edgeSources.length !== edgeTargets.length
    || edgeSources.length !== edgeWeights.length
  ) {
    throw new Error("WebGPU 关系数组长度不一致");
  }
  const degrees = new Uint32Array(nodeCount);
  for (let edgeIndex = 0; edgeIndex < edgeSources.length; edgeIndex += 1) {
    const source = edgeSources[edgeIndex];
    const target = edgeTargets[edgeIndex];
    if (source >= nodeCount || target >= nodeCount) {
      throw new Error("WebGPU 关系包含越界节点索引");
    }
    degrees[source] += 1;
    degrees[target] += 1;
  }
  const offsets = new Uint32Array(nodeCount + 1);
  for (let index = 0; index < nodeCount; index += 1) {
    offsets[index + 1] = offsets[index] + degrees[index];
  }
  const targets = new Uint32Array(offsets[nodeCount]);
  const weights = new Float32Array(offsets[nodeCount]);
  const cursors = offsets.slice(0, nodeCount);
  for (let edgeIndex = 0; edgeIndex < edgeSources.length; edgeIndex += 1) {
    const source = edgeSources[edgeIndex];
    const target = edgeTargets[edgeIndex];
    const weight = Number.isFinite(edgeWeights[edgeIndex])
      ? Math.max(0, edgeWeights[edgeIndex])
      : 0;
    const sourceCursor = cursors[source]++;
    targets[sourceCursor] = target;
    weights[sourceCursor] = weight;
    const targetCursor = cursors[target]++;
    targets[targetCursor] = source;
    weights[targetCursor] = weight;
  }
  return { offsets, targets, weights };
}

export function createWebGpuParamsBuffer(
  nodeCount: number,
  gridWidth: number,
  gridHeight: number,
  maxPerCell: number,
  world: LayoutWorld,
  values: {
    centerStrength: number;
    repulsionStrength: number;
    linkStrength: number;
    linkDistance: number;
    damping: number;
    alpha: number;
  },
): ArrayBuffer {
  const buffer = new ArrayBuffer(64);
  const view = new DataView(buffer);
  view.setUint32(0, nodeCount, true);
  view.setUint32(4, gridWidth, true);
  view.setUint32(8, gridHeight, true);
  view.setUint32(12, maxPerCell, true);
  view.setFloat32(16, world.minX, true);
  view.setFloat32(20, world.minY, true);
  view.setFloat32(24, world.width, true);
  view.setFloat32(28, world.height, true);
  view.setFloat32(32, world.centerX, true);
  view.setFloat32(36, world.centerY, true);
  view.setFloat32(40, values.centerStrength, true);
  view.setFloat32(44, values.repulsionStrength, true);
  view.setFloat32(48, values.linkStrength, true);
  view.setFloat32(52, values.linkDistance, true);
  view.setFloat32(56, values.damping, true);
  view.setFloat32(60, values.alpha, true);
  return buffer;
}

export function alignedBufferSize(byteLength: number): number {
  return Math.max(4, Math.ceil(byteLength / 4) * 4);
}
import type { LayoutWorld } from "../engine/layoutTypes";
