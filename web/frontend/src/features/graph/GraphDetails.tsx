import { Check, ExternalLink, FileCode2, FileText, GitFork, Network, Pencil, Tag, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/api";
import { ErrorNotice, LoadingState } from "../../components/Feedback";
import { MarkdownPreview } from "../../components/MarkdownPreview";
import type { GraphNodeDetails, GraphVisualizationNode } from "../../types/api";
import type { ReadableRelationPath } from "./relationPaths";

type GraphDetailsProps = {
  selected: GraphVisualizationNode | null;
  paths: ReadableRelationPath[];
  directRelationCount: number;
  isCore: boolean;
  sourceLabels: Map<string, string>;
  onSelect: (nodeId: string) => void;
  onChanged: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
};

export function GraphDetails(props: GraphDetailsProps) {
  if (!props.selected) return <div className="graph-inspector-empty">
    <Network size={28} /><strong>选择节点查看详情</strong><span>点击画布节点，查看摘要、关系和来源文件。</span>
  </div>;
  return <NodeDetails key={props.selected.node_id} {...props} selected={props.selected} />;
}

function NodeDetails({ selected, paths, directRelationCount, isCore, sourceLabels, onSelect, onChanged, onDirtyChange }: GraphDetailsProps & { selected: GraphVisualizationNode }) {
  const [detail, setDetail] = useState<GraphNodeDetails | null>(null);
  const [draft, setDraft] = useState(selected.summary);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState("");
  const summary = detail?.summary ?? selected.summary;
  const dirty = editing && draft !== summary;
  const sources = detail?.sources ?? [];
  const activeSource = sources.find((source) => source.source_id === sourceId) ?? sources[0];
  const hasOriginal = activeSource?.source_path && activeSource.exists_status === "active";
  const originalLink = hasOriginal ? `/documents?source=${encodeURIComponent(activeSource.source_id)}` : null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    void api.getNode(selected.node_id, controller.signal).then((data) => {
      if (!controller.signal.aborted) { setDetail(data); setLoading(false); }
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : "无法读取节点详情"); setLoading(false); }
    });
    return () => controller.abort();
  }, [selected.node_id, selected.updated_at]);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const updated = await api.updateNodeSummary(selected.node_id, draft, summary);
      setDetail(updated); setEditing(false); onDirtyChange(false);
      await onChanged();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "无法保存摘要"); }
    finally { setSaving(false); }
  };
  const remove = async () => {
    if (!window.confirm(`删除节点「${selected.keyword}」及其关联关系？仅关联此节点的文档及其索引也将永久删除，此操作不可恢复。`)) return;
    setSaving(true); setError(null);
    try { await api.deleteNode(selected.node_id); onDirtyChange(false); await onChanged(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "无法删除节点"); }
    finally { setSaving(false); }
  };

  return <article className="graph-inspector graph-node-sheet" aria-busy={loading || saving} onClickCapture={(event) => {
    if (dirty && (event.target as HTMLElement).closest("a") && !window.confirm("放弃尚未保存的摘要修改并打开来源文件？")) event.preventDefault();
  }}>
    <header className="graph-node-sheet__heading">
      <div><span className="graph-node-sheet__label">节点详情</span><h2>{selected.keyword}</h2></div>
      <div className="graph-node-sheet__heading-actions">
        <span className="node-type-chip">{isCore ? "核心概念" : selected.group_ids.length ? "群组成员" : "一般概念"}</span>
        {originalLink ? <Link to={originalLink}><ExternalLink size={12} />打开相关文件</Link> : <span className="field-hint">暂无原文件映射</span>}
      </div>
    </header>
    {error && <ErrorNotice message={error} />}
    <section className="graph-node-summary" aria-label="节点摘要">
      <div className="graph-node-summary__toolbar">
        <strong>摘要</strong>
        <div role="group" aria-label="摘要显示模式">
          <button type="button" aria-pressed={!editing} disabled={saving} onClick={() => {
            if (dirty && !window.confirm("放弃尚未保存的摘要修改？")) return;
            setEditing(false);
          }}>预览</button>
          <button type="button" aria-pressed={editing} disabled={loading || saving} onClick={() => { if (!editing) setDraft(summary); setEditing(true); }}><Pencil size={12} />编辑</button>
        </div>
      </div>
      <div className={`graph-node-summary__body ${editing ? "is-editing" : ""}`}>
        {editing ? <textarea aria-label="节点摘要编辑器" value={draft} maxLength={200000} disabled={saving} onChange={(event) => setDraft(event.target.value)} /> :
          summary ? <MarkdownPreview content={summary} /> : <p className="field-hint">暂无摘要</p>}
      </div>
      {editing && <footer><span>{dirty ? "有未保存修改" : "编辑节点摘要"}</span>
        <button type="button" disabled={saving} onClick={() => { setDraft(summary); setEditing(false); }}><X size={13} />取消</button>
        <button type="button" disabled={!dirty || saving} onClick={() => void save()}><Check size={13} />{saving ? "正在保存…" : "保存摘要"}</button>
      </footer>}
    </section>
    <section className="graph-node-tags" aria-label="标签与别名"><h3><Tag size={13} />标签与别名</h3>
      <div className="tag-list">{[...new Set([...selected.tags, ...selected.aliases])].map((tag) => <span key={tag}>{tag}</span>)}
        {!selected.tags.length && !selected.aliases.length && <small>暂无标签与别名</small>}</div>
    </section>
    <section className="graph-node-relations">
      <header><h3><GitFork size={14} />关系节点与关系线</h3><span>{detail?.relations.length ?? directRelationCount} 条直接关系</span></header>
      <div className="graph-path-list">
        {paths.map((path) => {
          const targetId = path.nodeIds.find((nodeId) => nodeId !== selected.node_id);
          return <button type="button" key={path.id} onClick={() => targetId && onSelect(targetId)}>
            <span>{path.text}</span><small>{path.nodeIds.length - 1} 跳 · 权重 {Math.round(path.weight * 100)}%</small>
          </button>;
        })}
        {!paths.length && <p className="field-hint">暂无关系路径</p>}
      </div>
      <div className="graph-node-groups"><h3><Network size={14} />来源节点群总结</h3>
        {loading ? <LoadingState label="正在读取群组…" /> : detail?.groups.length ?
          detail.groups.map((group, index) => <section key={group.group_id}><header><strong>节点群 {index + 1}</strong><span>{group.node_count ?? 0} 个节点</span></header>
            <MarkdownPreview content={group.summary || "暂无总结"} /></section>) :
          <p className="field-hint" title="使用顶部“总结节点群”生成">暂无节点群总结</p>}
      </div>
    </section>
    <section className="graph-node-sources">
      <header><h3><FileText size={14} />来源文件</h3><span>{sources.length} 个文档</span></header>
      <div className="graph-node-sources__list">{sources.map((source) => {
        const label = source.source_path || sourceLabels.get(source.source_id) || source.original_path || source.source_id;
        const content = <><FileText size={13} /><span>{label}</span></>;
        return <div key={source.source_id} className={`graph-node-source ${activeSource?.source_id === source.source_id ? "is-active" : ""}`}>
          {source.source_path && source.exists_status === "active" ?
            <Link to={`/documents?source=${encodeURIComponent(source.source_id)}`} title="在知识库中打开原文件">{content}</Link> :
            <span title="原文件未映射或已移除">{content}</span>}
          <button type="button" aria-label={`查看来源详情：${label}`}
            aria-pressed={activeSource?.source_id === source.source_id} onClick={() => setSourceId(source.source_id)}>详情</button>
        </div>;
      })}</div>
      {activeSource ? <dl>
        <div><dt>原文件</dt><dd>{originalLink ?
          <Link to={originalLink} title="在知识库中打开原文件"><FileText size={12} /><span>{activeSource.source_path}</span></Link> :
          activeSource.source_path || "暂无原文件映射"}</dd></div>
        <div><dt>处理文件</dt><dd>{activeSource.relative_path ?
          <Link to={`/build-status?source=${encodeURIComponent(activeSource.source_id)}`} title="在构建状态中打开预处理文件"><FileCode2 size={12} /><span>{activeSource.relative_path}</span></Link> : "暂无处理稿"}</dd></div>
      </dl> : !loading && <p className="field-hint">暂无来源绑定</p>}
      <footer>
        <button className="graph-node-delete" type="button" disabled={saving || dirty} onClick={() => void remove()}><Trash2 size={13} />删除节点</button>
        {originalLink && <Link to={originalLink}><ExternalLink size={13} />打开原文件</Link>}
      </footer>
    </section>
  </article>;
}
