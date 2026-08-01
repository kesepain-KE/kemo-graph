import {
  expandLayoutWorldToFit,
  type ForceSettings,
  type LayoutRuntimeStatus,
  type LayoutWorld,
} from "../engine/layoutTypes";
import { resolvePositionCollisions } from "../engine/collision";
import shaderSource from "./force-layout.wgsl?raw";
import {
  alignedBufferSize,
  buildWebGpuAdjacency,
  createWebGpuParamsBuffer,
} from "./buffers";
import { inspectWebGpuCapability } from "./capabilities";

const GRID_WIDTH = 32;
const GRID_HEIGHT = 24;
const MAX_PER_CELL = 128;
const WORKGROUP_SIZE = 128;
const DEFAULT_INITIALIZATION_TIMEOUT_MS = 5_000;
const REQUIRED_STORAGE_BUFFERS = 12;

export type WebGpuGraphInput = {
  positions: Float32Array<ArrayBuffer>;
  masses: Float32Array<ArrayBuffer>;
  collisionRadii: Float32Array<ArrayBuffer>;
  edgeSources: Uint32Array<ArrayBuffer>;
  edgeTargets: Uint32Array<ArrayBuffer>;
  edgeWeights: Float32Array<ArrayBuffer>;
  world: LayoutWorld;
};

export type WebGpuLayoutCallbacks = {
  onPositions: (positions: Float32Array<ArrayBuffer>) => void;
  onStatus: (status: LayoutRuntimeStatus) => void;
  onLost: (reason: string) => void;
};

export class WebGpuUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebGpuUnavailableError";
  }
}

type Pipelines = {
  clearGrid: GPUComputePipeline;
  binNodes: GPUComputePipeline;
  integrate: GPUComputePipeline;
};

type Buffers = {
  positionsA: GPUBuffer;
  positionsB: GPUBuffer;
  velocitiesA: GPUBuffer;
  velocitiesB: GPUBuffer;
  masses: GPUBuffer;
  gridCounts: GPUBuffer;
  gridIndices: GPUBuffer;
  adjacencyOffsets: GPUBuffer;
  adjacencyTargets: GPUBuffer;
  adjacencyWeights: GPUBuffer;
  pinned: GPUBuffer;
  collisionRadii: GPUBuffer;
  params: GPUBuffer;
  readback: GPUBuffer;
};

export class WebGpuLayout {
  readonly gpuName: string;

  private pipelines: Pipelines | null = null;
  private buffers: Buffers | null = null;
  private bindGroupAToB: GPUBindGroup | null = null;
  private bindGroupBToA: GPUBindGroup | null = null;
  private settings: ForceSettings;
  private positions: Float32Array<ArrayBuffer>;
  private readonly pinnedData: Float32Array<ArrayBuffer>;
  private currentIsA = true;
  private alpha = 1;
  private energy = 0;
  private iterations = 0;
  private stableFrames = 0;
  private running = false;
  private destroyed = false;
  private resourcesDestroyed = false;
  private failed = false;
  private frameInFlight = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  private constructor(
    private readonly device: GPUDevice,
    private readonly input: WebGpuGraphInput,
    settings: ForceSettings,
    private readonly callbacks: WebGpuLayoutCallbacks,
    gpuName: string,
  ) {
    this.settings = settings;
    this.positions = input.positions.slice();
    this.pinnedData = new Float32Array(input.masses.length * 4);
    this.gpuName = gpuName;
  }

