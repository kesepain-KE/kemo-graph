import { describe, expect, it } from "vitest";

import { DEFAULT_FORCE_SETTINGS } from "../engine/layoutTypes";
import { makeLayoutCacheKey } from "./GraphPositionCache";

describe("graph position cache key", () => {
  it("is stable for the same snapshot and force settings", () => {
    const key = makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-1",
      2,
      DEFAULT_FORCE_SETTINGS,
    );
    expect(key).toBe(makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-1",
      2,
      { ...DEFAULT_FORCE_SETTINGS },
    ));
    expect(key.startsWith("unbounded-v2|")).toBe(true);
  });

  it("changes across snapshots, anchors and physical parameters", () => {
    const base = makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-1",
      2,
      DEFAULT_FORCE_SETTINGS,
    );
    expect(makeLayoutCacheKey(
      "revision-2",
      "local",
      "node-1",
      2,
      DEFAULT_FORCE_SETTINGS,
    )).not.toBe(base);
    expect(makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-2",
      2,
      DEFAULT_FORCE_SETTINGS,
    )).not.toBe(base);
    expect(makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-1",
      2,
      { ...DEFAULT_FORCE_SETTINGS, linkDistance: 160 },
    )).not.toBe(base);
    expect(makeLayoutCacheKey(
      "revision-1",
      "local",
      "node-1",
      2,
      DEFAULT_FORCE_SETTINGS,
      1.25,
    )).not.toBe(base);
  });
});
