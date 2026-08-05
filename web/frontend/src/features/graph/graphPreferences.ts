import type { GraphVisualizationNode } from "../../types/api";
import type {
  GraphAppearanceSettings,
  GraphNodeKind,
  GraphNodeVisualStyle,
  GraphPerformanceSettings,
  GraphSemanticColors,
} from "./engine/GraphRenderer";
import {
  DEFAULT_FORCE_SETTINGS,
  type ForceSettings,
} from "./engine/layoutTypes";

const STORAGE_KEY = "kemo-graph.graph-preferences.v3";
const LEGACY_STORAGE_KEYS = ["kemo-graph.graph-preferences.v2"] as const;
const GROUP_PALETTE = [
  "#00a98f",
  "#8268d8",
  "#d18716",
  "#3278c8",
  "#c85778",
  "#4b9460",
  "#9b62b5",
  "#2d8f9d",
] as const;

export type GraphViewPreferences = {
  mode: "local" | "global";
  depth: number;
};

export type GraphFilterPreferences = {
  query: string;
  groupId: string;
  sourceId: string;
  tag: string;
  relation: string;
  minNodeWeight: number;
  minRefCount: number;
  minEdgeWeight: number;
};

export type GraphColorRule = {
  id: string;
  enabled: boolean;
  field: "group" | "tag" | "type" | "query";
  value: string;
  color: string;
};

export type GraphPreferences = {
  view: GraphViewPreferences;
  filters: GraphFilterPreferences;
  colorRules: GraphColorRule[];
  appearance: GraphAppearanceSettings;
  force: ForceSettings;
  performance: GraphPerformanceSettings;
};

export const DEFAULT_GRAPH_COLORS: GraphSemanticColors = {
  selectedNode: "#00a98f",
  relatedNode: "#7258c7",
  selectedRelation: "#f59e0b",
  globalNode: "#7258c7",
  globalRelation: "#8b72dc",
  unrelatedNode: "#9aa5a1",
  unrelatedRelation: "#aeb7b3",
};

export const DEFAULT_GRAPH_PREFERENCES: GraphPreferences = {
  view: { mode: "local", depth: 2 },
  filters: {
    query: "",
    groupId: "all",
    sourceId: "all",
    tag: "all",
    relation: "all",
    minNodeWeight: 0,
    minRefCount: 0,
    minEdgeWeight: 0,
  },
  colorRules: [],
  appearance: {
    canvasPreset: "light",
    showArrows: true,
    labelOpacity: 0.92,
    nodeScale: 1,
    edgeScale: 1,
    relationLabels: "auto",
    unrelatedOpacity: 0.16,
    colors: { ...DEFAULT_GRAPH_COLORS },
  },
  force: { ...DEFAULT_FORCE_SETTINGS },
  performance: {
    mode: "high",
    labelDensity: "high",
    maxFps: 120,
  },
};

export function loadGraphPreferences(): GraphPreferences {
  if (!("localStorage" in globalThis)) return cloneDefaults();
  try {
    const raw = globalThis.localStorage.getItem(STORAGE_KEY)
      ?? LEGACY_STORAGE_KEYS
        .map((key) => globalThis.localStorage.getItem(key))
        .find((value) => value !== null);
    if (!raw) return cloneDefaults();
    const preferences = sanitizePreferences(JSON.parse(raw) as unknown);
    // The browsing scope is intentionally session-local: every new page opens
    // around the selected anchor while all durable visual/force settings stay.
    preferences.view.mode = "local";
    return preferences;
  } catch {
    return cloneDefaults();
  }
}

export function saveGraphPreferences(preferences: GraphPreferences): void {
  if (!("localStorage" in globalThis)) return;
  try {
    globalThis.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // Private browsing and storage quotas must not break the graph page.
  }
}

