import {
  Application,
  Circle,
  Container,
  FederatedPointerEvent,
  Graphics,
  Rectangle,
  Text,
} from "pixi.js";

import type { PositionedNode } from "../../../lib/forceLayout";
import type { GraphEdge } from "../../../types/api";
import {
  GRAPH_MAX_ZOOM,
  GRAPH_MIN_ZOOM,
  graphLabel,
  nodeRadius,
  type GraphRenderScene,
  type GraphRenderer,
  type GraphRendererCallbacks,
} from "./GraphRenderer";
import {
  calculateLayoutWorld,
  graphNodeCollisionRadius,
  type LayoutWorld,
} from "./layoutTypes";
import { distanceToSegment, isPointInsideCircle } from "./hitTesting";

type ScreenPoint = { x: number; y: number };
type WorldBounds = { minX: number; minY: number; maxX: number; maxY: number };
type DragState =
  | { kind: "pan"; origin: ScreenPoint; pan: ScreenPoint }
  | { kind: "node"; nodeId: string };

const COLORS = {
  edge: 0x7258c7,
  edgeLabel: 0x78847f,
  core: { fill: 0xe7f3ef, stroke: 0x087f6a },
  concept: { fill: 0xf0edf8, stroke: 0x7258c7 },
  group: { fill: 0xfbf1df, stroke: 0xb9700a },
  text: 0x26332f,
  muted: 0x788580,
} as const;

const NODE_POINTER_SAFETY_PX = 12;
const EDGE_POINTER_THRESHOLD_PX = 10;

export class PixiGraphRenderer implements GraphRenderer {
  readonly backend = "pixi-webgl";

  private app: Application | null = null;
  private readonly world = new Container();
  private readonly edgeLayer = new Container();
  private readonly labelLayer = new Container();
  private readonly nodeLayer = new Container();
  private edgeGraphics: Graphics | null = null;
  private callbacks: GraphRendererCallbacks | null = null;
  private scene: GraphRenderScene | null = null;
  private zoom = 1;
  private pan: ScreenPoint = { x: 0, y: 0 };
  private drag: DragState | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private readonly nodeContainers = new Map<string, Container>();
  private readonly edgeLabels = new Map<string, Text>();
  private readonly sceneNodesById = new Map<string, PositionedNode>();
  private nodeLabelIds = new Set<string>();
  private edgeLabelIds = new Set<string>();
  private readonly manualPositions = new Map<string, ScreenPoint>();
  private readonly externalPositions = new Map<string, ScreenPoint>();
  private fpsWindowStartedAt = 0;
  private fpsFrames = 0;
  private maxRefCount = 1;
  private labelLodKey = "";
  private edgesDirty = false;
  private wheelFrame: number | null = null;
  private pendingWheelDelta = 0;
  private pendingWheelAnchor: ScreenPoint | null = null;
  private layoutWorld: LayoutWorld = calculateLayoutWorld(new Float32Array());
  private readonly onWheel = (event: WheelEvent) => {
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    const magnitude = Math.min(0.16, Math.max(0.025, Math.abs(event.deltaY) * 0.0015));
    this.pendingWheelDelta += direction * magnitude;
    this.pendingWheelAnchor = { x: event.offsetX, y: event.offsetY };
    if (this.wheelFrame !== null) return;
    this.wheelFrame = requestAnimationFrame(() => {
      this.wheelFrame = null;
      const delta = this.pendingWheelDelta;
      const anchor = this.pendingWheelAnchor;
      this.pendingWheelDelta = 0;
      this.pendingWheelAnchor = null;
      this.zoomBy(delta, anchor ?? undefined);
    });
  };