  static async create(
    input: WebGpuGraphInput,
    settings: ForceSettings,
    callbacks: WebGpuLayoutCallbacks,
    initializationTimeoutMs = DEFAULT_INITIALIZATION_TIMEOUT_MS,
  ): Promise<WebGpuLayout> {
    const capability = inspectWebGpuCapability();
    if (!capability.available || !navigator.gpu) {
      throw new WebGpuUnavailableError(capability.reason ?? "WebGPU 不可用");
    }
    const adapter = await withTimeout(
      navigator.gpu.requestAdapter({ powerPreference: "high-performance" }),
      initializationTimeoutMs,
      "请求高性能 WebGPU 适配器超时",
    );
    if (!adapter) throw new WebGpuUnavailableError("未找到可用的 WebGPU 适配器");
    if (adapter.limits.maxStorageBuffersPerShaderStage < REQUIRED_STORAGE_BUFFERS) {
      throw new WebGpuUnavailableError(
        `显卡每阶段仅支持 ${adapter.limits.maxStorageBuffersPerShaderStage} 个存储缓冲区，图谱布局至少需要 ${REQUIRED_STORAGE_BUFFERS} 个`,
      );
    }
    const device = await withTimeout(
      adapter.requestDevice({
        requiredLimits: {
          maxStorageBuffersPerShaderStage: REQUIRED_STORAGE_BUFFERS,
        },
      }),
      initializationTimeoutMs,
      "创建 WebGPU 设备超时",
    );
    const layout = new WebGpuLayout(
      device,
      input,
      settings,
      callbacks,
      adapterName(adapter),
    );
    try {
      await layout.initialize();
      layout.watchDevice();
      return layout;
    } catch (caught) {
      layout.destroy();
      throw caught;
    }
  }

  start(warmStart: boolean): void {
    if (this.destroyed || !this.buffers) return;
    this.alpha = warmStart ? 0.28 : 1;
    this.energy = 0;
    this.iterations = 0;
    this.stableFrames = 0;
    this.running = true;
    this.emitStatus(false);
    this.schedule();
  }

  updateSettings(settings: ForceSettings): void {
    this.settings = settings;
    this.alpha = Math.max(this.alpha, 0.72);
    this.stableFrames = 0;
    this.running = true;
    this.schedule();
  }

  play(): void {
    if (this.destroyed) return;
    this.running = true;
    this.schedule();
    this.emitStatus(false);
  }

  pause(): void {
    this.running = false;
    this.cancelTimer();
    this.emitStatus(false);
  }

  reheat(): void {
    this.alpha = 1;
    this.stableFrames = 0;
    this.running = true;
    this.schedule();
  }

  pin(index: number, x: number, y: number): void {
    if (!this.buffers || index < 0 || index >= this.input.masses.length) return;
    const offset = index * 4;
    this.pinnedData[offset] = x;
    this.pinnedData[offset + 1] = y;
    this.pinnedData[offset + 2] = 1;
    this.device.queue.writeBuffer(
      this.buffers.pinned,
      offset * Float32Array.BYTES_PER_ELEMENT,
      this.pinnedData,
      offset,
      4,
    );
    const position = new Float32Array([x, y]);
    const positionOffset = index * 2 * Float32Array.BYTES_PER_ELEMENT;
    this.device.queue.writeBuffer(this.buffers.positionsA, positionOffset, position);
    this.device.queue.writeBuffer(this.buffers.positionsB, positionOffset, position);
    this.positions[index * 2] = x;
    this.positions[index * 2 + 1] = y;
    this.alpha = Math.max(this.alpha, 0.5);
    this.running = true;
    this.schedule();
  }

  unpin(index: number): void {
    if (!this.buffers || index < 0 || index >= this.input.masses.length) return;
    const offset = index * 4;
    this.pinnedData[offset + 2] = 0;
    this.device.queue.writeBuffer(
      this.buffers.pinned,
      offset * Float32Array.BYTES_PER_ELEMENT,
      this.pinnedData,
      offset,
      4,
    );
    this.alpha = Math.max(this.alpha, 0.35);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.running = false;
    this.cancelTimer();
    // mapAsync cannot be cancelled. Keep GPU resources alive until the sole
    // in-flight readback settles, then release them from runFrame.finally.
    if (this.frameInFlight) return;
    this.finalizeDestroy();
  }

  private finalizeDestroy(): void {
    if (this.resourcesDestroyed) return;
    this.resourcesDestroyed = true;
    if (this.buffers?.readback.mapState === "mapped") {
      this.buffers.readback.unmap();
    }
    if (this.buffers) {
      for (const buffer of Object.values(this.buffers)) buffer.destroy();
    }
    this.buffers = null;
    this.bindGroupAToB = null;
    this.bindGroupBToA = null;
    this.pipelines = null;
    this.device.destroy();
  }

