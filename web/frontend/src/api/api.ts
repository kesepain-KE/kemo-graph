import type {
  AnswerQueryData,
  ApiEnvelope,
  ConfigData,
  DeleteDocumentData,
  DocumentContentData,
  DocumentListData,
  FullGraphData,
  GlobalQueryData,
  GraphNeighborhoodData,
  GraphQueryData,
  GraphVisualizationEdgesData,
  GraphVisualizationMetaData,
  GraphVisualizationNodesData,
  HybridQueryData,
  IngestData,
  IngestMode,
  ImportData,
  MaintenanceJob,
  MaintenanceJobListData,
  RagQueryData,
  RecycleCleanupData,
  SearchCacheClearData,
  SearchCacheDetailData,
  SearchCacheListData,
  StatusData,
  UploadData,
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

type RequestOptions = RequestInit & { timeoutMs?: number | null };

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code = "INTERNAL", status = 500) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: callerSignal, ...init } = options;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = timeoutMs === null
    ? null
    : globalThis.setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init.headers,
      },
    });
  } catch (caught) {
    if (controller.signal.aborted) {
      throw new ApiClientError("请求超时，请稍后重试。", "TIMEOUT", 0);
    }
    throw new ApiClientError(
      "无法连接后端服务，请确认 kemo-graph 已启动。",
      "NETWORK_ERROR",
      0,
    );
  } finally {
    if (timeout !== null) globalThis.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }

  let envelope: ApiEnvelope<T>;
  try {
    envelope = (await response.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiClientError(
      `后端返回了非 JSON 响应（HTTP ${response.status}）`,
      "INVALID_RESPONSE",
      response.status,
    );
  }
  if (!response.ok || !envelope.ok) {
    const error = envelope.ok ? null : envelope.error;
    throw new ApiClientError(
      error?.message ?? `请求失败（HTTP ${response.status}）`,
      error?.code ?? "INTERNAL",
      response.status,
    );
  }
  return envelope.data;
}

