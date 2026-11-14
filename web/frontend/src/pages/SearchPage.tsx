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
  SlidersHorizontal,
  Sparkles,
  TextSearch,
  Trash2,
  X,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import { SearchProgressCard, searchModeHelp } from "../components/SearchProgressCard";
import {
  useSearchSession,
  type SearchResult,
  type SearchResultDisplayMode,
} from "../context/SearchSessionContext";
import type {
  AnswerQueryData,
  GraphNode,
  GraphEdge,
  GraphPath,
  GraphGroup,
  GraphQueryData,
  GlobalQueryData,
  HybridQueryData,
  RagQueryData,
  RagResult,
  SearchCacheItem,
  SearchMode,
} from "../types/api";

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
  displayMode: SearchResultDisplayMode;
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
              title={node?.keyword ?? nodeId}
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
  nodes,
  edges,
  paths,
  onNode,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  paths: GraphPath[];
  onNode: (node: GraphNode) => void;
}) {
  const nodeMap = new Map(nodes.map((node) => [node.node_id, node]));
  const edgeMap = new Map(edges.map((edge) => [edge.edge_id, edge]));
  const title = paths.length ? "关系路径" : "关系连线";
  return (
    <section className="result-column relationship-results">
      <div className="result-column__heading">
        <span className="result-icon"><GitFork size={17} /></span>
        <div><h3>{title}</h3><p>{paths.length || edges.length} 条</p></div>
      </div>
      <div className="relationship-results__list">
        {paths.map((path) => (
          <article className="relation-chain-card" key={path.edge_ids.join("/")} title={path.text}>
            <RelationChain nodeIds={path.node_ids} edgeIds={path.edge_ids} nodes={nodeMap} edges={edgeMap} onNode={onNode} />
            <small>{path.depth} 跳 · 链路权重 {Math.round(path.weight * 100)}%</small>
          </article>
        ))}
        {!paths.length
          ? edges.map((relation) => (
              <article className="relation-chain-card" key={relation.edge_id}>
                <RelationChain
                  nodeIds={[relation.source_node_id, relation.target_node_id]}
                  edgeIds={[relation.edge_id]}
                  nodes={nodeMap}
                  edges={edgeMap}
                  onNode={onNode}
                />
                <small>
                  关系权重 {Math.round(relation.weight * 100)}%
                  {relation.support_count != null ? ` · ${relation.support_count} 条支持` : ""}
                </small>
              </article>
            ))
          : null}
      </div>
    </section>
  );
}

