import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Eye,
  FilePenLine,
  FileText,
  FolderInput,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { MarkdownPreview } from "../components/MarkdownPreview";
import { PageIntro } from "../components/PageIntro";
import { ThemedSelect } from "../components/ThemedSelect";
import { ALL_PROJECTS, ProjectFolders, DocumentOrganizationDialog, type OrganizationAction } from "../components/DocumentOrganization";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import type { DocumentListSummary, DocumentProject, DocumentRecord, ImportData, Pagination } from "../types/api";

type ImportJobStatus = "queued" | "processing" | "completed" | "failed";
type DetailMode = "preview" | "edit";

type ImportJob = {
  id: string;
  name: string;
  format: string;
  status: ImportJobStatus;
  detail: string;
};

const PAGE_SIZE = 6;
const ALLOWED_IMPORT_EXTENSIONS = new Set([
  "pdf", "docx", "pptx", "xlsx", "xlsm", "xls", "epub", "rtf",
  "md", "markdown", "txt", "log", "html", "htm", "rst", "csv", "tsv",
  "json", "jsonl", "ndjson", "yaml", "yml", "xml",
]);
const MAX_IMPORT_BYTES = 50 * 1024 * 1024;

function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`status-badge is-${status}`}>{label}</span>;
}

function statusText(status: DocumentRecord["graph_status"] | DocumentRecord["rag_status"]) {
  return {
    pending: "待处理",
    processing: "处理中",
    ready: "就绪",
    failed: "失败",
  }[status];
}

function needsRebuild(document: DocumentRecord) {
  return [document.graph_status, document.rag_status].some(
    (status) => status === "pending" || status === "failed",
  );
}

