import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { RuntimeTasksProvider } from "../context/RuntimeTasksContext";
import type { DocumentRecord } from "../types/api";
import { BuildDocumentCard, BuildStatusPage } from "./BuildStatusPage";

const document: DocumentRecord = {
  source_id: "1", relative_path: "one-processed.md", source_path: "研究/原文.docx",
  content_hash: "current", graph_status: "ready", rag_status: "failed",
};

describe("BuildStatusPage", () => {
  it("offers compact pipeline filters, the three requested actions, and split document panels", () => {
    const html = renderToStaticMarkup(<RuntimeTasksProvider><MemoryRouter><BuildStatusPage /></MemoryRouter></RuntimeTasksProvider>);
    expect(html).toContain('aria-label="筛选 知识图谱：失败"');
    expect(html).toContain('aria-label="筛选 RAG：失败"');
    expect(html).toContain("重新构建失败项");
    expect(html).toContain('aria-label="搜索构建文档"');
    expect(html).toContain('aria-label="刷新构建状态"');
    expect(html).toContain("每 5 秒自动刷新");
    expect(html).toContain("每页最多 6 篇");
    expect(html).toContain("build-status-list");
    expect(html).toContain('aria-label="预处理文档详情"');
    expect(html).not.toContain("<select");
  });
  it("does not invent successful steps for sources without a build record", () => {
    const html = renderToStaticMarkup(<MemoryRouter><BuildDocumentCard item={document} selected expanded disabled={false} onOpen={() => {}} onRebuild={() => {}} /></MemoryRouter>);
    expect(html.match(/暂无步骤记录/g)).toHaveLength(2);
    expect(html).not.toContain('class="build-steps"');
    expect(html).toContain("图谱 · 就绪");
    expect(html).toContain("RAG · 失败");
    expect(html).toContain('href="/documents?source=1"');
    expect(html).toContain("重新构建此文件");
  });
  it("keeps successful steps and the precise failure, marking records from a previous revision", () => {
    const item: DocumentRecord = { ...document, builds: { rag: {
      content_hash: "previous",
      steps: [
        { step: "chunk", label: "文档切片", status: "completed", error: null, started_at: "2026-11-12T08:00:00Z", finished_at: "2026-11-12T08:00:01Z" },
        { step: "embed", label: "生成文档向量", status: "failed", error: "模型网关超时", started_at: "2026-11-12T08:00:01Z", finished_at: "2026-11-12T08:00:31Z" },
      ],
    } } };
    const html = renderToStaticMarkup(<MemoryRouter><BuildDocumentCard item={item} selected expanded disabled={false} onOpen={() => {}} onRebuild={() => {}} /></MemoryRouter>);
    expect(html).toContain('class="is-completed"');
    expect(html).toContain("中断于「生成文档向量」");
    expect(html).toContain("模型网关超时");
    expect(html).toContain("上次构建 · 处理稿已更新");
  });
});
