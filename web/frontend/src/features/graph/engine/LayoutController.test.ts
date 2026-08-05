import { afterEach, describe, expect, it, vi } from "vitest";

import type { GraphEdge } from "../../../types/api";
import type { PositionedNode } from "../../../lib/forceLayout";
import { WebGpuLayout } from "../webgpu/WebGpuLayout";
import { LayoutController, shouldPreferWebGpu } from "./LayoutController";
import { DEFAULT_FORCE_SETTINGS, type WorkerInboundMessage } from "./layoutTypes";

class FakeWorker {
  static instances: FakeWorker[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly messages: WorkerInboundMessage[] = [];
  terminated = false;

  constructor() {
    FakeWorker.instances.push(this);
  }

  postMessage(message: WorkerInboundMessage): void {
    this.messages.push(message);
  }

  terminate(): void {
    this.terminated = true;
  }
}

const nodes: PositionedNode[] = [
  {
    node_id: "a",
    keyword: "A",
    summary: "A",
    aliases: [],
    tags: [],
    ref_count: 1,
    x: 300,
    y: 350,
  },
  {
    node_id: "b",
    keyword: "B",
    summary: "B",
    aliases: [],
    tags: [],
    ref_count: 3,
    x: 700,
    y: 350,
  },
];

const edges: GraphEdge[] = [{
  edge_id: "edge",
  source_node_id: "a",
  relation: "关联",
  target_node_id: "b",
  weight: 0.8,
}];

afterEach(() => {
  FakeWorker.instances = [];
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LayoutController", () => {
  it("prefers WebGPU for every non-empty graph outside compatibility mode", () => {
    expect(shouldPreferWebGpu("compatible", 5000)).toBe(false);
    expect(shouldPreferWebGpu("auto", 0)).toBe(false);
    expect(shouldPreferWebGpu("auto", 1)).toBe(true);
    expect(shouldPreferWebGpu("high", 2)).toBe(true);
  });

  it("transfers a typed graph to a worker and terminates it on destroy", async () => {
    vi.stubGlobal("Worker", FakeWorker);
    const positions = vi.fn();
    const statuses = vi.fn();
    const controller = new LayoutController(positions, statuses);

    await controller.start(nodes, edges, DEFAULT_FORCE_SETTINGS, "layout-key");

    const worker = FakeWorker.instances[0];
    expect(worker.messages[0]).toMatchObject({ type: "init", warmStart: false });
    const initMessage = worker.messages[0];
    expect(initMessage.type).toBe("init");
    if (initMessage.type === "init") {
      const radii = new Float32Array(initMessage.collisionRadii);
      expect(radii).toHaveLength(nodes.length);
      expect(radii[1]).toBeGreaterThan(radii[0]);
      expect(initMessage.world.width).toBeGreaterThanOrEqual(1000);
    }
    expect(positions).toHaveBeenCalledTimes(1);
    controller.destroy();
    expect(worker.messages.at(-1)).toEqual({ type: "stop" });
    expect(worker.terminated).toBe(true);
  });

  it("falls back to Worker when WebGPU initialization fails", async () => {
    vi.stubGlobal("Worker", FakeWorker);
    vi.spyOn(WebGpuLayout, "create").mockRejectedValue(
      new Error("测试适配器不可用"),
    );
    const statuses = vi.fn();
    const controller = new LayoutController(vi.fn(), statuses);

    await controller.start(nodes, edges, DEFAULT_FORCE_SETTINGS, "layout-key", {
      performanceMode: "high",
      webGpuTimeoutMs: 10,
    });

    expect(FakeWorker.instances).toHaveLength(1);
    expect(statuses).toHaveBeenLastCalledWith(expect.objectContaining({
      backend: "worker",
      running: true,
      fallbackReason: "测试适配器不可用",
    }));
    controller.destroy();
  });
});
