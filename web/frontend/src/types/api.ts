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
  weight?: number;
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

export type GraphRelation = GraphEdge & {
  text: string;
};

export type GraphPath = {
  text: string;
  node_ids: string[];
  edge_ids: string[];
  weight: number;
  depth: number;
};

export type GraphGroup = {
  group_id: string;
  summary: string;
  node_count?: number;
  edge_count?: number;
  node_ids?: string[];
};

export type Pagination = {
  page: number | null;
  page_size: number;
  total: number;
  total_pages: number;
};

export type FullGraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
  nodes_pagination: Pagination;
};

export type GraphVisualizationNode = GraphNode & {
  source_ids: string[];
  group_ids: string[];
};

export type GraphVisualizationMetaData = {
  revision: string;
  node_count: number;
  edge_count: number;
  group_count: number;
  groups: GraphGroup[];
};

export type GraphVisualizationNodesData = {
  revision: string;
  nodes: GraphVisualizationNode[];
  pagination: Pagination;
};

export type GraphVisualizationEdgesData = {
  revision: string;
  edges: GraphEdge[];
  pagination: Pagination;
};

export type GraphNeighborhoodData = {
  revision: string;
  anchor_node_id: string;
  depth: number;
  direction: "forward" | "backward" | "both";
  node_limit: number;
  edge_limit: number;
  truncated: boolean;
  edges_truncated: boolean;
  nodes: GraphVisualizationNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
};

export type GraphQueryData = {
  query: string;
  hit_nodes: GraphNode[];
  expanded_nodes: GraphNode[];
  edges: GraphEdge[];
  relations: GraphRelation[];
  paths: GraphPath[];
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
  entities?: Array<Pick<GraphNode, "node_id" | "keyword" | "summary"> & { score: number }>;
  communities?: Array<GraphGroup & { score: number }>;
};

export type AnswerQueryData = {
  query: string;
  answer: string;
  retrieval: HybridQueryData;
};

export type GlobalQueryData = {
  query: string;
  answer: string;
  communities: GraphGroup[];
  key_entities: GraphNode[];
};

export type SearchCacheMode = "answer" | "graph" | "rag" | "hybrid" | "global";

export type SearchCacheItem = {
  cache_key: string;
  query_mode: SearchCacheMode;
  query: string;
  normalized_query: string;
  params: Record<string, unknown>;
  params_json: string;
  result_size: number;
  state_hash: string;
  is_stale: boolean;
  hit_count: number;
  created_at: string;
  updated_at: string;
  last_hit_at: string | null;
};

export type SearchCacheListData = {
  items: SearchCacheItem[];
  total: number;
  page: number;
  page_size: number;
};

export type SearchCacheDetailData = SearchCacheItem & {
  result: AnswerQueryData | GraphQueryData | RagQueryData | HybridQueryData | GlobalQueryData;
};

export type SearchCacheClearData = {
  deleted: number;
  stale_only: boolean;
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
  content_hash?: string;
  graph_hash?: string | null;
  rag_hash?: string | null;
  graph_status: "pending" | "processing" | "ready" | "failed";
  rag_status: "pending" | "processing" | "ready" | "failed";
  exists_status?: "active" | "missing" | "deleted";
  created_at?: string | null;
  updated_at?: string | null;
};

export type DocumentListData = {
  documents: DocumentRecord[];
  pagination: Pagination;
};

export type DocumentContentData = {
  source_id: string;
  relative_path: string;
  content_hash: string;
  graph_hash?: string | null;
  rag_hash?: string | null;
  graph_status: DocumentRecord["graph_status"];
  rag_status: DocumentRecord["rag_status"];
  content: string;
};

export type DocumentContentUpdateData = {
  source_id: string;
  relative_path: string;
  changed: boolean;
  previous_content_hash: string;
  content_hash: string;
  graph_status: DocumentRecord["graph_status"];
  rag_status: DocumentRecord["rag_status"];
  updated_at?: string;
};

export type DocumentBatchDeleteData = {
  requested: number;
  deleted: number;
  failed: number;
  documents: DeleteDocumentData[];
  failures: Array<{ source_id: string; error_type: string; message: string }>;
  search_cache_deleted: number;
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

export type MaintenanceJobKind =
  | "ingest"
  | "organize_graph"
  | "rebuild_knowledge_base"
  | "rebuild_all"
  | "summarize"
  | "cleanup_recycle"
  | "update";

export type UpdateStatusData = {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  force_update_available: boolean;
  installation_mode: "git" | "source" | string;
  checked_at: string | null;
  worktree_clean: boolean;
  dirty_files: string[];
  can_apply: boolean;
  can_force_apply: boolean;
  blocking_reasons: string[];
  phase: "idle" | "checking" | "updating" | "completed" | "failed" | string;
  restart_required: boolean;
  error: string | null;
  repository_url: string;
  version_url: string;
};

export type RuntimeStatusData = {
  pid: number;
  restart_available: boolean;
  restart_pending: boolean;
  version: string;
};

export type RestartData = {
  restart_id: string;
  old_pid: number;
  status: "scheduled";
  message: string;
};

export type MaintenanceJobStatus = "queued" | "running" | "completed" | "failed";

export type MaintenanceJobEvent = {
  event_id: string;
  level: string;
  message: string;
  created_at: string;
};

export type MaintenanceJob = {
  job_id: string;
  kind: MaintenanceJobKind;
  status: MaintenanceJobStatus;
  progress: number;
  detail: string;
  result: unknown;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  events: MaintenanceJobEvent[];
};

export type MaintenanceJobListData = { jobs: MaintenanceJob[] };

export type IngestMode = "graph" | "rag" | "both";
export type SearchMode = SearchCacheMode;