export function DocumentsPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const { activeTasks, createTask, updateTask, refreshServerTasks } = useRuntimeTasks();
  const previousActiveTaskCountRef = useRef(activeTasks.length);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, page_size: PAGE_SIZE, total: 0, total_pages: 0 });
  const [summary, setSummary] = useState<DocumentListSummary | null>(null);
  const [projects, setProjects] = useState<DocumentProject[]>([{ name: "", document_count: 0 }]);
  const [activeProject, setActiveProject] = useState(ALL_PROJECTS);
  const [importProject, setImportProject] = useState("");
  const [organizationAction, setOrganizationAction] = useState<OrganizationAction | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [content, setContent] = useState("");
  const [draft, setDraft] = useState("");
  const [loadedHash, setLoadedHash] = useState<string | null>(null);
  const [detailMode, setDetailMode] = useState<DetailMode>("preview");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importJobs, setImportJobs] = useState<ImportJob[]>([]);
  const requestSequenceRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);
  const uploading = activeTasks.some((task) => task.kind === "import");
  const ingesting = activeTasks.some((task) =>
    ["ingest", "rebuild_knowledge_base", "rebuild_all"].includes(task.kind),
  );
  const isDirty = detailMode === "edit" && draft !== content;

  const loadDocuments = async (
    preferredPath?: string,
    projectFilter = activeProject,
    requestedPage = page,
    searchFilter = query,
  ): Promise<DocumentRecord[] | null> => {
    const requestId = ++requestSequenceRef.current;
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const [projectData, nextPage] = await Promise.all([
        api.getDocumentProjects(),
        api.getDocuments(
          requestedPage,
          PAGE_SIZE,
          "active",
          controller.signal,
          {
            project: projectFilter === ALL_PROJECTS ? undefined : projectFilter,
            search: searchFilter,
            includeSummary: true,
          },
        ),
      ]);
      if (requestId !== requestSequenceRef.current) return null;
      setProjects(projectData.projects);
      const next = nextPage.documents;
      setDocuments(next);
      setPagination(nextPage.pagination);
      setSummary(nextPage.summary ?? null);
      if (nextPage.pagination.page && nextPage.pagination.page !== requestedPage) setPage(nextPage.pagination.page);
      setCheckedIds((current) => new Set(
        [...current].filter((sourceId) => next.some((item) => item.source_id === sourceId)),
      ));
      setSelectedId((current) => {
        const candidates = next;
        const preferred = preferredPath ? candidates.find((document) => document.relative_path === preferredPath) : null;
        if (preferred) return preferred.source_id;
        if (current && candidates.some((document) => document.source_id === current)) return current;
        return candidates[0]?.source_id ?? null;
      });
      return next;
    } catch (caught) {
      if (controller.signal.aborted || requestId !== requestSequenceRef.current) return null;
      setDocuments([]);
      setPagination({ page: requestedPage, page_size: PAGE_SIZE, total: 0, total_pages: 0 });
      setSummary(null);
      setSelectedId(null);
      setError(caught instanceof Error ? caught.message : "无法加载文档列表");
      return null;
    } finally {
      if (requestId === requestSequenceRef.current) {
        setLoading(false);
        if (requestControllerRef.current === controller) requestControllerRef.current = null;
      }
    }
  };

  useEffect(() => {
    void loadDocuments(undefined, activeProject, page, query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject, page, query]);

  useEffect(() => () => {
    requestSequenceRef.current += 1;
    requestControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    const previousCount = previousActiveTaskCountRef.current;
    previousActiveTaskCountRef.current = activeTasks.length;
    if (previousCount > 0 && activeTasks.length === 0) void loadDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTasks.length]);

  const selected = documents.find((document) => document.source_id === selectedId) ?? null;
  const organizationDisabled = loading || uploading || ingesting || saving || deleting || isDirty;
  const selectProject = (name: string) => {
    if (isDirty && !window.confirm("当前修改尚未保存，确认放弃并切换项目？")) return;
    setActiveProject(name);
    setImportProject(name === ALL_PROJECTS ? "" : name);
    setCheckedIds(new Set());
    setQuery("");
    setSelectedId(null);
  };
  const finishOrganization = async (message: string, createdProject?: string) => {
    const target = createdProject ?? activeProject;
    if (createdProject !== undefined) {
      setActiveProject(createdProject);
      setImportProject(createdProject);
    }
    setCheckedIds(new Set());
    setPage(1);
    const refreshed = await loadDocuments(undefined, target, 1, query);
    if (refreshed) setNotice(message);
  };

  useEffect(() => {
    if (!selected) {
      setContent("");
      setDraft("");
      setLoadedHash(null);
      return;
    }
    let active = true;
    setContent("");
    setDraft("");
    setLoadedHash(null);
    setDetailMode("preview");
    setContentLoading(true);
    api.getDocumentContent(selected.source_id)
      .then((data) => {
        if (!active) return;
        setContent(data.content);
        setDraft(data.content);
        setLoadedHash(data.content_hash);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "无法读取文档内容");
      })
      .finally(() => active && setContentLoading(false));
    return () => { active = false; };
  }, [selected?.source_id]);

  const visibleDocuments = documents;
  const totalPages = Math.max(1, pagination.total_pages);
  const pageDocuments = documents;
  const rebuildCount = summary?.needs_rebuild_documents ?? documents.filter(needsRebuild).length;
  const deleteScopeCount = activeProject === ALL_PROJECTS
    ? projects.reduce((total, project) => total + project.document_count, 0)
    : projects.find((project) => project.name === activeProject)?.document_count ?? 0;

  useEffect(() => setPage(1), [query, activeProject]);
  useEffect(() => setPage((current) => Math.min(current, totalPages)), [totalPages]);

  const selectDocument = (sourceId: string) => {
    if (sourceId === selectedId) return;
    if (isDirty && !window.confirm("当前修改尚未保存，确认放弃并切换文档？")) return;
    setError(null);
    setSelectedId(sourceId);
  };

  const toggleChecked = (sourceId: string) => {
    setCheckedIds((current) => {
      const next = new Set(current);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const destinationProject = importProject;
    setActiveProject(destinationProject);
    setCheckedIds(new Set());
    const selectedFiles = Array.from(files);
    const initialJobs = selectedFiles.map((file): ImportJob => {
      const format = file.name.split(".").pop()?.toLocaleLowerCase() ?? "未知";
      const taskId = createTask("import", file.name, "等待校验并上传");
      if (!ALLOWED_IMPORT_EXTENSIONS.has(format)) {
        updateTask(taskId, { status: "failed", detail: "不支持此文件格式" }, `校验失败：不支持 .${format} 文件`);
        return { id: taskId, name: file.name, format, status: "failed", detail: "不支持此文件格式" };
      }
      if (file.size > MAX_IMPORT_BYTES) {
        updateTask(taskId, { status: "failed", detail: "文件超过 50 MB" }, "校验失败：文件大小超过 50 MB");
        return { id: taskId, name: file.name, format, status: "failed", detail: "文件超过 50 MB" };
      }
      return { id: taskId, name: file.name, format, status: "queued", detail: "等待上传" };
    });
    setImportJobs(initialJobs);
    const importable = selectedFiles
      .map((file, index) => ({ file, job: initialJobs[index] }))
      .filter(({ job }) => job.status === "queued");
    if (!importable.length) {
      setError("所选文件均无法导入，请检查格式和文件大小。");
      return;
    }
    setError(null);
    setNotice(null);
    let lastImportedPath: string | undefined;
    let completed = 0;
    let failed = initialJobs.filter((job) => job.status === "failed").length;
    for (const { file, job } of importable) {
      setImportJobs((current) => current.map((item) => item.id === job.id
        ? { ...item, status: "processing", detail: "上传与转换中" }
        : item));
      updateTask(job.id, { status: "running", detail: "上传并转换为 Markdown" }, "文件已发送到服务端，正在进行格式转换。");
      try {
        const imported: ImportData = await api.importFile(file, false, destinationProject);
        lastImportedPath = imported.markdown_relative_path;
        completed += 1;
        const detail = "转换完成，等待手动批量重建";
        setImportJobs((current) => current.map((item) => item.id === job.id
          ? { ...item, format: imported.detected_format, status: "completed", detail }
          : item));
        updateTask(job.id, { status: "completed", detail }, detail);
      } catch (caught) {
        failed += 1;
        const message = caught instanceof Error ? caught.message : "导入失败";
        setImportJobs((current) => current.map((item) => item.id === job.id
          ? { ...item, status: "failed", detail: message }
          : item));
        updateTask(job.id, { status: "failed", detail: message }, `处理失败：${message}`);
      }
    }
    setPage(1);
    const refreshed = await loadDocuments(lastImportedPath, destinationProject, 1, "");
    if (refreshed && completed) setNotice(`已成功转换 ${completed} 个文档，请点击“批量重建”更新 Graph/RAG。`);
    if (failed) setError(`${failed} 个文件导入失败，请查看导入记录。`);
    if (inputRef.current) inputRef.current.value = "";
  };

  const runIngest = async () => {
    if (!rebuildCount) {
      setNotice("当前没有待重建或失败的文档。");
      return;
    }
    setError(null);
    try {
      await api.startIngestJob(null, "both", true);
      await refreshServerTasks();
      setNotice(`已将 ${rebuildCount} 篇待处理文档加入后台重建队列。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法启动批量重建");
    }
  };

  const saveContent = async () => {
    if (!selected || !loadedHash || !isDirty) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.updateDocumentContent(selected.source_id, draft, loadedHash);
      setContent(draft);
      setLoadedHash(result.content_hash);
      setDetailMode("preview");
      await loadDocuments(selected.relative_path, activeProject, page, query);
      setNotice(result.changed
        ? "Markdown 已保存，旧 Graph/RAG 仍可使用；该文档已进入待重建列表。"
        : "内容没有变化，无需重建。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存文档失败");
    } finally {
      setSaving(false);
    }
  };

  const deleteOne = async () => {
    if (!selected) return;
    if (!window.confirm(`确认删除“${selected.relative_path}”？文件将移入当前知识库回收站；若已有同路径文件，将覆盖旧回收副本。`)) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteDocument(selected.source_id);
      await loadDocuments(undefined, activeProject, page, query);
      setNotice("文档已精确删除并移入回收站。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const deleteChecked = async () => {
    const ids = [...checkedIds];
    if (!ids.length) return;
    if (!window.confirm(`确认删除已勾选的 ${ids.length} 篇文档？它们将移入当前知识库回收站；同路径的旧回收副本将被覆盖。`)) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await api.deleteDocuments(ids);
      await loadDocuments(undefined, activeProject, page, query);
      setNotice(`批量删除完成：成功 ${result.deleted} 篇，失败 ${result.failed} 篇。`);
      if (result.failed) setError(result.failures.map((item) => item.message).join("；"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "批量删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const deleteAll = async () => {
    const targetCount = deleteScopeCount;
    if (!targetCount) return;
    const scope = activeProject === ALL_PROJECTS ? "当前知识库" : `项目“${activeProject || "未分组"}”`;
    if (!window.confirm(`危险操作：确认删除${scope}的全部 ${targetCount} 篇文档？${activeProject === ALL_PROJECTS ? "包含本知识库内所有项目，不影响其他 Store。" : "不会删除其他项目或 Store 的文档。"}`)) return;
    if (!window.confirm("请再次确认：全部文档、关联 Graph 与 RAG 数据将被清理，原文会移入回收站；同路径的旧回收副本将被覆盖。")) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await api.deleteAllDocuments(activeProject === ALL_PROJECTS ? undefined : activeProject);
      setPage(1);
      await loadDocuments(undefined, activeProject, 1, query);
      setNotice(`全部删除完成：成功 ${result.deleted} 篇，失败 ${result.failed} 篇。`);
      if (result.failed) setError(result.failures.map((item) => item.message).join("；"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "全部删除失败");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section className="documents-page page-stack">
      <PageIntro
        title="整理你的知识来源"
        description="按项目管理文档，支持重命名、移动和 Markdown 编辑；只有正文变化才需要手动重建索引。"
        actions={
          <>
            <input ref={inputRef} hidden multiple type="file" accept=".pdf,.docx,.pptx,.xlsx,.xlsm,.xls,.epub,.rtf,.md,.markdown,.txt,.log,.html,.htm,.rst,.csv,.tsv,.json,.jsonl,.ndjson,.yaml,.yml,.xml" onChange={(event) => void handleFiles(event.target.files)} />
            <div className="document-import-project"><span>导入到</span><ThemedSelect ariaLabel="导入目标项目" value={importProject} options={projects.map((item) => ({ value: item.name, label: item.name || "未分组" }))} onChange={setImportProject} disabled={uploading || ingesting} /></div>
            <button className="button button--secondary" disabled={uploading || ingesting || isDirty} onClick={() => inputRef.current?.click()}>
              {uploading ? <RefreshCw className="spin" size={16} /> : <Upload size={16} />}
              {uploading ? "正在导入" : "导入文档"}
            </button>
            <button className="button button--primary" disabled={ingesting || uploading || !rebuildCount} onClick={runIngest}>
              <RefreshCw className={ingesting ? "spin" : ""} size={16} />
              {ingesting ? "正在重建" : `批量重建${rebuildCount ? ` (${rebuildCount})` : ""}`}
            </button>
          </>
        }
      />

      {rebuildCount ? (
        <div className="document-rebuild-banner card">
          <span className="document-rebuild-banner__icon"><FilePenLine size={19} /></span>
          <span><strong>{rebuildCount} 篇文档等待重建</strong><small>正文已保存，旧索引会保留到新 Graph/RAG 成功替换为止。</small></span>
          <button className="button button--primary" disabled={ingesting} onClick={runIngest}>立即批量重建</button>
        </div>
      ) : null}
      {error ? <ErrorNotice message={error} /> : null}
      {notice ? <InfoNotice message={notice} /> : null}

      {importJobs.length ? (
        <div className="import-feedback card">
          <div className="import-feedback__header"><div><p className="eyebrow">Import queue</p><strong>文档转换进度</strong></div><span>{importJobs.filter((job) => job.status === "completed").length}/{importJobs.length} 完成</span></div>
          <div className="import-job-list">{importJobs.map((job) => (
            <div className={`import-job is-${job.status}`} key={job.id}>
              <span className="import-job__icon">{job.status === "processing" ? <RefreshCw className="spin" size={15} /> : <FileText size={15} />}</span>
              <span><strong>{job.name}</strong><small>{job.detail}</small></span><b>{job.format.toUpperCase()}</b>
            </div>
          ))}</div>
        </div>
      ) : null}

      <div className="documents-layout">
        <aside className="document-browser card">
          <div className="panel-title-row">
            <div><p className="eyebrow">Library</p><h3>文档列表</h3></div>
            <span className="count-chip">{pagination.total}</span>
          </div>
          <label className="search-field"><Search size={16} /><input aria-label="搜索文件名" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名…" /></label>
          <ProjectFolders projects={projects} active={activeProject} disabled={organizationDisabled} onSelect={selectProject} onCreate={() => setOrganizationAction({ kind: "create" })} />
          <div className="document-bulk-actions">
            <span>已勾选 {checkedIds.size} 篇</span>
            <button disabled={!checkedIds.size || organizationDisabled} onClick={() => setOrganizationAction({ kind: "move", sourceIds: [...checkedIds] })}><FolderInput size={14} />批量移动</button>
            <button disabled={!checkedIds.size || deleting} onClick={deleteChecked}><Trash2 size={14} />批量删除</button>
            <button className="is-danger" disabled={!deleteScopeCount || deleting} onClick={deleteAll}>{activeProject === ALL_PROJECTS ? "全库删除" : "清空本项目"}</button>
          </div>

          <div className="document-list" aria-live="polite">
            {loading ? <LoadingState label="正在读取来源…" /> : null}
            {!loading && visibleDocuments.length === 0 ? <div className="empty-compact"><FileText size={26} /><strong>暂无可显示文档</strong><span>上传文档后会在此显示。</span></div> : null}
            {!loading ? <div className="document-card-grid">{pageDocuments.map((document) => (
              <article className={`document-card ${selectedId === document.source_id ? "is-active" : ""}`} key={document.source_id}>
                <label className="document-card__check" title="勾选文档"><input type="checkbox" checked={checkedIds.has(document.source_id)} onChange={() => toggleChecked(document.source_id)} /><span /></label>
                <button className="document-card__select" onClick={() => selectDocument(document.source_id)}>
                  <span className="document-icon">MD</span>
                  <span className="document-card__body"><strong title={document.relative_path}>{document.relative_path}</strong><small>{document.updated_at ? new Date(document.updated_at).toLocaleString() : "更新时间未知"}</small><span className="document-row__statuses"><StatusBadge status={document.graph_status} label={`Graph · ${statusText(document.graph_status)}`} /><StatusBadge status={document.rag_status} label={`RAG · ${statusText(document.rag_status)}`} /></span></span>
                </button>
              </article>
            ))}</div> : null}
          </div>
          <footer className="document-pagination">
            <button aria-label="上一页" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}><ChevronLeft size={16} /></button>
            <span>第 {page} / {totalPages} 页<small>每页最多 {PAGE_SIZE} 篇</small></span>
            <button aria-label="下一页" disabled={page >= totalPages} onClick={() => setPage((current) => current + 1)}><ChevronRight size={16} /></button>
          </footer>
        </aside>

        <section className="document-preview card">
          <div className="preview-toolbar">
            <div><p className="eyebrow">Markdown Document</p><h3>{selected?.relative_path ?? "选择一份文档"}</h3></div>
            {selected ? <div className="preview-toolbar__actions">
              <button className="button button--secondary" disabled={organizationDisabled || Boolean(selected.source_uri)} title={selected.source_uri ? "外部同步资料请在上游系统修改" : "修改文件名，不重建索引"} onClick={() => setOrganizationAction({ kind: "rename", document: selected })}><FilePenLine size={15} />重命名</button>
              <button className="button button--secondary" disabled={organizationDisabled || Boolean(selected.source_uri)} onClick={() => setOrganizationAction({ kind: "move", sourceIds: [selected.source_id] })}><FolderInput size={15} />移动</button>
              <div className="document-mode-switch"><button className={detailMode === "preview" ? "is-active" : ""} onClick={() => { if (!isDirty || window.confirm("放弃尚未保存的修改？")) { setDraft(content); setDetailMode("preview"); } }}><Eye size={15} />预览</button><button className={detailMode === "edit" ? "is-active" : ""} onClick={() => setDetailMode("edit")}><Pencil size={15} />编辑</button></div>
              {detailMode === "edit" ? <><button className="button button--secondary" disabled={!isDirty || saving} onClick={() => setDraft(content)}><X size={15} />撤销</button><button className="button button--primary" disabled={!isDirty || saving} onClick={saveContent}>{saving ? <RefreshCw className="spin" size={15} /> : <Save size={15} />}{saving ? "保存中" : "保存"}</button></> : null}
              <button className="icon-button is-danger" disabled={deleting} title="精确删除当前文档" onClick={deleteOne}><Trash2 size={17} /></button>
            </div> : null}
          </div>
          <div className={`preview-body ${detailMode === "edit" ? "is-editing" : ""}`}>
            {contentLoading ? <LoadingState label="正在加载内容…" /> : null}
            {!contentLoading && selected && detailMode === "preview" ? <MarkdownPreview content={content} /> : null}
            {!contentLoading && selected && detailMode === "edit" ? <textarea className="document-editor" aria-label="Markdown 文本编辑器" spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)} /> : null}
            {!contentLoading && !selected ? <div className="empty-state"><CheckCircle2 size={32} /><h3>Markdown 编辑与预览区</h3><p>从左侧选择文档；编辑保存后会明确标记为待重建。</p></div> : null}
          </div>
        </section>
      </div>
      {organizationAction && <DocumentOrganizationDialog action={organizationAction} projects={projects} onClose={() => setOrganizationAction(null)} onComplete={finishOrganization} />}
    </section>
  );
}
