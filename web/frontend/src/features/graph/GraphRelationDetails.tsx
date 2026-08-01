import { ArrowRight, GitBranch, Link2, Network } from "lucide-react";

import type { GraphEdge, GraphNode } from "../../types/api";

type GraphRelationDetailsProps = {
  selected: GraphEdge;
  related: GraphEdge[];
  nodesById: Map<string, GraphNode>;
  onSelectNode: (nodeId: string) => void;
};

export function GraphRelationDetails({
  selected,
  related,
  nodesById,
  onSelectNode,
}: GraphRelationDetailsProps) {
  const source = nodesById.get(selected.source_node_id);
  const target = nodesById.get(selected.target_node_id);
  return (
    <div className="graph-relation-inspector">
      <div className="graph-inspector__heading">
        <div>
          <p className="eyebrow">Selected relation</p>
          <h3>{selected.relation}</h3>
        </div>
        <span className="node-type-chip">关系</span>
      </div>

      <div className="graph-relation-route" aria-label="关系端点">
        <button type="button" onClick={() => onSelectNode(selected.source_node_id)}>
          <Network size={14} />
          <span>{source?.keyword ?? selected.source_node_id}</span>
        </button>
        <span><ArrowRight size={16} /><strong>{selected.relation}</strong></span>
        <button type="button" onClick={() => onSelectNode(selected.target_node_id)}>
          <Network size={14} />
          <span>{target?.keyword ?? selected.target_node_id}</span>
        </button>
      </div>

      <div className="graph-inspector__metrics">
        <span><strong>{Math.round(selected.weight * 100)}%</strong>关系权重</span>
        <span><strong>{selected.support_count ?? 0}</strong>证据支持</span>
        <span><strong>{related.length}</strong>同类串联</span>
      </div>

      <section className="graph-relation-usages">
        <h4><GitBranch size={14} />使用该关系串联的节点</h4>
        <p>展示当前知识图谱中使用“{selected.relation}”连接的节点对。</p>
        <div className="graph-relation-usage-list">
          {related.slice(0, 40).map((edge) => (
            <button
              type="button"
              className={edge.edge_id === selected.edge_id ? "is-selected" : ""}
              key={edge.edge_id}
              onClick={() => onSelectNode(edge.target_node_id)}
            >
              <Link2 size={13} />
              <span>
                {nodesById.get(edge.source_node_id)?.keyword ?? edge.source_node_id}
                <b> →[{edge.relation}]→ </b>
                {nodesById.get(edge.target_node_id)?.keyword ?? edge.target_node_id}
              </span>
              <small>{Math.round(edge.weight * 100)}%</small>
            </button>
          ))}
        </div>
        {related.length > 40 ? <small className="field-hint">仅展示前 40 条，共 {related.length} 条。</small> : null}
      </section>
    </div>
  );
}