  async mount(
    host: HTMLElement,
    scene: GraphRenderScene,
    callbacks: GraphRendererCallbacks,
  ): Promise<void> {
    this.destroy();
    this.scene = scene;
    this.updateLayoutWorld(scene);
    this.callbacks = callbacks;

    const app = new Application();
    await app.init({
      preference: "webgl",
      powerPreference: scene.performance.mode === "high" ? "high-performance" : undefined,
      antialias: true,
      backgroundAlpha: 0,
      autoDensity: true,
      resolution: Math.min(globalThis.devicePixelRatio || 1, 2),
      resizeTo: host,
    });
    this.app = app;
    app.ticker.maxFPS = scene.performance.maxFps;
    this.fpsWindowStartedAt = performance.now();
    this.fpsFrames = 0;
    app.ticker.add(this.handleFrame);
    app.canvas.className = "graph-pixi-canvas";
    app.canvas.setAttribute("aria-hidden", "true");
    host.replaceChildren(app.canvas);

    this.world.addChild(this.edgeLayer, this.labelLayer, this.nodeLayer);
    this.edgeGraphics = new Graphics();
    this.edgeLayer.addChild(this.edgeGraphics);
    app.stage.addChild(this.world);
    app.stage.eventMode = "static";
    app.stage.cursor = "grab";
    this.updateStageHitArea();
    app.stage.on("pointerdown", this.handleStagePointerDown);
    app.stage.on("pointermove", this.handlePointerMove);
    app.stage.on("pointerup", this.handlePointerUp);
    app.stage.on("pointerupoutside", this.handlePointerUp);
    app.stage.on("pointertap", this.handleStagePointerTap);
    app.canvas.addEventListener("wheel", this.onWheel, { passive: false });

    this.resizeObserver = new ResizeObserver(() => {
      this.updateStageHitArea();
      this.applyViewport();
    });
    this.resizeObserver.observe(host);
    this.rebuildScene();
    this.applyViewport();
  }

  update(scene: GraphRenderScene): void {
    this.scene = scene;
    this.updateLayoutWorld(scene);
    const activeIds = new Set(scene.nodes.map((node) => node.node_id));
    for (const nodeId of this.manualPositions.keys()) {
      if (!activeIds.has(nodeId)) this.manualPositions.delete(nodeId);
    }
    for (const nodeId of this.externalPositions.keys()) {
      if (!activeIds.has(nodeId)) this.externalPositions.delete(nodeId);
    }
    if (this.app) this.rebuildScene();
    if (this.app) this.app.ticker.maxFPS = scene.performance.maxFps;
    if (this.app) this.applyViewport();
  }

  setPositions(nodeIds: string[], positions: Float32Array): void {
    if (positions.length !== nodeIds.length * 2) return;
    const draggedId = this.drag?.kind === "node" ? this.drag.nodeId : null;
    nodeIds.forEach((nodeId, index) => {
      if (nodeId === draggedId) return;
      const position = {
        x: positions[index * 2],
        y: positions[index * 2 + 1],
      };
      this.externalPositions.set(nodeId, position);
      this.nodeContainers.get(nodeId)?.position.set(position.x, position.y);
    });
    this.edgesDirty = true;
  }

  zoomBy(delta: number, anchor?: ScreenPoint): void {
    if (!this.app) return;
    const previousLodKey = this.labelLodKey;
    const previousScale = this.fitScale() * this.zoom;
    const point = anchor ?? {
      x: this.app.screen.width / 2,
      y: this.app.screen.height / 2,
    };
    const worldPoint = {
      x: (point.x - this.world.position.x) / Math.max(0.0001, previousScale),
      y: (point.y - this.world.position.y) / Math.max(0.0001, previousScale),
    };
    const nextZoom = Math.min(
      GRAPH_MAX_ZOOM,
      Math.max(GRAPH_MIN_ZOOM, this.zoom + delta),
    );
    if (nextZoom === this.zoom) return;
    this.zoom = nextZoom;
    const nextScale = this.fitScale() * this.zoom;
    const baseX = (this.app.screen.width - this.layoutWorld.width * nextScale) / 2
      - this.layoutWorld.minX * nextScale;
    const baseY = (this.app.screen.height - this.layoutWorld.height * nextScale) / 2
      - this.layoutWorld.minY * nextScale;
    this.pan = {
      x: point.x - worldPoint.x * nextScale - baseX,
      y: point.y - worldPoint.y * nextScale - baseY,
    };
    this.applyViewport();
    if (this.currentLabelLodKey() !== previousLodKey) this.rebuildScene();
  }

