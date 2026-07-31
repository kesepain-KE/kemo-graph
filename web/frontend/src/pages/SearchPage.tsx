import {
  ArrowRight,
  BookOpen,
  Network,
  Search,
  Sparkles,
  TextSearch,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/api";
import { ErrorNotice, LoadingState } from "../components/Feedback";
import type {
  GraphNode,
  GraphQueryData,
  HybridQueryData,
  RagQueryData,
  SearchMode,
} from "../types/api";

type SearchResult = GraphQueryData | RagQueryData | HybridQueryData;

const modes: Array<{ value: SearchMode; label: string; icon: typeof Network }> = [
  { value: "graph", label: "图谱", icon: Network },
  { value: "rag", label: "向量", icon: TextSearch },
  { value: "hybrid", label: "混合", icon: Sparkles },
];

const suggestions = ["知识图谱如何增强 RAG", "向量检索", "实体关系抽取"];
const granularityLabels = {
  small: "小粒度",
  medium: "中粒度",
  large: "大粒度",
} as const;

function GraphResults({ data, onNode }: { data: GraphQueryData; onNode: (node: GraphNode) => void }) {
  const nodes = [...data.hit_nodes, ...data.expanded_nodes];
  return (
    <section className="result-column">
      <div className="result-column__heading">
        <span className="result-icon result-icon--graph"><Network size={17} /></span>
        <div><h3>图谱结果</h3><p>{nodes.length} 个节点 · {data.edges.length} 条关系</p></div>
      </div>
      <div className="node-result-grid">
        {nodes.map((node) => (
          <button className="node-result-card" key={`${node.node_id}-${node.depth ?? 0}`} onClick={() => onNode(node)}>
            <span className="node-result-card__dot" />
            <span>
              <strong>{node.keyword}</strong>
              <small>{node.depth ? `第 ${node.depth} 层邻居` : `匹配度 ${Math.round((node.match_score ?? 1) * 100)}%`}</small>
              <p>{node.summary}</p>
            </span>
            <ArrowRight size={15} />
          </button>
        ))}
        {!nodes.length ? <div className="empty-result">没有命中图谱节点</div> : null}
      </div>
      {data.groups.length ? (
        <div className="group-summary">
          <span>群组摘要</span>
          {data.groups.map((group) => <p key={group.group_id}>{group.summary}</p>)}
        </div>
      ) : null}
    </section>
  );
}

function RagResults({ data }: { data: RagQueryData }) {
  return (
    <section className="result-column">
      <div className="result-column__heading">
        <span className="result-icon result-icon--rag"><BookOpen size={17} /></span>
        <div><h3>RAG 片段</h3><p>{data.results.length} 条语义召回</p></div>
      </div>
      <div className="rag-result-list">
        {data.results.map((result, index) => (
          <article className="rag-result-card" key={result.chunk_id}>
            <div className="rag-result-card__meta">
              <span>
                #{String(index + 1).padStart(2, "0")}
                {result.granularity ? ` · ${granularityLabels[result.granularity]}` : ""}
              </span>
              <strong>{Math.round(result.score * 100)}%</strong>
            </div>
            <p>{result.content}</p>
            <footer>
              <FileBadge />
              <span>{result.source.relative_path ?? result.source.source_id}</span>
            </footer>
          </article>
        ))}
        {!data.results.length ? <div className="empty-result">没有超过阈值的文档片段</div> : null}
      </div>
    </section>
  );
}

function FileBadge() {
  return <span className="mini-file-badge">MD</span>;
}

export function SearchPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runSearch = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data =
        mode === "graph"
          ? await api.queryGraph(query.trim(), { depth: 3, direction: "both" })
          : mode === "rag"
            ? await api.queryRag(query.trim(), { top_k: 10 })
            : await api.queryHybrid(query.trim(), {
                graph_depth: 3,
                rag_top_k: 10,
              });
      setResult(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "检索失败");
    } finally {
      setLoading(false);
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

  return (
    <section className="search-page page-stack">
      <div className="search-hero">
        <div className="search-orbit" aria-hidden="true"><Sparkles size={22} /></div>
        <p className="eyebrow">Local-first knowledge retrieval</p>
        <h2>从你的知识网络中找到答案</h2>
        <p>图谱理解结构，向量召回语义；混合模式会让两者互相增强。</p>

        <form className="hero-search" onSubmit={runSearch}>
          <Search size={21} />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入问题、概念或关系…"
          />
          <button disabled={loading || !query.trim()} type="submit">
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

      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <div className="search-loading card"><LoadingState label="正在执行召回、重排与阈值过滤…" /></div> : null}

      {result ? (
        <div className={`search-results ${mode === "hybrid" ? "is-hybrid" : ""}`}>
          {graphData ? (
            <GraphResults
              data={graphData}
              onNode={(node) => navigate(`/graph?node=${encodeURIComponent(node.node_id)}`)}
            />
          ) : null}
          {ragData ? <RagResults data={ragData} /> : null}
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
          <h3>一次查询，两种知识视角</h3>
          <p>检索结果会在这里按结构化节点与原文片段分别呈现。</p>
        </div>
      ) : null}
    </section>
  );
}
