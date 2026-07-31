import {
  ChevronDown,
  FileText,
  Focus,
  Hand,
  Minus,
  Network,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Tag,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import {
  computeForceLayout,
  selectNeighborhood,
  type PositionedNode,
} from "../lib/forceLayout";
import type { FullGraphData, GraphEdge, GraphNode } from "../types/api";

type Point = { x: number; y: number };
type NodeKind = "core" | "concept" | "group";
type Interaction =
  | { kind: "pan"; client: Point; pan: Point }
  | { kind: "node"; nodeId: string };

function nodeRadius(node: GraphNode): number {
  return Math.min(48, 27 + Math.sqrt(Math.max(1, node.ref_count)) * 5.5);
}

function graphLabel(value: string, max = 12): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function isGroupNode(node: GraphNode): boolean {
  return [node.keyword, ...node.aliases, ...node.tags].some((value) =>
    /(^|[\s_-])(group|cluster|community)([\s_-]|$)|群组|社群/i.test(value),
  );
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
  selectedId,
  highlightedIds,
  groupNodeIds,
  onSelect,
}: {
  nodes: PositionedNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  highlightedIds: Set<string>;
  groupNodeIds: Set<string>;
  onSelect: (nodeId: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const interactionRef = useRef<Interaction | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [manualPositions, setManualPositions] = useState<Map<string, Point>>(new Map());

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
  const maxRefCount = Math.max(1, ...nodes.map((node) => node.ref_count));

  const toCanvasPoint = (clientX: number, clientY: number): Point => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: ((clientX - rect.left) / rect.width) * 1000,
      y: ((clientY - rect.top) / rect.height) * 700,
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
    setZoom((value) => Math.min(2.2, Math.max(0.55, value - event.deltaY * 0.001)));
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
        viewBox="0 0 1000 700"
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
        <rect width="1000" height="700" fill="url(#grid)" />
        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {edges.map((edge) => {
            const source = positions.get(edge.source_node_id);
            const target = positions.get(edge.target_node_id);
            if (!source || !target) return null;
            const midpoint = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
            return (
              <g className="graph-edge-group" key={edge.edge_id}>
                <line
                  className="graph-edge"
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  markerEnd="url(#arrow)"
                  style={{ strokeWidth: 1.1 + Math.max(0, edge.weight) * 2.8 }}
                />
                {nodes.length <= 36 ? (
                  <text
                    className="graph-edge-label"
                    x={midpoint.x}
                    y={midpoint.y - 7}
                    textAnchor="middle"
                  >
                    {edge.relation}
                  </text>
                ) : null}
              </g>
            );
          })}

          {nodes.map((node) => {
            const position = positions.get(node.node_id) ?? node;
            const kind: NodeKind = groupNodeIds.has(node.node_id)
              ? "group"
              : isCoreNode(node, maxRefCount)
                ? "core"
                : "concept";
            const radius = nodeRadius(node);
            const selected = selectedId === node.node_id;
            const highlighted = highlightedIds.has(node.node_id);
            return (
              <g
                key={node.node_id}
                className={`graph-node graph-node--${kind} ${selected ? "is-selected" : ""} ${highlighted ? "is-highlighted" : ""}`}
                transform={`translate(${position.x} ${position.y})`}
                role="button"
                tabIndex={0}
                aria-label={`${node.keyword}，引用 ${node.ref_count} 次`}
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
                <circle className="graph-node__halo" r={radius + 8} />
                <circle className="graph-node__body" r={radius} />
                <text textAnchor="middle" y="4">
                  {graphLabel(node.keyword)}
                </text>
                <text className="graph-node__count" textAnchor="middle" y={radius + 20}>
                  ref · {node.ref_count}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <div className="graph-legend">
        <span><i className="legend-dot legend-dot--core" />核心概念</span>
        <span><i className="legend-dot legend-dot--concept" />一般概念</span>
        <span><i className="legend-dot legend-dot--group" />群组节点</span>
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

export function GraphPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<FullGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState(2);
  const [groupId, setGroupId] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(
    searchParams.get("node"),
  );

  const loadGraph = async () => {
    setLoading(true);
    setError(null);
    try {
      const graph = await api.fullGraph();
      setData(graph);
      const requestedNode = searchParams.get("node");
      if (requestedNode && graph.nodes.some((node) => node.node_id === requestedNode)) {
        setSelectedId(requestedNode);
      } else if (!selectedId && graph.nodes.length) {
        setSelectedId(
          [...graph.nodes].sort((a, b) => b.ref_count - a.ref_count)[0].node_id,
        );
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法加载图谱");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const highlightedIds = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    if (!keyword || !data) return new Set<string>();
    return new Set(
      data.nodes
        .filter((node) =>
          [node.keyword, ...node.aliases, ...node.tags].some((value) =>
            value.toLocaleLowerCase().includes(keyword),
          ),
        )
        .map((node) => node.node_id),
    );
  }, [data, query]);

  const focusedId = highlightedIds.values().next().value ?? selectedId;
  const group = data?.groups.find((item) => item.group_id === groupId) ?? null;
  const groupFilter = group?.node_ids?.length ? new Set(group.node_ids) : null;
  const neighborhood = useMemo(
    () =>
      data
        ? selectNeighborhood(data.nodes, data.edges, focusedId ?? null, depth)
        : new Set<string>(),
    [data, depth, focusedId],
  );
  const visibleNodes = useMemo(
    () =>
      (data?.nodes ?? []).filter(
        (node) =>
          neighborhood.has(node.node_id) &&
          (!groupFilter || groupFilter.has(node.node_id)),
      ),
    [data, groupFilter, neighborhood],
  );
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.node_id)),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () =>
      (data?.edges ?? []).filter(
        (edge) =>
          visibleIds.has(edge.source_node_id) && visibleIds.has(edge.target_node_id),
      ),
    [data, visibleIds],
  );
  const positionedNodes = useMemo(
    () => computeForceLayout(visibleNodes, visibleEdges),
    [visibleEdges, visibleNodes],
  );
  const selected = data?.nodes.find((node) => node.node_id === selectedId) ?? null;
  const selectedEdges = (data?.edges ?? []).filter(
    (edge) =>
      edge.source_node_id === selectedId || edge.target_node_id === selectedId,
  );
  const selectedGroups = (data?.groups ?? []).filter((item) =>
    item.node_ids?.includes(selectedId ?? ""),
  );
  const groupNodeIds = new Set(
    (data?.nodes ?? []).filter(isGroupNode).map((node) => node.node_id),
  );

  const selectNode = (nodeId: string) => {
    setSelectedId(nodeId);
    setSearchParams({ node: nodeId }, { replace: true });
  };

  return (
    <section className="graph-page page-stack">
      <PageIntro
        title="探索知识之间的连接"
        description="节点大小反映引用次数，边宽反映关系权重；拖拽节点可微调当前布局。"
        actions={
          <button className="button button--secondary" onClick={loadGraph} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} size={16} /> 刷新图谱
          </button>
        }
      />
      {error ? <ErrorNotice message={error} /> : null}

      <div className="graph-layout">
        <aside className="graph-explorer card">
          <div className="panel-title-row">
            <div><p className="eyebrow">Explore</p><h3>图谱探索</h3></div>
            <span className="count-chip">{visibleNodes.length}</span>
          </div>
          <label className="search-field">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索节点、别名或标签…"
            />
          </label>
          {query && highlightedIds.size === 0 ? (
            <span className="field-hint text-warning">没有匹配节点</span>
          ) : null}

          <section className="control-section">
            <div className="section-label"><span>关系深度</span><strong>{depth}</strong></div>
            <input
              className="range-input"
              type="range"
              min="1"
              max="4"
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
            />
            <div className="range-labels"><span>1</span><span>2</span><span>3</span><span>4</span></div>
          </section>

          <section className="control-section">
            <div className="section-label"><span>群组筛选</span></div>
            <label className="select-field">
              <select value={groupId} onChange={(event) => setGroupId(event.target.value)}>
                <option value="all">全部群组</option>
                {(data?.groups ?? []).map((item, index) => (
                  <option key={item.group_id} value={item.group_id}>
                    群组 {index + 1} · {item.node_count ?? item.node_ids?.length ?? 0} 节点
                  </option>
                ))}
              </select>
              <ChevronDown size={15} />
            </label>
          </section>

          <section className="control-section">
            <div className="section-label"><span>搜索命中</span><strong>{highlightedIds.size}</strong></div>
            <div className="matched-node-list">
              {(data?.nodes ?? [])
                .filter((node) => highlightedIds.has(node.node_id))
                .slice(0, 6)
                .map((node) => (
                  <button key={node.node_id} onClick={() => selectNode(node.node_id)}>
                    <span>{node.keyword}</span><small>ref {node.ref_count}</small>
                  </button>
                ))}
              {!query ? <p className="field-hint">输入关键词即可高亮并聚焦邻居。</p> : null}
            </div>
          </section>

          <div className="graph-mini-stats">
            <span><strong>{data?.nodes.length ?? 0}</strong>全部节点</span>
            <span><strong>{data?.edges.length ?? 0}</strong>全部关系</span>
          </div>
        </aside>

        <section className="graph-canvas card">
          {loading ? <LoadingState label="正在计算图谱布局…" /> : null}
          {!loading ? (
            <GraphCanvas
              nodes={positionedNodes}
              edges={visibleEdges}
              selectedId={selectedId}
              highlightedIds={highlightedIds}
              groupNodeIds={groupNodeIds}
              onSelect={selectNode}
            />
          ) : null}
        </section>

        <aside className="graph-detail card">
          {selected ? (
            <>
              <div className="detail-heading">
                <p className="eyebrow">Selected node</p>
                <h3>{selected.keyword}</h3>
                <span className="node-type-chip">
                  {groupNodeIds.has(selected.node_id)
                    ? "群组节点"
                    : isCoreNode(
                        selected,
                        Math.max(1, ...(data?.nodes ?? []).map((node) => node.ref_count)),
                      )
                      ? "核心概念"
                      : "一般概念"}
                </span>
              </div>
              <section className="detail-section">
                <h4>摘要</h4>
                <p>{selected.summary || "暂无摘要"}</p>
              </section>
              <section className="detail-section">
                <h4><Tag size={14} />标签与别名</h4>
                <div className="tag-list">
                  {[...selected.tags, ...selected.aliases].map((tag) => <span key={tag}>{tag}</span>)}
                  {!selected.tags.length && !selected.aliases.length ? <small>暂无标签</small> : null}
                </div>
              </section>
              <section className="detail-section detail-metrics">
                <span><strong>{selected.ref_count}</strong>关联来源</span>
                <span><strong>{selectedEdges.length}</strong>直接关系</span>
                <span><strong>{selectedGroups.length}</strong>所属群组</span>
              </section>
              <section className="detail-section">
                <div className="section-label"><h4><FileText size={14} />关联文档</h4></div>
                <div className="related-doc-card">
                  <span className="document-icon">SRC</span>
                  <div><strong>{selected.ref_count} 个来源绑定</strong><small>文档明细由来源接口提供</small></div>
                </div>
                <Link className="text-link" to="/documents">前往文档管理</Link>
              </section>
            </>
          ) : (
            <div className="empty-state compact">
              <Network size={30} /><h3>选择节点查看详情</h3><p>点击画布中的任意节点。</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
