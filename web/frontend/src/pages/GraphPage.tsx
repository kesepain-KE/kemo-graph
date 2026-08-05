import {
  Focus,
  Globe2,
  Hand,
  Maximize2,
  Minus,
  Minimize2,
  Network,
  PanelRightOpen,
  Plus,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent,
} from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import { GraphControlPanel } from "../features/graph/GraphControlPanel";
import { GraphDetails } from "../features/graph/GraphDetails";
import { GraphRelationDetails } from "../features/graph/GraphRelationDetails";
import {
  GraphViewport,
  type GraphViewportHandle,
} from "../features/graph/GraphViewport";
import {
  isGraphRevisionChanged,
  loadGlobalEdges,
  loadGraphSourceLabels,
  loadGraphCatalog,
  type GraphCatalog,
} from "../features/graph/data/GraphDataLoader";
import { makeLayoutCacheKey } from "../features/graph/data/GraphPositionCache";
import {
  loadGraphPreferences,
  resolveSemanticNodeVisualStyle,
  saveGraphPreferences,
} from "../features/graph/graphPreferences";
import type {
  GraphRenderScene,
} from "../features/graph/engine/GraphRenderer";
import type { LayoutRuntimeStatus } from "../features/graph/engine/layoutTypes";
import { trimSegmentForNodeProtection } from "../features/graph/engine/hitTesting";
import {
  calculateLayoutWorld,
  graphNodeCollisionRadius,
} from "../features/graph/engine/layoutTypes";
import { buildReadableRelationPaths } from "../features/graph/relationPaths";
import {
  computeInitialLayout,
  computeForceLayout,
  type PositionedNode,
} from "../lib/forceLayout";
import type {
  GraphEdge,
  GraphNeighborhoodData,
  GraphNode,
  GraphVisualizationNode,
} from "../types/api";

type Point = { x: number; y: number };
type Interaction =
  | { kind: "pan"; client: Point; pan: Point }
  | { kind: "node"; nodeId: string };

const SVG_NODE_POINTER_SAFETY = 12;

function nodeRadius(node: GraphNode): number {
  return Math.min(48, 27 + Math.sqrt(Math.max(1, node.ref_count)) * 5.5);
}

function graphLabel(value: string, max = 12): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function isCoreNode(node: GraphNode, maxRefCount: number): boolean {
  const explicitlyCore = [node.keyword, ...node.aliases, ...node.tags].some(
    (value) => /(^|[\s_-])core([\s_-]|$)|核心/i.test(value),
  );
  return explicitlyCore || node.ref_count >= Math.max(3, Math.ceil(maxRefCount * 0.65));
}

