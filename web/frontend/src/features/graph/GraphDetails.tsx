import { FileText, GitFork, Network, Tag } from "lucide-react";
import { Link } from "react-router-dom";

import type { GraphVisualizationNode } from "../../types/api";
import type { ReadableRelationPath } from "./relationPaths";

type GraphDetailsProps = {
  selected: GraphVisualizationNode | null;
  paths: ReadableRelationPath[];
  directRelationCount: number;
  isCore: boolean;
  sourceLabels: Map<string, string>;
  onSelect: (nodeId: string) => void;
};

export function GraphDetails({
  selected,
  paths,
  directRelationCount,
  isCore,
  sourceLabels,
  onSelect,
}: GraphDetailsProps) {
  if (!selected) {
    return (
      <div className="graph-inspector-empty">
        <Network size={28} />
        <strong>选择节点查看详情</strong>
        <span>点击画布中的任意节点，关系链会在这里展开。</span>
      </div>
    );
  }

  return (
    <div className="graph-inspector">
      <div className="graph-inspector__heading">
        <div>
          <p className="eyebrow">Selected node</p>
          <h3>{selected.keyword}</h3>
        </div>
        <span className="node-type-chip">
          {isCore ? "核心概念" : selected.group_ids.length ? "群组成员" : "一般概念"}
        </span>
      </div>

      <div className="graph-inspector__summary">
        <strong>摘要</strong>
        <p>{selected.summary || "暂无摘要"}</p>
      </div>

      <div className="graph-inspector__metrics">
        <span><strong>{selected.ref_count}</strong>来源绑定</span>
        <span><strong>{directRelationCount}</strong>直接关系</span>
        <span><strong>{Math.round((selected.weight ?? 0) * 100)}%</strong>节点权重</span>
      </div>

      <div className="graph-inspector__meta">
        <h4><Tag size={14} />标签与别名</h4>
        <div className="tag-list">
          {[...selected.tags, ...selected.aliases].map((tag) => <span key={tag}>{tag}</span>)}
          {!selected.tags.length && !selected.aliases.length ? <small>暂无标签</small> : null}
        </div>
      </div>

      <div className="graph-inspector__paths">
        <h4><GitFork size={14} />可读关系路径</h4>
        <p>A →[关系]→ B，并在存在连续有向关系时展示二跳路径。</p>
        <div className="graph-path-list">
          {paths.map((path) => {
            const targetId = path.nodeIds.find((nodeId) => nodeId !== selected.node_id);
            return (
              <button
                type="button"
                key={path.id}
                onClick={() => targetId && onSelect(targetId)}
              >
                <span>{path.text}</span>
                <small>路径权重 {Math.round(path.weight * 100)}%</small>
              </button>
            );
          })}
          {!paths.length ? <small className="field-hint">当前视图中暂无关系路径</small> : null}
        </div>
      </div>

      <div className="graph-inspector__sources">
        <h4><FileText size={14} />来源绑定</h4>
        <span>{selected.source_ids.length} 个文档 · {selected.group_ids.length} 个真实群组</span>
        <div className="graph-source-list">
          {selected.source_ids.slice(0, 5).map((sourceId) => (
            <small key={sourceId}>{sourceLabels.get(sourceId) ?? sourceId}</small>
          ))}
        </div>
        <Link className="text-link" to="/documents">前往文档管理</Link>
      </div>
    </div>
  );
}
