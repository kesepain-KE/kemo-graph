import {
  Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Circle, Database,
  Eye, FileCode2, FileText, FolderSearch, LoaderCircle, Network, Pencil,
  RefreshCw, RotateCcw, Save, Search, X, XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { MarkdownPreview } from "../components/MarkdownPreview";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import { BUILD_STATUS_LABELS, type BuildFilter, type BuildStatus } from "../lib/buildStatus";
import type { DocumentBuild, DocumentListSummary, DocumentRecord, Pagination, ProcessedDocument } from "../types/api";
import "../styles/build-status.css";

const PAGE_SIZE = 6;
const fileName = (path: string) => path.replace(/\\/g, "/").split("/").pop() || path;
const sourcePath = (item: DocumentRecord) => item.source_path || item.original_path || item.relative_path;
const formatSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;
const formatTime = (time: string) => new Date(time).toLocaleString("zh-CN", { hour12: false });

function PipelineSteps({ label, build, contentHash, status }: { label: string; build?: DocumentBuild; contentHash?: string; status: BuildStatus }) {
  const failed = build?.steps.find((step) => step.status === "failed");
  return <div className="build-pipeline">
    <div className="build-pipeline__label"><strong>{label}</strong>
      {build && (build.content_hash !== contentHash ? <small>上次构建 · 处理稿已更新</small> : status === "pending" ? <small>上次构建 · 待重新构建</small> : null)}
    </div>
    {build?.steps.length ? <ol className="build-steps" aria-label={`${label}构建步骤`}>
      {build.steps.map((step) => <li className={`is-${step.status}`} key={step.step} title={step.started_at ? `开始于 ${formatTime(step.started_at)}` : undefined}>
        {step.status === "completed" ? <CheckCircle2 size={13} /> : step.status === "failed" ? <XCircle size={13} /> : <LoaderCircle size={13} className="spin" />}
        <span>{step.label}</span>
        <span className="sr-only">{step.status === "completed" ? "成功" : step.status === "failed" ? "失败" : "进行中"}</span>
      </li>)}
    </ol> : <p className="build-pipeline__empty">暂无步骤记录</p>}
    {failed && <p className="build-step-error"><strong>中断于「{failed.label}」</strong><span>{failed.error || "构建失败，未返回错误详情"}</span></p>}
  </div>;
}

export function BuildDocumentCard({ item, selected, expanded, disabled, onOpen, onRebuild }: {
  item: DocumentRecord; selected: boolean; expanded: boolean; disabled: boolean;
  onOpen: () => void; onRebuild: () => void;
}) {
  return <article className={`build-document-card ${selected ? "is-selected" : ""}`}>
    <button className="build-document-card__heading" type="button" aria-expanded={expanded}
      aria-controls={`build-steps-${item.source_id}`} onClick={onOpen}>
      <FileText className="build-document-card__icon" size={20} strokeWidth={1.6} />
      <span className="build-document-card__name">
        <strong title={sourcePath(item)}>{fileName(sourcePath(item))}</strong>
        <small title={sourcePath(item)}>{sourcePath(item)}</small>
      </span>
      <span className="build-document-card__statuses">
        <span className={`status-badge is-${item.graph_status}`}>图谱 · {BUILD_STATUS_LABELS[item.graph_status]}</span>
        <span className={`status-badge is-${item.rag_status}`}>RAG · {BUILD_STATUS_LABELS[item.rag_status]}</span>
      </span>
      <ChevronDown size={15} className={expanded ? "is-expanded" : ""} />
    </button>
    {expanded && <div className="build-document-card__body" id={`build-steps-${item.source_id}`}>
      <div className="build-document-card__pipelines">
        <PipelineSteps label="知识图谱" build={item.builds?.graph} contentHash={item.content_hash} status={item.graph_status} />
        <PipelineSteps label="RAG" build={item.builds?.rag} contentHash={item.content_hash} status={item.rag_status} />
      </div>
      <div className="build-document-card__actions">
        {item.source_path ? <Link className="button button--secondary" to={`/documents?source=${encodeURIComponent(item.source_id)}`}>
          <FolderSearch size={14} />定位原文件
        </Link> : <button className="button button--secondary" type="button" disabled title="没有原文件映射，请重新导入原始资料"><FolderSearch size={14} />原文件不可用</button>}
        <button className="button button--secondary" type="button" disabled={disabled} onClick={onRebuild}><RotateCcw size={14} />重新构建此文件</button>
      </div>
    </div>}
  </article>;
}

function ProcessedDocumentPanel({ item, revision, onDirtyChange, onSaved, onRebuild }: {
  item: DocumentRecord; revision: number; onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void; onRebuild: (sourceId: string) => Promise<void>;
}) {
  const [data, setData] = useState<ProcessedDocument | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const dirty = editing && data !== null && draft !== data.content;

  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (editing) return;
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(null);
    void api.getProcessedDocument(item.source_id, controller.signal).then((result) => {
      if (active) { setData(result); setDraft(result.content); }
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "无法读取处理稿");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [item.source_id, item.content_hash, revision, editing]);

  const save = async (rebuild: boolean) => {
    if (!data) return;
    setSaving(true); setError(null); setMessage(null);
    try {
      const saved = await api.updateProcessedDocument(item.source_id, draft, data.content_hash);
      setData(saved); setDraft(saved.content); setEditing(false); onSaved();
      if (rebuild) {
        try {
          await onRebuild(item.source_id);
          setMessage("处理稿已保存，重建任务已提交。原文件未修改。");
        } catch (caught) {
          setError(`处理稿已保存，但重建未启动：${caught instanceof Error ? caught.message : "请求失败"}。可在文档卡片中重新构建。`);
        }
      } else {
        setMessage("处理稿已保存，未启动构建。原文件未修改。");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法保存处理稿");
    } finally { setSaving(false); }
  };

  return <>
    <header className="build-preview__details">
      <div className="build-preview__title"><FileCode2 size={20} strokeWidth={1.5} />
        <div><span>预处理文档</span><h2 title={sourcePath(item)}>{fileName(sourcePath(item))}</h2></div>
        <span className="build-preview__format">MARKDOWN</span>
      </div>
      <dl>
        <div><dt>处理路径</dt><dd title={data?.processed_path}>{data?.processed_path ?? item.relative_path}</dd></div>
        <div><dt>原文件</dt><dd title={sourcePath(item)}>{item.source_path || item.original_path || "暂无原文件映射"}</dd></div>
        <div className="build-preview__file-stats"><dt>文件大小</dt><dd>{data ? formatSize(data.size) : "—"}</dd><dt>修改时间</dt><dd>{data ? formatTime(data.modified_at) : "—"}</dd></div>
      </dl>
    </header>
    <div className="build-preview__toolbar">
      <div className="build-preview__tabs" aria-label="处理稿显示模式">
        <button type="button" aria-pressed={!editing} disabled={saving} onClick={() => {
          if (dirty && !window.confirm("放弃尚未保存的处理稿修改？")) return;
          setEditing(false); setDraft(data?.content ?? "");
        }}><Eye size={15} />预览</button>
        <button type="button" aria-pressed={editing} disabled={!data || loading || saving} onClick={() => { setEditing(true); setMessage(null); }}><Pencil size={14} />编辑</button>
      </div>
      {(saving || editing || loading && data) && <span>{saving ? "正在保存…" : editing ? dirty ? "有未保存修改" : "编辑中" : "正在读取…"}</span>}
    </div>
    {error && <div className="build-preview__notice"><ErrorNotice message={error} /></div>}
    {message && <div className="build-preview__notice"><InfoNotice message={message} /></div>}
    <div className={`build-preview__content ${editing ? "is-editing" : ""}`} aria-busy={loading}>
      {loading && !data ? <LoadingState label="正在读取预处理 Markdown…" /> : editing ? <textarea
        aria-label="预处理 Markdown 编辑器" spellCheck={false} value={draft} disabled={saving}
        onChange={(event) => setDraft(event.target.value)} /> : data?.content ? <MarkdownPreview content={data.content} /> :
          <div className="build-empty"><FileCode2 size={30} strokeWidth={1.4} /><strong>{error ? "无法预览处理稿" : "处理稿内容为空"}</strong><span>{error ? "请检查对应文件后刷新。" : "可以切换编辑模式补充内容。"}</span></div>}
    </div>
    {editing ? <footer className="build-editor-footer">
      <span>修改仅保存到处理目录</span>
      <div><button type="button" className="button button--secondary" disabled={saving} onClick={() => void save(false)}><Check size={15} />确认保存，不构建</button>
        <button type="button" className="button button--primary" disabled={saving} onClick={() => void save(true)}>
          {saving ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}保存并重新构建
        </button></div>
    </footer> : <footer className="build-preview__footer"><Circle size={7} fill="currentColor" />不影响原文件</footer>}
  </>;
}

export function BuildStatusPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSource = searchParams.get("source");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, page_size: PAGE_SIZE, total: 0, total_pages: 0 });
  const [summary, setSummary] = useState<DocumentListSummary | null>(null);
  const [query, setQuery] = useState("");
  const [graph, setGraph] = useState<BuildFilter>("all");
  const [rag, setRag] = useState<BuildFilter>("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [revision, setRevision] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [selected, setSelected] = useState<DocumentRecord | null>(null);
  const [expandedId, setExpandedId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const { activeTasks, refreshServerTasks } = useRuntimeTasks();
  const building = submitting || activeTasks.some((task) => ["ingest", "rebuild_all", "rebuild_knowledge_base", "organize_graph"].includes(task.kind));
  const refresh = () => setRevision((value) => value + 1);

  useEffect(() => {
    if (!requestedSource) return;
    const controller = new AbortController();
    void api.getProcessedDocument(requestedSource, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setSelected(data); setQuery(sourcePath(data)); setPage(1);
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "无法定位处理稿");
    });
    return () => controller.abort();
  }, [requestedSource]);

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
            setDocuments(response.documents); setPagination(response.pagination); setSummary(response.summary ?? null);
            const totalPages = Math.max(1, response.pagination.total_pages);
            if (page > totalPages) setPage(totalPages);
            setSelected((current) => response.documents.find((item) => item.source_id === current?.source_id) ?? (dirty ? current : response.documents[0] ?? null));
            setUpdatedAt(new Date().toLocaleTimeString()); setError(null);
          }
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "无法读取构建状态");
        } finally { if (active) setLoading(false); }
      }
      if (active && autoRefresh) timer = setTimeout(() => void load(), 5000);
    };
    void load();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [autoRefresh, revision, page, query, graph, rag, dirty]);

  const rebuildDocument = async (sourceId: string) => {
    await api.rebuildDocument(sourceId);
    await refreshServerTasks();
    refresh();
  };
  const runBuild = async (sourceId?: string) => {
    setSubmitting(true); setError(null); setNotice(null);
    try {
      if (sourceId) await rebuildDocument(sourceId);
      else { await api.retryFailedBuilds(); await refreshServerTasks(); refresh(); }
      setNotice(sourceId ? "已提交此文件的图谱与 RAG 重建任务。" : "已提交失败项重建任务，仅重试失败的构建链路。");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "无法提交重建任务"); }
    finally { setSubmitting(false); }
  };
  const totalPages = Math.max(1, pagination.total_pages);
  const currentPage = Math.min(page, totalPages);
  const hasFailures = summary && (summary.graph.failed > 0 || summary.rag.failed > 0);

  return <section className="build-status-page">
    <header className="build-overview">
      <div className="build-overview__intro"><h1>构建状态</h1></div>
      <div className="build-overview__pipelines">
        {([{ key: "rag", title: "RAG", subtitle: "向量索引", icon: Database }, { key: "graph", title: "知识图谱", subtitle: "Graph", icon: Network }] as const).map(({ key, title, subtitle, icon: Icon }) => {
          const filter = key === "rag" ? rag : graph;
          const setFilter = key === "rag" ? setRag : setGraph;
          return <section className="build-overview__pipeline" key={key} aria-label={`${title}构建概览`}>
            <h2><Icon size={14} /><strong>{title}</strong><small>{subtitle}</small></h2>
            <div className="build-overview__counts">{Object.entries(BUILD_STATUS_LABELS).map(([status, label]) => <button
              className={`is-${status}`} type="button" key={status} aria-label={`筛选 ${title}：${label}`} aria-pressed={filter === status}
              onClick={() => { setFilter(filter === status ? "all" : status as BuildStatus); setPage(1); }}>
              <strong>{summary ? summary[key][status as BuildStatus] : "—"}</strong><span>{label}</span>
            </button>)}</div>
          </section>;
        })}
      </div>
      <div className="build-overview__actions">
        <button className="button button--secondary" type="button" disabled={building || !hasFailures} onClick={() => void runBuild()} title="只重试失败的 Graph / RAG 链路"><RotateCcw size={15} />重新构建失败项</button>
        <button className="button button--secondary" type="button" aria-label="刷新构建状态" title="刷新" disabled={loading} onClick={refresh}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新构建状态</button>
      </div>
    </header>
    {error && <ErrorNotice message={error} />}
    {notice && <div className="build-status-notice"><InfoNotice message={notice} /><button className="icon-button" type="button" aria-label="关闭提示" onClick={() => setNotice(null)}><X size={14} /></button></div>}
    <div className="build-workspace">
      <section className="build-queue" aria-label="文档构建状态列表">
        <header className="build-queue__header"><div><h2>文档队列</h2><span>{updatedAt ? `${pagination.total} 篇文档` : "正在同步…"}</span></div>
          <label className="build-queue__search search-field"><Search size={15} /><input aria-label="搜索构建文档" placeholder="搜索文档或路径…" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} /></label>
          {(graph !== "all" || rag !== "all") && <div className="build-queue__filters">
            {graph !== "all" && <button type="button" onClick={() => { setGraph("all"); setPage(1); }}>图谱 · {BUILD_STATUS_LABELS[graph]}<X size={12} /></button>}
            {rag !== "all" && <button type="button" onClick={() => { setRag("all"); setPage(1); }}>RAG · {BUILD_STATUS_LABELS[rag]}<X size={12} /></button>}
          </div>}
        </header>
        <div className="build-status-list" aria-busy={loading}>
          {loading && !updatedAt ? <LoadingState label="正在读取构建状态…" /> : null}
          {!loading && !documents.length ? <div className="build-empty"><FileText size={30} strokeWidth={1.4} /><strong>{error ? "暂时无法读取状态" : "没有匹配的文档"}</strong><span>{error ? "请检查连接后刷新。" : "调整搜索条件，或在知识库添加资料。"}</span></div> : null}
          {documents.map((item) => <BuildDocumentCard key={item.source_id} item={item} selected={selected?.source_id === item.source_id}
            expanded={expandedId === item.source_id} disabled={building || (dirty && selected?.source_id === item.source_id)}
            onOpen={() => {
              if (selected?.source_id !== item.source_id && dirty && !window.confirm("放弃尚未保存的处理稿修改并切换文档？")) return;
              setSelected(item); setExpandedId((current) => current === item.source_id ? "" : item.source_id);
              if (requestedSource) setSearchParams({}, { replace: true });
            }}
            onRebuild={() => void runBuild(item.source_id)} />)}
        </div>
        <footer className="build-queue__footer">
          <div className="build-queue__sync"><label><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />每 5 秒自动刷新</label><span>{updatedAt ? `更新于 ${updatedAt}` : "尚未读取状态"}</span></div>
          <div className="build-pagination"><button type="button" aria-label="上一页" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></button>
            <span>{currentPage} / {totalPages}<small>每页最多 {PAGE_SIZE} 篇</small></span>
            <button type="button" aria-label="下一页" disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></button></div>
        </footer>
      </section>
      <section className="build-preview" aria-label="预处理文档详情">
        {selected ? <ProcessedDocumentPanel key={selected.source_id} item={selected} revision={revision} onDirtyChange={setDirty} onSaved={refresh} onRebuild={rebuildDocument} /> :
          <div className="build-empty"><FileCode2 size={36} strokeWidth={1.3} /><strong>选择文档预览</strong><small>预处理稿 · 不影响原文件</small></div>}
      </section>
    </div>
  </section>;
}
