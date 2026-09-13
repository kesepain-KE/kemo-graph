import type { DocumentRecord } from "../types/api";

export type BuildStatus = DocumentRecord["graph_status"];
export type BuildFilter = "all" | BuildStatus;
export const BUILD_STATUS_LABELS: Record<BuildStatus, string> = {
  pending: "待处理", processing: "处理中", ready: "就绪", failed: "失败",
};
export const BUILD_FILTER_OPTIONS = [
  { value: "all", label: "全部" },
  ...Object.entries(BUILD_STATUS_LABELS).map(([value, label]) => ({ value, label })),
];

export function filterBuildDocuments(documents: DocumentRecord[], query: string, graph: BuildFilter, rag: BuildFilter) {
  const keyword = query.trim().toLocaleLowerCase();
  return documents.filter((document) =>
    (!document.exists_status || document.exists_status === "active")
    && document.relative_path.toLocaleLowerCase().includes(keyword)
    && (graph === "all" || document.graph_status === graph)
    && (rag === "all" || document.rag_status === rag),
  );
}

export function countBuildStatuses(documents: DocumentRecord[], field: "graph_status" | "rag_status") {
  const counts: Record<BuildStatus, number> = { pending: 0, processing: 0, ready: 0, failed: 0 };
  for (const document of documents) {
    if (!document.exists_status || document.exists_status === "active") counts[document[field]] += 1;
  }
  return counts;
}
