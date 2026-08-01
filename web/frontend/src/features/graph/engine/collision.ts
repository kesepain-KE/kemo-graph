import type { LayoutWorld } from "./layoutTypes";

export type CollisionResolution = {
  overlaps: number;
  maxOverlap: number;
};

type PinLookup = {
  has: (index: number) => boolean;
};

export function resolvePositionCollisions(
  positions: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike>,
  world: LayoutWorld,
  passes = 6,
  pinned?: PinLookup,
): CollisionResolution {
  const count = collisionRadii.length;
  if (positions.length !== count * 2) {
    throw new Error("节点碰撞输入维度不一致");
  }
  if (count < 2) return { overlaps: 0, maxOverlap: 0 };
  let result = { overlaps: 0, maxOverlap: 0 };
  for (let pass = 0; pass < Math.max(1, passes); pass += 1) {
    result = resolveCollisionPass(positions, collisionRadii, world, pinned, true);
    if (result.overlaps === 0) return result;
  }
  return resolveCollisionPass(positions, collisionRadii, world, pinned, false);
}

function resolveCollisionPass(
  positions: Float32Array<ArrayBufferLike>,
  radii: Float32Array<ArrayBufferLike>,
  world: LayoutWorld,
  pinned: PinLookup | undefined,
  mutate: boolean,
): CollisionResolution {
  let maxRadius = 1;
  for (const radius of radii) maxRadius = Math.max(maxRadius, radius);
  const cellSize = maxRadius * 2;
  const buckets = new Map<string, number[]>();
  for (let index = 0; index < radii.length; index += 1) {
    const key = cellKey(
      positions[index * 2],
      positions[index * 2 + 1],
      world,
      cellSize,
    );
    const bucket = buckets.get(key);
    if (bucket) bucket.push(index);
    else buckets.set(key, [index]);
  }

  let overlaps = 0;
  let maxOverlap = 0;
  for (let index = 0; index < radii.length; index += 1) {
    const x = positions[index * 2];
    const y = positions[index * 2 + 1];
    const cellX = Math.floor((x - world.minX) / cellSize);
    const cellY = Math.floor((y - world.minY) / cellSize);
    for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
      for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
        const bucket = buckets.get(`${cellX + offsetX}:${cellY + offsetY}`) ?? [];
        for (const otherIndex of bucket) {
          if (otherIndex <= index) continue;
          const otherOffset = otherIndex * 2;
          let dx = positions[otherOffset] - positions[index * 2];
          let dy = positions[otherOffset + 1] - positions[index * 2 + 1];
          let distance = Math.hypot(dx, dy);
          const minimumDistance = radii[index] + radii[otherIndex];
          if (distance + 0.01 >= minimumDistance) continue;
          if (distance < 0.001) {
            const angle = deterministicAngle(index, otherIndex);
            dx = Math.cos(angle);
            dy = Math.sin(angle);
            distance = 1;
          }
          const overlap = minimumDistance - distance + 0.02;
          overlaps += 1;
          maxOverlap = Math.max(maxOverlap, overlap);
          if (!mutate) continue;
          const firstPinned = pinned?.has(index) ?? false;
          const secondPinned = pinned?.has(otherIndex) ?? false;
          if (firstPinned && secondPinned) continue;
          const unitX = dx / distance;
          const unitY = dy / distance;
          const firstShare = firstPinned ? 0 : secondPinned ? 1 : 0.5;
          const secondShare = secondPinned ? 0 : firstPinned ? 1 : 0.5;
          if (firstShare > 0) {
            positions[index * 2] -= unitX * overlap * firstShare;
            positions[index * 2 + 1] -= unitY * overlap * firstShare;
          }
          if (secondShare > 0) {
            positions[otherOffset] += unitX * overlap * secondShare;
            positions[otherOffset + 1] += unitY * overlap * secondShare;
          }
        }
      }
    }
  }
  return { overlaps, maxOverlap };
}

function cellKey(
  x: number,
  y: number,
  world: LayoutWorld,
  cellSize: number,
): string {
  return `${Math.floor((x - world.minX) / cellSize)}:${Math.floor((y - world.minY) / cellSize)}`;
}

function deterministicAngle(left: number, right: number): number {
  const hash = Math.imul(left + 1, 73856093) ^ Math.imul(right + 1, 19349663);
  return ((hash >>> 0) % 6283) / 1000;
}
