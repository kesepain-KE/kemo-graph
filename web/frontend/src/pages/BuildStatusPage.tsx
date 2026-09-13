import { ChevronLeft, ChevronRight, FileText, RefreshCw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import { ThemedSelect } from "../components/ThemedSelect";
import { BUILD_FILTER_OPTIONS, BUILD_STATUS_LABELS, countBuildStatuses, type BuildFilter, type BuildStatus } from "../lib/buildStatus";
import type { DocumentListSummary, DocumentRecord, Pagination } from "../types/api";

const PAGE_SIZE = 6;

export function BuildStatusPage() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, page_size: PAGE_SIZE, total: 0, total_pages: 0 });
  const [summary, setSummary] = useState<DocumentListSummary | null>(null);
  const [query, setQuery] = useState("");
  const [graph, setGraph] = useState<BuildFilter>("all");
  const [rag, setRag] = useState<BuildFilter>("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [revision, setRevision] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const load = async () => {
      if (!active) return;
      if (!document.hidden) {
        setLoading(true);
        try {
          const response = await api.getDocuments(page, PAGE_SIZE, "active", controller.signal, {
            search: query,
            graphStatus: graph === "all" ? undefined : graph,
            ragStatus: rag === "all" ? undefined : rag,
            includeSummary: true,
          });
          if (active) {
            setDocuments(response.documents);
            setPagination(response.pagination);
            setSummary(response.summary ?? null);
            if (response.pagination.page && response.pagination.page !== page) setPage(response.pagination.page);
            setUpdatedAt(new Date().toLocaleTimeString());
            setError(null);
          }
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "无法读取构建状态");
        } finally {
          if (active) setLoading(false);
        }
      }
      if (active && autoRefresh) timer = setTimeout(() => void load(), 5000);
    };
    void load();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [autoRefresh, revision, page, query, graph, rag]);

  const visible = documents;
  const totalPages = Math.max(1, pagination.total_pages);
  const currentPage = Math.min(page, totalPages);
  const pageDocuments = visible;
  useEffect(() => setPage(1), [query, graph, rag]);
  useEffect(() => setPage((value) => Math.min(value, totalPages)), [totalPages]);

  return <section className="build-status-page page-stack">
    <PageIntro title="图谱与向量构建状态" description="集中查看各文档的 Graph / RAG 构建结果。筛选不会启动或中断构建；上传、编辑及手动重建仍在文档管理中操作。"
      actions={<><Link className="button button--secondary" to="/documents"><FileText size={16} />文档管理</Link><button type="button" className="button button--secondary" disabled={loading} onClick={() => setRevision((value) => value + 1)}><RefreshCw size={16} className={loading ? "spin" : ""} />刷新状态</button></>} />
    {error && <ErrorNotice message={`${error}${updatedAt ? "；保留上次成功读取的状态。" : ""}`} />}
    <div className="build-status-summaries">
      {([{ title: "Graph · 知识图谱", field: "graph_status" }, { title: "RAG · 向量索引", field: "rag_status" }] as const).map(({ title, field }) => {
        const counts = summary?.[field === "graph_status" ? "graph" : "rag"] ?? countBuildStatuses(documents, field);
        return <section className="build-status-summary card" key={field} aria-label={title}>
          <h3>{title}</h3><div>{Object.entries(BUILD_STATUS_LABELS).map(([status, label]) => <span key={status}><small>{label}</small><strong>{updatedAt ? counts[status as BuildStatus] : "—"}</strong></span>)}</div>
        </section>;
      })}
    </div>
    <section className="build-status-browser card" aria-label="文档构建状态列表">
      <div className="build-status-controls">
        <label className="search-field"><Search size={16} /><input aria-label="搜索构建文档" placeholder="搜索文件名或项目路径…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <div className="document-filters">
          <div className="document-filter-control"><span>Graph</span><ThemedSelect ariaLabel="Graph 状态" value={graph} options={BUILD_FILTER_OPTIONS} onChange={(value) => setGraph(value as BuildFilter)} /></div>
          <div className="document-filter-control"><span>RAG</span><ThemedSelect ariaLabel="RAG 状态" value={rag} options={BUILD_FILTER_OPTIONS} onChange={(value) => setRag(value as BuildFilter)} /></div>
        </div>
      </div>
      <div className="build-status-readout"><span>{updatedAt ? `匹配 ${pagination.total} 篇 · 上次读取 ${updatedAt}` : "尚未读取状态"}</span><label><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />每 5 秒自动刷新</label></div>
      <div className="build-status-list" aria-busy={loading}>
        {loading && !updatedAt ? <LoadingState label="正在读取构建状态…" /> : null}
        {!loading && !visible.length ? <div className="empty-compact"><FileText size={26} /><strong>{error ? "暂时无法读取状态" : "没有匹配的文档"}</strong><span>{error ? "请检查服务连接后刷新。" : "可调整文件名和状态筛选，或前往文档管理导入资料。"}</span></div> : null}
        {pageDocuments.map((item) => <article className="build-status-row" key={item.source_id}>
          <span className="document-icon">MD</span><div><strong title={item.relative_path}>{item.relative_path}</strong><small>{item.updated_at ? `更新于 ${new Date(item.updated_at).toLocaleString()}` : "更新时间未知"}</small></div>
          <div className="document-row__statuses"><span className={`status-badge is-${item.graph_status}`}>Graph · {BUILD_STATUS_LABELS[item.graph_status]}</span><span className={`status-badge is-${item.rag_status}`}>RAG · {BUILD_STATUS_LABELS[item.rag_status]}</span></div>
        </article>)}
      </div>
      <footer className="document-pagination"><button type="button" aria-label="上一页" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></button><span>第 {currentPage} / {totalPages} 页<small>每页最多 {PAGE_SIZE} 篇</small></span><button type="button" aria-label="下一页" disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></button></footer>
    </section>
  </section>;
}
