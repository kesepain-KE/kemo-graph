import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RuntimeLogPanel } from "./RuntimeLogPanel";

describe("RuntimeLogPanel", () => {
  it("offers three log tabs and bounded internal scroll controls", () => {
    const html = renderToStaticMarkup(<RuntimeLogPanel />);
    expect(html).toContain("终端启动日志");
    expect(html).toContain("查询日志");
    expect(html).toContain("内部运行日志");
    expect(html).toContain("自动刷新");
    expect(html).toContain("跟随最新");
    expect(html).toContain("runtime-log-content");
    expect(html).toContain("日志日期（UTC）");
    expect(html).toContain("200 条");
    expect(html).toContain('role="tabpanel"');
  });
});
