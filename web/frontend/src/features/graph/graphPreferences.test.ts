import { describe, expect, it } from "vitest";

import type { GraphVisualizationNode } from "../../types/api";
import {
  DEFAULT_GRAPH_PREFERENCES,
  createColorRule,
  groupColor,
  loadGraphPreferences,
  resolveNodeVisualStyle,
  saveGraphPreferences,
} from "./graphPreferences";

const node: GraphVisualizationNode = {
  node_id: "node-1",
  keyword: "中央画布",
  summary: "知识图谱的画布",
  aliases: ["Canvas"],
  tags: ["前端"],
  ref_count: 4,
  source_ids: ["source-1"],
  group_ids: ["group-1"],
};

describe("graph preferences", () => {
  it("defaults to a local context without discarding the global graph", () => {
    expect(DEFAULT_GRAPH_PREFERENCES.view.mode).toBe("local");
    expect(DEFAULT_GRAPH_PREFERENCES.view.depth).toBe(2);
  });

  it("defaults to the GPU-first high-performance profile", () => {
    expect(DEFAULT_GRAPH_PREFERENCES.performance).toEqual({
      mode: "high",
      labelDensity: "high",
      maxFps: 120,
    });
  });

  it("uses stable real-group colors without keyword guessing", () => {
    expect(groupColor("group-1")).toBe(groupColor("group-1"));
    expect(resolveNodeVisualStyle(node, "concept", [], "light").stroke).toBe(
      groupColor("group-1"),
    );
  });

  it("applies the first matching custom rule", () => {
    const tagRule = createColorRule("tag", "前端", "#ff0000");
    const queryRule = createColorRule("query", "画布", "#0000ff");
    expect(resolveNodeVisualStyle(
      node,
      "concept",
      [tagRule, queryRule],
      "light",
    ).stroke).toBe("#ff0000");
  });

  it("persists force, appearance, depth and semantic colors across sessions", () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    const values = new Map<string, string>();
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
    try {
      saveGraphPreferences({
        ...DEFAULT_GRAPH_PREFERENCES,
        view: { mode: "global", depth: 10 },
        force: { ...DEFAULT_GRAPH_PREFERENCES.force, repulsionStrength: 23 },
        appearance: {
          ...DEFAULT_GRAPH_PREFERENCES.appearance,
          unrelatedOpacity: 0.24,
          colors: {
            ...DEFAULT_GRAPH_PREFERENCES.appearance.colors,
            selectedNode: "#123456",
          },
        },
      });
      const loaded = loadGraphPreferences();
      expect(loaded.view).toEqual({ mode: "local", depth: 10 });
      expect(loaded.force.repulsionStrength).toBe(23);
      expect(loaded.appearance.unrelatedOpacity).toBe(0.24);
      expect(loaded.appearance.colors.selectedNode).toBe("#123456");
    } finally {
      if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
      else Reflect.deleteProperty(globalThis, "localStorage");
    }
  });
});
