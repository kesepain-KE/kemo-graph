import { Focus, Hand, Minus, Plus, RotateCcw } from "lucide-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { LoadingState } from "../../components/Feedback";
import { LayoutController } from "./engine/LayoutController";
import type { GraphRenderScene, GraphRenderer } from "./engine/GraphRenderer";
import type { ForceSettings, LayoutRuntimeStatus } from "./engine/layoutTypes";

type GraphViewportProps = {
  scene: GraphRenderScene;
  onSelect: (nodeId: string) => void;
  onSelectEdge: (edgeId: string) => void;
  onBackendChange?: (backend: string) => void;
  onLayoutStatus?: (status: LayoutRuntimeStatus) => void;
  onFpsChange?: (fps: number) => void;
  layoutKey: string;
  forceSettings: ForceSettings;
  fallback: ReactNode;
};

export type GraphViewportHandle = {
  play: () => void;
  pause: () => void;
  reheat: () => void;
  focusSelected: () => void;
  resetView: () => void;
};

export const GraphViewport = forwardRef<GraphViewportHandle, GraphViewportProps>(function GraphViewport({
  scene,
  onSelect,
  onSelectEdge,
  onBackendChange,
  onLayoutStatus,
  onFpsChange,
  layoutKey,
  forceSettings,
  fallback,
}, forwardedRef) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<GraphRenderer | null>(null);
  const layoutRef = useRef<LayoutController | null>(null);
  const sceneRef = useRef(scene);
  const layoutKeyRef = useRef(layoutKey);
  const forceSettingsRef = useRef(forceSettings);
  const onSelectRef = useRef(onSelect);
  const onSelectEdgeRef = useRef(onSelectEdge);
  const onBackendChangeRef = useRef(onBackendChange);
  const onLayoutStatusRef = useRef(onLayoutStatus);
  const onFpsChangeRef = useRef(onFpsChange);
  const [status, setStatus] = useState<"initializing" | "ready" | "fallback">(
    "initializing",
  );
  const [failure, setFailure] = useState<string | null>(null);

  sceneRef.current = scene;
  layoutKeyRef.current = layoutKey;
  forceSettingsRef.current = forceSettings;
  onSelectRef.current = onSelect;
  onSelectEdgeRef.current = onSelectEdge;
  onBackendChangeRef.current = onBackendChange;
  onLayoutStatusRef.current = onLayoutStatus;
  onFpsChangeRef.current = onFpsChange;

  useImperativeHandle(forwardedRef, () => ({
    play: () => layoutRef.current?.play(),
    pause: () => layoutRef.current?.pause(),
    reheat: () => layoutRef.current?.reheat(),
    focusSelected: () => rendererRef.current?.focusNode(sceneRef.current.selectedId),
    resetView: () => rendererRef.current?.resetView(),
  }), []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    setFailure(null);
    if (sceneRef.current.performance.mode === "compatible") {
      setStatus("fallback");
      onBackendChangeRef.current?.("svg");
      return undefined;
    }
    setStatus("initializing");
    let disposed = false;
    let renderer: GraphRenderer | null = null;
    void (async () => {
      const { PixiGraphRenderer } = await import("./engine/PixiGraphRenderer");
      if (disposed) return;
      renderer = new PixiGraphRenderer();
      rendererRef.current = renderer;
      const layout = new LayoutController(
        (nodeIds, positions) => renderer?.setPositions(nodeIds, positions),
        (layoutStatus) => onLayoutStatusRef.current?.(layoutStatus),
      );
      layoutRef.current = layout;
      await renderer.mount(host, sceneRef.current, {
        onSelect: (nodeId) => onSelectRef.current(nodeId),
        onSelectEdge: (edgeId) => onSelectEdgeRef.current(edgeId),
        onNodeDragStart: (nodeId, x, y) => layout.pin(nodeId, x, y),
        onNodeDrag: (nodeId, x, y) => layout.pin(nodeId, x, y),
        onNodeDragEnd: (nodeId) => layout.unpin(nodeId),
        onFpsChange: (fps) => onFpsChangeRef.current?.(fps),
      });
      if (disposed) {
        renderer.destroy();
        return;
      }
      renderer.update(sceneRef.current);
      setStatus("ready");
      onBackendChangeRef.current?.(renderer.backend);
    })()
      .catch((caught: unknown) => {
        renderer?.destroy();
        layoutRef.current?.destroy();
        layoutRef.current = null;
        if (disposed) {
          return;
        }
        const message = caught instanceof Error ? caught.message : "PixiJS 初始化失败";
        setFailure(message);
        setStatus("fallback");
        onBackendChangeRef.current?.("svg");
      });
    return () => {
      disposed = true;
      renderer?.destroy();
      layoutRef.current?.destroy();
      layoutRef.current = null;
      if (rendererRef.current === renderer) rendererRef.current = null;
    };
  }, [scene.performance.mode]);

  useEffect(() => {
    if (status === "ready") rendererRef.current?.update(scene);
  }, [scene, status]);

  useEffect(() => {
    if (status !== "ready" || !layoutRef.current) return;
    void layoutRef.current.start(
      scene.nodes,
      scene.edges,
      forceSettings,
      layoutKey,
      {
        performanceMode: scene.performance.mode,
        nodeScale: scene.appearance.nodeScale,
      },
    );
  }, [
    scene.appearance.nodeScale,
    scene.edges,
    scene.nodes,
    status,
  ]);

  useEffect(() => {
    if (status === "ready") {
      layoutRef.current?.updateSettings(forceSettings, layoutKey);
    }
  }, [forceSettings, layoutKey, status]);

  if (status === "fallback") {
    return (
      <div className="graph-renderer-fallback" data-renderer-backend="svg">
        <span className="graph-renderer-fallback__note" title={failure ?? undefined}>
          {scene.performance.mode === "compatible"
            ? "已启用 SVG 兼容模式"
            : "GPU 渲染不可用，已自动切换 SVG"}
        </span>
        {fallback}
      </div>
    );
  }

  return (
    <div
      className="graph-canvas-wrap graph-canvas-wrap--pixi"
      data-renderer-backend={status === "ready" ? "pixi-webgl" : "initializing"}
    >
      <div
        ref={hostRef}
        className="graph-pixi-host"
        role="img"
        aria-label="使用本机 GPU 渲染的可缩放知识图谱"
      />
      {status === "initializing" ? (
        <LoadingState label="正在初始化本机 GPU 图谱引擎…" />
      ) : null}

      <div className="graph-legend">
        <span><i className="legend-dot legend-dot--core" />核心概念</span>
        <span><i className="legend-dot legend-dot--concept" />一般概念</span>
        <span><i className="legend-dot legend-dot--group" />真实群组配色</span>
      </div>

      <div className="canvas-tools">
        <button className="icon-button" title="拖拽画布" type="button">
          <Hand size={17} />
        </button>
        <button
          className="icon-button"
          title="聚焦所选节点"
          type="button"
          onClick={() => rendererRef.current?.focusNode(scene.selectedId)}
        >
          <Focus size={17} />
        </button>
        <div className="zoom-control">
          <button
            type="button"
            onClick={() => rendererRef.current?.zoomBy(-0.12)}
            aria-label="缩小"
          >
            <Minus size={15} />
          </button>
          <span>GPU</span>
          <button
            type="button"
            onClick={() => rendererRef.current?.zoomBy(0.12)}
            aria-label="放大"
          >
            <Plus size={15} />
          </button>
        </div>
      </div>
      <button
        className="reset-view"
        type="button"
        onClick={() => rendererRef.current?.resetView()}
      >
        <RotateCcw size={15} /> 重置视图
      </button>
    </div>
  );
});
