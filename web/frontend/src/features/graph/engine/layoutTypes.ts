export type ForceSettings = {
  centerStrength: number;
  repulsionStrength: number;
  linkStrength: number;
  linkDistance: number;
  damping: number;
  alphaDecay: number;
  stableEnergy: number;
};

export const DEFAULT_FORCE_SETTINGS: ForceSettings = {
  centerStrength: 0.52,
  repulsionStrength: 10,
  linkStrength: 1,
  linkDistance: 118,
  damping: 0.82,
  alphaDecay: 0.018,
  stableEnergy: 0.012,
};

export const GRAPH_LAYOUT_CENTER_X = 500;
export const GRAPH_LAYOUT_CENTER_Y = 350;
export const GRAPH_COLLISION_GAP = 8;

export type LayoutWorld = {
  width: number;
  height: number;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  centerX: number;
  centerY: number;
};

export function graphNodeVisualRadius(refCount: number): number {
  return Math.min(48, 27 + Math.sqrt(Math.max(1, refCount)) * 5.5);
}

export function graphNodeCollisionRadius(
  refCount: number,
  nodeScale = 1,
): number {
  return (
    graphNodeVisualRadius(refCount) * Math.max(0.5, nodeScale)
    + GRAPH_COLLISION_GAP / 2
  );
}

export function calculateLayoutWorld(
  collisionRadii: Float32Array<ArrayBufferLike>,
): LayoutWorld {
  let diskArea = 0;
  let maxRadius = 0;
  for (const radiusValue of collisionRadii) {
    const radius = Number.isFinite(radiusValue) ? Math.max(1, radiusValue) : 1;
    diskArea += Math.PI * radius * radius;
    maxRadius = Math.max(maxRadius, radius);
  }
  const aspectRatio = 10 / 7;
  const packingDensity = 0.32;
  const requiredArea = diskArea / packingDensity;
  const width = Math.max(
    1000,
    Math.sqrt(requiredArea * aspectRatio),
    maxRadius * 4,
  );
  const height = Math.max(
    700,
    width / aspectRatio,
    maxRadius * 4,
  );
  const minX = GRAPH_LAYOUT_CENTER_X - width / 2;
  const minY = GRAPH_LAYOUT_CENTER_Y - height / 2;
  return {
    width,
    height,
    minX,
    minY,
    maxX: minX + width,
    maxY: minY + height,
    centerX: GRAPH_LAYOUT_CENTER_X,
    centerY: GRAPH_LAYOUT_CENTER_Y,
  };
}

export function expandLayoutWorldToFit(
  world: LayoutWorld,
  positions: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike>,
  padding = 96,
): LayoutWorld {
  if (positions.length !== collisionRadii.length * 2) return world;
  let halfWidth = world.width / 2;
  let halfHeight = world.height / 2;
  for (let index = 0; index < collisionRadii.length; index += 1) {
    const x = positions[index * 2];
    const y = positions[index * 2 + 1];
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const radius = Number.isFinite(collisionRadii[index])
      ? Math.max(1, collisionRadii[index])
      : 1;
    halfWidth = Math.max(
      halfWidth,
      Math.abs(x - world.centerX) + radius + padding,
    );
    halfHeight = Math.max(
      halfHeight,
      Math.abs(y - world.centerY) + radius + padding,
    );
  }
  const width = halfWidth * 2;
  const height = halfHeight * 2;
  if (width === world.width && height === world.height) return world;
  return {
    width,
    height,
    minX: world.centerX - halfWidth,
    minY: world.centerY - halfHeight,
    maxX: world.centerX + halfWidth,
    maxY: world.centerY + halfHeight,
    centerX: world.centerX,
    centerY: world.centerY,
  };
}

export type LayoutRuntimeStatus = {
  backend: "worker" | "webgpu";
  running: boolean;
  stable: boolean;
  energy: number;
  iterations: number;
  gpuName?: string;
  fallbackReason?: string;
};

export type WorkerInitMessage = {
  type: "init";
  positions: ArrayBuffer;
  masses: ArrayBuffer;
  collisionRadii: ArrayBuffer;
  edgeSources: ArrayBuffer;
  edgeTargets: ArrayBuffer;
  edgeWeights: ArrayBuffer;
  world: LayoutWorld;
  settings: ForceSettings;
  warmStart: boolean;
};

export type WorkerControlMessage =
  | { type: "play" }
  | { type: "pause" }
  | { type: "stop" }
  | { type: "reheat" }
  | { type: "settings"; settings: ForceSettings }
  | { type: "pin"; index: number; x: number; y: number }
  | { type: "unpin"; index: number };

export type WorkerInboundMessage = WorkerInitMessage | WorkerControlMessage;

export type WorkerOutboundMessage =
  | {
      type: "positions";
      positions: ArrayBuffer;
      energy: number;
      iterations: number;
    }
  | {
      type: "status";
      running: boolean;
      stable: boolean;
      energy: number;
      iterations: number;
    };
