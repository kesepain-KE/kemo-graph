import { api, ApiClientError } from "../../../api/api";
import type {
  GraphEdge,
  GraphGroup,
  GraphVisualizationMetaData,
  GraphVisualizationNode,
} from "../../../types/api";

const NODE_PAGE_SIZE = 1000;
const EDGE_PAGE_SIZE = 2000;
const SNAPSHOT_ATTEMPTS = 3;

export class GraphSnapshotChangedError extends Error {
  readonly code = "GRAPH_CHANGED";

  constructor(message = "知识图谱在分页加载期间发生变化") {
    super(message);
    this.name = "GraphSnapshotChangedError";
  }
}

export type GraphCatalog = {
  revision: string;
  nodes: GraphVisualizationNode[];
  groups: GraphGroup[];
  meta: GraphVisualizationMetaData;
};

export async function loadGraphCatalog(): Promise<GraphCatalog> {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < SNAPSHOT_ATTEMPTS; attempt += 1) {
    try {
      return await loadGraphCatalogSnapshot();
    } catch (caught) {
      lastError = caught;
      if (!isGraphRevisionChanged(caught)) throw caught;
    }
  }
  throw lastError ?? new GraphSnapshotChangedError();
}

async function loadGraphCatalogSnapshot(): Promise<GraphCatalog> {
  const meta = await api.graphVisualizationMeta();
  const pages = Math.ceil(meta.node_count / NODE_PAGE_SIZE);
  const responses = await Promise.all(
    Array.from({ length: pages }, (_, index) => (
      api.graphVisualizationNodes(index + 1, NODE_PAGE_SIZE, meta.revision)
    )),
  );
  const nodes = responses.flatMap((response) => response.nodes);
  assertSnapshotPages(
    meta.revision,
    meta.node_count,
    responses.map((response) => ({
      revision: response.revision,
      total: response.pagination.total,
    })),
    nodes.map((node) => node.node_id),
    "节点",
  );
  const members = new Map<string, string[]>();
  for (const node of nodes) {
    for (const groupId of node.group_ids) {
      const groupMembers = members.get(groupId) ?? [];
      groupMembers.push(node.node_id);
      members.set(groupId, groupMembers);
    }
  }
  return {
    revision: meta.revision,
    nodes,
    groups: meta.groups.map((group) => ({
      ...group,
      node_ids: [...(members.get(group.group_id) ?? [])].sort(),
    })),
    meta,
  };
}

export async function loadGlobalEdges(
  meta: GraphVisualizationMetaData,
): Promise<GraphEdge[]> {
  const pages = Math.ceil(meta.edge_count / EDGE_PAGE_SIZE);
  const responses = await Promise.all(
    Array.from({ length: pages }, (_, index) => (
      api.graphVisualizationEdges(index + 1, EDGE_PAGE_SIZE, meta.revision)
    )),
  );
  const edges = responses.flatMap((response) => response.edges);
  assertSnapshotPages(
    meta.revision,
    meta.edge_count,
    responses.map((response) => ({
      revision: response.revision,
      total: response.pagination.total,
    })),
    edges.map((edge) => edge.edge_id),
    "关系",
  );
  return edges;
}

export async function loadGraphSourceLabels(): Promise<Map<string, string>> {
  const first = await api.getDocuments(1, 100, "all");
  const remaining = await Promise.all(
    Array.from(
      { length: Math.max(0, first.pagination.total_pages - 1) },
      (_, index) => api.getDocuments(index + 2, 100, "all"),
    ),
  );
  return new Map(
    [first, ...remaining]
      .flatMap((page) => page.documents)
      .map((document) => [
        document.source_id,
        document.original_path || document.relative_path || document.source_id,
      ]),
  );
}

export function isGraphRevisionChanged(caught: unknown): boolean {
  return caught instanceof GraphSnapshotChangedError
    || (caught instanceof ApiClientError && caught.code === "GRAPH_CHANGED")
    || (
      typeof caught === "object"
      && caught !== null
      && "code" in caught
      && caught.code === "GRAPH_CHANGED"
    );
}

function assertSnapshotPages(
  expectedRevision: string,
  expectedTotal: number,
  pages: Array<{ revision: string; total: number }>,
  ids: string[],
  itemLabel: string,
): void {
  if (pages.some((page) => (
    page.revision !== expectedRevision || page.total !== expectedTotal
  ))) {
    throw new GraphSnapshotChangedError(`${itemLabel}分页快照版本不一致`);
  }
  if (ids.length !== expectedTotal || new Set(ids).size !== ids.length) {
    throw new GraphSnapshotChangedError(`${itemLabel}分页数据不完整或包含重复项`);
  }
}