function GraphCanvas({
  nodes,
  edges,
  scene,
  onSelect,
  onSelectEdge,
}: {
  nodes: PositionedNode[];
  edges: GraphEdge[];
  scene: GraphRenderScene;
  onSelect: (nodeId: string) => void;
  onSelectEdge: (edgeId: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const interactionRef = useRef<Interaction | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [manualPositions, setManualPositions] = useState<Map<string, Point>>(new Map());
  const layoutWorld = useMemo(() => calculateLayoutWorld(new Float32Array(
    nodes.map((node) => graphNodeCollisionRadius(
      node.ref_count,
      scene.appearance.nodeScale,
    )),
  )), [nodes, scene.appearance.nodeScale]);

  useEffect(() => {
    setManualPositions(new Map());
  }, [nodes]);

  const positions = useMemo(
    () =>
      new Map(
        nodes.map((node) => [
          node.node_id,
          manualPositions.get(node.node_id) ?? { x: node.x, y: node.y },
        ]),
      ),
    [manualPositions, nodes],
  );
  const nodeLabels = useMemo(
    () => new Map(nodes.map((node) => [node.node_id, node.keyword])),
    [nodes],
  );
  const nodesById = useMemo(
    () => new Map(nodes.map((node) => [node.node_id, node])),
    [nodes],
  );

  const toCanvasPoint = (clientX: number, clientY: number): Point => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: layoutWorld.minX + ((clientX - rect.left) / rect.width) * layoutWorld.width,
      y: layoutWorld.minY + ((clientY - rect.top) / rect.height) * layoutWorld.height,
    };
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const interaction = interactionRef.current;
    if (!interaction) return;
    if (interaction.kind === "pan") {
      const current = toCanvasPoint(event.clientX, event.clientY);
      const start = toCanvasPoint(interaction.client.x, interaction.client.y);
      setPan({
        x: interaction.pan.x + current.x - start.x,
        y: interaction.pan.y + current.y - start.y,
      });
      return;
    }
    const point = toCanvasPoint(event.clientX, event.clientY);
    setManualPositions((current) => {
      const next = new Map(current);
      next.set(interaction.nodeId, {
        x: (point.x - pan.x) / zoom,
        y: (point.y - pan.y) / zoom,
      });
      return next;
    });
  };

  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setManualPositions(new Map());
  };

  const handleWheel = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const pointer = toCanvasPoint(event.clientX, event.clientY);
    const nextZoom = Math.min(2.2, Math.max(0.55, zoom - event.deltaY * 0.001));
    if (nextZoom === zoom) return;
    const worldPoint = {
      x: (pointer.x - pan.x) / zoom,
      y: (pointer.y - pan.y) / zoom,
    };
    setPan({
      x: pointer.x - worldPoint.x * nextZoom,
      y: pointer.y - worldPoint.y * nextZoom,
    });
    setZoom(nextZoom);
  };

  if (!nodes.length) {
    return (
      <div className="graph-empty">
        <Network size={40} />
        <h3>当前筛选下没有节点</h3>
        <p>调整群组、搜索词或关系深度后重试。</p>
      </div>
    );
  }

  return (
    <div className="graph-canvas-wrap">
      <svg
        ref={svgRef}
        className="graph-svg"
        viewBox={`${layoutWorld.minX} ${layoutWorld.minY} ${layoutWorld.width} ${layoutWorld.height}`}
        role="img"
        aria-label="可缩放拖拽的知识图谱"
        onPointerDown={(event) => {
          interactionRef.current = {
            kind: "pan",
            client: { x: event.clientX, y: event.clientY },
            pan,
          };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={onPointerMove}
        onPointerUp={(event) => {
          interactionRef.current = null;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          interactionRef.current = null;
        }}
        onWheel={handleWheel}
      >
        <defs>
          <pattern id="grid" width="34" height="34" patternUnits="userSpaceOnUse">
            <path d="M 34 0 L 0 0 0 34" className="graph-grid-line" />
          </pattern>
          <marker
            id="arrow"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 Z" className="graph-arrow" />
          </marker>
        </defs>
        <rect
          x={layoutWorld.minX}
          y={layoutWorld.minY}
          width={layoutWorld.width}
          height={layoutWorld.height}
          fill="url(#grid)"
        />
        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {edges.map((edge) => {
            const source = positions.get(edge.source_node_id);
            const target = positions.get(edge.target_node_id);
            if (!source || !target) return null;
            const midpoint = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
            const selected = scene.selectedEdgeId === edge.edge_id;
            const related = scene.contextMode === "local" && scene.relatedEdgeIds.has(edge.edge_id);
            const sourceNode = nodesById.get(edge.source_node_id);
            const targetNode = nodesById.get(edge.target_node_id);
            const hitSegment = sourceNode && targetNode
              ? trimSegmentForNodeProtection(
                source,
                target,
                nodeRadius(sourceNode) * scene.appearance.nodeScale + SVG_NODE_POINTER_SAFETY / zoom,
                nodeRadius(targetNode) * scene.appearance.nodeScale + SVG_NODE_POINTER_SAFETY / zoom,
              )
              : null;
            const stroke = selected || related
              ? scene.appearance.colors.selectedRelation
              : scene.contextMode === "local"
                ? scene.appearance.colors.unrelatedRelation
                : scene.appearance.colors.globalRelation;
            const alpha = selected || related || scene.contextMode === "global"
              ? selected ? 0.96 : 0.54
              : scene.appearance.unrelatedOpacity;
            return (
              <g className={`graph-edge-group ${selected ? "is-selected" : ""}`} key={edge.edge_id}>
                <line
                  className="graph-edge"
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  style={{
                    stroke,
                    opacity: alpha,
                    strokeWidth: (
                      1.1 + Math.max(0, edge.weight) * 2.8
                    ) * scene.appearance.edgeScale + (selected ? 1.8 : 0),
                  }}
                  markerEnd={scene.appearance.showArrows ? "url(#arrow)" : undefined}
                />
                {shouldShowSvgEdgeLabel(edge, scene, zoom) ? (
                  <text
                    className="graph-edge-label"
                    x={midpoint.x}
                    y={midpoint.y - 7}
                    textAnchor="middle"
                    style={{
                      fill: stroke,
                      opacity: scene.appearance.labelOpacity * Math.max(alpha, 0.3),
                    }}
                  >
                    {edge.relation}
                  </text>
                ) : null}
                {hitSegment ? <line
                  className="graph-edge-hit-target"
                  x1={hitSegment.source.x}
                  y1={hitSegment.source.y}
                  x2={hitSegment.target.x}
                  y2={hitSegment.target.y}
                  role="button"
                  tabIndex={0}
                  aria-label={`${nodeLabels.get(edge.source_node_id) ?? edge.source_node_id} 到 ${nodeLabels.get(edge.target_node_id) ?? edge.target_node_id} 的关系：${edge.relation}`}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onSelectEdge(edge.edge_id);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelectEdge(edge.edge_id);
                  }}
                /> : null}
              </g>
            );
          })}

          {nodes.map((node) => {
            const position = positions.get(node.node_id) ?? node;
            const kind = scene.nodeKinds.get(node.node_id) ?? "concept";
            const radius = nodeRadius(node) * scene.appearance.nodeScale;
            const selected = scene.selectedId === node.node_id;
            const highlighted = scene.highlightedIds.has(node.node_id);
            const visualStyle = scene.nodeStyles.get(node.node_id);
            return (
              <g
                key={node.node_id}
                className={`graph-node graph-node--${kind} ${selected ? "is-selected" : ""} ${highlighted ? "is-highlighted" : ""}`}
                transform={`translate(${position.x} ${position.y})`}
                role="button"
                tabIndex={0}
                aria-label={`${node.keyword}，引用 ${node.ref_count} 次`}
                style={{ opacity: visualStyle?.alpha ?? 1 }}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  interactionRef.current = { kind: "node", nodeId: node.node_id };
                  svgRef.current?.setPointerCapture(event.pointerId);
                  onSelect(node.node_id);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onSelect(node.node_id);
                }}
              >
                <circle className="graph-node__hit-target" r={radius + SVG_NODE_POINTER_SAFETY / zoom} />
                <circle
                  className="graph-node__halo"
                  r={radius + 8}
                  style={{ stroke: visualStyle?.stroke }}
                />
                <circle
                  className="graph-node__body"
                  r={radius}
                  style={{ fill: visualStyle?.fill, stroke: visualStyle?.stroke }}
                />
                {shouldShowSvgNodeLabel(node, kind, selected, highlighted, scene, zoom) ? (
                    <text
                      textAnchor="middle"
                      y="4"
                      style={{ fill: visualStyle?.text, opacity: scene.appearance.labelOpacity }}
                    >
                      {graphLabel(node.keyword)}
                    </text>
                  ) : null}
              </g>
            );
          })}
        </g>
      </svg>

      <div className="graph-legend">
        <span><i className="legend-dot legend-dot--core" />核心概念</span>
        <span><i className="legend-dot legend-dot--concept" />一般概念</span>
        <span><i className="legend-dot legend-dot--group" />真实群组配色</span>
      </div>

      <div className="canvas-tools">
        <button className="icon-button" title="拖拽画布"><Hand size={17} /></button>
        <button className="icon-button" title="聚焦所选节点" onClick={() => setPan({ x: 0, y: 0 })}>
          <Focus size={17} />
        </button>
        <div className="zoom-control">
          <button onClick={() => setZoom((value) => Math.max(0.55, value - 0.12))} aria-label="缩小">
            <Minus size={15} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom((value) => Math.min(2.2, value + 0.12))} aria-label="放大">
            <Plus size={15} />
          </button>
        </div>
      </div>
      <button className="reset-view" onClick={resetView}>
        <RotateCcw size={15} /> 重置视图
      </button>
    </div>
  );
}

