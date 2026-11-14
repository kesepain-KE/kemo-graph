import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { GraphDetails } from "./GraphDetails";
import { GraphCanvasTools } from "./GraphCanvasTools";
import { GraphControlPanel } from "./GraphControlPanel";
import { DEFAULT_GRAPH_PREFERENCES } from "./graphPreferences";
import type { GraphRenderScene } from "./engine/GraphRenderer";

describe("graph inspector and canvas controls", () => {
  it("orders the summary, tags, relationships, group summaries and original/processed sources", () => {
    const html = renderToStaticMarkup(<MemoryRouter><GraphDetails
      selected={{ node_id: "a", keyword: "知识图谱", summary: "节点摘要", tags: ["标签"], aliases: ["别名"], ref_count: 2, source_ids: [], group_ids: [] }}
      paths={[]} directRelationCount={0} isCore={false} sourceLabels={new Map()} onSelect={() => {}}
      onChanged={async () => {}} onDirtyChange={() => {}} /></MemoryRouter>);
    const labels = ["节点详情", "摘要", "标签与别名", "关系节点与关系线", "来源节点群总结", "来源文件", "删除节点"];
    for (let i = 1; i < labels.length; i++) expect(html.indexOf(labels[i])).toBeGreaterThan(html.indexOf(labels[i - 1]));
    expect(html).toContain('aria-label="摘要显示模式"');
    expect(html).toContain("暂无原文件映射");
    expect(html).not.toContain("来源绑定");
  });

  it("uses configured semantic colors for both node and relation legends, without invented group colors", () => {
    const scene = {
      selectedId: "a", contextMode: "local", appearance: DEFAULT_GRAPH_PREFERENCES.appearance,
    } as GraphRenderScene;
    const html = renderToStaticMarkup(<GraphCanvasTools scene={scene} panMode onPanMode={() => {}} onFocus={() => {}}
      onZoom={() => {}} onReset={() => {}} />);
    for (const name of ["选中节点", "相关节点", "无关节点", "相关关系", "无关关系", "手动拖动画布", "将所选节点居中", "重置视图"]) expect(html).toContain(name);
    expect(html).toContain(`background:${scene.appearance.colors.selectedNode}`);
    expect(html).toContain(`background:${scene.appearance.colors.selectedRelation}`);
    expect(html).not.toContain("真实群组配色");
  });

  it("keeps the original control sections and tabs available outside fullscreen", () => {
    const html = renderToStaticMarkup(<GraphControlPanel preferences={DEFAULT_GRAPH_PREFERENCES} onPreferencesChange={() => {}}
      catalog={null} activeNodes={[]} activeRelationTypes={[]} highlightedNodes={[]} sourceLabels={new Map()}
      rendererBackend="pixi-webgl" layoutStatus={null} fps={60} loading={false} onSelect={() => {}} onReheat={() => {}}
      onShowDetails={() => {}} onRequestClose={() => {}} />);
    for (const label of ["图谱控制", "详情", "筛选", "颜色组", "外观", "力度", "性能", "高级白", "高级黑", "显示关系箭头"]) expect(html).toContain(label);
  });
});
