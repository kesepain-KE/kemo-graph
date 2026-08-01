import type { GraphEdge } from "../../../types/api";
import type { PositionedNode } from "../../../lib/forceLayout";
import {
  readLayoutPositions,
  writeLayoutPositions,
} from "../data/GraphPositionCache";
import type { GraphPerformanceMode } from "./GraphRenderer";
import { resolvePositionCollisions } from "./collision";
import {
  WebGpuLayout,
  type WebGpuGraphInput,
} from "../webgpu/WebGpuLayout";
import type {
  ForceSettings,
  LayoutRuntimeStatus,
  WorkerInboundMessage,
  WorkerOutboundMessage,
} from "./layoutTypes";
import {
  calculateLayoutWorld,
  graphNodeCollisionRadius,
} from "./layoutTypes";

type PositionListener = (nodeIds: string[], positions: Float32Array) => void;
type StatusListener = (status: LayoutRuntimeStatus) => void;

type LayoutStartOptions = {
  performanceMode?: GraphPerformanceMode;
  webGpuTimeoutMs?: number;
  nodeScale?: number;
};

type PreparedLayoutGraph = Omit<WebGpuGraphInput, "positions">;

export class LayoutController {
  private worker: Worker | null = null;
  private webGpu: WebGpuLayout | null = null;
  private generation = 0;
  private nodeIds: string[] = [];
  private indexById = new Map<string, number>();
  private positions: Float32Array<ArrayBuffer> = new Float32Array();
  private cacheKey: string | null = null;
  private cacheWritten = false;
  private settings: ForceSettings | null = null;
  private fallbackReason: string | undefined;

  constructor(
    private readonly onPositions: PositionListener,
    private readonly onStatus: StatusListener,
  ) {}

  async start(
    nodes: PositionedNode[],
    edges: GraphEdge[],
    settings: ForceSettings,
    cacheKey: string,
    options: LayoutStartOptions = {},
  ): Promise<void> {
    this.stopBackends();
    const generation = ++this.generation;
    this.nodeIds = nodes.map((node) => node.node_id);
    this.indexById = new Map(
      this.nodeIds.map((nodeId, index) => [nodeId, index]),
    );
    this.cacheKey = cacheKey;
    this.cacheWritten = false;
    this.settings = settings;
    this.fallbackReason = undefined;

    const graphData = prepareLayoutGraph(
      nodes,
      edges,
      this.indexById,
      options.nodeScale ?? 1,
    );
    const cached = await readLayoutPositions(cacheKey, this.nodeIds).catch(() => null);
    if (generation !== this.generation) return;
    this.positions = cached ?? positionsFromNodes(nodes);
    resolvePositionCollisions(
      this.positions,
      graphData.collisionRadii,
      graphData.world,
      12,
    );
    this.onPositions(this.nodeIds, this.positions.slice());

    if (nodes.length === 0) {
      this.onStatus({
        backend: "worker",
        running: false,
        stable: true,
        energy: 0,
        iterations: 0,
      });
      return;
    }

    const performanceMode = options.performanceMode ?? "auto";
    if (shouldPreferWebGpu(performanceMode, nodes.length)) {
      this.onStatus({
        backend: "webgpu",
        running: false,
        stable: false,
        energy: 0,
        iterations: 0,
      });
      try {
        const webGpu = await WebGpuLayout.create(
          {
            positions: this.positions.slice(),
            masses: graphData.masses,
            collisionRadii: graphData.collisionRadii,
            edgeSources: graphData.edgeSources,
            edgeTargets: graphData.edgeTargets,
            edgeWeights: graphData.edgeWeights,
            world: graphData.world,
          },
          settings,
          {
            onPositions: (positions) => {
              if (generation !== this.generation) return;
              this.positions = positions;
              this.onPositions(this.nodeIds, positions);
            },
            onStatus: (status) => {
              if (generation !== this.generation) return;
              this.onStatus(status);
              if (status.stable) this.writeStableCache();
            },
            onLost: (reason) => {
              if (generation !== this.generation) return;
              this.fallbackReason = reason;
              this.stopWebGpu();
              this.startWorker(
                generation,
                graphData,
                this.settings ?? settings,
                true,
                reason,
              );
            },
          },
          options.webGpuTimeoutMs,
        );
        if (generation !== this.generation) {
          webGpu.destroy();
          return;
        }
        this.webGpu = webGpu;
        webGpu.start(cached !== null);
        return;
      } catch (caught) {
        if (generation !== this.generation) return;
        this.fallbackReason = caught instanceof Error
          ? caught.message
          : "WebGPU 初始化失败";
      }
    }

    this.startWorker(
      generation,
      graphData,
      settings,
      cached !== null,
      this.fallbackReason,
    );
  }