  private async initialize(): Promise<void> {
    if (
      this.input.positions.length !== this.input.masses.length * 2
      || this.input.collisionRadii.length !== this.input.masses.length
    ) {
      throw new WebGpuUnavailableError("WebGPU 节点位置与质量维度不一致");
    }
    const adjacency = buildWebGpuAdjacency(
      this.input.masses.length,
      this.input.edgeSources,
      this.input.edgeTargets,
      this.input.edgeWeights,
    );
    const velocities = new Float32Array(this.input.positions.length);
    const positionBytes = this.input.positions.byteLength;
    const velocityBytes = velocities.byteLength;
    this.device.pushErrorScope("validation");
    const buffers: Buffers = {
      positionsA: createBuffer(this.device, "positions-a", this.input.positions, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST),
      positionsB: createBuffer(this.device, "positions-b", this.input.positions, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST),
      velocitiesA: createBuffer(this.device, "velocities-a", velocities, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC),
      velocitiesB: createBuffer(this.device, "velocities-b", velocities, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC),
      masses: createBuffer(this.device, "masses", this.input.masses, GPUBufferUsage.STORAGE),
      gridCounts: createEmptyBuffer(this.device, "grid-counts", GRID_WIDTH * GRID_HEIGHT * 4, GPUBufferUsage.STORAGE),
      gridIndices: createEmptyBuffer(this.device, "grid-indices", GRID_WIDTH * GRID_HEIGHT * MAX_PER_CELL * 4, GPUBufferUsage.STORAGE),
      adjacencyOffsets: createBuffer(this.device, "adjacency-offsets", adjacency.offsets, GPUBufferUsage.STORAGE),
      adjacencyTargets: createBuffer(this.device, "adjacency-targets", adjacency.targets, GPUBufferUsage.STORAGE),
      adjacencyWeights: createBuffer(this.device, "adjacency-weights", adjacency.weights, GPUBufferUsage.STORAGE),
      pinned: createBuffer(this.device, "pinned-nodes", this.pinnedData, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST),
      collisionRadii: createBuffer(this.device, "collision-radii", this.input.collisionRadii, GPUBufferUsage.STORAGE),
      params: createEmptyBuffer(this.device, "force-params", 64, GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST),
      readback: createEmptyBuffer(this.device, "layout-readback", positionBytes + velocityBytes, GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST),
    };
    const layout = this.device.createBindGroupLayout({
      label: "graph-force-layout",
      entries: [
        storageEntry(0, "read-only-storage"),
        storageEntry(1, "read-only-storage"),
        storageEntry(2, "storage"),
        storageEntry(3, "storage"),
        storageEntry(4, "read-only-storage"),
        storageEntry(5, "storage"),
        storageEntry(6, "storage"),
        storageEntry(7, "read-only-storage"),
        storageEntry(8, "read-only-storage"),
        storageEntry(9, "read-only-storage"),
        storageEntry(10, "read-only-storage"),
        storageEntry(11, "read-only-storage"),
        { binding: 12, visibility: GPUShaderStage.COMPUTE, buffer: { type: "uniform" } },
      ],
    });
    const pipelineLayout = this.device.createPipelineLayout({ bindGroupLayouts: [layout] });
    const module = this.device.createShaderModule({ label: "graph-force-shader", code: shaderSource });
    this.pipelines = {
      clearGrid: this.device.createComputePipeline({ layout: pipelineLayout, compute: { module, entryPoint: "clearGrid" } }),
      binNodes: this.device.createComputePipeline({ layout: pipelineLayout, compute: { module, entryPoint: "binNodes" } }),
      integrate: this.device.createComputePipeline({ layout: pipelineLayout, compute: { module, entryPoint: "integrate" } }),
    };
    this.buffers = buffers;
    this.bindGroupAToB = this.createBindGroup(layout, buffers.positionsA, buffers.velocitiesA, buffers.positionsB, buffers.velocitiesB);
    this.bindGroupBToA = this.createBindGroup(layout, buffers.positionsB, buffers.velocitiesB, buffers.positionsA, buffers.velocitiesA);
    const validationError = await this.device.popErrorScope();
    if (validationError) {
      throw new WebGpuUnavailableError(`WebGPU 管线校验失败：${validationError.message}`);
    }
  }