function SvgGraphFallback({
  scene,
  onSelect,
  onSelectEdge,
}: {
  scene: GraphRenderScene;
  onSelect: (nodeId: string) => void;
  onSelectEdge: (edgeId: string) => void;
}) {
  const positioned = useMemo(
    () => computeForceLayout(
      scene.nodes,
      scene.edges,
      180,
      scene.appearance.nodeScale,
    ),
    [scene.appearance.nodeScale, scene.edges, scene.nodes],
  );
  return (
    <GraphCanvas
      nodes={positioned}
      edges={scene.edges}
      scene={{ ...scene, nodes: positioned }}
      onSelect={onSelect}
      onSelectEdge={onSelectEdge}
    />
  );
}

function shouldShowSvgEdgeLabel(
  edge: GraphEdge,
  scene: GraphRenderScene,
  zoom: number,
): boolean {
  if (scene.selectedEdgeId === edge.edge_id) return true;
  if (scene.appearance.relationLabels === "never") return false;
  if (scene.appearance.relationLabels === "always") return scene.edges.length <= 2000;
  const touchesSelected = Boolean(scene.selectedId) && (
    edge.source_node_id === scene.selectedId || edge.target_node_id === scene.selectedId
  );
  if (scene.appearance.relationLabels === "selected") return touchesSelected;
  return touchesSelected || (zoom >= 1.55 && scene.edges.length <= 500);
}