  private startWorker(
    generation: number,
    graphData: PreparedLayoutGraph,
    settings: ForceSettings,
    warmStart: boolean,
    fallbackReason?: string,
  ): void {
    if (!("Worker" in globalThis)) {
      this.onStatus({
        backend: "worker",
        running: false,
        stable: false,
        energy: 0,
        iterations: 0,
        fallbackReason: fallbackReason ?? "当前浏览器未提供 Web Worker",
      });
      return;
    }

    let worker: Worker;
    try {
      worker = new Worker(new URL("../worker/layout.worker.ts", import.meta.url), {
        type: "module",
      });
    } catch {
      this.onStatus({
        backend: "worker",
        running: false,
        stable: false,
        energy: 0,
        iterations: 0,
        fallbackReason,
      });
      return;
    }
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<WorkerOutboundMessage>) => {
      if (generation !== this.generation) return;
      this.handleWorkerMessage(event.data);
    };
    worker.onerror = () => {
      if (generation !== this.generation) return;
      this.onStatus({
        backend: "worker",
        running: false,
        stable: false,
        energy: 0,
        iterations: 0,
        fallbackReason,
      });
      this.stopWorker();
    };

    const masses = graphData.masses.slice();
    const collisionRadii = graphData.collisionRadii.slice();
    const sources = graphData.edgeSources.slice();
    const targets = graphData.edgeTargets.slice();
    const weights = graphData.edgeWeights.slice();
    const workerPositions = this.positions.slice();
    const message: WorkerInboundMessage = {
      type: "init",
      positions: workerPositions.buffer,
      masses: masses.buffer,
      collisionRadii: collisionRadii.buffer,
      edgeSources: sources.buffer,
      edgeTargets: targets.buffer,
      edgeWeights: weights.buffer,
      world: graphData.world,
      settings,
      warmStart,
    };
    worker.postMessage(message, [
      workerPositions.buffer,
      masses.buffer,
      collisionRadii.buffer,
      sources.buffer,
      targets.buffer,
      weights.buffer,
    ]);
    this.onStatus({
      backend: "worker",
      running: true,
      stable: false,
      energy: 0,
      iterations: 0,
      fallbackReason,
    });
  }

  updateSettings(settings: ForceSettings, cacheKey?: string): void {
    this.settings = settings;
    if (cacheKey && cacheKey !== this.cacheKey) {
      this.cacheKey = cacheKey;
      this.cacheWritten = false;
    }
    if (this.webGpu) this.webGpu.updateSettings(settings);
    else this.post({ type: "settings", settings });
  }

  play(): void {
    if (this.webGpu) this.webGpu.play();
    else this.post({ type: "play" });
  }

  pause(): void {
    if (this.webGpu) this.webGpu.pause();
    else this.post({ type: "pause" });
  }

  reheat(): void {
    this.cacheWritten = false;
    if (this.webGpu) this.webGpu.reheat();
    else this.post({ type: "reheat" });
  }

  pin(nodeId: string, x: number, y: number): void {
    const index = this.indexById.get(nodeId);
    if (index === undefined) return;
    this.positions[index * 2] = x;
    this.positions[index * 2 + 1] = y;
    if (this.webGpu) this.webGpu.pin(index, x, y);
    else this.post({ type: "pin", index, x, y });
  }

  unpin(nodeId: string): void {
    const index = this.indexById.get(nodeId);
    if (index === undefined) return;
    if (this.webGpu) this.webGpu.unpin(index);
    else this.post({ type: "unpin", index });
  }

  destroy(): void {
    this.generation += 1;
    this.stopBackends();
    this.nodeIds = [];
    this.indexById.clear();
    this.positions = new Float32Array();
    this.cacheKey = null;
    this.settings = null;
    this.fallbackReason = undefined;
  }

  private handleWorkerMessage(message: WorkerOutboundMessage): void {
    if (message.type === "positions") {
      this.positions = new Float32Array(message.positions);
      this.onPositions(this.nodeIds, this.positions);
      this.onStatus({
        backend: "worker",
        running: true,
        stable: false,
        energy: message.energy,
        iterations: message.iterations,
        fallbackReason: this.fallbackReason,
      });
      return;
    }
    this.onStatus({
      backend: "worker",
      running: message.running,
      stable: message.stable,
      energy: message.energy,
      iterations: message.iterations,
      fallbackReason: this.fallbackReason,
    });
    if (message.stable) this.writeStableCache();
  }

  private post(message: WorkerInboundMessage): void {
    try {
      this.worker?.postMessage(message);
    } catch {
      // A failing worker is normalized by its error callback.
    }
  }

  private writeStableCache(): void {
    if (this.cacheWritten || !this.cacheKey) return;
    this.cacheWritten = true;
    void writeLayoutPositions(
      this.cacheKey,
      this.nodeIds,
      this.positions,
    ).catch(() => undefined);
  }

  private stopBackends(): void {
    this.stopWorker();
    this.stopWebGpu();
  }

  private stopWebGpu(): void {
    this.webGpu?.destroy();
    this.webGpu = null;
  }

  private stopWorker(): void {
    if (!this.worker) return;
    try {
      this.worker.postMessage({ type: "stop" } satisfies WorkerInboundMessage);
    } catch {
      // The browser may already have terminated a failed worker.
    }
    this.worker.terminate();
    this.worker = null;
  }
}