  private createBindGroup(
    layout: GPUBindGroupLayout,
    currentPositions: GPUBuffer,
    currentVelocities: GPUBuffer,
    nextPositions: GPUBuffer,
    nextVelocities: GPUBuffer,
  ): GPUBindGroup {
    if (!this.buffers) {
      throw new WebGpuUnavailableError("WebGPU 缓冲区尚未初始化");
    }
    const buffers = this.buffers;
    return this.device.createBindGroup({
      layout,
      entries: [
        bufferEntry(0, currentPositions),
        bufferEntry(1, currentVelocities),
        bufferEntry(2, nextPositions),
        bufferEntry(3, nextVelocities),
        bufferEntry(4, buffers.masses),
        bufferEntry(5, buffers.gridCounts),
        bufferEntry(6, buffers.gridIndices),
        bufferEntry(7, buffers.adjacencyOffsets),
        bufferEntry(8, buffers.adjacencyTargets),
        bufferEntry(9, buffers.adjacencyWeights),
        bufferEntry(10, buffers.pinned),
        bufferEntry(11, buffers.collisionRadii),
        bufferEntry(12, buffers.params),
      ],
    });
  }

  private watchDevice(): void {
    void this.device.lost.then((info) => {
      if (!this.destroyed) this.fail(`WebGPU 设备已丢失：${info.message || info.reason}`);
    });
    this.device.addEventListener("uncapturederror", (event) => {
      if (!this.destroyed) this.fail(`WebGPU 未捕获错误：${event.error.message}`);
    });
  }

  private schedule(delay = 0): void {
    if (
      !this.running
      || this.destroyed
      || this.failed
      || this.timer !== null
      || this.frameInFlight
    ) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.runFrame();
    }, delay);
  }

  private async runFrame(): Promise<void> {
    if (
      !this.running
      || this.destroyed
      || this.failed
      || !this.buffers
      || !this.pipelines
      || !this.bindGroupAToB
      || !this.bindGroupBToA
    ) return;
    this.frameInFlight = true;
    const startedAt = performance.now();
    let nextDelay = 0;
    try {
      const params = createWebGpuParamsBuffer(
        this.input.masses.length,
        GRID_WIDTH,
        GRID_HEIGHT,
        MAX_PER_CELL,
        this.input.world,
        { ...this.settings, alpha: this.alpha },
      );
      this.device.queue.writeBuffer(this.buffers.params, 0, params);
      const bindGroup = this.currentIsA ? this.bindGroupAToB : this.bindGroupBToA;
      const outputPositions = this.currentIsA
        ? this.buffers.positionsB
        : this.buffers.positionsA;
      const outputVelocities = this.currentIsA
        ? this.buffers.velocitiesB
        : this.buffers.velocitiesA;
      const encoder = this.device.createCommandEncoder({ label: "graph-force-frame" });
      const pass = encoder.beginComputePass();
      pass.setBindGroup(0, bindGroup);
      pass.setPipeline(this.pipelines.clearGrid);
      pass.dispatchWorkgroups(Math.ceil(GRID_WIDTH * GRID_HEIGHT / WORKGROUP_SIZE));
      pass.setPipeline(this.pipelines.binNodes);
      pass.dispatchWorkgroups(Math.ceil(this.input.masses.length / WORKGROUP_SIZE));
      pass.setPipeline(this.pipelines.integrate);
      pass.dispatchWorkgroups(Math.ceil(this.input.masses.length / WORKGROUP_SIZE));
      pass.end();
      const positionsByteLength = this.positions.byteLength;
      encoder.copyBufferToBuffer(outputPositions, 0, this.buffers.readback, 0, positionsByteLength);
      encoder.copyBufferToBuffer(
        outputVelocities,
        0,
        this.buffers.readback,
        positionsByteLength,
        positionsByteLength,
      );
      this.device.queue.submit([encoder.finish()]);
      this.currentIsA = !this.currentIsA;
      await this.buffers.readback.mapAsync(GPUMapMode.READ);
      if (this.destroyed || this.failed) {
        if (this.buffers.readback.mapState === "mapped") this.buffers.readback.unmap();
        return;
      }
      const mapped = this.buffers.readback.getMappedRange();
      const positionsBuffer = mapped.slice(0, positionsByteLength);
      const velocitiesBuffer = mapped.slice(
        positionsByteLength,
        positionsByteLength * 2,
      );
      this.buffers.readback.unmap();
      this.positions = new Float32Array(positionsBuffer);
      const velocities = new Float32Array(velocitiesBuffer);
      if (!allFinite(this.positions) || !allFinite(velocities)) {
        throw new Error("WebGPU 布局返回非有限坐标");
      }
      this.input.world = expandLayoutWorldToFit(
        this.input.world,
        this.positions,
        this.input.collisionRadii,
      );
      const collisionResult = resolvePositionCollisions(
        this.positions,
        this.input.collisionRadii,
        this.input.world,
        3,
        { has: (index) => this.pinnedData[index * 4 + 2] > 0.5 },
      );
      this.device.queue.writeBuffer(this.buffers.positionsA, 0, this.positions);
      this.device.queue.writeBuffer(this.buffers.positionsB, 0, this.positions);
      this.energy = Math.max(
        averageEnergy(velocities),
        collisionResult.overlaps > 0
          ? this.settings.stableEnergy * 2 + collisionResult.maxOverlap
          : 0,
      );
      this.iterations += 1;
      this.alpha = Math.max(0.02, this.alpha * (1 - this.settings.alphaDecay));
      if (this.alpha < 0.08 && this.energy < this.settings.stableEnergy) {
        this.stableFrames += 1;
      } else {
        this.stableFrames = 0;
      }
      this.callbacks.onPositions(this.positions);
      const stable = this.stableFrames >= 12;
      if (stable) {
        this.running = false;
        this.emitStatus(true);
        return;
      }
      if (this.iterations % 6 === 0) this.emitStatus(false);
      nextDelay = Math.max(0, 16 - (performance.now() - startedAt));
    } catch (caught) {
      if (this.buffers?.readback.mapState === "mapped") this.buffers.readback.unmap();
      if (!this.destroyed) {
        this.fail(caught instanceof Error ? caught.message : "WebGPU 布局执行失败");
      }
    } finally {
      if (this.buffers?.readback.mapState === "mapped") this.buffers.readback.unmap();
      this.frameInFlight = false;
      if (this.destroyed) this.finalizeDestroy();
      else if (this.running && !this.failed) this.schedule(nextDelay);
    }
  }

  private emitStatus(stable: boolean): void {
    this.callbacks.onStatus({
      backend: "webgpu",
      running: this.running,
      stable,
      energy: this.energy,
      iterations: this.iterations,
      gpuName: this.gpuName,
    });
  }

  private fail(reason: string): void {
    if (this.failed || this.destroyed) return;
    this.failed = true;
    this.running = false;
    this.cancelTimer();
    this.callbacks.onLost(reason);
  }

  private cancelTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}