export const api = {
  status: () => request<StatusData>("/status", { timeoutMs: 5_000 }),
  ingest: (paths: string[] | null = null, mode: IngestMode = "both") =>
    request<IngestData>("/ingest", {
      method: "POST",
      body: JSON.stringify({ paths, mode }),
      timeoutMs: null,
    }),
  startIngestJob: (paths: string[] | null = null, mode: IngestMode = "both") =>
    request<MaintenanceJob>("/jobs/ingest", {
      method: "POST",
      body: JSON.stringify({ paths, mode }),
    }),
  organizeGraph: (options: { use_llm?: boolean; summarize?: boolean } = {}) =>
    request<MaintenanceJob>("/maintenance/organize-graph", {
      method: "POST",
      body: JSON.stringify({
        use_llm: options.use_llm ?? true,
        summarize: options.summarize ?? true,
      }),
    }),
  rebuildKnowledgeBase: () =>
    request<MaintenanceJob>("/maintenance/rebuild-knowledge-base", { method: "POST" }),
  rebuildAll: () =>
    request<MaintenanceJob>("/maintenance/rebuild-all", { method: "POST" }),
  getJobs: (limit = 100) =>
    request<MaintenanceJobListData>(`/jobs?limit=${limit}`, { timeoutMs: 5_000 }),
  getJob: (jobId: string) =>
    request<MaintenanceJob>(`/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 5_000 }),
  queryGraph: (
    query: string,
    options: {
      depth?: number;
      direction?: string;
      confidence?: number;
      force?: boolean;
    } = {},
  ) => {
    const { force = false, ...payload } = options;
    return request<GraphQueryData>(`/query/graph${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ query, ...payload }),
      timeoutMs: 2 * 60_000,
    });
  },
  queryRag: (
    query: string,
    options: { top_k?: number; threshold?: number; force?: boolean } = {},
  ) => {
    const { force = false, ...payload } = options;
    return request<RagQueryData>(`/query/rag${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ query, ...payload }),
      timeoutMs: 2 * 60_000,
    });
  },
  queryHybrid: (
    query: string,
    options: {
      graph_depth?: number;
      rag_top_k?: number;
      graph_confidence?: number;
      rag_threshold?: number;
      force?: boolean;
    } = {},
  ) => {
    const { force = false, ...payload } = options;
    return request<HybridQueryData>(`/query/hybrid${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ query, ...payload }),
      timeoutMs: 2 * 60_000,
    });
  },
  queryAnswer: (
    query: string,
    options: {
      graph_depth?: number;
      rag_top_k?: number;
      graph_confidence?: number;
      rag_threshold?: number;
      force?: boolean;
    } = {},
  ) => {
    const { force = false, ...payload } = options;
    return request<AnswerQueryData>(`/query/answer${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ query, ...payload }),
      timeoutMs: 3 * 60_000,
    });
  },
  queryGlobal: (
    query: string,
    options: { top_k?: number; force?: boolean } = {},
  ) => {
    const { force = false, ...payload } = options;
    return request<GlobalQueryData>(`/query/global${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ query, ...payload }),
      timeoutMs: 2 * 60_000,
    });
  },
  getSearchCache: (page = 1, pageSize = 20) => {
    const parameters = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    return request<SearchCacheListData>(`/search/cache?${parameters.toString()}`);
  },
  getSearchCacheDetail: (cacheKey: string) =>
    request<SearchCacheDetailData>(`/search/cache/${encodeURIComponent(cacheKey)}`),
  clearSearchCache: (staleOnly = false) =>
    request<SearchCacheClearData>(
      `/search/cache${staleOnly ? "?stale_only=true" : ""}`,
      { method: "DELETE" },
    ),
  fullGraph: (nodesPage?: number, nodesPageSize = 100) => {
    const query = nodesPage === undefined
      ? ""
      : `?nodes_page=${nodesPage}&nodes_page_size=${nodesPageSize}`;
    return request<FullGraphData>(`/graph${query}`);
  },
  graphVisualizationMeta: () =>
    request<GraphVisualizationMetaData>("/graph/visualization/meta"),
  graphVisualizationNodes: (
    page = 1,
    pageSize = 1000,
    expectedRevision?: string,
  ) => {
    const parameters = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (expectedRevision) parameters.set("expected_revision", expectedRevision);
    return request<GraphVisualizationNodesData>(
      `/graph/visualization/nodes?${parameters.toString()}`,
    );
  },
  graphVisualizationEdges: (
    page = 1,
    pageSize = 2000,
    expectedRevision?: string,
  ) => {
    const parameters = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (expectedRevision) parameters.set("expected_revision", expectedRevision);
    return request<GraphVisualizationEdgesData>(
      `/graph/visualization/edges?${parameters.toString()}`,
    );
  },
  graphNeighborhood: (
    nodeId: string,
    options: {
      depth?: number;
      direction?: "forward" | "backward" | "both";
      limit?: number;
      edgeLimit?: number;
      expectedRevision?: string;
    } = {},
  ) => {
    const parameters = new URLSearchParams();
    if (options.depth !== undefined) parameters.set("depth", String(options.depth));
    if (options.direction) parameters.set("direction", options.direction);
    if (options.limit !== undefined) parameters.set("limit", String(options.limit));
    if (options.edgeLimit !== undefined) {
      parameters.set("edge_limit", String(options.edgeLimit));
    }
    if (options.expectedRevision) {
      parameters.set("expected_revision", options.expectedRevision);
    }
    const query = parameters.size ? `?${parameters.toString()}` : "";
    return request<GraphNeighborhoodData>(
      `/graph/neighborhood/${encodeURIComponent(nodeId)}${query}`,
    );
  },
  deleteDocument: (sourceId: string) =>
    request<DeleteDocumentData>(`/documents/${encodeURIComponent(sourceId)}`, {
      method: "DELETE",
    }),
  emptyRecycle: () =>
    request<RecycleCleanupData>("/maintenance/recycle", {
      method: "DELETE",
    }),
  getDocuments: (page = 1, pageSize = 20, status?: "active" | "pending" | "all") => {
    const parameters = new URLSearchParams();
    if (page !== 1) parameters.set("page", String(page));
    if (pageSize !== 20) parameters.set("page_size", String(pageSize));
    if (status) parameters.set("status", status);
    const query = parameters.size ? `?${parameters.toString()}` : "";
    return request<DocumentListData>(`/documents${query}`);
  },
  getDocumentContent: (sourceId: string) =>
    request<DocumentContentData>(
      `/documents/${encodeURIComponent(sourceId)}/content`,
    ),
  getConfig: () => request<ConfigData>("/config"),
  saveConfig: (config: ConfigData) =>
    request<ConfigData>("/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  uploadFile: async (file: File) =>
    request<UploadData>("/upload", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content: await file.text() }),
      timeoutMs: 2 * 60_000,
    }),
  importFile: (file: File, ingestAfterImport = true) => {
    const body = new FormData();
    body.append("file", file, file.name);
    return request<ImportData>(`/import?ingest=${ingestAfterImport}`, {
      method: "POST",
      body,
      timeoutMs: null,
    });
  },
};
