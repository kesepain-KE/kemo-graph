import type { IngestData } from "../types/api";

export type IngestHistoryItem = {
  id: string;
  createdAt: string;
  processed: number;
  graphUpdated: number;
  ragUpdated: number;
  failed: number;
};

const STORAGE_KEY = "kemo-graph.ingest-history";

export function loadIngestHistory(): IngestHistoryItem[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? (parsed as IngestHistoryItem[]) : [];
  } catch {
    return [];
  }
}

export function recordIngest(result: IngestData): IngestHistoryItem[] {
  const next = [
    {
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
      processed: result.processed,
      graphUpdated: result.graph_updated,
      ragUpdated: result.rag_updated,
      failed: result.failed,
    },
    ...loadIngestHistory(),
  ].slice(0, 12);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event("kemo:history-updated"));
  return next;
}
