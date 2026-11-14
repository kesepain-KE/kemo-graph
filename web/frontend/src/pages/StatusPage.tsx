import {
  Activity,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  GitBranch,
  Network,
  RefreshCw,
  ScrollText,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import { RuntimeLogPanel } from "../components/RuntimeLogPanel";
import { loadIngestHistory, type IngestHistoryItem } from "../lib/history";
import type { StatusData } from "../types/api";

const HISTORY_PAGE_SIZE = 6;
const sections = [
  { id: "status", label: "状态", description: "规模、健康与整理记录", icon: Activity },
  { id: "logs", label: "日志", description: "终端、查询与内部运行", icon: ScrollText },
] as const;

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function StatusPage() {
  const [activeSection, setActiveSection] = useState<"status" | "logs">("status");
  const [status, setStatus] = useState<StatusData | null>(null);
  const [history, setHistory] = useState<IngestHistoryItem[]>(loadIngestHistory());
  const [historyPage, setHistoryPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await api.status());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取状态");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const updateHistory = () => {
      setHistory(loadIngestHistory());
      setHistoryPage(1);
    };
    window.addEventListener("kemo:history-updated", updateHistory);
    return () => window.removeEventListener("kemo:history-updated", updateHistory);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const historyPageCount = Math.max(1, Math.ceil(history.length / HISTORY_PAGE_SIZE));
  const visibleHistory = history.slice(
    (historyPage - 1) * HISTORY_PAGE_SIZE,
    historyPage * HISTORY_PAGE_SIZE,
  );

  useEffect(() => {
    if (historyPage > historyPageCount) setHistoryPage(historyPageCount);
  }, [historyPage, historyPageCount]);

  const metrics = status
    ? [
        { label: "活动文档", value: status.sources.active, icon: FileText, note: `共 ${status.sources.total} 条来源记录` },
        { label: "知识节点", value: status.graph.total_nodes, icon: Network, note: `${status.sources.pending_graph} 个待更新来源` },
        { label: "关系边", value: status.graph.total_edges, icon: GitBranch },
        { label: "向量", value: status.rag.total_vectors, icon: Database, note: `${status.rag.total_chunks} 个文本切片` },
        { label: "群组", value: status.graph.total_groups, icon: Activity },
      ]
    : [];

  return (
    <section className="status-page page-stack">
      <PageIntro title="运行状态" />
      <div className="status-layout">
        <aside className="status-nav card" aria-label="运行状态分类">
          <header><strong>运行监控</strong></header>
          <div className="status-nav__list" role="tablist" aria-label="运行状态分类" aria-orientation="vertical">
            {sections.map(({ id, label, description, icon: Icon }, index) => (
              <button type="button" role="tab" key={id} id={`status-tab-${id}`}
                className="status-nav__item" aria-label={label} aria-selected={activeSection === id}
                aria-controls={`status-panel-${id}`} tabIndex={activeSection === id ? 0 : -1}
                onClick={() => setActiveSection(id)}
                onKeyDown={(event) => {
                  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1
                    : (index + (event.key === "ArrowUp" ? -1 : 1) + sections.length) % sections.length;
                  setActiveSection(sections[next].id);
                  document.getElementById(`status-tab-${sections[next].id}`)?.focus();
                }}>
                <Icon size={18} aria-hidden="true" /><span><strong>{label}</strong><small>{description}</small></span>
              </button>
            ))}
          </div>
        </aside>
        <div className="status-content">
          <section className="status-panel" role="tabpanel" id="status-panel-status"
            aria-labelledby="status-tab-status" hidden={activeSection !== "status"} tabIndex={0}>
            <header className="status-panel__header">
              <div><h2>状态概览</h2></div>
              <button type="button" className="button button--secondary" onClick={load} disabled={loading}>
                <RefreshCw className={loading ? "spin" : ""} size={16} />刷新状态
              </button>
            </header>
            {error ? <ErrorNotice message={error} /> : null}
            {loading && !status ? <div className="card"><LoadingState label="正在检查服务…" /></div> : null}
            {status ? <div className="status-overview" aria-busy={loading}>
              <section className="status-metrics card" aria-label="知识库规模">
                {metrics.map(({ label, value, icon: Icon, note }) => (
                  <article className="status-metric" key={label}>
                    <div className="status-metric__label"><span>{label}</span><Icon size={17} aria-hidden="true" /></div>
                    <strong>{value.toLocaleString()}</strong>
                    {note && <small>{note}</small>}
                  </article>
                ))}
              </section>
              <div className="status-details">
                <section className="status-health card" aria-labelledby="status-health-title">
                  <h2 id="status-health-title">服务健康</h2>
                  <div className="status-health-list">
                    <div className="status-health-item">
                      <span className={`status-health-icon ${status.initialized ? "is-good" : "is-warn"}`}>
                        {status.initialized ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}
                      </span>
                      <div>
                        <div className="status-health-item__title"><strong>知识库初始化</strong><span className={status.initialized ? "is-good" : "is-warn"}>{status.initialized ? "正常" : "待初始化"}</span></div>
                        {!status.initialized && <small>尚未创建知识库</small>}
                      </div>
                    </div>
                    <div className="status-health-item">
                      <span className={`status-health-icon ${status.rag.faiss_healthy ? "is-good" : "is-warn"}`}>
                        {status.rag.faiss_healthy ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}
                      </span>
                      <div>
                        <div className="status-health-item__title"><strong title="FAISS 向量索引">向量索引</strong><span className={status.rag.faiss_healthy ? "is-good" : "is-warn"}>{status.rag.faiss_healthy ? "健康" : "需检查"}</span></div>
                      </div>
                    </div>
                    <div className="status-health-item">
                      <span className={`status-health-icon ${status.sources.pending_graph + status.sources.pending_rag === 0 ? "is-good" : "is-warn"}`}><Clock3 size={17} /></span>
                      <div>
                        <div className="status-health-item__title"><strong>增量整理队列</strong><span className={status.sources.pending_graph + status.sources.pending_rag === 0 ? "is-good" : "is-warn"}>{status.sources.pending_graph + status.sources.pending_rag} 项</span></div>
                        <small>图谱 {status.sources.pending_graph} · RAG {status.sources.pending_rag}</small>
                      </div>
                    </div>
                  </div>
                </section>
                <section className="status-history card" aria-labelledby="status-history-title">
                  <header className="status-history__header">
                    <div><h2 id="status-history-title">最近整理</h2><p>本机记录</p></div>
                    <span>共 {history.length} 条</span>
                  </header>
                  {history.length ? (
                    <>
                      <div className="status-history__table">
                        <table aria-label="最近整理记录">
                          <thead>
                            <tr><th scope="col">整理结果</th><th scope="col">时间</th><th scope="col">处理文档</th><th scope="col">图谱更新</th><th scope="col">RAG 更新</th><th scope="col">失败</th></tr>
                          </thead>
                          <tbody>
                            {visibleHistory.map((item) => (
                              <tr key={item.id}>
                                <td className="status-history__result">
                                  <span className={item.failed ? "is-failed" : "is-completed"}>
                                    {item.failed ? <TriangleAlert size={14} /> : <CheckCircle2 size={14} />}
                                    {item.failed ? "部分失败" : "整理完成"}
                                  </span>
                                </td>
                                <td className="status-history__time"><time dateTime={item.createdAt}>{formatTime(item.createdAt)}</time></td>
                                <td data-label="处理文档">{item.processed}</td>
                                <td data-label="图谱更新">{item.graphUpdated}</td>
                                <td data-label="RAG 更新">{item.ragUpdated}</td>
                                <td data-label="失败" className={item.failed ? "text-danger" : ""}>{item.failed}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <nav className="status-history-pagination" aria-label="最近整理翻页">
                        <span>第 {historyPage} / {historyPageCount} 页<small>每页 {HISTORY_PAGE_SIZE} 条</small></span>
                        <div>
                          <button
                            type="button"
                            aria-label="上一页"
                            disabled={historyPage <= 1}
                            onClick={() => setHistoryPage((page) => Math.max(1, page - 1))}
                          ><ChevronLeft size={15} /></button>
                          <button
                            type="button"
                            aria-label="下一页"
                            disabled={historyPage >= historyPageCount}
                            onClick={() => setHistoryPage((page) => Math.min(historyPageCount, page + 1))}
                          ><ChevronRight size={15} /></button>
                        </div>
                      </nav>
                    </>
                  ) : <div className="empty-compact"><Clock3 size={25} /><strong>暂无本地记录</strong></div>}
                </section>
              </div>
            </div> : null}
          </section>
          <section className="status-panel status-panel--logs" role="tabpanel" id="status-panel-logs"
            aria-labelledby="status-tab-logs" hidden={activeSection !== "logs"} tabIndex={0}>
            <RuntimeLogPanel active={activeSection === "logs"} />
          </section>
        </div>
      </div>
    </section>
  );
}