function GraphResults({
  nodes,
  query,
  onNode,
  displayMode,
}: {
  nodes: GraphNode[];
  query: string;
  onNode: (node: GraphNode) => void;
  displayMode: SearchResultDisplayMode;
}) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(nodes.length / GRAPH_RESULT_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * GRAPH_RESULT_PAGE_SIZE;
  const visibleNodes = nodes.slice(pageStart, pageStart + GRAPH_RESULT_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [query]);

  return (
    <section className="result-column result-column--graph">
      <div className="result-column__heading">
        <span className="result-icon result-icon--graph"><Network size={17} /></span>
        <div><h3>图谱节点</h3><p>{nodes.length} 个节点</p></div>
      </div>
      <div className="node-result-grid" aria-live="polite">
        {visibleNodes.map((node) => (
          <button className="node-result-card" key={`${node.node_id}-${node.depth ?? 0}`} onClick={() => onNode(node)}>
            <span className="node-result-card__dot" />
            <span>
              <strong>{node.keyword}</strong>
              <small>{node.depth ? `第 ${node.depth} 层邻居` : node.match_score != null
                ? `匹配度 ${Math.round(node.match_score * 100)}%` : "直接命中"}</small>
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
    </section>
  );
}

function GraphGroups({ groups, displayMode }: { groups: GraphGroup[]; displayMode: SearchResultDisplayMode }) {
  return (
    <section className="result-column global-result">
      <div className="result-column__heading">
        <span className="result-icon"><Network size={17} /></span>
        <div><h3>节点群摘要</h3><p>{groups.length} 个相关群组</p></div>
      </div>
      <div className="global-result__groups">
        {groups.map((group, index) => (
          <article key={group.group_id}>
            <strong title={group.group_id}>群组 {index + 1}</strong>
            {group.node_count != null ? <small>{group.node_count} 个节点</small> : null}
            <SearchResultText content={group.summary} displayMode={displayMode} />
          </article>
        ))}
      </div>
    </section>
  );
}

function RagSource({ result }: { result: RagResult }) {
  return (
    <footer className="rag-source">
      <FileText size={14} />
      {result.source.source_path ? (
        <Link to={`/documents?path=${encodeURIComponent(result.source.source_path)}`}>
          {result.source.source_path}
        </Link>
      ) : <span>{result.source.original_path ?? result.source.relative_path ?? result.source.source_id}</span>}
    </footer>
  );
}

function RagResults({
  data,
  displayMode,
}: {
  data: RagQueryData;
  displayMode: SearchResultDisplayMode;
}) {
  return (
    <section className="result-column result-column--rag">
      <div className="result-column__heading">
        <span className="result-icon result-icon--rag"><BookOpen size={17} /></span>
        <div><h3>文档片段</h3><p>{data.results.length} 条语义召回</p></div>
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
            <RagSource result={result} />
          </article>
          );
        })}
        {!data.results.length ? <div className="empty-result">没有超过阈值的文档片段</div> : null}
      </div>
    </section>
  );
}

type ResultTabId = "answer" | "nodes" | "edges" | "paths" | "rag" | "groups" | "scores";
type ResultTab = { id: ResultTabId; label: string; icon: typeof Network; count?: number };
type ResultScore = { id: string; label: string; kind: string; metric: string; value: number; percent: boolean };

