import type {
  ApiEnvelope,
  ConfigData,
  DeleteDocumentData,
  DocumentContentData,
  DocumentListData,
  FullGraphData,
  GraphQueryData,
  HybridQueryData,
  IngestData,
  IngestMode,
  ImportData,
  RagQueryData,
  RecycleCleanupData,
  StatusData,
  UploadData,
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

type RequestOptions = RequestInit & { timeoutMs?: number };

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
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);

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
    globalThis.clearTimeout(timeout);
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
      timeoutMs: 10 * 60_000,
    }),
  queryGraph: (
    query: string,
    options: { depth?: number; direction?: string; confidence?: number } = {},
  ) =>
    request<GraphQueryData>("/query/graph", {
      method: "POST",
      body: JSON.stringify({ query, ...options }),
      timeoutMs: 2 * 60_000,
    }),
  queryRag: (
    query: string,
    options: { top_k?: number; threshold?: number } = {},
  ) =>
    request<RagQueryData>("/query/rag", {
      method: "POST",
      body: JSON.stringify({ query, ...options }),
      timeoutMs: 2 * 60_000,
    }),
  queryHybrid: (
    query: string,
    options: {
      graph_depth?: number;
      rag_top_k?: number;
      graph_confidence?: number;
      rag_threshold?: number;
    } = {},
  ) =>
    request<HybridQueryData>("/query/hybrid", {
      method: "POST",
      body: JSON.stringify({ query, ...options }),
      timeoutMs: 2 * 60_000,
    }),
  fullGraph: () => request<FullGraphData>("/graph"),
  deleteDocument: (sourceId: string) =>
    request<DeleteDocumentData>(`/documents/${encodeURIComponent(sourceId)}`, {
      method: "DELETE",
    }),
  emptyRecycle: () =>
    request<RecycleCleanupData>("/maintenance/recycle", {
      method: "DELETE",
    }),
  getDocuments: () => request<DocumentListData>("/documents"),
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
      timeoutMs: 10 * 60_000,
    });
  },
};
