import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ALL_PROJECTS, documentProject, ProjectFolders, DocumentOrganizationDialog } from "./DocumentOrganization";

describe("project folders", () => {
  it("classifies root and nested files without losing the project", () => {
    expect(documentProject("note.md")).toBe("");
    expect(documentProject("Research/nested/note.md")).toBe("Research");
    expect(documentProject("Research\\note.md")).toBe("Research");
  });
  it("renders all, ungrouped and empty project folders", () => {
    const html = renderToStaticMarkup(<ProjectFolders projects={[{ name: "", document_count: 2 }, { name: "Empty", document_count: 0 }]} active={ALL_PROJECTS} disabled={false} onSelect={() => {}} onCreate={() => {}} />);
    expect(html).toContain("全部文档");
    expect(html).toContain("未分组");
    expect(html).toContain("Empty");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("新建项目");
  });
  it("uses a labelled modal and explains that projects do not isolate retrieval", () => {
    const html = renderToStaticMarkup(<DocumentOrganizationDialog action={{ kind: "create" }} projects={[{ name: "", document_count: 0 }]} onClose={() => {}} onComplete={async () => {}} />);
    expect(html).toContain("新建项目文件夹");
    expect(html).toContain('aria-labelledby="document-organization-title"');
    expect(html).toContain("不会隔离图谱或检索数据");
    expect(html).not.toContain("<select");
  });
});
