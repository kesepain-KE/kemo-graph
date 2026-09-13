import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { countBuildStatuses, filterBuildDocuments } from "../lib/buildStatus";
import type { DocumentRecord } from "../types/api";
import { BuildStatusPage } from "./BuildStatusPage";

const documents: DocumentRecord[] = [
  { source_id: "1", relative_path: "Project/One.md", graph_status: "ready", rag_status: "failed" },
  { source_id: "2", relative_path: "Project/Two.md", graph_status: "processing", rag_status: "pending", exists_status: "active" },
  { source_id: "3", relative_path: "Other/Three.md", graph_status: "pending", rag_status: "ready" },
  { source_id: "4", relative_path: "Deleted.md", graph_status: "ready", rag_status: "failed", exists_status: "deleted" },
  { source_id: "5", relative_path: "Missing.md", graph_status: "ready", rag_status: "failed", exists_status: "missing" },
];

describe("BuildStatusPage", () => {
  it("combines filename, Graph and RAG filters without deleted sources", () => {
    expect(filterBuildDocuments(documents, " PROJECT/ ", "ready", "failed").map((item) => item.source_id)).toEqual(["1"]);
    expect(filterBuildDocuments(documents, "", "processing", "all").map((item) => item.source_id)).toEqual(["2"]);
    expect(filterBuildDocuments(documents, "", "ready", "pending")).toEqual([]);
    expect(filterBuildDocuments(documents, "", "all", "all")).toHaveLength(3);
  });
  it("counts each active build pipeline separately", () => {
    expect(countBuildStatuses(documents, "graph_status")).toEqual({ pending: 1, processing: 1, ready: 1, failed: 0 });
    expect(countBuildStatuses(documents, "rag_status")).toEqual({ pending: 1, processing: 0, ready: 1, failed: 1 });
  });
  it("offers themed filters, refresh and six-document pagination on a dedicated page", () => {
    const html = renderToStaticMarkup(<MemoryRouter><BuildStatusPage /></MemoryRouter>);
    expect(html).toContain('aria-label="Graph 状态"');
    expect(html).toContain('aria-label="RAG 状态"');
    expect(html).toContain("每 5 秒自动刷新");
    expect(html).toContain("每页最多 6 篇");
    expect(html).toContain('href="/documents"');
    expect(html).toContain("build-status-list");
    expect(html).not.toContain("<select");
  });
});
