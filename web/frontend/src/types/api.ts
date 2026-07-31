export type ApiErrorBody = {
  code: string;
  message: string;
};

export type ApiEnvelope<T> =
  | { ok: true; data: T; error: null }
  | { ok: false; data: null; error: ApiErrorBody };

export type SourceSummary = {
  total: number;
  active: number;
  pending_graph: number;
  pending_rag: number;
};

export type StatusData = {
  initialized: boolean;
  sources: SourceSummary;
  graph: {
    total_nodes: number;
    total_edges: number;
    total_groups: number;
  };
  rag: {
    total_chunks: number;
    total_vectors: number;
    faiss_healthy: boolean;
  };
};

export type GraphNode = {
  node_id: string;
  keyword: string;
  summary: string;
  aliases: string[];
  tags: string[];
  ref_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  match_score?: number;
  depth?: number;
  direction?: string;
};

export type GraphEdge = {
  edge_id: string;
  source_node_id: string;
  relation: string;
  target_node_id: string;
  weight: number;
  support_count?: number;
  created_at?: string | null;
};

export type GraphGroup = {
  group_id: string;
  summary: string;
  node_count?: number;
  edge_count?: number;
  node_ids?: string[];
};

export type FullGraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
};

export type GraphQueryData = {
  query: string;
  hit_nodes: GraphNode[];
  expanded_nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
};

export type RagResult = {
  chunk_id: string;
  content: string;
  score: number;
  granularity?: "small" | "medium" | "large";
  parent_chunk_id?: string | null;
  context?: {
    chunk_id: string;
    content: string;
    granularity: "small" | "medium" | "large";
  } | null;
  source: {
    source_id: string;
    relative_path: string | null;
  };
};

export type RagQueryData = {
  query: string;
  results: RagResult[];
};

export type HybridQueryData = {
  query?: string;
  graph: GraphQueryData;
  rag: RagQueryData;
};

export type IngestDetail = {
  path: string;
  graph: string;
  rag: string;
  error?: string;
  graph_error?: string;
  rag_error?: string;
};

export type IngestData = {
  processed: number;
  graph_updated: number;
  rag_updated: number;
  skipped: number;
  failed: number;
  details: IngestDetail[];
};

export type DeleteDocumentData = {
  deleted_source_id: string;
  relative_path: string;
  recycled_path: string | null;
  graph_deleted: boolean;
  rag_deleted: boolean;
};

export type RecycleCleanupData = {
  deleted: number;
  forced: boolean;
};

export type DocumentRecord = {
  source_id: string;
  relative_path: string;
  original_path?: string;
  graph_status: "pending" | "processing" | "ready" | "failed";
  rag_status: "pending" | "processing" | "ready" | "failed";
  exists_status?: "active" | "missing" | "deleted";
  created_at?: string | null;
  updated_at?: string | null;
};

export type DocumentListData = { documents: DocumentRecord[] };

export type DocumentContentData = {
  source_id: string;
  relative_path: string;
  content: string;
};

export type UploadData = {
  source_id: string | null;
  filename: string;
  path: string;
  size: number;
};

export type ImportData = {
  source_id: string;
  original_filename: string;
  detected_format: string;
  markdown_relative_path: string;
  conversion_status: "completed";
  ingest_status: "pending" | "completed" | "failed";
  size: number;
  ingest?: IngestData;
  ingest_error?: string;
};

export type ConfigData = Record<string, unknown>;

export type IngestMode = "graph" | "rag" | "both";
export type SearchMode = "graph" | "rag" | "hybrid";
