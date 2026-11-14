import { Focus, Hand, Minus, Plus, RotateCcw } from "lucide-react";
import type { GraphRenderScene } from "./engine/GraphRenderer";

type Props = {
  scene: GraphRenderScene;
  panMode: boolean;
  onPanMode: () => void;
  onFocus: () => void;
  onZoom: (delta: number) => void;
  onReset: () => void;
  zoomLabel?: string;
};

export function GraphCanvasTools({ scene, panMode, onPanMode, onFocus, onZoom, onReset, zoomLabel = "缩放" }: Props) {
  const colors = scene.appearance.colors;
  const local = scene.contextMode === "local";
  const nodes = local ? [
    { label: "选中节点", color: colors.selectedNode },
    { label: "相关节点", color: colors.relatedNode },
    { label: "无关节点", color: colors.unrelatedNode },
  ] : [{ label: "全局节点", color: colors.globalNode }];
  const edges = local ? [
    { label: "相关关系", color: colors.selectedRelation },
    { label: "无关关系", color: colors.unrelatedRelation },
  ] : [{ label: "全局关系", color: colors.globalRelation }];
  return <>
    <div className="graph-legend" aria-label="节点与关系颜色图例">
      <div>{nodes.map(({ label, color }) => <span key={label}><i className="legend-dot" style={{ background: color }} />{label}</span>)}</div>
      <div>{edges.map(({ label, color }) => <span key={label}><i className="legend-line" style={{ background: color }} />{label}</span>)}</div>
    </div>
    <div className="canvas-tools" aria-label="图谱画布工具">
      <button className="icon-button" title="手动拖动画布" aria-label="手动拖动画布" aria-pressed={panMode}
        type="button" onClick={onPanMode}><Hand size={16} /></button>
      <button className="icon-button" title="将所选节点居中" aria-label="将所选节点居中" disabled={!scene.selectedId}
        type="button" onClick={onFocus}><Focus size={16} /></button>
      <div className="zoom-control">
        <button type="button" onClick={() => onZoom(-0.12)} aria-label="缩小图谱"><Minus size={15} /></button>
        <span>{zoomLabel}</span>
        <button type="button" onClick={() => onZoom(0.12)} aria-label="放大图谱"><Plus size={15} /></button>
      </div>
    </div>
    <button className="reset-view" type="button" onClick={onReset}><RotateCcw size={14} />重置视图</button>
  </>;
}