function shouldShowSvgNodeLabel(
  node: PositionedNode,
  kind: "core" | "concept" | "group",
  selected: boolean,
  highlighted: boolean,
  scene: GraphRenderScene,
  zoom: number,
): boolean {
  if (selected || highlighted) return true;
  if (scene.performance.labelDensity === "high") return scene.nodes.length <= 3000 || zoom >= 1.35;
  if (scene.performance.labelDensity === "low") return kind === "core" && (scene.nodes.length <= 800 || zoom >= 1.7);
  if (scene.nodes.length <= 500) return true;
  const maxRef = Math.max(1, ...scene.nodes.map((item) => item.ref_count));
  return kind === "core" || node.ref_count >= Math.max(3, maxRef * 0.55) || zoom >= 1.6;
}

export function GraphPage() {
  const viewportRef = useRef<GraphViewportHandle>(null);
  const fullscreenRef = useRef<HTMLDivElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const { refreshServerTasks } = useRuntimeTasks();
  const [catalog, setCatalog] = useState<GraphCatalog | null>(null);
  const [localGraph, setLocalGraph] = useState<GraphNeighborhoodData | null>(null);
  const [globalEdges, setGlobalEdges] = useState<GraphEdge[] | null>(null);
  const [sourceLabels, setSourceLabels] = useState<Map<string, string>>(new Map());
  const [preferences, setPreferences] = useState(loadGraphPreferences);
  const [loading, setLoading] = useState(true);
  const [graphLoading, setGraphLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rendererBackend, setRendererBackend] = useState("正在初始化");
  const [layoutStatus, setLayoutStatus] = useState<LayoutRuntimeStatus | null>(null);
  const [fps, setFps] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fullscreenPanelOpen, setFullscreenPanelOpen] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("node"));
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [summarySubmitting, setSummarySubmitting] = useState(false);

  const viewMode = preferences.view.mode;
  const depth = preferences.view.depth;

  useEffect(() => saveGraphPreferences(preferences), [preferences]);

  useEffect(() => {
    const syncFullscreenState = () => {
      const active = document.fullscreenElement === fullscreenRef.current;
      setIsFullscreen(active);
      if (active) {
        setFullscreenPanelOpen(true);
      }
      requestAnimationFrame(() => viewportRef.current?.reheat());
    };
    document.addEventListener("fullscreenchange", syncFullscreenState);
    return () => document.removeEventListener("fullscreenchange", syncFullscreenState);
  }, []);

  const loadGraph = async () => {
    setLoading(true);
    setError(null);
    try {
      const [graph, labels] = await Promise.all([
        loadGraphCatalog(),
        loadGraphSourceLabels().catch(() => new Map<string, string>()),
      ]);
      const edges = await loadGlobalEdges(graph.meta);
      setCatalog(graph);
      setSourceLabels(labels);
      setLocalGraph(null);
      setGlobalEdges(edges);
      setSelectedEdgeId(null);
      const requestedNode = searchParams.get("node");
      if (requestedNode && graph.nodes.some((node) => node.node_id === requestedNode)) {
        setSelectedId(requestedNode);
      } else if (graph.nodes.length) {
        setSelectedId((current) => (
          current && graph.nodes.some((node) => node.node_id === current)
            ? current
            : [...graph.nodes].sort((left, right) => right.ref_count - left.ref_count)[0].node_id
        ));
      } else {
        setSelectedId(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法加载图谱");
    } finally {
      setLoading(false);
    }
  };

  const summarizeGroups = async () => {
    setSummarySubmitting(true);
    setError(null);
    try {
      await api.startSummarizeJob();
      await refreshServerTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法启动节点群总结");
    } finally {
      setSummarySubmitting(false);
    }
  };

  useEffect(() => {
    void loadGraph();
    // The initial URL node is intentionally read only once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!catalog || !selectedId) {
      setGraphLoading(false);
      return undefined;
    }
    let cancelled = false;
    const loadActiveGraph = async () => {
      setGraphLoading(true);
      try {
        if (viewMode === "local") {
          setLocalGraph(null);
          const neighborhood = await api.graphNeighborhood(selectedId, {
            depth,
            direction: "both",
            limit: 5000,
            edgeLimit: 20000,
            expectedRevision: catalog.revision,
          });
          if (!cancelled) setLocalGraph(neighborhood);
        } else {
          setGraphLoading(false);
        }
      } catch (caught) {
        if (!cancelled) {
          if (isGraphRevisionChanged(caught)) {
            setError("图谱刚刚发生更新，正在重新载入一致快照…");
            void loadGraph();
          } else {
            setError(caught instanceof Error ? caught.message : "无法加载图谱数据");
          }
        }
      } finally {
        if (!cancelled) setGraphLoading(false);
      }
    };
    void loadActiveGraph();
    return () => {
      cancelled = true;
    };
  }, [catalog, depth, selectedId, viewMode]);

  const data = useMemo(() => {
    if (!catalog) return null;
    return {
      nodes: catalog.nodes,
      edges: globalEdges ?? [],
    };
  }, [catalog, globalEdges]);

  const activeLocalGraph = viewMode === "local"
    && localGraph?.anchor_node_id === selectedId
    && localGraph.depth === depth
    ? localGraph
    : null;

  const relatedNodeIds = useMemo(() => {
    if (viewMode !== "local") return new Set<string>();
    const ids = new Set(activeLocalGraph?.nodes.map((node) => node.node_id) ?? []);
    if (selectedId) ids.add(selectedId);
    return ids;
  }, [activeLocalGraph, selectedId, viewMode]);
  const relatedEdgeIds = useMemo(() => new Set(
    viewMode === "local" ? (activeLocalGraph?.edges ?? []).map((edge) => edge.edge_id) : [],
  ), [activeLocalGraph, viewMode]);

  const highlightedNodes = useMemo(() => {
    const keyword = preferences.filters.query.trim().toLocaleLowerCase();
    if (!keyword || !catalog) return [];
    return catalog.nodes.filter((node) => (
      [node.keyword, node.summary, ...node.aliases, ...node.tags].some(
        (value) => value.toLocaleLowerCase().includes(keyword),
      )
    ));
  }, [catalog, preferences.filters.query]);
  const highlightedIds = useMemo(
    () => new Set(highlightedNodes.map((node) => node.node_id)),
    [highlightedNodes],
  );

  const groupFilter = useMemo(() => {
    if (preferences.filters.groupId === "all") return null;
    const group = catalog?.groups.find(
      (item) => item.group_id === preferences.filters.groupId,
    );
    return group?.node_ids?.length ? new Set(group.node_ids) : new Set<string>();
  }, [catalog, preferences.filters.groupId]);

  const visibleNodes = useMemo<GraphVisualizationNode[]>(() => (
    (data?.nodes ?? []).filter((node) => (
      (!groupFilter || groupFilter.has(node.node_id))
      && (preferences.filters.sourceId === "all" || node.source_ids.includes(preferences.filters.sourceId))
      && (preferences.filters.tag === "all" || node.tags.includes(preferences.filters.tag))
      && (node.weight ?? 0) >= preferences.filters.minNodeWeight
      && node.ref_count >= preferences.filters.minRefCount
    ))
  ), [
    data,
    groupFilter,
    preferences.filters.minNodeWeight,
    preferences.filters.minRefCount,
    preferences.filters.sourceId,
    preferences.filters.tag,
  ]);
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.node_id)),
    [visibleNodes],
  );
  const activeRelationTypes = useMemo(
    () => [...new Set((data?.edges ?? []).map((edge) => edge.relation))].sort(),
    [data],
  );
  const visibleEdges = useMemo(() => (
    (data?.edges ?? []).filter((edge) => (
      visibleIds.has(edge.source_node_id)
      && visibleIds.has(edge.target_node_id)
      && edge.weight >= preferences.filters.minEdgeWeight
      && (
        preferences.filters.relation === "all"
        || edge.relation === preferences.filters.relation
      )
    ))
  ), [
    data,
    preferences.filters.minEdgeWeight,
    preferences.filters.relation,
    visibleIds,
  ]);
  const positionedNodes = useMemo(
    () => computeInitialLayout(visibleNodes, preferences.appearance.nodeScale),
    [preferences.appearance.nodeScale, visibleNodes],
  );
  const selected = catalog?.nodes.find((node) => node.node_id === selectedId) ?? null;
  const selectedEdge = visibleEdges.find((edge) => edge.edge_id === selectedEdgeId) ?? null;
  const relatedRelationEdges = useMemo(() => (
    selectedEdge ? [selectedEdge] : []
  ), [selectedEdge]);
  const selectedRelationNodeIds = useMemo(() => new Set(
    relatedRelationEdges.flatMap((edge) => [edge.source_node_id, edge.target_node_id]),
  ), [relatedRelationEdges]);
  const selectedRelationEdgeIds = useMemo(() => new Set(
    relatedRelationEdges.map((edge) => edge.edge_id),
  ), [relatedRelationEdges]);
  const selectedEdges = visibleEdges.filter((edge) => (
    edge.source_node_id === selectedId || edge.target_node_id === selectedId
  ));
  const nodesById = useMemo(
    () => new Map<string, GraphNode>(
      (catalog?.nodes ?? []).map((node) => [node.node_id, node]),
    ),
    [catalog],
  );
  const maxRefCount = Math.max(1, ...(catalog?.nodes ?? []).map((node) => node.ref_count));
  const nodeKinds = useMemo(() => new Map(
    positionedNodes.map((node) => [
      node.node_id,
      isCoreNode(node, maxRefCount) ? "core" as const : "concept" as const,
    ]),
  ), [maxRefCount, positionedNodes]);
  const nodeStyles = useMemo(() => new Map(
    visibleNodes.map((node) => {
      const highlighted = highlightedIds.has(node.node_id);
      const isSelected = viewMode === "local" && node.node_id === selectedId;
      const isRelated = viewMode === "local" && relatedNodeIds.has(node.node_id);
      const isSelectedRelationEndpoint = selectedEdge !== null && (
        node.node_id === selectedEdge.source_node_id || node.node_id === selectedEdge.target_node_id
      );
      const isRelationNode = selectedRelationNodeIds.has(node.node_id);
      const color = selectedEdge
        ? isSelectedRelationEndpoint
          ? preferences.appearance.colors.selectedNode
          : isRelationNode
            ? preferences.appearance.colors.relatedNode
            : preferences.appearance.colors.unrelatedNode
        : viewMode === "global"
        ? preferences.appearance.colors.globalNode
        : isSelected
          ? preferences.appearance.colors.selectedNode
          : isRelated
            ? preferences.appearance.colors.relatedNode
            : preferences.appearance.colors.unrelatedNode;
      const alpha = selectedEdge
        ? isSelectedRelationEndpoint || isRelationNode || highlighted
          ? 1
          : preferences.appearance.unrelatedOpacity
        : viewMode === "global" || isSelected || isRelated || highlighted
        ? 1
        : preferences.appearance.unrelatedOpacity;
      return [
        node.node_id,
        resolveSemanticNodeVisualStyle(
          node,
          color,
          preferences.appearance.canvasPreset,
          alpha,
        ),
      ];
    }),
  ), [
    highlightedIds,
    preferences.appearance,
    relatedNodeIds,
    selectedEdge,
    selectedId,
    selectedRelationNodeIds,
    visibleNodes,
    viewMode,
  ]);
  const contextEdges = useMemo(() => (
    viewMode === "local" && activeLocalGraph
      ? activeLocalGraph.edges.filter((edge) => (
        visibleIds.has(edge.source_node_id) && visibleIds.has(edge.target_node_id)
      ))
      : visibleEdges
  ), [activeLocalGraph, viewMode, visibleEdges, visibleIds]);
  const selectedPaths = useMemo(
    () => buildReadableRelationPaths(selectedId, contextEdges, nodesById, 24),
    [contextEdges, nodesById, selectedId],
  );
  const selectedIsCore = selected ? isCoreNode(selected, maxRefCount) : false;
  const sceneHighlightedIds = useMemo(() => (
    selectedEdge
      ? new Set([...highlightedIds, ...selectedRelationNodeIds])
      : highlightedIds
  ), [highlightedIds, selectedEdge, selectedRelationNodeIds]);

  const graphScene = useMemo<GraphRenderScene>(() => ({
    nodes: positionedNodes,
    edges: visibleEdges,
    selectedId: selectedEdge ? null : selectedId,
    selectedEdgeId,
    highlightedIds: sceneHighlightedIds,
    relatedNodeIds: selectedEdge ? selectedRelationNodeIds : relatedNodeIds,
    relatedEdgeIds: selectedEdge ? selectedRelationEdgeIds : relatedEdgeIds,
    contextMode: selectedEdge ? "local" : viewMode,
    nodeKinds,
    nodeStyles,
    appearance: preferences.appearance,
    performance: preferences.performance,
  }), [
    sceneHighlightedIds,
    nodeKinds,
    nodeStyles,
    positionedNodes,
    preferences.appearance,
    preferences.performance,
    relatedEdgeIds,
    relatedNodeIds,
    selectedEdge,
    selectedEdgeId,
    selectedId,
    selectedRelationEdgeIds,
    selectedRelationNodeIds,
    visibleEdges,
    viewMode,
  ]);
  const layoutKey = useMemo(() => makeLayoutCacheKey(
    catalog?.revision ?? "empty",
    "global",
    null,
    0,
    preferences.force,
    preferences.appearance.nodeScale,
  ), [
    catalog?.revision,
    preferences.appearance.nodeScale,
    preferences.force,
  ]);

  const selectNode = (nodeId: string) => {
    setSelectedId(nodeId);
    setSelectedEdgeId(null);
    setPreferences((current) => ({
      ...current,
      view: { ...current.view, mode: "local" },
    }));
    setSearchParams({ node: nodeId }, { replace: true });
  };

  const selectEdge = (edgeId: string) => {
    setSelectedEdgeId(edgeId);
    if (isFullscreen) setFullscreenPanelOpen(true);
  };

  const returnToGlobal = () => {
    setSelectedEdgeId(null);
    setPreferences((current) => ({
      ...current,
      view: { ...current.view, mode: "global" },
    }));
  };

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === fullscreenRef.current) {
        await document.exitFullscreen();
        return;
      }
      if (!fullscreenRef.current || !document.fullscreenEnabled) {
        throw new Error("当前浏览器未开放全屏显示能力");
      }
      await fullscreenRef.current.requestFullscreen();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法进入图谱全屏模式");
    }
  };

  return (
    <section className="graph-page page-stack">
      <PageIntro
        title="探索知识之间的连接"
        actions={
          <>
            <button className="button button--secondary" type="button" onClick={loadGraph} disabled={loading}>
              <RefreshCw className={loading ? "spin" : ""} size={16} />刷新图谱
            </button>
            <button
              className="button button--primary"
              type="button"
              onClick={() => void summarizeGroups()}
              disabled={summarySubmitting}
              title="根据当前图谱重新生成节点群摘要"
            >
              <Network className={summarySubmitting ? "spin" : ""} size={16} />
              {summarySubmitting ? "正在提交" : "总结节点群"}
            </button>
          </>
        }
      />
      {error ? <ErrorNotice message={error} /> : null}

      <div
        ref={fullscreenRef}
        className={`graph-layout graph-layout--studio ${preferences.appearance.canvasPreset === "obsidian" ? "is-theme-dark" : "is-theme-light"} ${isFullscreen ? "is-fullscreen-mode" : ""} ${isFullscreen && !fullscreenPanelOpen ? "is-panel-collapsed" : ""}`}
      >
        <section className={`graph-canvas card ${preferences.appearance.canvasPreset === "obsidian" ? "is-obsidian" : ""}`}>
          {loading ? <LoadingState label="正在加载一致图谱快照…" /> : null}
          {!loading ? (
            <GraphViewport
              ref={viewportRef}
              scene={graphScene}
              onSelect={selectNode}
              onSelectEdge={selectEdge}
              onBackendChange={(backend) => {
                setRendererBackend(backend);
                if (backend === "svg") setFps(0);
              }}
              onLayoutStatus={setLayoutStatus}
              onFpsChange={setFps}
              layoutKey={layoutKey}
              forceSettings={preferences.force}
              fallback={<SvgGraphFallback scene={graphScene} onSelect={selectNode} onSelectEdge={selectEdge} />}
            />
          ) : null}
          <div className="graph-fullscreen-toolbar" aria-label="图谱全屏工具">
            <button
              className={`graph-fullscreen-button graph-return-global ${viewMode === "global" ? "is-active" : ""}`}
              type="button"
              onClick={returnToGlobal}
              aria-pressed={viewMode === "global"}
              title="显示全部知识节点和关系"
            >
              <Globe2 size={16} />
              <span>回归全局</span>
            </button>
            {isFullscreen && !fullscreenPanelOpen ? (
              <button
                className="graph-fullscreen-button"
                type="button"
                onClick={() => setFullscreenPanelOpen(true)}
                title="打开图谱操作面板"
              >
                <PanelRightOpen size={16} />
                <span>操作面板</span>
              </button>
            ) : null}
            <button
              className="graph-fullscreen-button"
              type="button"
              onClick={() => void toggleFullscreen()}
              disabled={!document.fullscreenEnabled}
              title={isFullscreen ? "退出全屏" : "全屏查看知识图谱"}
              aria-pressed={isFullscreen}
            >
              {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              <span>{isFullscreen ? "退出全屏" : "全屏图谱"}</span>
            </button>
          </div>

        </section>

        {!isFullscreen ? (
          <aside
            className={`graph-node-detail-card card ${preferences.appearance.canvasPreset === "obsidian" ? "is-theme-dark" : "is-theme-light"}`}
            aria-label="节点或关系详情"
          >
            <div className="graph-node-detail-card__scroll">
              {selectedEdge ? (
                <GraphRelationDetails
                  selected={selectedEdge}
                  related={relatedRelationEdges}
                  nodesById={nodesById}
                  onSelectNode={selectNode}
                />
              ) : (
                <GraphDetails
                  selected={selected}
                  paths={selectedPaths}
                  directRelationCount={selectedEdges.length}
                  isCore={selectedIsCore}
                  sourceLabels={sourceLabels}
                  onSelect={selectNode}
                />
              )}
            </div>
          </aside>
        ) : null}

        {isFullscreen ? (
          <GraphControlPanel
            preferences={preferences}
            onPreferencesChange={setPreferences}
            catalog={catalog}
            activeNodes={visibleNodes}
            activeRelationTypes={activeRelationTypes}
            highlightedNodes={highlightedNodes}
            selected={selected}
            selectedEdge={selectedEdge}
            relatedRelationEdges={relatedRelationEdges}
            nodesById={nodesById}
            selectedPaths={selectedPaths}
            selectedRelationCount={selectedEdges.length}
            selectedIsCore={selectedIsCore}
            sourceLabels={sourceLabels}
            rendererBackend={rendererBackend}
            layoutStatus={layoutStatus}
            fps={fps}
            loading={loading || graphLoading}
            onSelect={selectNode}
            onReheat={() => viewportRef.current?.reheat()}
            fullscreen
            visible={fullscreenPanelOpen}
            onRequestClose={() => setFullscreenPanelOpen(false)}
          />
        ) : null}
      </div>
    </section>
  );
}
