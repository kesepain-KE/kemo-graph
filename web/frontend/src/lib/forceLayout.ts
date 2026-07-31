import type { GraphEdge, GraphNode } from "../types/api";

export type PositionedNode = GraphNode & { x: number; y: number };

type Particle = PositionedNode & { vx: number; vy: number };

const WIDTH = 1000;
const HEIGHT = 700;

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
): PositionedNode[] {
  if (nodes.length === 0) return [];
  const radius = Math.min(WIDTH, HEIGHT) * 0.34;
  const particles: Particle[] = nodes.map((node, index) => {
    const jitter = (hash(node.node_id) % 1000) / 1000 - 0.5;
    const angle = (index / nodes.length) * Math.PI * 2 + jitter * 0.4;
    return {
      ...node,
      x: WIDTH / 2 + Math.cos(angle) * radius * (0.72 + Math.abs(jitter) * 0.4),
      y: HEIGHT / 2 + Math.sin(angle) * radius * (0.72 + Math.abs(jitter) * 0.4),
      vx: 0,
      vy: 0,
    };
  });
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
        const distanceSquared = Math.max(dx * dx + dy * dy, 36);
        const distance = Math.sqrt(distanceSquared);
        dx /= distance;
        dy /= distance;
        const force = (repulsion / distanceSquared) * cooling;
        a.vx -= dx * force;
        a.vy -= dy * force;
        b.vx += dx * force;
        b.vy += dy * force;
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

    for (const particle of particles) {
      particle.vx += (WIDTH / 2 - particle.x) * 0.0009;
      particle.vy += (HEIGHT / 2 - particle.y) * 0.0009;
      particle.vx *= 0.78;
      particle.vy *= 0.78;
      const speed = Math.max(1, Math.hypot(particle.vx, particle.vy) / 9);
      particle.x += particle.vx / speed;
      particle.y += particle.vy / speed;
      particle.x = Math.min(WIDTH - 60, Math.max(60, particle.x));
      particle.y = Math.min(HEIGHT - 60, Math.max(60, particle.y));
    }
  }

  return particles.map(({ vx: _vx, vy: _vy, ...particle }) => particle);
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
