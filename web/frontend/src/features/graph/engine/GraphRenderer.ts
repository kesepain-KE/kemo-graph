import type { GraphEdge } from "../../../types/api";
import type { PositionedNode } from "../../../lib/forceLayout";
import { graphNodeVisualRadius } from "./layoutTypes";

export type GraphNodeKind = "core" | "concept" | "group";

export type GraphCanvasPreset = "light" | "obsidian";
export type GraphRelationLabelMode = "auto" | "selected" | "always" | "never";
export type GraphPerformanceMode = "auto" | "high" | "compatible";
export type GraphLabelDensity = "low" | "balanced" | "high";

export type GraphSemanticColors = {
  selectedNode: string;
  relatedNode: string;
  selectedRelation: string;
  globalNode: string;
  globalRelation: string;
  unrelatedNode: string;
  unrelatedRelation: string;
};

export type GraphAppearanceSettings = {
  canvasPreset: GraphCanvasPreset;
  showArrows: boolean;
  labelOpacity: number;
  nodeScale: number;
  edgeScale: number;
  relationLabels: GraphRelationLabelMode;
  unrelatedOpacity: number;
  colors: GraphSemanticColors;
};

export type GraphPerformanceSettings = {
  mode: GraphPerformanceMode;
  labelDensity: GraphLabelDensity;
  maxFps: number;
};

export type GraphNodeVisualStyle = {
  fill: string;
  stroke: string;
  text: string;
  alpha: number;
  groupId: string | null;
};

export type GraphContextMode = "global" | "local";

export type GraphRenderScene = {
  nodes: PositionedNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  selectedEdgeId: string | null;
  highlightedIds: Set<string>;
  relatedNodeIds: Set<string>;
  relatedEdgeIds: Set<string>;
  contextMode: GraphContextMode;
  nodeKinds: Map<string, GraphNodeKind>;
  nodeStyles: Map<string, GraphNodeVisualStyle>;
  appearance: GraphAppearanceSettings;
  performance: GraphPerformanceSettings;
};

export type GraphRendererCallbacks = {
  onSelect: (nodeId: string) => void;
  onSelectEdge?: (edgeId: string) => void;
  onNodeDragStart?: (nodeId: string, x: number, y: number) => void;
  onNodeDrag?: (nodeId: string, x: number, y: number) => void;
  onNodeDragEnd?: (nodeId: string) => void;
  onFpsChange?: (fps: number) => void;
};

export interface GraphRenderer {
  readonly backend: string;
  mount(
    host: HTMLElement,
    scene: GraphRenderScene,
    callbacks: GraphRendererCallbacks,
  ): Promise<void>;
  update(scene: GraphRenderScene): void;
  setPositions(nodeIds: string[], positions: Float32Array): void;
  zoomBy(delta: number, anchor?: { x: number; y: number }): void;
  focusNode(nodeId: string | null): void;
  resetView(): void;
  destroy(): void;
}

export const GRAPH_WORLD_WIDTH = 1000;
export const GRAPH_WORLD_HEIGHT = 700;
export const GRAPH_MIN_ZOOM = 0.35;
export const GRAPH_MAX_ZOOM = 4;

export function nodeRadius(refCount: number): number {
  return graphNodeVisualRadius(refCount);
}

export function graphLabel(value: string, max = 12): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}