  focusNode(nodeId: string | null): void {
    if (!nodeId || !this.scene || !this.app) {
      this.pan = { x: 0, y: 0 };
      this.applyViewport();
      return;
    }
    const node = this.scene.nodes.find((item) => item.node_id === nodeId);
    if (!node) return;
    const position = this.manualPositions.get(nodeId)
      ?? this.externalPositions.get(nodeId)
      ?? node;
    const scale = this.fitScale() * this.zoom;
    this.pan = {
      x: (this.layoutWorld.centerX - position.x) * scale,
      y: (this.layoutWorld.centerY - position.y) * scale,
    };
    this.applyViewport();
  }

  resetView(): void {
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.manualPositions.clear();
    this.rebuildScene();
    this.applyViewport();
  }

  destroy(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    if (this.wheelFrame !== null) cancelAnimationFrame(this.wheelFrame);
    this.wheelFrame = null;
    this.pendingWheelDelta = 0;
    this.pendingWheelAnchor = null;
    this.clearLayer(this.edgeLayer);
    this.clearLayer(this.labelLayer);
    this.clearLayer(this.nodeLayer);
    this.world.removeFromParent();
    if (this.app) {
      this.app.ticker.remove(this.handleFrame);
      this.app.canvas.removeEventListener("wheel", this.onWheel);
      this.app.destroy({ removeView: true });
    }
    this.app = null;
    this.callbacks = null;
    this.scene = null;
    this.drag = null;
    this.edgeGraphics = null;
    this.nodeContainers.clear();
    this.edgeLabels.clear();
    this.sceneNodesById.clear();
    this.nodeLabelIds.clear();
    this.edgeLabelIds.clear();
    this.externalPositions.clear();
    this.world.removeChildren();
    this.fpsFrames = 0;
    this.edgesDirty = false;
    this.labelLodKey = "";
  }

  private readonly handleStagePointerDown = (event: FederatedPointerEvent) => {
    if (!this.app) return;
    this.drag = {
      kind: "pan",
      origin: { x: event.global.x, y: event.global.y },
      pan: { ...this.pan },
    };
    this.app.stage.cursor = "grabbing";
  };

  private readonly handlePointerMove = (event: FederatedPointerEvent) => {
    if (!this.drag) return;
    if (this.drag.kind === "pan") {
      this.pan = {
        x: this.drag.pan.x + event.global.x - this.drag.origin.x,
        y: this.drag.pan.y + event.global.y - this.drag.origin.y,
      };
      this.applyViewport();
      return;
    }
    const local = this.world.toLocal(event.global);
    this.manualPositions.set(this.drag.nodeId, { x: local.x, y: local.y });
    const container = this.nodeContainers.get(this.drag.nodeId);
    container?.position.set(local.x, local.y);
    this.callbacks?.onNodeDrag?.(this.drag.nodeId, local.x, local.y);
    this.edgesDirty = true;
  };

  private readonly handlePointerUp = () => {
    if (this.drag?.kind === "node") {
      const nodeId = this.drag.nodeId;
      const position = this.manualPositions.get(nodeId);
      if (position) this.externalPositions.set(nodeId, position);
      this.manualPositions.delete(nodeId);
      this.callbacks?.onNodeDragEnd?.(nodeId);
    }
    this.drag = null;
    if (this.app) this.app.stage.cursor = "grab";
  };

  private readonly handleStagePointerTap = (event: FederatedPointerEvent) => {
    if (!this.scene || !this.callbacks?.onSelectEdge || !this.app) return;
    const point = this.world.toLocal(event.global);
    const positions = this.positions();
    const worldScale = Math.max(0.0001, this.world.scale.x);
    const nodeSafety = NODE_POINTER_SAFETY_PX / worldScale;
    const insideNodeProtection = this.scene.nodes.some((node) => {
      const position = positions.get(node.node_id);
      if (!position) return false;
      const radius = nodeRadius(node.ref_count) * this.scene!.appearance.nodeScale;
      return isPointInsideCircle(point, position, radius + nodeSafety);
    });
    if (insideNodeProtection) return;

    const threshold = EDGE_POINTER_THRESHOLD_PX / worldScale;
    let match: GraphEdge | null = null;
    let matchDistance = threshold;
    for (const edge of this.scene.edges) {
      const source = positions.get(edge.source_node_id);
      const target = positions.get(edge.target_node_id);
      if (!source || !target) continue;
      const distance = distanceToSegment(point, source, target);
      if (distance <= matchDistance) {
        match = edge;
        matchDistance = distance;
      }
    }
    if (match) this.callbacks.onSelectEdge(match.edge_id);
  };

