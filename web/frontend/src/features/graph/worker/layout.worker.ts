import type {
  ForceSettings,
  LayoutWorld,
  WorkerInboundMessage,
  WorkerOutboundMessage,
} from "../engine/layoutTypes";
import { resolvePositionCollisions } from "../engine/collision";
import {
  buildBarnesHutTree,
  calculateBarnesHutCollision,
  calculateBarnesHutRepulsion,
} from "./barnesHut";

type WorkerScope = {
  onmessage: ((event: MessageEvent<WorkerInboundMessage>) => void) | null;
  postMessage: (message: WorkerOutboundMessage, transfer?: Transferable[]) => void;
};

const scope = self as unknown as WorkerScope;
let positions = new Float32Array();
let velocities = new Float32Array();
let masses = new Float32Array();
let collisionRadii = new Float32Array();
let edgeSources = new Uint32Array();
let edgeTargets = new Uint32Array();
let edgeWeights = new Float32Array();
let settings: ForceSettings | null = null;
let world: LayoutWorld = {
  width: 1000,
  height: 700,
  minX: 0,
  minY: 0,
  maxX: 1000,
  maxY: 700,
  centerX: 500,
  centerY: 350,
};
let running = false;
let alpha = 1;
let energy = 0;
let iterations = 0;
let stableFrames = 0;
let framesSinceSnapshot = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
const pinned = new Map<number, { x: number; y: number }>();

scope.onmessage = (event) => {
  const message = event.data;
  switch (message.type) {
    case "init":
      positions = new Float32Array(message.positions);
      velocities = new Float32Array(positions.length);
      masses = new Float32Array(message.masses);
      collisionRadii = new Float32Array(message.collisionRadii);
      edgeSources = new Uint32Array(message.edgeSources);
      edgeTargets = new Uint32Array(message.edgeTargets);
      edgeWeights = new Float32Array(message.edgeWeights);
      world = message.world;
      settings = message.settings;
      pinned.clear();
      alpha = message.warmStart ? 0.28 : 1;
      energy = 0;
      iterations = 0;
      stableFrames = 0;
      framesSinceSnapshot = 0;
      running = true;
      schedule();
      break;
    case "play":
      running = true;
      schedule();
      emitStatus(false);
      break;
    case "pause":
      running = false;
      cancelTimer();
      emitStatus(false);
      break;
    case "stop":
      running = false;
      cancelTimer();
      pinned.clear();
      break;
    case "reheat":
      alpha = 1;
      stableFrames = 0;
      running = true;
      schedule();
      break;
    case "settings":
      settings = message.settings;
      alpha = Math.max(alpha, 0.72);
      stableFrames = 0;
      running = true;
      schedule();
      break;
    case "pin":
      pinned.set(message.index, { x: message.x, y: message.y });
      alpha = Math.max(alpha, 0.5);
      running = true;
      schedule();
      break;
    case "unpin":
      pinned.delete(message.index);
      alpha = Math.max(alpha, 0.35);
      break;
  }
};

function schedule(): void {
  if (!running || timer !== null || !settings || positions.length === 0) return;
  timer = setTimeout(runFrame, 0);
}

function runFrame(): void {
  timer = null;
  if (!running || !settings) return;
  const frameStartedAt = performance.now();
  let ticks = 0;
  do {
    tick(settings);
    ticks += 1;
  } while (ticks < 3 && performance.now() - frameStartedAt < 10);

  if (alpha < 0.08 && energy < settings.stableEnergy) stableFrames += 1;
  else stableFrames = 0;
  framesSinceSnapshot += 1;
  const stable = stableFrames >= 12;
  if (framesSinceSnapshot >= 3 || stable) emitPositions();
  if (stable) {
    running = false;
    emitStatus(true);
    return;
  }
  schedule();
}

function tick(active: ForceSettings): void {
  const count = positions.length / 2;
  if (count === 0) return;
  const forces = new Float32Array(positions.length);
  const tree = buildBarnesHutTree(positions, masses, collisionRadii);

  for (let index = 0; index < count; index += 1) {
    const x = positions[index * 2];
    const y = positions[index * 2 + 1];
    const repulsion = calculateBarnesHutRepulsion(
      tree,
      index,
      active.repulsionStrength,
      alpha,
    );
    const collision = calculateBarnesHutCollision(tree, index);
    forces[index * 2] += repulsion.x + collision.x;
    forces[index * 2 + 1] += repulsion.y + collision.y;
    forces[index * 2] += (
      (world.centerX - x) * active.centerStrength * 0.0009 * alpha
    );
    forces[index * 2 + 1] += (
      (world.centerY - y) * active.centerStrength * 0.0009 * alpha
    );
  }

  for (let edgeIndex = 0; edgeIndex < edgeSources.length; edgeIndex += 1) {
    const source = edgeSources[edgeIndex];
    const target = edgeTargets[edgeIndex];
    const sourceOffset = source * 2;
    const targetOffset = target * 2;
    const dx = positions[targetOffset] - positions[sourceOffset];
    const dy = positions[targetOffset + 1] - positions[sourceOffset + 1];
    const distance = Math.max(1, Math.hypot(dx, dy));
    const strength = (
      (distance - active.linkDistance)
      * 0.004
      * active.linkStrength
      * Math.max(0.08, edgeWeights[edgeIndex])
      * alpha
    );
    const forceX = (dx / distance) * strength;
    const forceY = (dy / distance) * strength;
    forces[sourceOffset] += forceX;
    forces[sourceOffset + 1] += forceY;
    forces[targetOffset] -= forceX;
    forces[targetOffset + 1] -= forceY;
  }

  let totalEnergy = 0;
  for (let index = 0; index < count; index += 1) {
    const offset = index * 2;
    const fixed = pinned.get(index);
    if (fixed) {
      positions[offset] = fixed.x;
      positions[offset + 1] = fixed.y;
      velocities[offset] = 0;
      velocities[offset + 1] = 0;
      continue;
    }
    velocities[offset] = (
      velocities[offset] + forces[offset] / Math.max(0.5, masses[index])
    ) * active.damping;
    velocities[offset + 1] = (
      velocities[offset + 1] + forces[offset + 1] / Math.max(0.5, masses[index])
    ) * active.damping;
    const speed = Math.hypot(velocities[offset], velocities[offset + 1]);
    const limiter = Math.max(1, speed / 10);
    velocities[offset] /= limiter;
    velocities[offset + 1] /= limiter;
    positions[offset] += velocities[offset];
    positions[offset + 1] += velocities[offset + 1];
    totalEnergy += velocities[offset] ** 2 + velocities[offset + 1] ** 2;
  }
  const collisionResult = resolvePositionCollisions(
    positions,
    collisionRadii,
    world,
    3,
    pinned,
  );
  energy = Math.max(
    totalEnergy / count,
    collisionResult.overlaps > 0
      ? active.stableEnergy * 2 + collisionResult.maxOverlap
      : 0,
  );
  alpha = Math.max(0.02, alpha * (1 - active.alphaDecay));
  iterations += 1;
}

function emitPositions(): void {
  framesSinceSnapshot = 0;
  const snapshot = positions.slice();
  scope.postMessage(
    {
      type: "positions",
      positions: snapshot.buffer,
      energy,
      iterations,
    },
    [snapshot.buffer],
  );
}

function emitStatus(stable: boolean): void {
  scope.postMessage({
    type: "status",
    running,
    stable,
    energy,
    iterations,
  });
}

function cancelTimer(): void {
  if (timer !== null) clearTimeout(timer);
  timer = null;
}
