import {
  CheckCircle2,
  FileText,
  Filter,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { MarkdownPreview } from "../components/MarkdownPreview";
import { PageIntro } from "../components/PageIntro";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import type { DocumentRecord, ImportData, IngestData } from "../types/api";

type StatusFilter = "all" | "pending" | "processing" | "ready" | "failed";
type ImportJobStatus = "queued" | "processing" | "completed" | "failed";

type ImportJob = {
  id: string;
  name: string;
  format: string;
  status: ImportJobStatus;
  detail: string;
};

const ALLOWED_IMPORT_EXTENSIONS = new Set([
  "pdf", "docx", "md", "markdown", "txt", "html", "htm", "rst", "csv",
]);
const MAX_IMPORT_BYTES = 50 * 1024 * 1024;

function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`status-badge is-${status}`}>{label}</span>;
}

export function DocumentsPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const { activeTasks, createTask, updateTask, refreshServerTasks } = useRuntimeTasks();
  const previousActiveTaskCountRef = useRef(activeTasks.length);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const [query, setQuery] = useState("");
  const [graphFilter, setGraphFilter] = useState<StatusFilter>("all");
  const [ragFilter, setRagFilter] = useState<StatusFilter>("all");
  const [loading, setLoading] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importJobs, setImportJobs] = useState<ImportJob[]>([]);
  const [ingestResult, setIngestResult] = useState<IngestData | null>(null);
  const uploading = activeTasks.some((task) => task.kind === "import");
  const ingesting = activeTasks.some((task) =>
    ["ingest", "rebuild_knowledge_base", "rebuild_all"].includes(task.kind),
  );

  const loadDocuments = async (preferredPath?: string): Promise<DocumentRecord[] | null> => {
    setLoading(true);
    setError(null);
    try {
      const firstPage = await api.getDocuments(1, 100);
      const allDocuments = [...firstPage.documents];
      for (let page = 2; page <= firstPage.pagination.total_pages; page += 1) {
        const nextPage = await api.getDocuments(page, 100);
        allDocuments.push(...nextPage.documents);
      }
      const next = allDocuments.filter(
        (document) => !document.exists_status || document.exists_status === "active",
      );
      setDocuments(next);
      setSelectedId((current) => {
        const preferred = preferredPath
          ? next.find((document) => document.relative_path === preferredPath)
          : null;
        if (preferred) return preferred.source_id;
        if (current && next.some((document) => document.source_id === current)) {
          return current;
        }
        return next[0]?.source_id ?? null;
      });
      return next;
    } catch (caught) {
      setDocuments([]);
      setSelectedId(null);
      setError(caught instanceof Error ? caught.message : "无法加载文档列表");
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const previousCount = previousActiveTaskCountRef.current;
    previousActiveTaskCountRef.current = activeTasks.length;
    if (previousCount > 0 && activeTasks.length === 0) {
      void loadDocuments();
    }
    // 任务可能由已卸载的文档页完成；当前页面需要在全局任务归零时重新同步列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTasks.length]);

  const selected = documents.find((document) => document.source_id === selectedId) ?? null;

  useEffect(() => {
    if (!selected) {
      setPreview("");
      return;
    }
    let active = true;
    setPreview("");
    setPreviewLoading(true);
    api
      .getDocumentContent(selected.source_id)
      .then((data) => active && setPreview(data.content))
      .catch((caught: unknown) => {
        if (!active) return;
        setPreview("");
        setError(
          caught instanceof Error ? caught.message : "无法读取文档内容",
        );
      })
      .finally(() => active && setPreviewLoading(false));
    return () => {
      active = false;
    };
  }, [selected]);

  const visibleDocuments = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return documents.filter((document) => {
      const matchesQuery =
        !keyword || document.relative_path.toLocaleLowerCase().includes(keyword);
      const matchesGraph =
        graphFilter === "all" || document.graph_status === graphFilter;
      const matchesRag = ragFilter === "all" || document.rag_status === ragFilter;
      return matchesQuery && matchesGraph && matchesRag;
    });
  }, [documents, graphFilter, query, ragFilter]);

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const selectedFiles = Array.from(files);
    const initialJobs = selectedFiles.map((file): ImportJob => {
      const format = file.name.split(".").pop()?.toLocaleLowerCase() ?? "未知";
      const taskId = createTask("import", file.name, "等待校验并上传");
      if (!ALLOWED_IMPORT_EXTENSIONS.has(format)) {
        updateTask(
          taskId,
          { status: "failed", detail: "不支持此文件格式" },
          `校验失败：不支持 .${format} 文件`,
        );
        return { id: taskId, name: file.name, format, status: "failed", detail: "不支持此文件格式" };
      }
      if (file.size > MAX_IMPORT_BYTES) {
        updateTask(
          taskId,
          { status: "failed", detail: "文件超过 50 MB" },
          "校验失败：文件大小超过 50 MB",
        );
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
    let backgroundQueued = false;
    let backgroundError: string | null = null;
    for (const { file, job } of importable) {
      setImportJobs((current) => current.map((currentJob) => (
        currentJob.id === job.id
          ? { ...currentJob, status: "processing", detail: "上传与转换中" }
          : currentJob
      )));
      updateTask(
        job.id,
        { status: "running", detail: "上传并转换为 Markdown" },
        "文件已发送到服务端，正在进行格式转换。",
      );
      try {
        const imported: ImportData = await api.importFile(file, false);
        lastImportedPath = imported.markdown_relative_path;
        completed += 1;
        const detail = "转换完成，已进入知识库整理队列";
        setImportJobs((current) => current.map((currentJob) => (
          currentJob.id === job.id
            ? {
                ...currentJob,
                format: imported.detected_format,
                status: "completed",
                detail,
              }
            : currentJob
        )));
        updateTask(
          job.id,
          { status: "completed", detail },
          detail,
        );
      } catch (caught) {
        failed += 1;
        const message = caught instanceof Error ? caught.message : "导入失败";
        setImportJobs((current) => current.map((currentJob) => (
          currentJob.id === job.id
            ? { ...currentJob, status: "failed", detail: message }
            : currentJob
        )));
        updateTask(
          job.id,
          { status: "failed", detail: message },
          `处理失败：${message}`,
        );
      }
    }
    if (completed) {
      try {
        await api.startIngestJob(null, "both");
        await refreshServerTasks();
        backgroundQueued = true;
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "无法启动后台整理";
        backgroundError = `文档已转换，但${message}`;
      }
    }
    const refreshed = await loadDocuments(lastImportedPath);
    if (refreshed && completed) {
      setNotice(backgroundQueued
        ? `已成功导入 ${completed} 个文档，Graph/RAG 正在后台整理。`
        : `已成功转换 ${completed} 个文档，但尚未进入 Graph/RAG 整理队列。`);
    }
    const errors = [
      backgroundError,
      failed ? `${failed} 个文件导入失败，请查看下方详情。` : null,
    ].filter((message): message is string => Boolean(message));
    if (errors.length) setError(errors.join(" "));
    if (inputRef.current) inputRef.current.value = "";
  };

  const runIngest = async () => {
    setIngestResult(null);
    setError(null);
    try {
      await api.startIngestJob(null, "both");
      await refreshServerTasks();
      setNotice("批量整理已进入后台队列，可在右上角运行记录中持续追踪。");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "整理失败";
      setError(message);
    }
  };

  const deleteSelected = async () => {
    if (!selected) return;
    if (!window.confirm(`确认删除 ${selected.relative_path}？文件将移入回收站。`)) return;
    try {
      await api.deleteDocument(selected.source_id);
      await loadDocuments();
      setNotice("文档已删除并移入回收站。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  return (
    <section className="documents-page page-stack">
      <PageIntro
        title="整理你的知识来源"
        description="导入常见文档格式，由服务端转换为 Markdown，并自动构建 Graph 与 RAG。"
        actions={
          <>
            <input
              ref={inputRef}
              hidden
              multiple
              type="file"
              accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.rst,.csv"
              onChange={(event) => void handleFiles(event.target.files)}
            />
            <button
              className="button button--secondary"
              disabled={uploading || ingesting}
              onClick={() => inputRef.current?.click()}
            >
              {uploading ? <RefreshCw className="spin" size={16} /> : <Upload size={16} />}
              {uploading ? "正在导入" : "导入文档"}
            </button>
            <button
              className="button button--primary"
              disabled={ingesting || uploading}
              onClick={runIngest}
            >
              <RefreshCw className={ingesting ? "spin" : ""} size={16} />
              {ingesting ? "正在整理" : "批量整理"}
            </button>
          </>
        }
      />

      {error ? <ErrorNotice message={error} /> : null}
      {notice ? <InfoNotice message={notice} /> : null}

      {importJobs.length ? (
        <div className="import-feedback card">
          <div className="import-feedback__header">
            <div>
              <p className="eyebrow">Import queue</p>
              <strong>文档转换与整理进度</strong>
            </div>
            <span>{importJobs.filter((job) => job.status === "completed").length}/{importJobs.length} 完成</span>
          </div>
          <div className="import-job-list">
            {importJobs.map((job) => (
              <div className={`import-job is-${job.status}`} key={job.id}>
                <span className="import-job__icon">
                  {job.status === "processing" ? <RefreshCw className="spin" size={15} /> : <FileText size={15} />}
                </span>
                <span>
                  <strong>{job.name}</strong>
                  <small>{job.detail}</small>
                </span>
                <b>{job.format.toUpperCase()}</b>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {ingesting || ingestResult ? (
        <div className="ingest-feedback card">
          <div className="ingest-feedback__head">
            <span>{ingesting ? "正在构建图谱与向量索引" : "本次整理完成"}</span>
            <strong>
              {ingesting ? "处理中" : `${ingestResult?.processed ?? 0} 个文档`}
            </strong>
          </div>
          <div className={`progress-track ${ingesting ? "is-running" : "is-complete"}`}>
            <span />
          </div>
          {ingestResult ? (
            <div className="ingest-stats">
              <span>Graph 更新 {ingestResult.graph_updated}</span>
              <span>RAG 更新 {ingestResult.rag_updated}</span>
              <span>跳过 {ingestResult.skipped}</span>
              <span className={ingestResult.failed ? "text-danger" : ""}>
                失败 {ingestResult.failed}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="documents-layout">
        <aside className="document-browser card">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Library</p>
              <h3>文档列表</h3>
            </div>
            <span className="count-chip">{visibleDocuments.length}</span>
          </div>

          <label className="search-field">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件名…"
            />
          </label>

          <div className="document-filters">
            <Filter size={14} />
            <label>
              <span>Graph</span>
              <select
                value={graphFilter}
                onChange={(event) => setGraphFilter(event.target.value as StatusFilter)}
              >
                <option value="all">全部</option>
                <option value="pending">待处理</option>
                <option value="processing">处理中</option>
                <option value="ready">就绪</option>
                <option value="failed">失败</option>
              </select>
            </label>
            <label>
              <span>RAG</span>
              <select
                value={ragFilter}
                onChange={(event) => setRagFilter(event.target.value as StatusFilter)}
              >
                <option value="all">全部</option>
                <option value="pending">待处理</option>
                <option value="processing">处理中</option>
                <option value="ready">就绪</option>
                <option value="failed">失败</option>
              </select>
            </label>
          </div>

          <div className="document-list">
            {loading ? <LoadingState label="正在读取来源…" /> : null}
            {!loading && visibleDocuments.length === 0 ? (
              <div className="empty-compact">
                <FileText size={26} />
                <strong>暂无可显示文档</strong>
                <span>上传 PDF、DOCX、Markdown、TXT、HTML、RST 或 CSV 开始整理。</span>
              </div>
            ) : null}
            {visibleDocuments.map((document) => (
              <button
                className={`document-row ${selectedId === document.source_id ? "is-active" : ""}`}
                key={document.source_id}
                onClick={() => {
                  setError(null);
                  setSelectedId(document.source_id);
                }}
              >
                <span className="document-icon">MD</span>
                <span className="document-row__body">
                  <strong>{document.relative_path}</strong>
                  <small>{document.updated_at ?? "更新时间未知"}</small>
                  <span className="document-row__statuses">
                    <StatusBadge status={document.graph_status} label={`G · ${document.graph_status}`} />
                    <StatusBadge status={document.rag_status} label={`R · ${document.rag_status}`} />
                  </span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="document-preview card">
          <div className="preview-toolbar">
            <div>
              <p className="eyebrow">Markdown Preview</p>
              <h3>{selected?.relative_path ?? "选择一份文档"}</h3>
            </div>
            {selected ? (
              <button className="icon-button is-danger" title="删除文档" onClick={deleteSelected}>
                <Trash2 size={17} />
              </button>
            ) : null}
          </div>
          <div className="preview-body">
            {previewLoading ? <LoadingState label="正在加载内容…" /> : null}
            {!previewLoading && preview ? <MarkdownPreview content={preview} /> : null}
            {!previewLoading && !preview ? (
              <div className="empty-state">
                <CheckCircle2 size={32} />
                <h3>Markdown 预览区</h3>
                <p>从左侧选择文档，内容将从服务端安全加载并预览。</p>
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </section>
  );
}