  private rebuildScene(): void {
    if (!this.scene) return;
    this.clearLayer(this.labelLayer);
    this.clearLayer(this.nodeLayer);
    this.nodeContainers.clear();
    this.edgeLabels.clear();
    this.sceneNodesById.clear();
    for (const node of this.scene.nodes) this.sceneNodesById.set(node.node_id, node);
    this.maxRefCount = Math.max(1, ...this.scene.nodes.map((node) => node.ref_count));
    this.prepareLabelCandidates();
    this.labelLodKey = this.currentLabelLodKey();
    this.drawEdges();
    for (const node of this.scene.nodes) this.drawNode(node);
  }

  private drawEdges(): void {
    if (!this.scene || !this.edgeGraphics) return;
    this.edgeGraphics.clear();
    const positions = this.positions();
    const visibleBounds = this.visibleWorldBounds(120);
    const visibleLabelIds = new Set<string>();
    for (const edge of this.scene.edges) {
      const source = positions.get(edge.source_node_id);
      const target = positions.get(edge.target_node_id);
      if (!source || !target) continue;
      if (!segmentIntersectsBounds(source, target, visibleBounds)) continue;
      const selected = this.scene.selectedEdgeId === edge.edge_id;
      const related = this.scene.contextMode === "local"
        && this.scene.relatedEdgeIds.has(edge.edge_id);
      const edgeColor = colorNumber(
        selected || related
          ? this.scene.appearance.colors.selectedRelation
          : this.scene.contextMode === "local"
            ? this.scene.appearance.colors.unrelatedRelation
            : this.scene.appearance.colors.globalRelation,
      );
      const edgeAlpha = selected || related || this.scene.contextMode === "global"
        ? selected ? 0.96 : 0.42
        : this.scene.appearance.unrelatedOpacity;
      const width = (
        1.1 + Math.max(0, edge.weight) * 2.8
      ) * this.scene.appearance.edgeScale + (selected ? 1.8 : 0);
      this.edgeGraphics
        .moveTo(source.x, source.y)
        .lineTo(target.x, target.y)
        .stroke({ color: edgeColor, alpha: edgeAlpha, width });
      if (this.scene.appearance.showArrows) {
        this.drawArrow(
          this.edgeGraphics,
          edge,
          source,
          target,
          this.sceneNodesById,
          edgeColor,
          edgeAlpha,
        );
      }
      if (this.edgeLabelIds.has(edge.edge_id)) {
        this.updateEdgeLabel(edge, source, target, edgeColor, edgeAlpha);
        visibleLabelIds.add(edge.edge_id);
      }
    }
    for (const [edgeId, label] of this.edgeLabels) {
      label.visible = visibleLabelIds.has(edgeId);
    }
    this.edgesDirty = false;
  }

  private drawArrow(
    graphics: Graphics,
    edge: GraphEdge,
    source: ScreenPoint,
    target: ScreenPoint,
    nodesById: Map<string, PositionedNode>,
    edgeColor: number,
    edgeAlpha: number,
  ): void {
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.hypot(dx, dy);
    if (distance < 1) return;
    const targetNode = nodesById.get(edge.target_node_id);
    const radius = targetNode
      ? nodeRadius(targetNode.ref_count) * (this.scene?.appearance.nodeScale ?? 1)
      : 28;
    const ux = dx / distance;
    const uy = dy / distance;
    const tipX = target.x - ux * radius;
    const tipY = target.y - uy * radius;
    const baseX = tipX - ux * 10;
    const baseY = tipY - uy * 10;
    const sideX = -uy * 4.2;
    const sideY = ux * 4.2;
    graphics
      .poly([
        tipX,
        tipY,
        baseX + sideX,
        baseY + sideY,
        baseX - sideX,
        baseY - sideY,
      ])
      .fill({ color: edgeColor, alpha: Math.min(1, edgeAlpha + 0.2) });
  }

