import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SearchSessionProvider, type SearchResult } from "../context/SearchSessionContext";
import type { GraphQueryData, RagQueryData, SearchMode } from "../types/api";
import { SearchPage, SearchResultPanels } from "./SearchPage";

const graph: GraphQueryData = {
  query: "知识检索",
  hit_nodes: [{ node_id: "graph", keyword: "知识图谱", summary: "图谱摘要", aliases: [], tags: [], ref_count: 1 }],
  expanded_nodes: [{ node_id: "rag", keyword: "RAG", summary: "向量摘要", aliases: [], tags: [], ref_count: 1, depth: 1 }],
  edges: [{ edge_id: "edge", source_node_id: "graph", target_node_id: "rag", relation: "增强", weight: 0.9 }],
  relations: [],
  paths: [{ node_ids: ["graph", "rag"], edge_ids: ["edge"], depth: 1, weight: 0.9, text: "知识图谱增强 RAG" }],
  groups: [{ group_id: "group", summary: "检索增强群组摘要" }],
};
const rag: RagQueryData = {
  query: "知识检索",
  results: [{
    chunk_id: "chunk",
    content: "精确命中片段",
    score: 0.91,
    granularity: "small",
    context: { chunk_id: "parent", content: "包含完整上下文的检索依据", granularity: "large" },
    source: { source_id: "source", source_path: "研究/原文.pdf", relative_path: "processed/source.md" },
  }],
};

function renderPanels(mode: SearchMode, result: SearchResult) {
  return renderToStaticMarkup(<MemoryRouter>
    <SearchResultPanels mode={mode} result={result} displayMode="source" onDisplayModeChange={() => {}} onNode={() => {}} />
  </MemoryRouter>);
}

describe("knowledge search layout", () => {
  it("keeps history and search controls above one empty result area", () => {
    const html = renderToStaticMarkup(<SearchSessionProvider><MemoryRouter><SearchPage /></MemoryRouter></SearchSessionProvider>);
    expect(html.slice(0, html.indexOf("</header>"))).toContain("搜索历史");
    expect(html).toContain('role="search"');
    expect(html).toContain('aria-label="检索问题"');
    expect(html).toContain("重新检索");
    expect(html).toContain("获取回答");
    expect(html.match(/role="tab"/g)).toHaveLength(5);
    expect(html).toContain("从一个问题开始");
    expect(html.match(/search-placeholder__logo/g)).toHaveLength(1);
    expect(html).not.toContain('aria-label="检索结果分类"');
    expect(html).toContain('aria-label="检索结果与依据"');
  });

  it.each<[SearchMode, SearchResult, string[]]>([
    ["answer", { query: "知识检索", answer: "综合回答", retrieval: { graph, rag } },
      ["LLM 回答", "图谱节点", "关系连线", "关系路径", "文档片段", "节点群摘要", "相关度"]],
    ["graph", graph, ["图谱节点", "关系连线", "关系路径", "节点群摘要", "相关度"]],
    ["rag", rag, ["文档片段", "相关度"]],
    ["hybrid", { graph, rag }, ["图谱节点", "关系连线", "关系路径", "文档片段", "节点群摘要", "相关度"]],
    ["global", { query: "知识检索", answer: "全局总结", communities: graph.groups, key_entities: graph.hit_nodes },
      ["全局回答", "图谱节点", "节点群摘要"]],
  ])("uses the actual recalled categories and a single content pane in %s mode", (mode, result, tabs) => {
    const html = renderPanels(mode, result);
    expect([...html.matchAll(/role="tab"[^>]*aria-label="([^"]+)"/g)].map((match) => match[1])).toEqual(tabs);
    expect(html.match(/role="tabpanel"/g)).toHaveLength(1);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(html).not.toContain("answer-evidence");
  });

  it("keeps precise matches, full context and links to original files in vector results", () => {
    const html = renderPanels("rag", rag);
    expect(html).toContain("精确命中片段");
    expect(html).toContain("包含完整上下文的检索依据");
    expect(html).toContain("查看实际命中的精确片段");
    expect(html).toContain(`href="/documents?path=${encodeURIComponent("研究/原文.pdf")}"`);
    expect(html).not.toContain("processed/source.md");
  });

  it("shows empty retrievals without presenting a fabricated answer or context", () => {
    const graphHtml = renderPanels("graph", { ...graph, hit_nodes: [], expanded_nodes: [], edges: [], paths: [], groups: [] });
    expect(graphHtml).toContain("没有召回相关内容");
    expect(graphHtml).not.toContain('role="tab"');
    const ragHtml = renderPanels("rag", { query: "没有资料", results: [] });
    expect(ragHtml).toContain("没有召回相关内容");
    expect(ragHtml).not.toContain('role="tab"');
  });

  it("does not fabricate scores for graph nodes without a returned match score", () => {
    const html = renderPanels("graph", { ...graph, expanded_nodes: [], edges: [], paths: [], groups: [] });
    expect(html).toContain("直接命中");
    expect(html).not.toContain("100%");
    expect(html).not.toContain('aria-label="相关度"');
  });

  it("keeps edge-only recalls available even when there are no relation paths", () => {
    const html = renderPanels("graph", { ...graph, paths: [], relations: [], groups: [] });
    expect(html).toContain('aria-label="关系连线"');
    expect(html).not.toContain('aria-label="关系路径"');
    expect(html).not.toContain('aria-label="节点群摘要"');
  });

  it("shows an answer alone when its retrieval contains no evidence", () => {
    const html = renderPanels("answer", {
      query: "无资料", answer: "知识库没有相关记录",
      retrieval: { graph: { ...graph, hit_nodes: [], expanded_nodes: [], edges: [], relations: [], paths: [], groups: [] },
        rag: { query: "无资料", results: [] } },
    });
    expect(html.match(/role="tab"/g)).toHaveLength(1);
    expect(html).toContain("知识库没有相关记录");
    expect(html).not.toContain('aria-label="相关度"');
  });
});
