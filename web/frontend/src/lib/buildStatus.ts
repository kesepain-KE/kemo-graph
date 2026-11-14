import type { DocumentRecord } from "../types/api";

export type BuildStatus = DocumentRecord["graph_status"];
export type BuildFilter = "all" | BuildStatus;
export const BUILD_STATUS_LABELS: Record<BuildStatus, string> = {
  pending: "待处理", processing: "处理中", ready: "就绪", failed: "失败",
};
