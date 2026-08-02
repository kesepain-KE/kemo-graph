import { describe, expect, it } from "vitest";

import {
  distanceToSegment,
  isPointInsideCircle,
  trimSegmentForNodeProtection,
} from "./hitTesting";

describe("graph pointer hit testing", () => {
  const source = { x: 0, y: 0 };
  const target = { x: 100, y: 0 };

  it("reserves the node center and outer safety ring for node interaction", () => {
    expect(isPointInsideCircle(source, source, 30)).toBe(true);
    expect(isPointInsideCircle({ x: 29.9, y: 0 }, source, 30)).toBe(true);
    expect(isPointInsideCircle({ x: 30.1, y: 0 }, source, 30)).toBe(false);
  });

  it("trims the relation hit segment away from both protected node areas", () => {
    const segment = trimSegmentForNodeProtection(source, target, 30, 24);
    expect(segment).toEqual({
      source: { x: 30, y: 0 },
      target: { x: 76, y: 0 },
    });
    expect(distanceToSegment({ x: 50, y: 4 }, segment!.source, segment!.target)).toBe(4);
  });

  it("disables relation hits when two node protection areas overlap", () => {
    expect(trimSegmentForNodeProtection(source, { x: 40, y: 0 }, 22, 22)).toBeNull();
  });
});
