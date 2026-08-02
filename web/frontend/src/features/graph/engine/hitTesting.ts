export type HitPoint = { x: number; y: number };

export type ProtectedSegment = {
  source: HitPoint;
  target: HitPoint;
};

export function isPointInsideCircle(
  point: HitPoint,
  center: HitPoint,
  radius: number,
): boolean {
  return Math.hypot(point.x - center.x, point.y - center.y) <= Math.max(0, radius);
}

export function distanceToSegment(
  point: HitPoint,
  source: HitPoint,
  target: HitPoint,
): number {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared <= 0.0001) return Math.hypot(point.x - source.x, point.y - source.y);
  const projection = Math.max(0, Math.min(1, (
    (point.x - source.x) * dx + (point.y - source.y) * dy
  ) / lengthSquared));
  return Math.hypot(
    point.x - (source.x + projection * dx),
    point.y - (source.y + projection * dy),
  );
}

export function trimSegmentForNodeProtection(
  source: HitPoint,
  target: HitPoint,
  sourceProtectionRadius: number,
  targetProtectionRadius: number,
): ProtectedSegment | null {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.hypot(dx, dy);
  const sourceInset = Math.max(0, sourceProtectionRadius);
  const targetInset = Math.max(0, targetProtectionRadius);
  if (distance <= sourceInset + targetInset || distance <= 0.0001) return null;
  const ux = dx / distance;
  const uy = dy / distance;
  return {
    source: {
      x: source.x + ux * sourceInset,
      y: source.y + uy * sourceInset,
    },
    target: {
      x: target.x - ux * targetInset,
      y: target.y - uy * targetInset,
    },
  };
}
