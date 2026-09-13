import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api/api";
import type {
  GraphVisualizationEdgesData,
  GraphVisualizationMetaData,
  GraphVisualizationNodesData,
} from "../../../types/api";
import {
  GraphSnapshotChangedError,
  loadGlobalEdges,
  loadGraphCatalog,
  loadGraphSourceLabels,
} from "./GraphDataLoader";

const meta: GraphVisualizationMetaData = {
  revision: "revision-1",
  node_count: 1,
  edge_count: 1,
  group_count: 1,
  groups: [{ group_id: "group-1", summary: "测试群组", node_count: 1 }],
};

const nodesPage: GraphVisualizationNodesData = {
  revision: meta.revision,
  nodes: [{
    node_id: "node-1",
    keyword: "节点一",
    summary: "摘要",
    aliases: [],
    tags: [],
    ref_count: 2,
    source_ids: ["source-1"],
    group_ids: ["group-1"],
  }],
  pagination: { page: 1, page_size: 1000, total: 1, total_pages: 1 },
};

const edgesPage: GraphVisualizationEdgesData = {
  revision: meta.revision,
  edges: [{
    edge_id: "edge-1",
    source_node_id: "node-1",
    relation: "自关联",
    target_node_id: "node-1",
    weight: 0.8,
  }],
  pagination: { page: 1, page_size: 2000, total: 1, total_pages: 1 },
};

afterEach(() => vi.restoreAllMocks());

describe("GraphDataLoader", () => {
  it("assembles catalog groups from independently paged nodes", async () => {
    vi.spyOn(api, "graphVisualizationMeta").mockResolvedValue(meta);
    vi.spyOn(api, "graphVisualizationNodes").mockResolvedValue(nodesPage);

    const catalog = await loadGraphCatalog();

    expect(catalog.revision).toBe(meta.revision);
    expect(catalog.nodes).toHaveLength(1);
    expect(catalog.groups[0].node_ids).toEqual(["node-1"]);
  });

  it("discards a changed snapshot and retries from fresh meta", async () => {
    const metaSpy = vi.spyOn(api, "graphVisualizationMeta").mockResolvedValue(meta);
    vi.spyOn(api, "graphVisualizationNodes")
      .mockResolvedValueOnce({ ...nodesPage, revision: "revision-old" })
      .mockResolvedValueOnce(nodesPage);

    await expect(loadGraphCatalog()).resolves.toMatchObject({ revision: "revision-1" });
    expect(metaSpy).toHaveBeenCalledTimes(2);
  });

  it("rejects incomplete or duplicated relationship pages", async () => {
    vi.spyOn(api, "graphVisualizationEdges").mockResolvedValue({
      ...edgesPage,
      edges: [],
    });

    await expect(loadGlobalEdges(meta)).rejects.toBeInstanceOf(
      GraphSnapshotChangedError,
    );
  });

  it("loads user-facing source filenames across document pages", async () => {
    vi.spyOn(api, "getDocuments")
      .mockResolvedValueOnce({
        documents: [{
          source_id: "source-1",
          relative_path: "markdown/one.md",
          original_path: "notes/one.pdf",
          graph_status: "ready",
          rag_status: "ready",
        }],
        pagination: { page: 1, page_size: 100, total: 2, total_pages: 2 },
      })
      .mockResolvedValueOnce({
        documents: [{
          source_id: "source-2",
          relative_path: "markdown/two.md",
          graph_status: "ready",
          rag_status: "ready",
        }],
        pagination: { page: 2, page_size: 100, total: 2, total_pages: 2 },
      });

    await expect(loadGraphSourceLabels()).resolves.toEqual(new Map([
      ["source-1", "notes/one.pdf"],
      ["source-2", "markdown/two.md"],
    ]));
  });
});
