export type BarnesHutTree = {
  root: BarnesHutQuad;
  positions: Float32Array<ArrayBufferLike>;
  masses: Float32Array<ArrayBufferLike>;
  collisionRadii: Float32Array<ArrayBufferLike>;
};

type BarnesHutQuad = {
  x: number;
  y: number;
  size: number;
  mass: number;
  centerX: number;
  centerY: number;
  maxRadius: number;
  indices: number[] | null;
  children: BarnesHutQuad[] | null;
};

const MAX_DEPTH = 18;
const THETA = 0.72;

export function buildBarnesHutTree(
  positions: Float32Array<ArrayBufferLike>,
  masses: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike> = new Float32Array(masses.length),
): BarnesHutTree {
  const count = Math.floor(positions.length / 2);
  if (
    positions.length % 2 !== 0
    || masses.length !== count
    || collisionRadii.length !== count
  ) {
    throw new Error("Barnes-Hut 输入维度不一致");
  }
  const root = createRootQuad(positions, collisionRadii);
  for (let index = 0; index < count; index += 1) {
    const x = positions[index * 2];
    const y = positions[index * 2 + 1];
    const mass = masses[index];
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(mass)) {
      throw new Error("Barnes-Hut 输入包含非有限数值");
    }
    const radius = collisionRadii[index];
    if (!Number.isFinite(radius) || radius < 0) {
      throw new Error("Barnes-Hut 碰撞半径无效");
    }
    insert(
      root,
      index,
      x,
      y,
      Math.max(0.0001, mass),
      radius,
      positions,
      masses,
      collisionRadii,
      0,
    );
  }
  return { root, positions, masses, collisionRadii };
}

export function calculateBarnesHutRepulsion(
  tree: BarnesHutTree,
  index: number,
  strength: number,
  alpha: number,
): { x: number; y: number } {
  const offset = index * 2;
  if (offset + 1 >= tree.positions.length) return { x: 0, y: 0 };
  return repulsionForce(
    tree.root,
    tree,
    index,
    tree.positions[offset],
    tree.positions[offset + 1],
    Math.max(0, strength),
    Math.max(0, alpha),
  );
}

export function calculateBarnesHutCollision(
  tree: BarnesHutTree,
  index: number,
  strength = 0.38,
): { x: number; y: number } {
  const offset = index * 2;
  if (offset + 1 >= tree.positions.length) return { x: 0, y: 0 };
  return collisionForce(
    tree.root,
    tree,
    index,
    tree.positions[offset],
    tree.positions[offset + 1],
    tree.collisionRadii[index],
    Math.max(0, strength),
  );
}

function createQuad(x: number, y: number, size: number): BarnesHutQuad {
  return {
    x,
    y,
    size,
    mass: 0,
    centerX: 0,
    centerY: 0,
    maxRadius: 0,
    indices: [],
    children: null,
  };
}

function createRootQuad(
  positions: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike>,
): BarnesHutQuad {
  if (positions.length === 0) return createQuad(-100, -250, 1200);
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let maxRadius = 0;
  for (let index = 0; index < collisionRadii.length; index += 1) {
    minX = Math.min(minX, positions[index * 2]);
    minY = Math.min(minY, positions[index * 2 + 1]);
    maxX = Math.max(maxX, positions[index * 2]);
    maxY = Math.max(maxY, positions[index * 2 + 1]);
    maxRadius = Math.max(maxRadius, collisionRadii[index]);
  }
  const size = Math.max(1, maxX - minX, maxY - minY) + maxRadius * 2 + 2;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return createQuad(centerX - size / 2, centerY - size / 2, size);
}

function insert(
  quad: BarnesHutQuad,
  index: number,
  x: number,
  y: number,
  mass: number,
  radius: number,
  positions: Float32Array<ArrayBufferLike>,
  masses: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike>,
  depth: number,
): void {
  const previousMass = quad.mass;
  quad.mass += mass;
  quad.centerX = (quad.centerX * previousMass + x * mass) / quad.mass;
  quad.centerY = (quad.centerY * previousMass + y * mass) / quad.mass;
  quad.maxRadius = Math.max(quad.maxRadius, radius);

  if (quad.children === null && quad.indices?.length === 0) {
    quad.indices.push(index);
    return;
  }
  if (quad.children === null && (depth >= MAX_DEPTH || quad.size < 0.01)) {
    quad.indices?.push(index);
    return;
  }
  if (quad.children === null) {
    quad.children = subdivide(quad);
    const existingIndices = quad.indices ?? [];
    quad.indices = null;
    for (const existing of existingIndices) {
      insertIntoChild(
        quad,
        existing,
        positions[existing * 2],
        positions[existing * 2 + 1],
        Math.max(0.0001, masses[existing]),
        collisionRadii[existing],
        positions,
        masses,
        collisionRadii,
        depth + 1,
      );
    }
  }
  insertIntoChild(
    quad,
    index,
    x,
    y,
    mass,
    radius,
    positions,
    masses,
    collisionRadii,
    depth + 1,
  );
}

