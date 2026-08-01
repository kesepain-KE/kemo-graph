import {
  Check,
  ChevronDown,
  CircleGauge,
  Filter,
  Gauge,
  Network,
  Palette,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { GraphEdge, GraphNode, GraphVisualizationNode } from "../../types/api";
import type { GraphCatalog } from "./data/GraphDataLoader";
import { GraphDetails } from "./GraphDetails";
import { GraphRelationDetails } from "./GraphRelationDetails";
import { type GraphPreferences } from "./graphPreferences";
import type { LayoutRuntimeStatus } from "./engine/layoutTypes";
import type { ReadableRelationPath } from "./relationPaths";

type GraphControlPanelProps = {
  preferences: GraphPreferences;
  onPreferencesChange: (preferences: GraphPreferences) => void;
  catalog: GraphCatalog | null;
  activeNodes: GraphVisualizationNode[];
  activeRelationTypes: string[];
  highlightedNodes: GraphVisualizationNode[];
  selected: GraphVisualizationNode | null;
  selectedEdge: GraphEdge | null;
  relatedRelationEdges: GraphEdge[];
  nodesById: Map<string, GraphNode>;
  selectedPaths: ReadableRelationPath[];
  selectedRelationCount: number;
  selectedIsCore: boolean;
  sourceLabels: Map<string, string>;
  rendererBackend: string;
  layoutStatus: LayoutRuntimeStatus | null;
  fps: number;
  loading: boolean;
  onSelect: (nodeId: string) => void;
  onReheat: () => void;
  fullscreen?: boolean;
  visible?: boolean;
  onRequestClose?: () => void;
};

export function GraphControlPanel({
  preferences,
  onPreferencesChange,
  catalog,
  activeNodes,
  activeRelationTypes,
  highlightedNodes,
  selected,
  selectedEdge,
  relatedRelationEdges,
  nodesById,
  selectedPaths,
  selectedRelationCount,
  selectedIsCore,
  sourceLabels,
  rendererBackend,
  layoutStatus,
  fps,
  loading,
  onSelect,
  onReheat,
  fullscreen = false,
  visible = true,
  onRequestClose,
}: GraphControlPanelProps) {
  const [fullscreenTab, setFullscreenTab] = useState<"controls" | "details">("controls");
  const sourceIds = useMemo(() => sortedUnique(
    (catalog?.nodes ?? []).flatMap((node) => node.source_ids),
  ), [catalog]);
  const tags = useMemo(() => sortedUnique(
    (catalog?.nodes ?? []).flatMap((node) => node.tags),
  ), [catalog]);

  useEffect(() => {
    if (fullscreen) setFullscreenTab("controls");
  }, [fullscreen]);

  useEffect(() => {
    if (fullscreen && selectedEdge) setFullscreenTab("details");
  }, [fullscreen, selectedEdge]);

  const setView = (patch: Partial<GraphPreferences["view"]>) => {
    onPreferencesChange({
      ...preferences,
      view: { ...preferences.view, ...patch },
    });
  };
  const setFilters = (patch: Partial<GraphPreferences["filters"]>) => {
    onPreferencesChange({
      ...preferences,
      filters: { ...preferences.filters, ...patch },
    });
  };
  const setAppearance = (patch: Partial<GraphPreferences["appearance"]>) => {
    onPreferencesChange({
      ...preferences,
      appearance: { ...preferences.appearance, ...patch },
    });
  };
  const setForce = (patch: Partial<GraphPreferences["force"]>) => {
    onPreferencesChange({
      ...preferences,
      force: { ...preferences.force, ...patch },
    });
  };
  const setPerformance = (patch: Partial<GraphPreferences["performance"]>) => {
    onPreferencesChange({
      ...preferences,
      performance: { ...preferences.performance, ...patch },
    });
  };
  const setColors = (patch: Partial<GraphPreferences["appearance"]["colors"]>) => {
    setAppearance({
      colors: { ...preferences.appearance.colors, ...patch },
    });
  };

  return (
    <aside
      className={`graph-control-bubble card ${preferences.appearance.canvasPreset === "obsidian" ? "is-theme-dark" : "is-theme-light"} ${fullscreen ? "is-fullscreen-panel" : ""} ${visible ? "" : "is-fullscreen-hidden"}`}
      aria-label="图谱控制与详情"
      aria-hidden={fullscreen && !visible}
    >
      <div className="graph-control-bubble__scroll">
        <header className="graph-control-bubble__header">
          <div className="graph-control-bubble__title">
            <span><Network size={19} /></span>
            <div><p className="eyebrow">Graph studio</p><h2>图谱控制台</h2></div>
          </div>
          <div className="graph-control-bubble__header-actions">
            <div className="graph-control-bubble__live">
              <i className={loading ? "is-loading" : ""} />
              {loading ? "载入中" : "本机实时"}
            </div>
            {fullscreen && onRequestClose ? (
              <>
                <button
                  className="graph-control-bubble__close"
                  type="button"
                  onClick={onReheat}
                  aria-label="重新加热图谱布局"
                  title="重新加热图谱布局"
                >
                  <RefreshCw size={17} />
                </button>
                <button
                  className="graph-control-bubble__close"
                  type="button"
                  onClick={onRequestClose}
                  aria-label="收起图谱操作面板"
                  title="收起操作面板"
                >
                  <X size={18} />
                </button>
              </>
            ) : null}
          </div>
        </header>

        {fullscreen ? (
          <div className="graph-fullscreen-tabs" role="tablist" aria-label="全屏图谱面板">
            <button
              type="button"
              role="tab"
              aria-selected={fullscreenTab === "controls"}
              className={fullscreenTab === "controls" ? "is-active" : ""}
              onClick={() => setFullscreenTab("controls")}
            >图谱控制</button>
            <button
              type="button"
              role="tab"
              aria-selected={fullscreenTab === "details"}
              className={fullscreenTab === "details" ? "is-active" : ""}
              onClick={() => setFullscreenTab("details")}
            >详情</button>
          </div>
        ) : null}

        {!fullscreen || fullscreenTab === "details" ? (
          <section className="graph-control-card graph-control-card--details">
            {selectedEdge ? (
              <GraphRelationDetails
                selected={selectedEdge}
                related={relatedRelationEdges}
                nodesById={nodesById}
                onSelectNode={onSelect}
              />
            ) : (
              <GraphDetails
                selected={selected}
                paths={selectedPaths}
                directRelationCount={selectedRelationCount}
                isCore={selectedIsCore}
                sourceLabels={sourceLabels}
                onSelect={onSelect}
              />
            )}
          </section>
        ) : null}

        {!fullscreen || fullscreenTab === "controls" ? (
          <div className="graph-control-sections">

        <ControlSection
          icon={<Filter size={17} />}
          title="筛选"
          description="范围、节点、来源与关系门槛"
          defaultOpen={!fullscreen}
          resetKey={fullscreen}
        >
          <label className="graph-panel-search">
            <Search size={15} />
            <input
              value={preferences.filters.query}
              onChange={(event) => setFilters({ query: event.target.value })}
              placeholder="节点、别名、标签或摘要…"
            />
          </label>

          <div className="graph-segmented" role="group" aria-label="图谱范围">
            <button
              type="button"
              className={preferences.view.mode === "local" ? "is-active" : ""}
              onClick={() => setView({ mode: "local" })}
            >局部图谱</button>
            <button
              type="button"
              className={preferences.view.mode === "global" ? "is-active" : ""}
              onClick={() => setView({ mode: "global" })}
            >全局图谱</button>
          </div>

          <RangeControl
            label="邻居深度"
            value={preferences.view.depth}
            min={1}
            max={10}
            step={1}
            unit="跳"
            disabled={preferences.view.mode === "global"}
            onChange={(depth) => setView({ depth })}
          />
          <SelectControl
            label="真实群组"
            value={preferences.filters.groupId}
            onChange={(groupId) => setFilters({ groupId })}
            options={[
              { value: "all", label: "全部群组" },
              ...(catalog?.groups ?? []).map((group, index) => ({
                value: group.group_id,
                label: `群组 ${index + 1} · ${group.node_count ?? group.node_ids?.length ?? 0} 节点`,
              })),
            ]}
          />
          <SelectControl
            label="来源文件"
            value={preferences.filters.sourceId}
            onChange={(sourceId) => setFilters({ sourceId })}
            options={[
              { value: "all", label: "全部来源" },
              ...sourceIds.map((sourceId) => ({
                value: sourceId,
                label: sourceLabels.get(sourceId) ?? sourceId,
              })),
            ]}
          />
          <SelectControl
            label="标签"
            value={preferences.filters.tag}
            onChange={(tag) => setFilters({ tag })}
            options={[
              { value: "all", label: "全部标签" },
              ...tags.map((tag) => ({ value: tag, label: tag })),
            ]}
          />
          <SelectControl
            label="关系类型"
            value={preferences.filters.relation}
            onChange={(relation) => setFilters({ relation })}
            options={[
              { value: "all", label: "全部关系" },
              ...activeRelationTypes.map((relation) => ({ value: relation, label: relation })),
            ]}
          />
          <RangeControl
            label="最低节点权重"
            value={preferences.filters.minNodeWeight}
            min={0}
            max={1}
            step={0.05}
            onChange={(minNodeWeight) => setFilters({ minNodeWeight })}
          />
          <RangeControl
            label="最低引用数"
            value={preferences.filters.minRefCount}
            min={0}
            max={Math.max(10, ...activeNodes.map((node) => node.ref_count))}
            step={1}
            unit="次"
            onChange={(minRefCount) => setFilters({ minRefCount })}
          />
          <RangeControl
            label="最低关系权重"
            value={preferences.filters.minEdgeWeight}
            min={0}
            max={1}
            step={0.05}
            onChange={(minEdgeWeight) => setFilters({ minEdgeWeight })}
          />

          <div className="graph-match-list">
            <div><span>搜索命中</span><strong>{highlightedNodes.length}</strong></div>
            {highlightedNodes.slice(0, 8).map((node) => (
              <button type="button" key={node.node_id} onClick={() => onSelect(node.node_id)}>
                <span>{node.keyword}</span><small>ref {node.ref_count}</small>
              </button>
            ))}
          </div>
        </ControlSection>

        <ControlSection
          icon={<Palette size={17} />}
          title="颜色组"
          description="按全局、局部与当前选择配置语义颜色"
          resetKey={fullscreen}
        >
          <p className="graph-control-hint">
            颜色不再依赖 Obsidian 分组规则；局部模式会强化相关内容，并按下方透明度虚化无关内容。
          </p>
          <div className="graph-semantic-color-list">
            <ColorControl label="选中节点" value={preferences.appearance.colors.selectedNode} onChange={(selectedNode) => setColors({ selectedNode })} />
            <ColorControl label="相关节点" value={preferences.appearance.colors.relatedNode} onChange={(relatedNode) => setColors({ relatedNode })} />
            <ColorControl label="选中及相关关系" value={preferences.appearance.colors.selectedRelation} onChange={(selectedRelation) => setColors({ selectedRelation })} />
            <ColorControl label="全局节点" value={preferences.appearance.colors.globalNode} onChange={(globalNode) => setColors({ globalNode })} />
            <ColorControl label="全局关系" value={preferences.appearance.colors.globalRelation} onChange={(globalRelation) => setColors({ globalRelation })} />
            <ColorControl label="局部无关节点" value={preferences.appearance.colors.unrelatedNode} onChange={(unrelatedNode) => setColors({ unrelatedNode })} />
            <ColorControl label="局部无关关系" value={preferences.appearance.colors.unrelatedRelation} onChange={(unrelatedRelation) => setColors({ unrelatedRelation })} />
          </div>
          <RangeControl
            label="无关内容透明度"
            value={preferences.appearance.unrelatedOpacity}
            min={0.02}
            max={0.8}
            step={0.02}
            onChange={(unrelatedOpacity) => setAppearance({ unrelatedOpacity })}
          />
        </ControlSection>

        <ControlSection
          icon={<SlidersHorizontal size={17} />}
          title="外观"
          description="画布预设、节点、连线与标签"
          defaultOpen={fullscreen}
          resetKey={fullscreen}
        >
          <div className="graph-segmented" role="group" aria-label="画布预设">
            <button
              type="button"
              className={preferences.appearance.canvasPreset === "light" ? "is-active" : ""}
              onClick={() => setAppearance({ canvasPreset: "light" })}
            >高级白</button>
            <button
              type="button"
              className={preferences.appearance.canvasPreset === "obsidian" ? "is-active" : ""}
              onClick={() => setAppearance({ canvasPreset: "obsidian" })}
            >高级黑</button>
          </div>
          <ToggleControl
            label="显示关系箭头"
            checked={preferences.appearance.showArrows}
            onChange={(showArrows) => setAppearance({ showArrows })}
          />
          <RangeControl
            label="文字透明度"
            value={preferences.appearance.labelOpacity}
            min={0.1}
            max={1}
            step={0.05}
            onChange={(labelOpacity) => setAppearance({ labelOpacity })}
          />
          <RangeControl
            label="节点大小"
            value={preferences.appearance.nodeScale}
            min={0.5}
            max={2}
            step={0.05}
            unit="×"
            onChange={(nodeScale) => setAppearance({ nodeScale })}
          />
          <RangeControl
            label="连线粗细"
            value={preferences.appearance.edgeScale}
            min={0.35}
            max={3}
            step={0.05}
            unit="×"
            onChange={(edgeScale) => setAppearance({ edgeScale })}
          />
          <SelectControl
            label="关系标签"
            value={preferences.appearance.relationLabels}
            onChange={(relationLabels) => setAppearance({
              relationLabels: relationLabels as GraphPreferences["appearance"]["relationLabels"],
            })}
            options={[
              { value: "auto", label: "自动（选中或高缩放）" },
              { value: "selected", label: "仅所选节点" },
              { value: "always", label: "始终显示" },
              { value: "never", label: "从不显示" },
            ]}
          />
        </ControlSection>

        <ControlSection
          icon={<CircleGauge size={17} />}
          title="力度"
          description="本机实时物理模拟参数"
          defaultOpen={fullscreen}
          resetKey={fullscreen}
        >
          <div className="graph-layout-actions">
            <button type="button" onClick={onReheat}><RefreshCw size={14} />重新加热</button>
          </div>
          <RangeControl label="图谱向心力" value={preferences.force.centerStrength} min={0} max={2} step={0.02} onChange={(centerStrength) => setForce({ centerStrength })} />
          <RangeControl label="节点排斥力" value={preferences.force.repulsionStrength} min={0} max={40} step={0.5} onChange={(repulsionStrength) => setForce({ repulsionStrength })} />
          <RangeControl label="链接吸引力" value={preferences.force.linkStrength} min={0} max={4} step={0.05} onChange={(linkStrength) => setForce({ linkStrength })} />
          <RangeControl label="连线长度" value={preferences.force.linkDistance} min={30} max={360} step={1} unit="px" onChange={(linkDistance) => setForce({ linkDistance })} />
          <RangeControl label="阻尼" value={preferences.force.damping} min={0.4} max={0.98} step={0.01} onChange={(damping) => setForce({ damping })} />
          <RangeControl label="冷却速度" value={preferences.force.alphaDecay} min={0.002} max={0.1} step={0.002} onChange={(alphaDecay) => setForce({ alphaDecay })} />
          <RangeControl label="稳定阈值" value={preferences.force.stableEnergy} min={0.001} max={0.2} step={0.001} onChange={(stableEnergy) => setForce({ stableEnergy })} />
          <div className="graph-runtime-line">
            <span>{layoutStatus?.running ? "布局求解中" : layoutStatus?.stable ? "布局已稳定" : "布局已暂停"}</span>
            <strong>{layoutStatus?.iterations ?? 0} iter</strong>
          </div>
        </ControlSection>

        <ControlSection
          icon={<Gauge size={17} />}
          title="性能"
          description="渲染后端、帧率与设备偏好"
          resetKey={fullscreen}
        >
          <SelectControl
            label="性能档位"
            value={preferences.performance.mode}
            onChange={(mode) => setPerformance({ mode: mode as GraphPreferences["performance"]["mode"] })}
            options={[
              { value: "auto", label: "自动" },
              { value: "high", label: "高性能" },
              { value: "compatible", label: "兼容模式（SVG）" },
            ]}
          />
          <SelectControl
            label="标签密度"
            value={preferences.performance.labelDensity}
            onChange={(labelDensity) => setPerformance({ labelDensity: labelDensity as GraphPreferences["performance"]["labelDensity"] })}
            options={[
              { value: "low", label: "低" },
              { value: "balanced", label: "平衡" },
              { value: "high", label: "高" },
            ]}
          />
          <RangeControl label="最大帧率" value={preferences.performance.maxFps} min={20} max={120} step={10} unit="FPS" onChange={(maxFps) => setPerformance({ maxFps })} />
          <div className="graph-performance-grid">
            <span><small>渲染后端</small><strong>{rendererBackend}</strong></span>
            <span><small>布局后端</small><strong>{layoutStatus?.backend ?? "等待中"}</strong></span>
            <span><small>实时帧率</small><strong>{fps ? `${Math.round(fps)} FPS` : "--"}</strong></span>
            <span><small>当前规模</small><strong>{activeNodes.length} 节点</strong></span>
            <span className="graph-performance-grid__wide"><small>GPU 适配器</small><strong>{layoutStatus?.gpuName ?? "未公开或使用 Worker"}</strong></span>
          </div>
          {layoutStatus?.fallbackReason ? (
            <p className="graph-backend-warning">WebGPU 已回退：{layoutStatus.fallbackReason}</p>
          ) : null}
          <p className="graph-control-hint">浏览器只能请求高性能 GPU，不能强制指定独立显卡。</p>
        </ControlSection>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ControlSection({
  icon,
  title,
  description,
  defaultOpen = false,
  resetKey,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  defaultOpen?: boolean;
  resetKey?: string | number | boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => setOpen(defaultOpen), [defaultOpen, resetKey]);
  return (
    <section className={`graph-control-card ${open ? "is-open" : ""}`}>
      <button className="graph-control-card__toggle" type="button" onClick={() => setOpen((value) => !value)}>
        <span className="graph-control-card__icon">{icon}</span>
        <span><strong>{title}</strong><small>{description}</small></span>
        <ChevronDown size={16} />
      </button>
      {open ? <div className="graph-control-card__body">{children}</div> : null}
    </section>
  );
}

function RangeControl({
  label,
  value,
  min,
  max,
  step,
  unit = "",
  disabled = false,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className={`graph-range-control ${disabled ? "is-disabled" : ""}`}>
      <span><strong>{label}</strong><output>{formatNumber(value)}{unit ? ` ${unit}` : ""}</output></span>
      <input type="range" value={value} min={min} max={max} step={step} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function SelectControl({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="graph-select-control">
      <span>{label}</span>
      <ThemedSelect
        ariaLabel={label}
        value={value}
        options={options}
        onChange={onChange}
      />
    </div>
  );
}

function ThemedSelect({
  ariaLabel,
  value,
  options,
  onChange,
}: {
  ariaLabel: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const selected = options[selectedIndex];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const move = (direction: -1 | 1) => {
    if (!options.length) return;
    const next = (selectedIndex + direction + options.length) % options.length;
    onChange(options[next].value);
  };

  return (
    <div className={`graph-themed-select ${open ? "is-open" : ""}`} ref={rootRef}>
      <button
        type="button"
        className="graph-themed-select__trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            move(event.key === "ArrowDown" ? 1 : -1);
            setOpen(true);
          }
        }}
      >
        <span>{selected?.label ?? "请选择"}</span>
        <ChevronDown size={15} />
      </button>
      {open ? (
        <div className="graph-themed-select__menu" role="listbox" aria-label={ariaLabel}>
          {options.map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={option.value === value ? "is-selected" : ""}
              key={option.value}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value ? <Check size={14} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ColorControl({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="graph-semantic-color-control">
      <span>{label}</span>
      <span className="graph-semantic-color-control__value">
        <code>{value.toUpperCase()}</code>
        <input
          type="color"
          aria-label={`${label}颜色`}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </span>
    </label>
  );
}

function ToggleControl({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="graph-toggle-control">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

function sortedUnique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}
