import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function response<T>(data: T): Response {
  return new Response(JSON.stringify({ ok: true, data, error: null }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("management API", () => {
  it("loads documents and document content from the real endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          documents: [],
          pagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
        }),
      )
      .mockResolvedValueOnce(
        response({ source_id: "s1", relative_path: "a.md", content: "# A" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getDocuments()).resolves.toEqual({
      documents: [],
      pagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    await expect(api.getDocumentContent("s/1")).resolves.toMatchObject({ content: "# A" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/documents");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/documents/s%2F1/content");
  });

  it("uploads Markdown content as the JSON contract requires", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ source_id: "s1", filename: "a.md", path: "a.md", size: 3 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# A"], "a.md", { type: "text/markdown" });

    await api.uploadFile(file);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ filename: "a.md", content: "# A" });
  });

  it("imports binary documents through multipart without forcing a JSON content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        source_id: "s1",
        original_filename: "guide.pdf",
        detected_format: "pdf",
        markdown_relative_path: "guide-stable.md",
        conversion_status: "completed",
        ingest_status: "completed",
        size: 4,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1, 2, 3, 4])], "guide.pdf");

    await api.importFile(file);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/import?ingest=true");
    expect(init.body).toBeInstanceOf(FormData);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("empties the recycle bin through the destructive maintenance endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ deleted: 3, forced: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.emptyRecycle()).resolves.toEqual({ deleted: 3, forced: true });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/maintenance/recycle");
    expect(init.method).toBe("DELETE");
  });

  it("submits ingest and graph maintenance work to persistent background jobs", async () => {
    const job = {
      job_id: "job-1",
      kind: "ingest",
      status: "queued",
      progress: 0,
      detail: "queued",
      result: null,
      error: null,
      created_at: "2026-08-01T00:00:00Z",
      started_at: null,
      finished_at: null,
      updated_at: "2026-08-01T00:00:00Z",
      events: [],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(job))
      .mockResolvedValueOnce(response({ ...job, kind: "organize_graph" }))
      .mockResolvedValueOnce(response({ ...job, kind: "rebuild_knowledge_base" }))
      .mockResolvedValueOnce(response({ ...job, kind: "rebuild_all" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.startIngestJob(["a.md"], "both");
    await api.organizeGraph({ use_llm: false, summarize: false });
    await api.rebuildKnowledgeBase();
    await api.rebuildAll();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/jobs/ingest",
      "/api/v1/maintenance/organize-graph",
      "/api/v1/maintenance/rebuild-knowledge-base",
      "/api/v1/maintenance/rebuild-all",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      paths: ["a.md"],
      mode: "both",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
      use_llm: false,
      summarize: false,
    });
    expect(fetchMock.mock.calls.every(([, init]) => init.method === "POST")).toBe(true);
  });

  it("lists jobs and encodes a job id when loading its event history", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ jobs: [] }))
      .mockResolvedValueOnce(response({ job_id: "job/1", events: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getJobs(25)).resolves.toEqual({ jobs: [] });
    await expect(api.getJob("job/1")).resolves.toMatchObject({ job_id: "job/1" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/jobs?limit=25");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/jobs/job%2F1");
  });

  it("forces query refresh and manages shared search history", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({ query: "alpha", answer: "answer", retrieval: { graph: {}, rag: {} } }),
      )
      .mockResolvedValueOnce(response({ query: "alpha", results: [] }))
      .mockResolvedValueOnce(
        response({ items: [], total: 0, page: 2, page_size: 5 }),
      )
      .mockResolvedValueOnce(
        response({ cache_key: "cache/1", query_mode: "rag", result: {} }),
      )
      .mockResolvedValueOnce(response({ deleted: 2, stale_only: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.queryAnswer("alpha", { graph_depth: 4, rag_top_k: 8, force: true });
    await api.queryRag("alpha", { top_k: 8, force: true });
    await api.getSearchCache(2, 5);
    await api.getSearchCacheDetail("cache/1");
    await api.clearSearchCache(true);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/query/answer?force=true",
      "/api/v1/query/rag?force=true",
      "/api/v1/search/cache?page=2&page_size=5",
      "/api/v1/search/cache/cache%2F1",
      "/api/v1/search/cache?stale_only=true",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      query: "alpha",
      graph_depth: 4,
      rag_top_k: 8,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
      query: "alpha",
      top_k: 8,
    });
    expect(fetchMock.mock.calls[4][1].method).toBe("DELETE");
  });

  it("loads revisioned visualization pages and deterministic neighborhoods", async () => {
    const revision = "a".repeat(64);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          revision,
          node_count: 4,
          edge_count: 3,
          group_count: 1,
          groups: [],
        }),
      )
      .mockResolvedValueOnce(
        response({
          revision,
          nodes: [],
          pagination: { page: 2, page_size: 500, total: 0, total_pages: 0 },
        }),
      )
      .mockResolvedValueOnce(
        response({
          revision,
          edges: [],
          pagination: { page: 3, page_size: 750, total: 0, total_pages: 0 },
        }),
      )
      .mockResolvedValueOnce(
        response({
          revision,
          anchor_node_id: "node/1",
          depth: 3,
          direction: "backward",
          node_limit: 400,
          edge_limit: 900,
          truncated: false,
          edges_truncated: false,
          nodes: [],
          edges: [],
          groups: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await api.graphVisualizationMeta();
    await api.graphVisualizationNodes(2, 500, revision);
    await api.graphVisualizationEdges(3, 750, revision);
    await api.graphNeighborhood("node/1", {
      depth: 3,
      direction: "backward",
      limit: 400,
      edgeLimit: 900,
      expectedRevision: revision,
    });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/graph/visualization/meta",
      `/api/v1/graph/visualization/nodes?page=2&page_size=500&expected_revision=${revision}`,
      `/api/v1/graph/visualization/edges?page=3&page_size=750&expected_revision=${revision}`,
      `/api/v1/graph/neighborhood/node%2F1?depth=3&direction=backward&limit=400&edge_limit=900&expected_revision=${revision}`,
    ]);
  });

  it("normalizes connection failures into a friendly error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await expect(api.getConfig()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: 0,
    });
  });
});