  private updateEdgeLabel(
    edge: GraphEdge,
    source: ScreenPoint,
    target: ScreenPoint,
    edgeColor: number,
    edgeAlpha: number,
  ): void {
    let label = this.edgeLabels.get(edge.edge_id);
    if (!label) {
      label = new Text({
        text: edge.relation,
        style: {
          fill: edgeColor,
          fontFamily: "Inter, sans-serif",
          fontSize: 10,
          fontWeight: "600",
        },
      });
      label.anchor.set(0.5);
      this.edgeLabels.set(edge.edge_id, label);
      this.labelLayer.addChild(label);
    }
    label.visible = true;
    label.style.fill = edgeColor;
    label.alpha = (this.scene?.appearance.labelOpacity ?? 1) * Math.max(0.25, edgeAlpha);
    label.position.set((source.x + target.x) / 2, (source.y + target.y) / 2 - 7);
  }

  private prepareLabelCandidates(): void {
    if (!this.scene) return;
    const nodeBudget = this.nodeLabelBudget();
    const rankedNodes = [...this.scene.nodes].sort((left, right) => {
      const score = (node: PositionedNode) => {
        if (node.node_id === this.scene?.selectedId) return 4_000_000;
        if (this.scene?.highlightedIds.has(node.node_id)) return 3_000_000;
        const kindBonus = this.scene?.nodeKinds.get(node.node_id) === "core" ? 1_000_000 : 0;
        return kindBonus + node.ref_count / this.maxRefCount * 100_000;
      };
      return score(right) - score(left) || left.node_id.localeCompare(right.node_id);
    });
    this.nodeLabelIds = new Set(
      rankedNodes.slice(0, nodeBudget).map((node) => node.node_id),
    );

    const mode = this.scene.appearance.relationLabels;
    const focusedEdgeId = this.scene.selectedEdgeId;
    if (focusedEdgeId) {
      this.edgeLabelIds = new Set([focusedEdgeId]);
      return;
    }
    if (mode === "never") {
      this.edgeLabelIds = new Set();
      return;
    }
    const selectedId = this.scene.selectedId;
    const selectedEdges = this.scene.edges.filter((edge) => Boolean(selectedId) && (
      edge.source_node_id === selectedId || edge.target_node_id === selectedId
    ));
    if (mode === "selected" || (mode === "auto" && this.zoom < 1.55)) {
      this.edgeLabelIds = new Set(
        selectedEdges.slice(0, 96).map((edge) => edge.edge_id),
      );
      return;
    }
    const budget = this.edgeLabelBudget(mode === "always");
    const selectedEdgeIds = new Set(selectedEdges.map((edge) => edge.edge_id));
    const rankedEdges = [...this.scene.edges].sort((left, right) => {
      const selectedDelta = Number(selectedEdgeIds.has(right.edge_id))
        - Number(selectedEdgeIds.has(left.edge_id));
      return selectedDelta || right.weight - left.weight || left.edge_id.localeCompare(right.edge_id);
    });
    this.edgeLabelIds = new Set(
      rankedEdges.slice(0, budget).map((edge) => edge.edge_id),
    );
  }

  private nodeLabelBudget(): number {
    if (!this.scene) return 0;
    const base = this.scene.performance.labelDensity === "low"
      ? 180
      : this.scene.performance.labelDensity === "high"
        ? 800
        : 480;
    const zoomFactor = this.zoom >= 2.2 ? 1.75 : this.zoom >= 1.35 ? 1.35 : 1;
    return Math.min(this.scene.nodes.length, Math.round(base * zoomFactor));
  }

  private edgeLabelBudget(always: boolean): number {
    if (!this.scene) return 0;
    const base = this.scene.performance.labelDensity === "low"
      ? 48
      : this.scene.performance.labelDensity === "high"
        ? 220
        : 110;
    return Math.min(
      this.scene.edges.length,
      Math.round(base * (always ? 1.45 : 1) * (this.zoom >= 2.2 ? 1.35 : 1)),
    );
  }

