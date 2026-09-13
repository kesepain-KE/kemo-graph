import { Folder, FolderPlus, Folders, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/api";
import type { DocumentProject, DocumentRecord } from "../types/api";
import { ThemedSelect } from "./ThemedSelect";

// Slash cannot be a project name, unlike a plain-text sentinel.
export const ALL_PROJECTS = "/";
export function documentProject(path: string): string {
  const parts = path.replaceAll("\\", "/").split("/");
  return parts.length > 1 ? parts[0] : "";
}

export function ProjectFolders({ projects, active, disabled, onSelect, onCreate }: {
  projects: DocumentProject[]; active: string; disabled: boolean;
  onSelect: (name: string) => void; onCreate: () => void;
}) {
  return <section className="document-projects" aria-label="项目文件夹">
    <header><strong><Folders size={16} />项目文件夹</strong><button type="button" disabled={disabled} onClick={onCreate}><FolderPlus size={15} />新建项目</button></header>
    <div className="document-projects__list">
      <button type="button" aria-pressed={active === ALL_PROJECTS} disabled={disabled} onClick={() => onSelect(ALL_PROJECTS)}><Folders size={15} />全部文档</button>
      {projects.map((project) => <button type="button" key={project.name} aria-pressed={active === project.name} disabled={disabled} onClick={() => onSelect(project.name)} title={project.name || "未分组"}>
        <Folder size={15} /><span>{project.name || "未分组"}</span><small>{project.document_count}</small>
      </button>)}
    </div>
  </section>;
}

export type OrganizationAction = { kind: "create" } | { kind: "rename"; document: DocumentRecord } | { kind: "move"; sourceIds: string[] };

export function DocumentOrganizationDialog({ action, projects, onClose, onComplete }: {
  action: OrganizationAction; projects: DocumentProject[];
  onClose: () => void; onComplete: (message: string, createdProject?: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(action.kind === "rename" ? action.document.relative_path.split("/").pop() ?? "" : "");
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  const title = action.kind === "create" ? "新建项目文件夹" : action.kind === "rename" ? "重命名文档" : `移动 ${action.sourceIds.length} 篇文档`;
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (action.kind === "create") {
        const created = await api.createDocumentProject(name);
        await onComplete(`已创建项目“${created.name}”。接下来上传的文档将放入此项目。`, created.name);
      } else if (action.kind === "rename") {
        await api.renameDocument(action.document.source_id, name, action.document.relative_path);
        await onComplete("文档已重命名，已有图谱和向量索引保持不变，无需重建。");
      } else {
        await api.moveDocuments(action.sourceIds, project);
        await onComplete(`已移动 ${action.sourceIds.length} 篇文档至“${project || "未分组"}”，无需重建索引。`);
      }
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败，请重试");
    } finally { setBusy(false); }
  };
  return <dialog ref={dialog} className="document-organization-dialog" aria-labelledby="document-organization-title" onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <header><h3 id="document-organization-title">{title}</h3><button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={18} /></button></header>
      {action.kind === "move" ? <label className="document-organization-field"><span>目标项目</span><ThemedSelect ariaLabel="目标项目" value={project} options={projects.map((item) => ({ value: item.name, label: item.name || "未分组" }))} onChange={setProject} disabled={busy} /></label>
        : <label className="document-organization-field"><span>{action.kind === "create" ? "项目名称" : "文档名称"}</span><input autoFocus required maxLength={160} disabled={busy} value={name} onChange={(event) => setName(event.target.value)} placeholder={action.kind === "create" ? "例如：产品设计、研究笔记" : "输入文档名称"} /></label>}
      <p>{action.kind === "create" ? "项目是当前知识库中的真实文件夹，用于分类管理，不会隔离图谱或检索数据。" : "只调整规范 Markdown 的名称或位置，不修改正文和原始导入文件。目标同名文件不会被覆盖。"}</p>
      {error && <p className="document-organization-error" role="alert">{error}</p>}
      <footer><button className="button button--secondary" type="button" disabled={busy} onClick={onClose}>取消</button><button className="button button--primary" type="submit" disabled={busy || (action.kind !== "move" && !name.trim())}>{busy ? "处理中…" : "确认"}</button></footer>
    </form>
  </dialog>;
}
