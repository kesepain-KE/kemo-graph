import type { GraphEdge, GraphNode } from "../types/api";
import { resolvePositionCollisions } from "../features/graph/engine/collision";
import {
  calculateLayoutWorld,
  graphNodeCollisionRadius,
} from "../features/graph/engine/layoutTypes";

export type PositionedNode = GraphNode & { x: number; y: number };

type Particle = PositionedNode & { vx: number; vy: number };

function hash(value: string): number {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return result >>> 0;
}

export function computeForceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  iterations = 180,
  nodeScale = 1,
): PositionedNode[] {
  if (nodes.length === 0) return [];
  const collisionRadii = new Float32Array(
    nodes.map((node) => graphNodeCollisionRadius(node.ref_count, nodeScale)),
  );
  const world = calculateLayoutWorld(collisionRadii);
  const particles: Particle[] = computeInitialLayout(nodes, nodeScale).map((node) => ({
    ...node,
    vx: 0,
    vy: 0,
  }));
  const indexById = new Map(
    particles.map((particle, index) => [particle.node_id, index]),
  );
  const springs = edges.flatMap((edge) => {
    const source = indexById.get(edge.source_node_id);
    const target = indexById.get(edge.target_node_id);
    return source === undefined || target === undefined
      ? []
      : [{ source, target, weight: edge.weight }];
  });
  const repulsion = Math.max(1800, 7800 / Math.sqrt(nodes.length));

  for (let tick = 0; tick < iterations; tick += 1) {
    const cooling = 1 - tick / iterations;
    for (let left = 0; left < particles.length; left += 1) {
      for (let right = left + 1; right < particles.length; right += 1) {
        const a = particles[left];
        const b = particles[right];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let rawDistance = Math.hypot(dx, dy);
        if (rawDistance < 0.001) {
          const angle = ((hash(`${a.node_id}:${b.node_id}`) % 6283) / 1000);
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          rawDistance = 1;
        }
        const distanceSquared = Math.max(dx * dx + dy * dy, 36);
        const distance = Math.sqrt(distanceSquared);
        dx /= distance;
        dy /= distance;
        const force = (repulsion / distanceSquared) * cooling;
        a.vx -= dx * force;
        a.vy -= dy * force;
        b.vx += dx * force;
        b.vy += dy * force;
        const minimumDistance = collisionRadii[left] + collisionRadii[right];
        if (rawDistance < minimumDistance) {
          const collisionForce = (
            minimumDistance - rawDistance
          ) * (0.16 + cooling * 0.18);
          a.vx -= dx * collisionForce;
          a.vy -= dy * collisionForce;
          b.vx += dx * collisionForce;
          b.vy += dy * collisionForce;
        }
      }
    }

    for (const spring of springs) {
      const source = particles[spring.source];
      const target = particles[spring.target];
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(Math.hypot(dx, dy), 1);
      const desired = 118 + (1 - Math.min(1, spring.weight)) * 34;
      const force = (distance - desired) * 0.013 * cooling;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      source.vx += fx;
      source.vy += fy;
      target.vx -= fx;
      target.vy -= fy;
    }

    particles.forEach((particle) => {
      particle.vx += (world.centerX - particle.x) * 0.0009;
      particle.vy += (world.centerY - particle.y) * 0.0009;
      particle.vx *= 0.78;
      particle.vy *= 0.78;
      const speed = Math.max(1, Math.hypot(particle.vx, particle.vy) / 9);
      particle.x += particle.vx / speed;
      particle.y += particle.vy / speed;
    });
  }

  const finalPositions = new Float32Array(particles.length * 2);
  particles.forEach((particle, index) => {
    finalPositions[index * 2] = particle.x;
    finalPositions[index * 2 + 1] = particle.y;
  });
  resolvePositionCollisions(finalPositions, collisionRadii, world, 16);
  particles.forEach((particle, index) => {
    particle.x = finalPositions[index * 2];
    particle.y = finalPositions[index * 2 + 1];
  });

  return particles.map(({ vx: _vx, vy: _vy, ...particle }) => particle);
}

export function computeInitialLayout(
  nodes: GraphNode[],
  nodeScale = 1,
): PositionedNode[] {
  if (nodes.length === 0) return [];
  const collisionRadii = new Float32Array(
    nodes.map((node) => graphNodeCollisionRadius(node.ref_count, nodeScale)),
  );
  const world = calculateLayoutWorld(collisionRadii);
  const maxRadius = Math.max(...collisionRadii);
  const radiusX = Math.max(1, world.width / 2 - maxRadius - 12);
  const radiusY = Math.max(1, world.height / 2 - maxRadius - 12);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  return nodes.map((node, index) => {
    const jitter = (hash(node.node_id) % 1000) / 1000 - 0.5;
    const angle = index * goldenAngle + jitter * 0.16;
    const radialScale = Math.sqrt((index + 0.5) / nodes.length);
    return {
      ...node,
      x: world.centerX + Math.cos(angle) * radiusX * radialScale,
      y: world.centerY + Math.sin(angle) * radiusY * radialScale,
    };
  });
}

export function selectNeighborhood(
  nodes: GraphNode[],
  edges: GraphEdge[],
  anchorId: string | null,
  depth: number,
): Set<string> {
  if (!anchorId || !nodes.some((node) => node.node_id === anchorId)) {
    return new Set(nodes.map((node) => node.node_id));
  }
  const adjacency = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!adjacency.has(edge.source_node_id)) adjacency.set(edge.source_node_id, new Set());
    if (!adjacency.has(edge.target_node_id)) adjacency.set(edge.target_node_id, new Set());
    adjacency.get(edge.source_node_id)?.add(edge.target_node_id);
    adjacency.get(edge.target_node_id)?.add(edge.source_node_id);
  }
  const visible = new Set([anchorId]);
  let frontier = new Set([anchorId]);
  for (let layer = 0; layer < depth; layer += 1) {
    const next = new Set<string>();
    for (const nodeId of frontier) {
      for (const neighbor of adjacency.get(nodeId) ?? []) {
        if (!visible.has(neighbor)) next.add(neighbor);
        visible.add(neighbor);
      }
    }
    frontier = next;
  }
  return visible;
}