function ScoreResults({ scores }: { scores: ResultScore[] }) {
  return (
    <section className="result-column result-column--scores">
      <div className="result-column__heading">
        <span className="result-icon"><SlidersHorizontal size={17} /></span>
        <div><h3>相关度</h3><p>{scores.length} 项召回评分</p></div>
      </div>
      <div className="search-score-table">
        <table>
          <thead><tr><th scope="col">内容</th><th scope="col">类型</th><th scope="col">指标</th><th scope="col">得分</th></tr></thead>
          <tbody>
            {scores.map((score) => (
              <tr key={score.id}>
                <th scope="row">{score.label}</th>
                <td>{score.kind}</td>
                <td>{score.metric}</td>
                <td>{score.percent ? `${Math.round(score.value * 100)}%` : score.value.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function SearchResultPanels({
  result,
  mode,
  displayMode,
  onDisplayModeChange,
  onNode,
}: {
  result: SearchResult;
  mode: SearchMode;
  displayMode: SearchResultDisplayMode;
  onDisplayModeChange: (mode: SearchResultDisplayMode) => void;
  onNode: (node: GraphNode) => void;
}) {
  const [selectedTab, setSelectedTab] = useState<ResultTabId | null>(null);
  const resultId = useId();
  const browserRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const retrieval = mode === "answer" ? (result as AnswerQueryData).retrieval
    : mode === "hybrid" ? result as HybridQueryData : null;
  const graph = mode === "graph" ? result as GraphQueryData : retrieval?.graph;
  const rag = mode === "rag" ? result as RagQueryData : retrieval?.rag;
  const global = mode === "global" ? result as GlobalQueryData : null;
  const answer = mode === "answer" ? (result as AnswerQueryData).answer : global?.answer;
  const nodes = graph ? [...graph.hit_nodes, ...graph.expanded_nodes] : global?.key_entities ?? [];
  const edges = [...new Map([...(graph?.edges ?? []), ...(graph?.relations ?? [])].map((edge) => [edge.edge_id, edge])).values()];
  const paths = graph?.paths ?? [];
  const groups = [...new Map([
    ...(graph?.groups ?? []), ...(retrieval?.communities ?? global?.communities ?? []),
  ].map((group) => [group.group_id, group])).values()];
  const nodeNames = new Map(nodes.map((node) => [node.node_id, node.keyword]));
  const scores: ResultScore[] = [
    ...(rag?.results ?? []).map((item, index) => ({
      id: `rag:${item.chunk_id}`,
      label: `片段 ${index + 1} · ${item.source.source_path ?? item.source.original_path ?? item.source.relative_path ?? item.source.source_id}`,
      kind: "文档片段", metric: "语义相似度", value: item.score, percent: true,
    })),
    ...nodes.filter((node) => node.match_score != null).map((node) => ({
      id: `node:${node.node_id}`, label: node.keyword,
      kind: "图谱节点", metric: "节点匹配度", value: node.match_score!, percent: true,
    })),
    ...edges.map((edge) => ({
      id: `edge:${edge.edge_id}`,
      label: `${nodeNames.get(edge.source_node_id) ?? edge.source_node_id} → ${nodeNames.get(edge.target_node_id) ?? edge.target_node_id}`,
      kind: "关系连线", metric: "关系权重", value: edge.weight, percent: true,
    })),
    ...paths.map((path, index) => ({
      id: `path:${index}`, label: path.text,
      kind: "关系路径", metric: "链路权重", value: path.weight, percent: true,
    })),
    ...(retrieval?.entities ?? []).map((entity) => ({
      id: `entity:${entity.node_id}`, label: entity.keyword,
      kind: "实体", metric: "融合得分", value: entity.score, percent: false,
    })),
    ...(retrieval?.communities ?? []).map((group) => ({
      id: `group:${group.group_id}`, label: group.group_id,
      kind: "节点群", metric: "融合得分", value: group.score, percent: false,
    })),
  ];
  const tabs: ResultTab[] = [];
  if (answer?.trim()) tabs.push({ id: "answer", label: mode === "global" ? "全局回答" : "LLM 回答", icon: Bot });
  if (nodes.length) tabs.push({ id: "nodes", label: "图谱节点", icon: Network, count: nodes.length });
  if (edges.length) tabs.push({ id: "edges", label: "关系连线", icon: GitFork, count: edges.length });
  if (paths.length) tabs.push({ id: "paths", label: "关系路径", icon: GitFork, count: paths.length });
  if (rag?.results.length) tabs.push({ id: "rag", label: "文档片段", icon: BookOpen, count: rag.results.length });
  if (groups.length) tabs.push({ id: "groups", label: "节点群摘要", icon: Globe2, count: groups.length });
  if (scores.length) tabs.push({ id: "scores", label: "相关度", icon: SlidersHorizontal, count: scores.length });
  const activeTab = tabs.find((tab) => tab.id === selectedTab) ?? tabs[0];
  const activeId = activeTab?.id;

  useEffect(() => setSelectedTab(null), [result]);
  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [result, activeId]);

  useEffect(() => {
    const browser = browserRef.current;
    const stage = browser?.closest<HTMLElement>(".page-stage");
    if (!browser || !stage) return;
    const resize = () => {
      const style = getComputedStyle(stage);
      browser.style.setProperty("--search-result-height", `${Math.max(240,
        stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
      )}px`);
    };
    // Move the outer workspace into view before scrolling the result text.
    const expandForReading = (event: WheelEvent) => {
      if (event.ctrlKey || event.shiftKey || event.deltaY <= 0) return;
      const distance = browser.getBoundingClientRect().top - stage.getBoundingClientRect().top
        - parseFloat(getComputedStyle(stage).paddingTop);
      if (distance <= 1) return;
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1);
      const movement = Math.min(distance, delta);
      stage.scrollTop += movement;
      if (contentRef.current && delta > movement) contentRef.current.scrollTop += delta - movement;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(stage);
    browser.addEventListener("wheel", expandForReading, { passive: false });
    return () => {
      observer.disconnect();
      browser.removeEventListener("wheel", expandForReading);
    };
  }, []);

  return (
    <section className="search-results" ref={browserRef} aria-label="检索结果与依据">
      <div className="search-result-view-bar">
        {tabs.length ? (
          <div className="search-result-tabs" role="tablist" aria-label="检索结果分类"
            onKeyDown={(event) => {
              const index = tabs.findIndex((tab) => tab.id === activeId);
              const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
                : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
                : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
              if (next == null) return;
              event.preventDefault();
              setSelectedTab(tabs[next].id);
              event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next].focus();
            }}>
            {tabs.map(({ id, label, icon: Icon, count }) => (
              <button type="button" role="tab" key={id} id={`${resultId}-${id}`}
                aria-label={label} aria-selected={activeId === id} aria-controls={`${resultId}-content`}
                tabIndex={activeId === id ? 0 : -1}
                className={`search-result-tab ${activeId === id ? "is-active" : ""}`}
                onClick={() => setSelectedTab(id)}>
                <Icon size={15} /><span>{label}</span>
                {count != null ? <span className="search-result-tab__count" aria-hidden="true">{count}</span> : null}
              </button>
            ))}
          </div>
        ) : <strong className="search-result-label">检索结果</strong>}
        <div className="search-result-view-switch" role="group" aria-label="结果显示方式">
          <button type="button" className={displayMode === "markdown" ? "is-active" : ""}
            aria-pressed={displayMode === "markdown"} onClick={() => onDisplayModeChange("markdown")}>
            <FileText size={14} />Markdown
          </button>
          <button type="button" className={displayMode === "source" ? "is-active" : ""}
            aria-pressed={displayMode === "source"} onClick={() => onDisplayModeChange("source")}>
            <Code2 size={14} />文本源码
          </button>
        </div>
      </div>
      <div className="search-result-content" ref={contentRef} role="tabpanel" id={`${resultId}-content`}
        aria-labelledby={activeId ? `${resultId}-${activeId}` : undefined}
        aria-label={activeId ? undefined : "检索结果"} tabIndex={0}>
        {activeId === "answer" ? (
          <article className="answer-result"><SearchResultText content={answer!} displayMode={displayMode} /></article>
        ) : null}
        {activeId === "nodes" ? <GraphResults nodes={nodes} query={"query" in result ? result.query ?? "" : ""}
          displayMode={displayMode} onNode={onNode} /> : null}
        {activeId === "edges" || activeId === "paths" ? <RelationshipResults nodes={nodes} edges={edges}
          paths={activeId === "paths" ? paths : []} onNode={onNode} /> : null}
        {activeId === "rag" && rag ? <RagResults data={rag} displayMode={displayMode} /> : null}
        {activeId === "groups" ? <GraphGroups groups={groups} displayMode={displayMode} /> : null}
        {activeId === "scores" ? <ScoreResults scores={scores} /> : null}
        {!activeId ? <div className="empty-result">没有召回相关内容，试试其他关键词</div> : null}
      </div>
    </section>
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
  const {
    query,
    mode,
    result,
    loading,
    error,
    progress,
    historyRevision,
    setQuery,
    selectMode,
    resultDisplayMode,
    setResultDisplayMode,
    runSearch: startSearch,
    restoreResult,
  } = useSearchSession();
  const [historyItems, setHistoryItems] = useState<SearchCacheItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyAction, setHistoryAction] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyNotice, setHistoryNotice] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
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
  }, [historyRevision, loadHistory]);

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
    setHistoryNotice(null);
    const data = await startSearch(query, mode, force);
    if (data) {
      setHistoryNotice(force ? "已强制刷新结果并更新服务端缓存。" : null);
    }
  };

  const restoreCachedResult = async (item: SearchCacheItem) => {
    setHistoryAction(item.cache_key);
    setHistoryError(null);
    setHistoryNotice(null);
    try {
      const detail = await api.getSearchCacheDetail(item.cache_key);
      restoreResult(detail.query, detail.query_mode, detail.result);
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

  return (
    <section className="search-page page-stack">
      <header className="search-page-header">
        <div>
          <h1>知识检索</h1>
        </div>
        <button
          className="search-history-trigger"
          type="button"
          aria-label={`搜索历史，${historyTotal} 条缓存`}
          aria-expanded={historyOpen}
          aria-controls="search-history-panel"
          onClick={() => setHistoryOpen(true)}
        >
          <PanelRightOpen size={16} />
          <span>搜索历史</span>
          <em>{historyTotal}</em>
        </button>
      </header>

      <div className="search-workbench">
        <div className="search-hero">
          <form className="hero-search" role="search" onSubmit={(event) => void runSearch(event)}>
            <label className="hero-search__input">
              <Search size={18} />
              <input
                autoFocus
                aria-label="检索问题"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="输入问题、概念或实体名称…"
              />
            </label>
            <button
              className="hero-search__refresh"
              disabled={loading || !query.trim()}
              onClick={() => void runSearch(undefined, true)}
              title="忽略缓存，重新检索"
              type="button"
            >
              <RefreshCw size={16} />重新检索
            </button>
            <button className="hero-search__submit" disabled={loading || !query.trim()} type="submit">
              {loading ? "处理中…" : mode === "answer" ? "获取回答" : "开始检索"}<ArrowRight size={17} />
            </button>
          </form>

          <div className="search-controls">
            <div className="search-mode-switch" role="tablist" aria-label="检索模式">
              {modes.map(({ value, label, icon: Icon }) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === value}
                  disabled={loading}
                  title={searchModeHelp[value].description}
                  className={mode === value ? "is-active" : ""}
                  key={value}
                  onClick={() => {
                    setHistoryNotice(null);
                    selectMode(value);
                  }}
                >
                  <Icon size={16} />{label}
                </button>
              ))}
            </div>

          </div>

          <div className="search-suggestions" role="group" aria-label="示例问题">
            {suggestions.map((suggestion) => (
              <button key={suggestion} onClick={() => setQuery(suggestion)}>{suggestion}</button>
            ))}
          </div>
        </div>

        <div className="search-workspace" aria-busy={loading}>
          {historyNotice || progress || error ? (
            <div className="search-workspace-feedback">
              {historyNotice ? <p className="search-workspace-notice" role="status">{historyNotice}</p> : null}
              {progress ? (
                <details className="search-flow" open={loading || Boolean(error)}>
                  <summary>
                    <span>{loading ? "正在检索" : error ? "检索失败" : progress.status === "restored" ? "历史快照" : "检索完成"}</span>
                  </summary>
                  <SearchProgressCard progress={progress} />
                </details>
              ) : null}
              {error ? <ErrorNotice message={error} /> : null}
            </div>
          ) : null}

          {result ? (
            <SearchResultPanels result={result} mode={mode} displayMode={resultDisplayMode}
              onDisplayModeChange={setResultDisplayMode}
              onNode={(node) => navigate(`/graph?node=${encodeURIComponent(node.node_id)}`)} />
          ) : (
            <section className="search-empty-result" aria-label="检索结果与依据">
              <div className="search-placeholder">
                <img className="search-placeholder__logo" src="/kemo-graph-logo.png" alt="" aria-hidden="true" />
                <h3>{loading ? "正在检索…" : error ? "暂无结果" : "从一个问题开始"}</h3>
                {error ? <p>调整问题或重新检索</p> : null}
                {!loading && !error ? <Link to="/documents"><BookOpen size={14} />管理知识库</Link> : null}
              </div>
            </section>
          )}
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
            <span><strong id="search-history-title">搜索历史</strong><small>{historyTotal} 条记录</small></span>
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