export function createColorRule(
  field: GraphColorRule["field"] = "group",
  value = "",
  color: string = GROUP_PALETTE[0],
): GraphColorRule {
  const id = "crypto" in globalThis && "randomUUID" in globalThis.crypto
    ? globalThis.crypto.randomUUID()
    : `rule-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return { id, enabled: true, field, value, color: normalizeHexColor(color) };
}

export function resolveNodeVisualStyle(
  node: GraphVisualizationNode,
  kind: GraphNodeKind,
  rules: GraphColorRule[],
  canvasPreset: GraphAppearanceSettings["canvasPreset"],
): GraphNodeVisualStyle {
  const matched = rules.find((rule) => rule.enabled && matchesRule(rule, node, kind));
  const groupId = node.group_ids[0] ?? null;
  const stroke = matched?.color
    ?? (groupId ? groupColor(groupId) : kind === "core" ? "#087f6a" : "#7258c7");
  return {
    stroke,
    fill: canvasPreset === "obsidian"
      ? mixHex(stroke, "#121620", 0.66)
      : mixHex(stroke, "#ffffff", 0.87),
    text: canvasPreset === "obsidian" ? "#f2f6f5" : "#26332f",
    alpha: 1,
    groupId,
  };
}

export function resolveSemanticNodeVisualStyle(
  node: GraphVisualizationNode,
  color: string,
  canvasPreset: GraphAppearanceSettings["canvasPreset"],
  alpha = 1,
): GraphNodeVisualStyle {
  const stroke = normalizeHexColor(color);
  return {
    stroke,
    fill: canvasPreset === "obsidian"
      ? mixHex(stroke, "#121620", 0.66)
      : mixHex(stroke, "#ffffff", 0.87),
    text: canvasPreset === "obsidian" ? "#f2f6f5" : "#26332f",
    alpha: Math.min(1, Math.max(0.02, alpha)),
    groupId: node.group_ids[0] ?? null,
  };
}

export function groupColor(groupId: string): string {
  let hash = 2166136261;
  for (let index = 0; index < groupId.length; index += 1) {
    hash ^= groupId.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return GROUP_PALETTE[(hash >>> 0) % GROUP_PALETTE.length];
}

function matchesRule(
  rule: GraphColorRule,
  node: GraphVisualizationNode,
  kind: GraphNodeKind,
): boolean {
  const value = rule.value.trim().toLocaleLowerCase();
  if (!value) return false;
  if (rule.field === "group") return node.group_ids.includes(rule.value);
  if (rule.field === "type") return kind === value;
  if (rule.field === "tag") {
    return node.tags.some((tag) => tag.toLocaleLowerCase() === value);
  }
  return [node.keyword, node.summary, ...node.aliases, ...node.tags].some(
    (text) => text.toLocaleLowerCase().includes(value),
  );
}

function sanitizePreferences(value: unknown): GraphPreferences {
  const input = isRecord(value) ? value : {};
  const view = isRecord(input.view) ? input.view : {};
  const filters = isRecord(input.filters) ? input.filters : {};
  const appearance = isRecord(input.appearance) ? input.appearance : {};
  const force = isRecord(input.force) ? input.force : {};
  const performance = isRecord(input.performance) ? input.performance : {};
  return {
    view: {
      mode: view.mode === "local" ? "local" : "global",
      depth: integer(view.depth, 1, 10, 2),
    },
    filters: {
      query: text(filters.query, ""),
      groupId: text(filters.groupId, "all"),
      sourceId: text(filters.sourceId, "all"),
      tag: text(filters.tag, "all"),
      relation: text(filters.relation, "all"),
      minNodeWeight: numeric(filters.minNodeWeight, 0, 1, 0),
      minRefCount: integer(filters.minRefCount, 0, 100000, 0),
      minEdgeWeight: numeric(filters.minEdgeWeight, 0, 1, 0),
    },
    colorRules: Array.isArray(input.colorRules)
      ? input.colorRules.flatMap((rule, index) => {
        if (!isRecord(rule)) return [];
        const field = ["group", "tag", "type", "query"].includes(String(rule.field))
          ? rule.field as GraphColorRule["field"]
          : "query";
        return [{
          id: text(rule.id, `rule-${index}`),
          enabled: rule.enabled !== false,
          field,
          value: text(rule.value, ""),
          color: normalizeHexColor(text(rule.color, GROUP_PALETTE[index % GROUP_PALETTE.length])),
        }];
      }).slice(0, 64)
      : [],
    appearance: {
      canvasPreset: appearance.canvasPreset === "obsidian" ? "obsidian" : "light",
      showArrows: appearance.showArrows !== false,
      labelOpacity: numeric(appearance.labelOpacity, 0.1, 1, 0.92),
      nodeScale: numeric(appearance.nodeScale, 0.5, 2, 1),
      edgeScale: numeric(appearance.edgeScale, 0.35, 3, 1),
      relationLabels: ["selected", "always", "never"].includes(
        String(appearance.relationLabels),
      )
        ? appearance.relationLabels as GraphAppearanceSettings["relationLabels"]
        : "auto",
      unrelatedOpacity: numeric(appearance.unrelatedOpacity, 0.02, 0.8, 0.16),
      colors: sanitizeGraphColors(appearance.colors),
    },
    force: {
      centerStrength: numeric(force.centerStrength, 0, 2, DEFAULT_FORCE_SETTINGS.centerStrength),
      repulsionStrength: numeric(force.repulsionStrength, 0, 40, DEFAULT_FORCE_SETTINGS.repulsionStrength),
      linkStrength: numeric(force.linkStrength, 0, 4, DEFAULT_FORCE_SETTINGS.linkStrength),
      linkDistance: numeric(force.linkDistance, 30, 360, DEFAULT_FORCE_SETTINGS.linkDistance),
      damping: numeric(force.damping, 0.4, 0.98, DEFAULT_FORCE_SETTINGS.damping),
      alphaDecay: numeric(force.alphaDecay, 0.002, 0.1, DEFAULT_FORCE_SETTINGS.alphaDecay),
      stableEnergy: numeric(force.stableEnergy, 0.001, 0.2, DEFAULT_FORCE_SETTINGS.stableEnergy),
    },
    performance: {
      mode: ["auto", "high", "compatible"].includes(String(performance.mode))
        ? performance.mode as GraphPerformanceSettings["mode"]
        : DEFAULT_GRAPH_PREFERENCES.performance.mode,
      labelDensity: ["low", "balanced", "high"].includes(String(performance.labelDensity))
        ? performance.labelDensity as GraphPerformanceSettings["labelDensity"]
        : DEFAULT_GRAPH_PREFERENCES.performance.labelDensity,
      maxFps: integer(
        performance.maxFps,
        20,
        120,
        DEFAULT_GRAPH_PREFERENCES.performance.maxFps,
      ),
    },
  };
}

function cloneDefaults(): GraphPreferences {
  return {
    ...DEFAULT_GRAPH_PREFERENCES,
    view: { ...DEFAULT_GRAPH_PREFERENCES.view },
    filters: { ...DEFAULT_GRAPH_PREFERENCES.filters },
    colorRules: [],
    appearance: {
      ...DEFAULT_GRAPH_PREFERENCES.appearance,
      colors: { ...DEFAULT_GRAPH_PREFERENCES.appearance.colors },
    },
    force: { ...DEFAULT_GRAPH_PREFERENCES.force },
    performance: { ...DEFAULT_GRAPH_PREFERENCES.performance },
  };
}

function sanitizeGraphColors(value: unknown): GraphSemanticColors {
  const input = isRecord(value) ? value : {};
  return {
    selectedNode: normalizeHexColor(text(input.selectedNode, DEFAULT_GRAPH_COLORS.selectedNode)),
    relatedNode: normalizeHexColor(text(input.relatedNode, DEFAULT_GRAPH_COLORS.relatedNode)),
    selectedRelation: normalizeHexColor(text(input.selectedRelation, DEFAULT_GRAPH_COLORS.selectedRelation)),
    globalNode: normalizeHexColor(text(input.globalNode, DEFAULT_GRAPH_COLORS.globalNode)),
    globalRelation: normalizeHexColor(text(input.globalRelation, DEFAULT_GRAPH_COLORS.globalRelation)),
    unrelatedNode: normalizeHexColor(text(input.unrelatedNode, DEFAULT_GRAPH_COLORS.unrelatedNode)),
    unrelatedRelation: normalizeHexColor(text(input.unrelatedRelation, DEFAULT_GRAPH_COLORS.unrelatedRelation)),
  };
}

function normalizeHexColor(value: string): string {
  return /^#[0-9a-f]{6}$/i.test(value) ? value.toLocaleLowerCase() : "#00a98f";
}

function mixHex(left: string, right: string, rightRatio: number): string {
  const ratio = Math.max(0, Math.min(1, rightRatio));
  const channel = (value: string, offset: number) => Number.parseInt(value.slice(offset, offset + 2), 16);
  const mixed = [1, 3, 5].map((offset) => Math.round(
    channel(left, offset) * (1 - ratio) + channel(right, offset) * ratio,
  ));
  return `#${mixed.map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function numeric(
  value: unknown,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
}

function integer(
  value: unknown,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  return Math.round(numeric(value, minimum, maximum, fallback));
}
