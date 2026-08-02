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
  TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import { loadIngestHistory, type IngestHistoryItem } from "../lib/history";
import type { StatusData } from "../types/api";

const HISTORY_PAGE_SIZE = 6;

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function StatusPage() {
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
        { label: "活动文档", value: status.sources.active, icon: FileText, tone: "amber", note: `共 ${status.sources.total} 条记录` },
        { label: "知识节点", value: status.graph.total_nodes, icon: Network, tone: "cyan", note: `${status.sources.pending_graph} 个待更新来源` },
        { label: "关系边", value: status.graph.total_edges, icon: GitBranch, tone: "purple", note: "有向关系与证据权重" },
        { label: "向量", value: status.rag.total_vectors, icon: Database, tone: "blue", note: `${status.rag.total_chunks} 个文本切片` },
        { label: "群组", value: status.graph.total_groups, icon: Activity, tone: "green", note: "连通知识群摘要" },
      ]
    : [];

  return (
    <section className="status-page page-stack">
      <PageIntro
        title="知识库运行概览"
        description="检查来源状态、图谱规模、向量一致性和最近由当前浏览器触发的整理记录。"
        actions={<button className="button button--secondary" onClick={load} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />刷新状态</button>}
      />
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <div className="card"><LoadingState label="正在检查数据库与 FAISS…" /></div> : null}

      {status ? (
        <>
          <div className="metric-grid">
            {metrics.map(({ label, value, icon: Icon, tone, note }) => (
              <article className={`metric-card card tone-${tone}`} key={label}>
                <span className="metric-icon"><Icon size={20} /></span>
                <div><span>{label}</span><strong>{value.toLocaleString()}</strong><small>{note}</small></div>
              </article>
            ))}
          </div>

          <div className="status-lower-grid">
            <section className="health-panel card">
              <div className="panel-title-row"><div><p className="eyebrow">Health checks</p><h3>服务健康</h3></div></div>
              <div className="health-list">
                <div><span className={`health-icon ${status.initialized ? "is-good" : "is-warn"}`}>{status.initialized ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}</span><span><strong>知识库初始化</strong><small>{status.initialized ? "sources.db 已就绪" : "尚未创建知识库"}</small></span><b>{status.initialized ? "正常" : "待初始化"}</b></div>
                <div><span className={`health-icon ${status.rag.faiss_healthy ? "is-good" : "is-warn"}`}>{status.rag.faiss_healthy ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}</span><span><strong>FAISS 索引</strong><small>索引 ID 与 rag.db 一致性</small></span><b>{status.rag.faiss_healthy ? "健康" : "需检查"}</b></div>
                <div><span className={`health-icon ${status.sources.pending_graph + status.sources.pending_rag === 0 ? "is-good" : "is-warn"}`}><Clock3 size={17} /></span><span><strong>增量整理队列</strong><small>Graph {status.sources.pending_graph} · RAG {status.sources.pending_rag}</small></span><b>{status.sources.pending_graph + status.sources.pending_rag}</b></div>
              </div>
            </section>

            <section className="history-panel card">
              <div className="panel-title-row"><div><p className="eyebrow">Recent ingest</p><h3>最近整理</h3></div><span className="count-chip">{history.length}</span></div>
              <div className="history-list">
                {visibleHistory.map((item) => (
                  <article key={item.id}>
                    <span className={`history-dot ${item.failed ? "is-failed" : ""}`} />
                    <div><strong>{item.failed ? "整理部分失败" : "整理完成"}</strong><small>{formatTime(item.createdAt)}</small></div>
                    <p>处理 {item.processed} · Graph {item.graphUpdated} · RAG {item.ragUpdated}</p>
                  </article>
                ))}
                {!history.length ? <div className="empty-compact"><Clock3 size={25} /><strong>暂无本地记录</strong><span>从文档管理页触发整理后会记录在这里。</span></div> : null}
              </div>
              {history.length ? (
                <nav className="history-pagination" aria-label="最近整理翻页">
                  <button
                    type="button"
                    aria-label="上一页"
                    disabled={historyPage <= 1}
                    onClick={() => setHistoryPage((page) => Math.max(1, page - 1))}
                  ><ChevronLeft size={15} /></button>
                  <span>第 {historyPage} / {historyPageCount} 页<small>每页 6 条</small></span>
                  <button
                    type="button"
                    aria-label="下一页"
                    disabled={historyPage >= historyPageCount}
                    onClick={() => setHistoryPage((page) => Math.min(historyPageCount, page + 1))}
                  ><ChevronRight size={15} /></button>
                </nav>
              ) : null}
            </section>
          </div>
        </>
      ) : null}
    </section>
  );
}
