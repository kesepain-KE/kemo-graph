import { ChevronLeft, ChevronRight, LoaderCircle, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/api";
import type { GraphNodeSearchData } from "../../types/api";

type Props = {
  query: string;
  revision?: string;
  onQueryChange: (query: string) => void;
  onResults: (ids: string[]) => void;
  onSelect: (nodeId: string) => void;
};

export function GraphNodeSearch({ query, revision, onQueryChange, onResults, onSelect }: Props) {
  const [data, setData] = useState<GraphNodeSearchData | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef(onResults);
  resultsRef.current = onResults;

  useEffect(() => { setOffset(0); }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(null);
    resultsRef.current([]);
    if (!query.trim()) { setLoading(false); return; }
    setLoading(true);
    const timer = window.setTimeout(() => {
      void api.searchGraphNodes(query, offset, controller.signal).then((result) => {
        if (controller.signal.aborted) return;
        setData(result); setLoading(false);
        resultsRef.current(result.nodes.map((node) => node.node_id));
      }).catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught instanceof Error ? caught.message : "搜索失败"); setLoading(false);
      });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, offset, revision]);

  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);

  const choose = (nodeId: string) => { onSelect(nodeId); setOpen(false); };
  return <div className="graph-node-search" ref={rootRef}
    onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
    <form className="search-field" role="search" onSubmit={(event) => {
      event.preventDefault();
      if (data?.nodes[0]) choose(data.nodes[0].node_id);
    }}>
      {loading ? <LoaderCircle size={15} className="spin" /> : <Search size={15} />}
      <input aria-label="搜索图谱节点和文本" aria-controls="graph-search-results" aria-expanded={open && !!query.trim()}
        value={query} maxLength={1000} placeholder="搜索节点和文本…" autoComplete="off"
        onFocus={() => setOpen(true)} onChange={(event) => { onQueryChange(event.target.value); setOpen(true); }} />
      {query && <button type="button" aria-label="清空节点搜索" onClick={() => onQueryChange("")}><X size={14} /></button>}
    </form>
    {open && query.trim() && <div id="graph-search-results" className="graph-search-results" aria-label="节点搜索结果" aria-busy={loading}>
      <header><strong>{loading ? "正在搜索…" : error ? "搜索失败" : `${data?.total ?? 0} 个匹配节点`}</strong><span>全文检索</span></header>
      {error ? <p role="alert">{error}</p> : null}
      {!loading && !error && !data?.nodes.length ? <p>没有找到匹配节点，试试节点名、别名或摘要关键词。</p> : null}
      <div className="graph-search-results__list">
        {data?.nodes.map((node) => <button type="button" key={node.node_id} onClick={() => choose(node.node_id)}>
          <strong>{node.keyword}</strong><span>{node.snippet || node.summary || "暂无摘要"}</span>
        </button>)}
      </div>
      {data && data.total > data.limit && <footer>
        <span>{offset + 1}–{offset + data.nodes.length} / {data.total}</span>
        <button type="button" aria-label="上一页搜索结果" disabled={!offset || loading} onClick={() => setOffset(offset - data.limit)}><ChevronLeft size={15} /></button>
        <button type="button" aria-label="下一页搜索结果" disabled={offset + data.limit >= data.total || loading} onClick={() => setOffset(offset + data.limit)}><ChevronRight size={15} /></button>
      </footer>}
    </div>}
  </div>;
}