export function shouldPreferWebGpu(
  mode: GraphPerformanceMode,
  nodeCount: number,
): boolean {
  if (mode === "compatible" || nodeCount === 0) return false;
  return mode === "high" || nodeCount >= 256;
}

function prepareLayoutGraph(
  nodes: PositionedNode[],
  edges: GraphEdge[],
  indexById: Map<string, number>,
  nodeScale: number,
): PreparedLayoutGraph {
  const masses = new Float32Array(
    nodes.map((node) => 1 + Math.log1p(Math.max(0, node.ref_count))),
  );
  const mappedEdges = edges.flatMap((edge) => {
    const source = indexById.get(edge.source_node_id);
    const target = indexById.get(edge.target_node_id);
    return source === undefined || target === undefined
      ? []
      : [{ source, target, weight: edge.weight }];
  });
  const collisionRadii = new Float32Array(
    nodes.map((node) => graphNodeCollisionRadius(node.ref_count, nodeScale)),
  );
  return {
    masses,
    collisionRadii,
    edgeSources: new Uint32Array(mappedEdges.map((edge) => edge.source)),
    edgeTargets: new Uint32Array(mappedEdges.map((edge) => edge.target)),
    edgeWeights: new Float32Array(mappedEdges.map((edge) => edge.weight)),
    world: calculateLayoutWorld(collisionRadii),
  };
}

function positionsFromNodes(nodes: PositionedNode[]): Float32Array<ArrayBuffer> {
  const positions = new Float32Array(nodes.length * 2);
  nodes.forEach((node, index) => {
    positions[index * 2] = node.x;
    positions[index * 2 + 1] = node.y;
  });
  return positions;
}

export type { ForceSettings, LayoutRuntimeStatus } from "./layoutTypes";