  private currentLabelLodKey(): string {
    if (!this.scene) return "empty";
    const zoomBand = this.zoom >= 2.2 ? 3 : this.zoom >= 1.55 ? 2 : this.zoom >= 1.35 ? 1 : 0;
    return [
      zoomBand,
      this.scene.performance.labelDensity,
      this.scene.appearance.relationLabels,
    ].join(":");
  }

  private drawNode(node: PositionedNode): void {
    if (!this.scene) return;
    const kind = this.scene.nodeKinds.get(node.node_id) ?? "concept";
    const fallbackPalette = COLORS[kind];
    const visualStyle = this.scene.nodeStyles.get(node.node_id);
    const palette = {
      fill: visualStyle ? colorNumber(visualStyle.fill) : fallbackPalette.fill,
      stroke: visualStyle ? colorNumber(visualStyle.stroke) : fallbackPalette.stroke,
      text: visualStyle ? colorNumber(visualStyle.text) : COLORS.text,
    };
    const radius = nodeRadius(node.ref_count) * this.scene.appearance.nodeScale;
    const selected = this.scene.selectedId === node.node_id;
    const highlighted = this.scene.highlightedIds.has(node.node_id);
    const container = new Container();
    const position = this.manualPositions.get(node.node_id)
      ?? this.externalPositions.get(node.node_id)
      ?? node;
    container.position.set(position.x, position.y);
    container.eventMode = "static";
    container.cursor = "pointer";
    container.hitArea = new Circle(0, 0, radius + this.nodeHitPaddingWorld());
    container.alpha = visualStyle?.alpha ?? 1;

    const body = new Graphics();
    if (selected || highlighted) {
      body
        .circle(0, 0, radius + 8)
        .stroke({
          color: palette.stroke,
          alpha: highlighted ? 0.88 : 0.5,
          width: highlighted ? 3 : 1.6,
        });
    }
    body
      .circle(0, 0, radius)
      .fill({ color: palette.fill, alpha: 1 })
      .stroke({ color: palette.stroke, width: selected ? 2.8 : 1.6 });

    container.addChild(body);
    if (this.shouldShowNodeLabel(node, selected, highlighted)) {
      const label = new Text({
        text: graphLabel(node.keyword),
        style: {
          fill: palette.text,
          fontFamily: "Inter, sans-serif",
          fontSize: 12,
          fontWeight: "700",
          align: "center",
        },
      });
      label.anchor.set(0.5);
      label.alpha = this.scene.appearance.labelOpacity;
      container.addChild(label);
      if (selected || this.scene.performance.labelDensity === "high") {
        const count = new Text({
          text: `ref · ${node.ref_count}`,
          style: {
            fill: this.scene.appearance.canvasPreset === "obsidian" ? 0xaab4b0 : COLORS.muted,
            fontFamily: "Inter, sans-serif",
            fontSize: 9,
            fontWeight: "500",
          },
        });
        count.anchor.set(0.5, 0);
        count.alpha = this.scene.appearance.labelOpacity;
        count.position.set(0, radius + 9);
        container.addChild(count);
      }
    }
    container.on("pointerdown", (event: FederatedPointerEvent) => {
      event.stopPropagation();
      this.drag = { kind: "node", nodeId: node.node_id };
      const activePosition = this.manualPositions.get(node.node_id)
        ?? this.externalPositions.get(node.node_id)
        ?? node;
      this.callbacks?.onNodeDragStart?.(
        node.node_id,
        activePosition.x,
        activePosition.y,
      );
      this.callbacks?.onSelect(node.node_id);
    });
    container.on("pointerup", this.handlePointerUp);
    container.on("pointerupoutside", this.handlePointerUp);
    container.on("pointertap", (event: FederatedPointerEvent) => event.stopPropagation());
    this.nodeContainers.set(node.node_id, container);
    this.nodeLayer.addChild(container);
  }