function createBuffer(
  device: GPUDevice,
  label: string,
  data: ArrayBufferView<ArrayBufferLike>,
  usage: GPUBufferUsageFlags,
): GPUBuffer {
  const buffer = device.createBuffer({
    label,
    size: alignedBufferSize(data.byteLength),
    usage,
    mappedAtCreation: true,
  });
  new Uint8Array(buffer.getMappedRange()).set(
    new Uint8Array(data.buffer, data.byteOffset, data.byteLength),
  );
  buffer.unmap();
  return buffer;
}

function createEmptyBuffer(
  device: GPUDevice,
  label: string,
  byteLength: number,
  usage: GPUBufferUsageFlags,
): GPUBuffer {
  return device.createBuffer({ label, size: alignedBufferSize(byteLength), usage });
}

function storageEntry(
  binding: number,
  type: GPUBufferBindingType,
): GPUBindGroupLayoutEntry {
  return { binding, visibility: GPUShaderStage.COMPUTE, buffer: { type } };
}

function bufferEntry(binding: number, buffer: GPUBuffer): GPUBindGroupEntry {
  return { binding, resource: { buffer } };
}

function adapterName(adapter: GPUAdapter): string {
  const info = adapter.info;
  const values = [info.vendor, info.architecture, info.device, info.description]
    .filter((value, index, items) => value && items.indexOf(value) === index);
  return values.join(" · ") || "高性能 WebGPU 适配器";
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  message: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timer = setTimeout(() => reject(new WebGpuUnavailableError(message)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function allFinite(values: Float32Array<ArrayBufferLike>): boolean {
  for (const value of values) if (!Number.isFinite(value)) return false;
  return true;
}

function averageEnergy(velocities: Float32Array<ArrayBufferLike>): number {
  if (!velocities.length) return 0;
  let total = 0;
  for (let index = 0; index < velocities.length; index += 2) {
    total += velocities[index] ** 2 + velocities[index + 1] ** 2;
  }
  return total / (velocities.length / 2);
}
