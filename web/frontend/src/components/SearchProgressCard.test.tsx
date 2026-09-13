import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SearchProgressCard, searchModeHelp, type SearchProgress } from "./SearchProgressCard";

const progress: SearchProgress = {
  query: "如何查找知识关系？",
  mode: "answer",
  status: "running",
  startedAt: Date.now() - 5000,
  finishedAt: null,
  force: false,
};

describe("SearchProgressCard", () => {
  it("renders reported intermediate stages without long explanations", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, steps: [
      { id: "llm", label: "LLM 优化", status: "completed" },
      { id: "split", label: "关键词与拆分", status: "completed" },
      { id: "embedding", label: "查询向量化", status: "completed" },
      { id: "vector", label: "向量召回", status: "completed" },
      { id: "chunks", label: "切片聚合", status: "completed" },
      { id: "rerank", label: "重排序", status: "running" },
    ] }} />);
    for (const label of ["LLM 优化", "关键词与拆分", "查询向量化", "向量召回", "切片聚合", "重排序"]) expect(html).toContain(label);
    expect(html.match(/aria-current="step"/g)).toHaveLength(1);
    expect(html).not.toContain("本次流程包含");
  });

  it("does not invent work after a cache hit or confirm an unreported stage", () => {
    const cached = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, status: "completed", steps: [{ id: "cache_hit", label: "缓存命中", status: "completed" }] }} />);
    expect(cached).toContain("缓存命中");
    expect(cached).not.toContain("重排序");
    const unconfirmed = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, status: "failed", steps: [{ id: "rerank", label: "重排序", status: "running" }] }} />);
    expect(unconfirmed).toContain("重排序 · 未确认");
  });
  it("shows an honest pending state instead of fabricated internal progress", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={progress} />);
    expect(html).toContain("检索中");
    expect(html).toContain("等待返回");
    expect(html).toContain('aria-current="step"');
    expect(html).not.toContain("当前接口不提供内部子步骤进度");
    expect(html).not.toContain("向量召回完成");
    expect(html).toContain("可切换页面，请勿刷新或关闭网页。");
    expect(html).not.toContain("search-progress__note");
  });

  it.each(Object.keys(searchModeHelp) as SearchProgress["mode"][])("explains the actual request mode: %s", (mode) => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, mode }} />);
    expect(html).toContain(searchModeHelp[mode].label);
    expect(html).not.toContain(searchModeHelp[mode].processing);
  });

  it("keeps completion and final elapsed time visible", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, status: "completed", finishedAt: progress.startedAt + 12000 }} />);
    expect(html).toContain("检索完成");
    expect(html).toContain("耗时 12 秒");
    expect(html).not.toContain('aria-current="step"');
  });

  it("distinguishes request failure from an empty successful result", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, status: "failed", finishedAt: progress.startedAt + 1000 }} />);
    expect(html).toContain("检索失败");
    expect(html).toContain("请查看错误信息后重试。");
    expect(html).not.toContain("已展示");
  });

  it("does not present restored history as a newly executed search", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, status: "restored", finishedAt: progress.startedAt }} />);
    expect(html).toContain("历史结果");
    expect(html).toContain("历史缓存，点击“重新检索”可更新。");
    expect(html).not.toContain("已提交");
    expect(html).not.toContain("耗时");
  });

  it("indicates forced refresh and safely escapes the query", () => {
    const html = renderToStaticMarkup(<SearchProgressCard progress={{ ...progress, force: true, query: "<script>alert(1)</script>" }} />);
    expect(html).toContain("跳过缓存");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
