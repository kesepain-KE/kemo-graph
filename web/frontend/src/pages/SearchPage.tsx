import {
  ArrowRight,
  Bot,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Code2,
  FileText,
  GitFork,
  Globe2,
  History,
  Network,
  PanelRightOpen,
  RefreshCw,
  Search,
  Sparkles,
  TextSearch,
  Trash2,
  X,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import type {
  AnswerQueryData,
  GraphNode,
  GraphEdge,
  GraphPath,
  GraphQueryData,
  GlobalQueryData,
  HybridQueryData,
  RagQueryData,
  SearchCacheItem,
  SearchMode,
} from "../types/api";

type SearchResult =
  | AnswerQueryData
  | GraphQueryData
  | RagQueryData
  | HybridQueryData
  | GlobalQueryData;

type ResultDisplayMode = "markdown" | "source";

const SearchMarkdownContent = lazy(() =>
  import("../components/SearchMarkdownContent").then((module) => ({
    default: module.SearchMarkdownContent,
  })),
);

const modes: Array<{ value: SearchMode; label: string; icon: typeof Network }> = [
  { value: "answer", label: "LLM 回答", icon: Bot },
  { value: "graph", label: "图谱", icon: Network },
  { value: "rag", label: "向量", icon: TextSearch },
  { value: "hybrid", label: "混合", icon: Sparkles },
  { value: "global", label: "全局", icon: Globe2 },
];

const modeLabels: Record<SearchMode, string> = {
  answer: "LLM 回答",
  graph: "图谱",
  rag: "向量",
  hybrid: "混合",
  global: "全局",
};

const suggestions = ["知识图谱如何增强 RAG", "向量检索", "实体关系抽取"];
const granularityLabels = {
  small: "小粒度",
  medium: "中粒度",
  large: "大粒度",
} as const;
const GRAPH_RESULT_PAGE_SIZE = 6;

function SearchResultText({
  content,
  displayMode,
  compact = false,
}: {
  content: string;
  displayMode: ResultDisplayMode;
  compact?: boolean;
}) {
  if (displayMode === "source") {
    return <pre className={`search-result-source ${compact ? "is-compact" : ""}`}>{content}</pre>;
  }
  return (
    <div className={`search-result-markdown ${compact ? "is-compact" : ""}`}>
      <Suspense fallback={<pre className="search-result-source">{content}</pre>}>
        <SearchMarkdownContent content={content} />
      </Suspense>
    </div>
  );
}

function RelationChain({
  nodeIds,
  edgeIds,
  nodes,
  edges,
  onNode,
}: {
  nodeIds: string[];
  edgeIds: string[];
  nodes: Map<string, GraphNode>;
  edges: Map<string, GraphEdge>;
  onNode: (node: GraphNode) => void;
}) {
  return (
    <div className="relation-chain">
      {nodeIds.map((nodeId, index) => {
        const node = nodes.get(nodeId);
        const edge = index > 0 ? edges.get(edgeIds[index - 1]) : null;
        const previousId = index > 0 ? nodeIds[index - 1] : null;
        const isForward = edge
          ? edge.source_node_id === previousId && edge.target_node_id === nodeId
          : true;
        return (
          <span className="relation-chain__segment" key={`${nodeId}-${index}`}>
            {edge ? (
              <span className="relation-chain__edge" title={`权重 ${Math.round(edge.weight * 100)}%`}>
                {isForward ? "→" : "←"}<em>{edge.relation}</em>{isForward ? "→" : "←"}
              </span>
            ) : null}
            <button
              type="button"
              className="relation-chain__node"
              disabled={!node}
              onClick={() => node && onNode(node)}
            >
              {node?.keyword ?? nodeId}
            </button>
          </span>
        );
      })}
    </div>
  );
}

function RelationshipResults({
  data,
  nodes,
  onNode,
}: {
  data: GraphQueryData;
  nodes: GraphNode[];
  onNode: (node: GraphNode) => void;
}) {
  const nodeMap = new Map(nodes.map((node) => [node.node_id, node]));
  const edgeMap = new Map(data.edges.map((edge) => [edge.edge_id, edge]));
  const paths: GraphPath[] = data.paths ?? [];
  const relations = data.relations ?? [];
  if (!paths.length && !relations.length) return null;
  return (
    <section className="relationship-results">
      <header><GitFork size={15} /><strong>关系路径</strong><span>{paths.length || relations.length} 条</span></header>
      <div className="relationship-results__list">
        {paths.map((path) => (
          <article className="relation-chain-card" key={path.edge_ids.join("/")} title={path.text}>
            <RelationChain nodeIds={path.node_ids} edgeIds={path.edge_ids} nodes={nodeMap} edges={edgeMap} onNode={onNode} />
            <small>{path.depth} 跳 · 链路权重 {Math.round(path.weight * 100)}%</small>
          </article>
        ))}
        {!paths.length
          ? relations.map((relation) => (
              <article className="relation-chain-card" key={relation.edge_id} title={relation.text}>
                <RelationChain
                  nodeIds={[relation.source_node_id, relation.target_node_id]}
                  edgeIds={[relation.edge_id]}
                  nodes={nodeMap}
                  edges={edgeMap}
                  onNode={onNode}
                />
                <small>关系权重 {Math.round(relation.weight * 100)}%</small>
              </article>
            ))
          : null}
      </div>
    </section>
  );
}

function GraphResults({
  data,
  onNode,
  displayMode,
}: {
  data: GraphQueryData;
  onNode: (node: GraphNode) => void;
  displayMode: ResultDisplayMode;
}) {
  const nodes = [...data.hit_nodes, ...data.expanded_nodes];
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(nodes.length / GRAPH_RESULT_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * GRAPH_RESULT_PAGE_SIZE;
  const visibleNodes = nodes.slice(pageStart, pageStart + GRAPH_RESULT_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [data.query]);

  return (
    <section className="result-column result-column--graph">
      <div className="result-column__heading">
        <span className="result-icon result-icon--graph"><Network size={17} /></span>
        <div><h3>图谱结果</h3><p>{nodes.length} 个节点 · {data.edges.length} 条关系</p></div>
      </div>
      <RelationshipResults data={data} nodes={nodes} onNode={onNode} />
      <div className="node-result-grid" aria-live="polite">
        {visibleNodes.map((node) => (
          <button className="node-result-card" key={`${node.node_id}-${node.depth ?? 0}`} onClick={() => onNode(node)}>
            <span className="node-result-card__dot" />
            <span>
              <strong>{node.keyword}</strong>
              <small>{node.depth ? `第 ${node.depth} 层邻居` : `匹配度 ${Math.round((node.match_score ?? 1) * 100)}%`}</small>
              <div className="node-result-card__summary">
                <SearchResultText content={node.summary} displayMode={displayMode} compact />
              </div>
            </span>
            <ArrowRight size={15} />
          </button>
        ))}
        {!nodes.length ? <div className="empty-result">没有命中图谱节点</div> : null}
      </div>
      {nodes.length > GRAPH_RESULT_PAGE_SIZE ? (
        <footer className="graph-result-pagination" aria-label="图谱检索结果分页">
          <button
            type="button"
            aria-label="上一页图谱结果"
            disabled={currentPage <= 1}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            第 {currentPage} / {pageCount} 页
            <small>每页最多 {GRAPH_RESULT_PAGE_SIZE} 个节点</small>
          </span>
          <button
            type="button"
            aria-label="下一页图谱结果"
            disabled={currentPage >= pageCount}
            onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
          >
            <ChevronRight size={16} />
          </button>
        </footer>
      ) : null}
      {data.groups.length ? (
        <div className="group-summary">
          <span>群组摘要</span>
          {data.groups.map((group) => (
            <SearchResultText key={group.group_id} content={group.summary} displayMode={displayMode} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function RagResults({ data, displayMode }: { data: RagQueryData; displayMode: ResultDisplayMode }) {
  return (
    <section className="result-column result-column--rag">
      <div className="result-column__heading">
        <span className="result-icon result-icon--rag"><BookOpen size={17} /></span>
        <div><h3>RAG 片段</h3><p>{data.results.length} 条语义召回</p></div>
      </div>
      <div className="rag-result-list">
        {data.results.map((result, index) => {
          const contextContent = result.context?.content?.trim();
          const matchedContent = result.content.trim();
          const hasParentContext = Boolean(
            contextContent && contextContent !== matchedContent,
          );
          return (
          <article className="rag-result-card" key={result.chunk_id}>
            <div className="rag-result-card__meta">
              <span>
                #{String(index + 1).padStart(2, "0")}
                {result.granularity ? ` · ${granularityLabels[result.granularity]}` : ""}
                {hasParentContext && result.context
                  ? ` · 展开为${granularityLabels[result.context.granularity]}上下文`
                  : ""}
              </span>
              <strong>{Math.round(result.score * 100)}%</strong>
            </div>
            <div className="rag-result-card__content">
              <SearchResultText
                content={contextContent || matchedContent}
                displayMode={displayMode}
              />
            </div>
            {hasParentContext ? (
              <details className="rag-result-card__match">
                <summary>查看实际命中的精确片段</summary>
                <SearchResultText content={matchedContent} displayMode={displayMode} />
              </details>
            ) : null}
            <footer>
              <FileBadge />
              <span>{result.source.relative_path ?? result.source.source_id}</span>
            </footer>
          </article>
          );
        })}
        {!data.results.length ? <div className="empty-result">没有超过阈值的文档片段</div> : null}
      </div>
    </section>
  );
}

function FileBadge() {
  return <span className="mini-file-badge">MD</span>;
}

function GlobalResults({ data, displayMode }: { data: GlobalQueryData; displayMode: ResultDisplayMode }) {
  return (
    <section className="result-column global-result">
      <div className="result-column__heading">
        <span className="result-icon result-icon--global"><Globe2 size={17} /></span>
        <div><h3>全局回答</h3><p>{data.communities.length} 个相关节点群</p></div>
      </div>
      <div className={`global-result__answer is-${displayMode}`}>
        <SearchResultText content={data.answer} displayMode={displayMode} />
      </div>
      {data.communities.length ? (
        <div className="global-result__groups">
          {data.communities.map((group) => (
            <article key={group.group_id}>
              <strong>{group.group_id}</strong>
              <SearchResultText content={group.summary} displayMode={displayMode} />
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function AnswerResults({
  data,
  onNode,
  displayMode,
}: {
  data: AnswerQueryData;
  onNode: (node: GraphNode) => void;
  displayMode: ResultDisplayMode;
}) {
  return (
    <div className="answer-results">
      <section className="result-column answer-result">
        <div className="result-column__heading">
          <span className="result-icon result-icon--answer"><Bot size={18} /></span>
          <div>
            <h3>LLM 回答</h3>
            <p>基于图谱结构与向量片段的混合知识上下文</p>
          </div>
          <span className="answer-result__badge"><Sparkles size={13} />混合增强</span>
        </div>
        <div className={`answer-result__body is-${displayMode}`}>
          <SearchResultText content={data.answer} displayMode={displayMode} />
        </div>
      </section>

      <section className="answer-evidence" aria-label="回答依据">
        <header>
          <span>回答依据</span>
          <small>以下内容是本次回答实际使用的检索结果</small>
        </header>
        <div className="answer-evidence__grid">
          <GraphResults data={data.retrieval.graph} onNode={onNode} displayMode={displayMode} />
          <RagResults data={data.retrieval.rag} displayMode={displayMode} />
        </div>
      </section>
    </div>
  );
}

function formatHistoryTime(value: string | null): string {
  if (!value) return "尚未重复命中";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function SearchPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("answer");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyItems, setHistoryItems] = useState<SearchCacheItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyAction, setHistoryAction] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyNotice, setHistoryNotice] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [resultDisplayMode, setResultDisplayMode] = useState<ResultDisplayMode>("markdown");

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const data = await api.getSearchCache(1, 30);
      setHistoryItems(data.items);
      setHistoryTotal(data.total);
    } catch (caught) {
      setHistoryError(caught instanceof Error ? caught.message : "无法读取搜索历史");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    if (!historyOpen) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHistoryOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [historyOpen]);

  const runSearch = async (event?: FormEvent, force = false) => {
    event?.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setHistoryNotice(null);
    try {
      const data =
        mode === "answer"
          ? await api.queryAnswer(query.trim(), {
              graph_depth: 3,
              rag_top_k: 10,
              force,
            })
          : mode === "graph"
          ? await api.queryGraph(query.trim(), { depth: 3, direction: "both", force })
          : mode === "rag"
            ? await api.queryRag(query.trim(), { top_k: 10, force })
            : mode === "global"
              ? await api.queryGlobal(query.trim(), { top_k: 5, force })
              : await api.queryHybrid(query.trim(), {
                  graph_depth: 3,
                  rag_top_k: 10,
                  force,
                });
      setResult(data);
      setHistoryNotice(force ? "已强制刷新结果并更新服务端缓存。" : null);
      await loadHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const restoreCachedResult = async (item: SearchCacheItem) => {
    setHistoryAction(item.cache_key);
    setHistoryError(null);
    setHistoryNotice(null);
    try {
      const detail = await api.getSearchCacheDetail(item.cache_key);
      setQuery(detail.query);
      setMode(detail.query_mode);
      setResult(detail.result);
      setError(null);
      setHistoryOpen(false);
      setHistoryNotice(
        detail.is_stale
          ? "已加载过期历史快照；重新检索可获得当前知识库结果。"
          : "已从服务端缓存加载历史结果。",
      );
    } catch (caught) {
      setHistoryError(caught instanceof Error ? caught.message : "无法加载缓存详情");
    } finally {
      setHistoryAction(null);
    }
  };

  const clearHistory = async (staleOnly: boolean) => {
    if (!staleOnly && !window.confirm("确认清空全部搜索缓存和历史结果？")) return;
    setHistoryAction(staleOnly ? "clear-stale" : "clear-all");
    setHistoryError(null);
    setHistoryNotice(null);
    try {
      const cleared = await api.clearSearchCache(staleOnly);
      setHistoryNotice(
        staleOnly
          ? `已清理 ${cleared.deleted} 条过期缓存。`
          : `已清空 ${cleared.deleted} 条搜索缓存。`,
      );
      await loadHistory();
    } catch (caught) {
      setHistoryError(caught instanceof Error ? caught.message : "无法清理搜索缓存");
    } finally {
      setHistoryAction(null);
    }
  };

  const graphData =
    result && mode === "graph"
      ? (result as GraphQueryData)
      : result && mode === "hybrid"
        ? (result as HybridQueryData).graph
        : null;
  const ragData =
    result && mode === "rag"
      ? (result as RagQueryData)
      : result && mode === "hybrid"
        ? (result as HybridQueryData).rag
        : null;
  const globalData = result && mode === "global" ? (result as GlobalQueryData) : null;
  const answerData = result && mode === "answer" ? (result as AnswerQueryData) : null;

  return (
    <section className="search-page page-stack">
      <div className="search-hero">
        <div className="search-orbit" aria-hidden="true"><Sparkles size={22} /></div>
        <p className="eyebrow">Local-first knowledge retrieval</p>
        <h2>从你的知识网络中找到答案</h2>
        <p>图谱理解结构，向量召回语义；混合模式会让两者互相增强。</p>

        <form className="hero-search" onSubmit={(event) => void runSearch(event)}>
          <Search size={21} />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入问题、概念或关系…"
          />
          <button
            className="hero-search__refresh"
            disabled={loading || !query.trim()}
            onClick={() => void runSearch(undefined, true)}
            title="跳过已有缓存并重新执行检索"
            type="button"
          >
            <RefreshCw size={16} />刷新
          </button>
          <button className="hero-search__submit" disabled={loading || !query.trim()} type="submit">
            {loading ? "检索中" : "开始检索"}<ArrowRight size={17} />
          </button>
        </form>

        <div className="search-mode-switch" role="tablist" aria-label="检索模式">
          {modes.map(({ value, label, icon: Icon }) => (
            <button
              type="button"
              role="tab"
              aria-selected={mode === value}
              className={mode === value ? "is-active" : ""}
              key={value}
              onClick={() => {
                setMode(value);
                setResult(null);
              }}
            >
              <Icon size={16} />{label}
            </button>
          ))}
        </div>

        <div className="search-suggestions">
          <span>试试：</span>
          {suggestions.map((suggestion) => (
            <button key={suggestion} onClick={() => setQuery(suggestion)}>{suggestion}</button>
          ))}
        </div>
      </div>

      <button
        className="search-history-trigger"
        type="button"
        aria-label={`搜索历史，${historyTotal} 条缓存`}
        aria-expanded={historyOpen}
        aria-controls="search-history-panel"
        onClick={() => setHistoryOpen(true)}
      >
        <PanelRightOpen size={17} />
        <span>搜索历史</span>
        <em>{historyTotal}</em>
      </button>

      <div className="search-workspace">
        <div className="search-primary">
          {error ? <ErrorNotice message={error} /> : null}
          {loading ? <div className="search-loading card"><LoadingState label="正在执行召回、重排与阈值过滤…" /></div> : null}

          {result ? (
            <div className="search-results-frame">
              <div className="search-result-view-bar" aria-label="结果显示方式">
                <span>内容显示</span>
                <div className="search-result-view-switch" role="group" aria-label="Markdown 或原文显示">
                  <button
                    className={resultDisplayMode === "markdown" ? "is-active" : ""}
                    type="button"
                    aria-pressed={resultDisplayMode === "markdown"}
                    onClick={() => setResultDisplayMode("markdown")}
                  >
                    <FileText size={14} />Markdown
                  </button>
                  <button
                    className={resultDisplayMode === "source" ? "is-active" : ""}
                    type="button"
                    aria-pressed={resultDisplayMode === "source"}
                    onClick={() => setResultDisplayMode("source")}
                  >
                    <Code2 size={14} />原文
                  </button>
                </div>
              </div>
              <div className={`search-results ${mode === "hybrid" ? "is-hybrid" : ""} ${mode === "answer" ? "is-answer" : ""}`}>
                {answerData ? (
                  <AnswerResults
                    data={answerData}
                    displayMode={resultDisplayMode}
                    onNode={(node) => navigate(`/graph?node=${encodeURIComponent(node.node_id)}`)}
                  />
                ) : null}
                {graphData ? (
                  <GraphResults
                    data={graphData}
                    displayMode={resultDisplayMode}
                    onNode={(node) => navigate(`/graph?node=${encodeURIComponent(node.node_id)}`)}
                  />
                ) : null}
                {ragData ? <RagResults data={ragData} displayMode={resultDisplayMode} /> : null}
                {globalData ? <GlobalResults data={globalData} displayMode={resultDisplayMode} /> : null}
              </div>
            </div>
          ) : null}

          {!result && !loading && !error ? (
            <div className="search-placeholder">
              <img
                className="search-placeholder__logo"
                src="/kemo-graph-logo.png"
                alt=""
                aria-hidden="true"
              />
              <h3>一次查询，多种知识视角</h3>
              <p>服务端缓存会保留成功结果，切换页面后仍可从右侧历史抽屉恢复。</p>
            </div>
          ) : null}
        </div>
      </div>

      {historyOpen ? (
        <div className="search-history-drawer">
          <button
            className="search-history-drawer__backdrop"
            type="button"
            aria-label="关闭搜索历史"
            onClick={() => setHistoryOpen(false)}
          />
          <aside
            className="search-history search-history-drawer__panel card"
            id="search-history-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="search-history-title"
          >
          <header className="search-history__header">
            <span className="search-history__icon"><History size={18} /></span>
            <span><strong id="search-history-title">搜索历史</strong><small>{historyTotal} 条服务端缓存</small></span>
            <button
              disabled={historyLoading}
              onClick={() => void loadHistory()}
              title="刷新历史"
              type="button"
            >
              <RefreshCw className={historyLoading ? "spin" : ""} size={15} />
            </button>
            <button
              onClick={() => setHistoryOpen(false)}
              title="关闭历史"
              type="button"
            >
              <X size={16} />
            </button>
          </header>

          <div className="search-history__actions">
            <button
              disabled={Boolean(historyAction) || !historyItems.some((item) => item.is_stale)}
              onClick={() => void clearHistory(true)}
              type="button"
            >
              <Trash2 size={14} />清理过期
            </button>
            <button
              className="is-danger"
              disabled={Boolean(historyAction) || historyTotal === 0}
              onClick={() => void clearHistory(false)}
              type="button"
            >
              清空全部
            </button>
          </div>

          {historyNotice ? <p className="search-history__notice">{historyNotice}</p> : null}
          {historyError ? <p className="search-history__error">{historyError}</p> : null}
          {historyLoading && !historyItems.length ? <LoadingState label="正在读取搜索历史…" /> : null}

          <div className="search-history__list">
            {historyItems.map((item) => (
              <button
                className={`search-history__item ${item.is_stale ? "is-stale" : ""}`}
                disabled={Boolean(historyAction)}
                key={item.cache_key}
                onClick={() => void restoreCachedResult(item)}
                type="button"
              >
                <span className="search-history__item-top">
                  <em>{modeLabels[item.query_mode]}</em>
                  {item.is_stale ? <i>已过期</i> : <i className="is-current">可复用</i>}
                </span>
                <strong>{item.query}</strong>
                <small>
                  {formatHistoryTime(item.last_hit_at ?? item.updated_at)}
                  <span>命中 {item.hit_count} 次</span>
                </small>
                {historyAction === item.cache_key ? <RefreshCw className="spin" size={14} /> : null}
              </button>
            ))}
            {!historyLoading && !historyItems.length ? (
              <div className="search-history__empty">
                <History size={22} />
                <span>还没有搜索缓存</span>
              </div>
            ) : null}
          </div>
          </aside>
        </div>
      ) : null}
    </section>
  );
}