function subdivide(quad: BarnesHutQuad): BarnesHutQuad[] {
  const half = quad.size / 2;
  return [
    createQuad(quad.x, quad.y, half),
    createQuad(quad.x + half, quad.y, half),
    createQuad(quad.x, quad.y + half, half),
    createQuad(quad.x + half, quad.y + half, half),
  ];
}

function insertIntoChild(
  quad: BarnesHutQuad,
  index: number,
  x: number,
  y: number,
  mass: number,
  radius: number,
  positions: Float32Array<ArrayBufferLike>,
  masses: Float32Array<ArrayBufferLike>,
  collisionRadii: Float32Array<ArrayBufferLike>,
  depth: number,
): void {
  if (!quad.children) return;
  const half = quad.size / 2;
  const right = x >= quad.x + half ? 1 : 0;
  const bottom = y >= quad.y + half ? 1 : 0;
  insert(
    quad.children[bottom * 2 + right],
    index,
    x,
    y,
    mass,
    radius,
    positions,
    masses,
    collisionRadii,
    depth,
  );
}

function repulsionForce(
  quad: BarnesHutQuad,
  tree: BarnesHutTree,
  index: number,
  x: number,
  y: number,
  strength: number,
  alpha: number,
): { x: number; y: number } {
  if (quad.mass === 0) return { x: 0, y: 0 };
  if (quad.children === null) {
    let forceX = 0;
    let forceY = 0;
    for (const otherIndex of quad.indices ?? []) {
      if (otherIndex === index) continue;
      let dx = x - tree.positions[otherIndex * 2];
      let dy = y - tree.positions[otherIndex * 2 + 1];
      if (Math.abs(dx) + Math.abs(dy) < 0.001) {
        const direction = index < otherIndex ? -1 : 1;
        dx = direction * (0.01 + ((index + otherIndex) % 7) * 0.002);
        dy = direction * (0.008 + ((index * 3 + otherIndex) % 5) * 0.002);
      }
      const contribution = pointRepulsion(
        dx,
        dy,
        strength,
        tree.masses[otherIndex],
        alpha,
      );
      forceX += contribution.x;
      forceY += contribution.y;
    }
    return { x: forceX, y: forceY };
  }

  const dx = x - quad.centerX;
  const dy = y - quad.centerY;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const containsTarget = (
    x >= quad.x
    && x < quad.x + quad.size
    && y >= quad.y
    && y < quad.y + quad.size
  );
  if (!containsTarget && quad.size / distance < THETA) {
    return pointRepulsion(dx, dy, strength, quad.mass, alpha);
  }

  let forceX = 0;
  let forceY = 0;
  for (const child of quad.children) {
    const childForce = repulsionForce(
      child,
      tree,
      index,
      x,
      y,
      strength,
      alpha,
    );
    forceX += childForce.x;
    forceY += childForce.y;
  }
  return { x: forceX, y: forceY };
}

function pointRepulsion(
  dx: number,
  dy: number,
  strength: number,
  mass: number,
  alpha: number,
): { x: number; y: number } {
  const distanceSquared = Math.max(36, dx * dx + dy * dy);
  const distance = Math.sqrt(distanceSquared);
  const force = strength * 40 * mass * alpha / distanceSquared;
  return { x: (dx / distance) * force, y: (dy / distance) * force };
}

function collisionForce(
  quad: BarnesHutQuad,
  tree: BarnesHutTree,
  index: number,
  x: number,
  y: number,
  radius: number,
  strength: number,
): { x: number; y: number } {
  if (quad.mass === 0 || !couldOverlap(quad, x, y, radius)) {
    return { x: 0, y: 0 };
  }
  if (quad.children === null) {
    let forceX = 0;
    let forceY = 0;
    for (const otherIndex of quad.indices ?? []) {
      if (otherIndex === index) continue;
      let dx = x - tree.positions[otherIndex * 2];
      let dy = y - tree.positions[otherIndex * 2 + 1];
      let distance = Math.hypot(dx, dy);
      if (distance < 0.001) {
        const direction = index < otherIndex ? -1 : 1;
        dx = direction * (0.71 + ((index + otherIndex) % 7) * 0.03);
        dy = direction * (0.53 + ((index * 3 + otherIndex) % 5) * 0.03);
        distance = Math.hypot(dx, dy);
      }
      const minimumDistance = radius + tree.collisionRadii[otherIndex];
      if (distance >= minimumDistance) continue;
      const magnitude = (minimumDistance - distance) * strength;
      forceX += (dx / distance) * magnitude;
      forceY += (dy / distance) * magnitude;
    }
    return { x: forceX, y: forceY };
  }
  let forceX = 0;
  let forceY = 0;
  for (const child of quad.children) {
    const childForce = collisionForce(
      child,
      tree,
      index,
      x,
      y,
      radius,
      strength,
    );
    forceX += childForce.x;
    forceY += childForce.y;
  }
  return { x: forceX, y: forceY };
}

function couldOverlap(
  quad: BarnesHutQuad,
  x: number,
  y: number,
  radius: number,
): boolean {
  const closestX = Math.max(quad.x, Math.min(x, quad.x + quad.size));
  const closestY = Math.max(quad.y, Math.min(y, quad.y + quad.size));
  return Math.hypot(x - closestX, y - closestY) <= radius + quad.maxRadius;
}