  private positions(): Map<string, ScreenPoint> {
    return new Map(
      (this.scene?.nodes ?? []).map((node) => [
        node.node_id,
        this.manualPositions.get(node.node_id)
          ?? this.externalPositions.get(node.node_id)
          ?? { x: node.x, y: node.y },
      ]),
    );
  }

  private fitScale(): number {
    if (!this.app) return 1;
    return Math.min(
      this.app.screen.width / this.layoutWorld.width,
      this.app.screen.height / this.layoutWorld.height,
    );
  }

  private applyViewport(): void {
    if (!this.app) return;
    const scale = this.fitScale() * this.zoom;
    this.world.scale.set(scale);
    this.world.position.set(
      (this.app.screen.width - this.layoutWorld.width * scale) / 2
        - this.layoutWorld.minX * scale
        + this.pan.x,
      (this.app.screen.height - this.layoutWorld.height * scale) / 2
        - this.layoutWorld.minY * scale
        + this.pan.y,
    );
    this.updateNodeHitAreas();
    this.edgesDirty = true;
  }

  private nodeHitPaddingWorld(): number {
    return NODE_POINTER_SAFETY_PX / Math.max(0.0001, this.world.scale.x);
  }

  private updateNodeHitAreas(): void {
    if (!this.scene) return;
    const padding = this.nodeHitPaddingWorld();
    for (const node of this.scene.nodes) {
      const container = this.nodeContainers.get(node.node_id);
      if (!container) continue;
      const radius = nodeRadius(node.ref_count) * this.scene.appearance.nodeScale;
      container.hitArea = new Circle(0, 0, radius + padding);
    }
  }

  private updateLayoutWorld(scene: GraphRenderScene): void {
    const radii = new Float32Array(
      scene.nodes.map((node) => graphNodeCollisionRadius(
        node.ref_count,
        scene.appearance.nodeScale,
      )),
    );
    this.layoutWorld = calculateLayoutWorld(radii);
  }

  private shouldShowNodeLabel(
    node: PositionedNode,
    selected: boolean,
    highlighted: boolean,
  ): boolean {
    return selected || highlighted || this.nodeLabelIds.has(node.node_id);
  }

  private readonly handleFrame = () => {
    if (this.edgesDirty) this.drawEdges();
    this.fpsFrames += 1;
    const now = performance.now();
    const elapsed = now - this.fpsWindowStartedAt;
    if (elapsed < 650) return;
    this.callbacks?.onFpsChange?.(this.fpsFrames * 1000 / Math.max(1, elapsed));
    this.fpsFrames = 0;
    this.fpsWindowStartedAt = now;
  };

  private updateStageHitArea(): void {
    if (!this.app) return;
    this.app.stage.hitArea = new Rectangle(
      0,
      0,
      this.app.screen.width,
      this.app.screen.height,
    );
  }

  private visibleWorldBounds(padding: number): WorldBounds {
    if (!this.app) {
      return {
        minX: this.layoutWorld.minX,
        minY: this.layoutWorld.minY,
        maxX: this.layoutWorld.maxX,
        maxY: this.layoutWorld.maxY,
      };
    }
    const scale = Math.max(0.0001, this.world.scale.x);
    return {
      minX: -this.world.position.x / scale - padding,
      minY: -this.world.position.y / scale - padding,
      maxX: (this.app.screen.width - this.world.position.x) / scale + padding,
      maxY: (this.app.screen.height - this.world.position.y) / scale + padding,
    };
  }

  private clearLayer(layer: Container): void {
    for (const child of layer.removeChildren()) child.destroy({ children: true });
  }
}

function colorNumber(value: string): number {
  const parsed = Number.parseInt(value.replace(/^#/, ""), 16);
  return Number.isFinite(parsed) ? parsed : 0x7258c7;
}

function segmentIntersectsBounds(
  source: ScreenPoint,
  target: ScreenPoint,
  bounds: WorldBounds,
): boolean {
  return !(
    Math.max(source.x, target.x) < bounds.minX
    || Math.min(source.x, target.x) > bounds.maxX
    || Math.max(source.y, target.y) < bounds.minY
    || Math.min(source.y, target.y) > bounds.maxY
  );
}
