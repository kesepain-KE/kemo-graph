import {
  BookOpen, ChevronDown, ChevronRight, Download, FilePlus2, FileText, Folder,
  FolderOpen, FolderPlus, Maximize2, Minimize2, MoreHorizontal, Network, Pencil,
  Plus, RefreshCw, Save, Search, Trash2, Upload, X,
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { MarkdownPreview } from "../components/MarkdownPreview";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import type { LibraryData, LibraryEntry, OriginalPreview } from "../types/api";
import "../styles/library.css";

const parentPath = (path: string) => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
function findEntry(entries: LibraryEntry[], path: string): LibraryEntry | undefined {
  for (const entry of entries) {
    if (entry.path === path) return entry;
    const child = entry.children && findEntry(entry.children, path);
    if (child) return child;
  }
}
function formatSize(bytes: number | null) {
  if (bytes === null) return "文件夹";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function DirectoryTree({ entries, directory, selected, onOpen }: {
  entries: LibraryEntry[]; directory: string; selected: string; onOpen: (entry: LibraryEntry) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const render = (items: LibraryEntry[], depth = 0) => items.map((entry) => {
    const folder = entry.kind === "directory";
    const expanded = !collapsed.has(entry.path);
    return <div key={entry.path}>
      <button type="button" className={`library-tree-row ${directory === entry.path || selected === entry.path ? "is-active" : ""}`}
        style={{ "--tree-depth": depth } as CSSProperties} title={entry.path}
        aria-expanded={folder ? expanded : undefined} onClick={() => onOpen(entry)}>
        <span className="library-tree-chevron" onClick={(event) => {
          if (!folder) return;
          event.stopPropagation();
          setCollapsed((current) => {
            const next = new Set(current);
            if (expanded) next.add(entry.path); else next.delete(entry.path);
            return next;
          });
        }}>{folder ? expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}</span>
        {folder ? expanded ? <FolderOpen size={16} /> : <Folder size={16} /> : <FileText size={16} />}
        <span>{entry.name}</span>
      </button>
      {folder && expanded && entry.children ? render(entry.children, depth + 1) : null}
    </div>;
  });
  return <>{render(entries)}</>;
}

type EntryAction = { kind: "create"; entryKind: "file" | "directory" } | { kind: "rename" | "move"; entry: LibraryEntry };
function EntryDialog({ action, directory, onClose, onComplete }: {
  action: EntryAction; directory: string; onClose: () => void;
  onComplete: (path: string, message: string, kind?: "file" | "directory") => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(action.kind === "create" ? "" : action.entry.name);
  const [kind, setKind] = useState<"file" | "directory">(action.kind === "create" ? action.entryKind : "directory");
  const [destination, setDestination] = useState(action.kind === "create" ? directory : parentPath(action.entry.path));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  const title = action.kind === "create" ? "新建" : action.kind === "rename" ? "重命名" : "移动";
  return <dialog ref={dialog} className="library-dialog" onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError(null);
      try {
        if (action.kind === "create") {
          const result = await api.createLibraryEntry(directory, name, kind);
          onComplete(result.path, `已创建${kind === "directory" ? "文件夹" : "文件"}。`, kind);
        } else {
          const target = [action.kind === "rename" ? parentPath(action.entry.path) : destination.trim().replace(/\/+$/, ""),
            action.kind === "rename" ? name : action.entry.name].filter(Boolean).join("/");
          const result = await api.moveLibraryEntry(action.entry.path, target);
          onComplete(result.path, `${title}完成，图谱来源关联已更新。`);
        }
        onClose();
      } catch (caught) { setError(caught instanceof Error ? caught.message : "操作失败"); }
      finally { setBusy(false); }
    }}>
      <header><h2>{title}</h2><button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={18} /></button></header>
      {action.kind === "create" && <div className="library-new-types">
        <button type="button" aria-pressed={kind === "directory"} onClick={() => setKind("directory")}><FolderPlus size={17} />文件夹</button>
        <button type="button" aria-pressed={kind === "file"} onClick={() => setKind("file")}><FilePlus2 size={17} />文档</button>
      </div>}
      <label><span>{action.kind === "move" ? "目标目录（库内路径，留空表示根目录）" : "名称"}</span>
        <input autoFocus required={action.kind !== "move"} maxLength={action.kind === "move" ? 2048 : 160} disabled={busy}
          value={action.kind === "move" ? destination : name}
          placeholder={action.kind === "create" && kind === "file" ? "例如：研究笔记.md" : "例如：技术调研"}
          onChange={(event) => action.kind === "move" ? setDestination(event.target.value) : setName(event.target.value)} />
      </label>
      {error && <p role="alert" className="text-danger">{error}</p>}
      <footer><button className="button button--secondary" type="button" disabled={busy} onClick={onClose}>取消</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "处理中…" : "确认"}</button></footer>
    </form>
  </dialog>;
}

export function DocumentsPage() {
  const [parameters, setParameters] = useSearchParams();
  const initialPath = parameters.get("path") ?? "";
  const [directory, setDirectory] = useState(initialPath ? parentPath(initialPath) : parameters.get("directory") ?? "");
  const [selectedPath, setSelectedPath] = useState(initialPath);
  const [library, setLibrary] = useState<LibraryData | null>(null);
  const [query, setQuery] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<OriginalPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [action, setAction] = useState<EntryAction | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [widths, setWidths] = useState([21, 30, 49]);
  const fileInput = useRef<HTMLInputElement>(null);
  const directoryMenu = useRef<HTMLDetailsElement>(null);
  const layout = useRef<HTMLDivElement>(null);
  const drag = useRef<{ index: number; x: number; widths: number[] } | null>(null);
  const { activeTasks, createTask, updateTask, refreshServerTasks } = useRuntimeTasks();
  const selected = library && findEntry(library.tree, selectedPath);
  const dirty = editing && draft !== (preview?.content ?? "");
  const building = activeTasks.some((task) => ["ingest", "rebuild_knowledge_base", "rebuild_all"].includes(task.kind));
  useEffect(() => {
    const path = parameters.get("path");
    setSelectedPath(path ?? "");
    setDirectory(path ? parentPath(path) : parameters.get("directory") ?? "");
    const source = parameters.get("source");
    if (!source) return;
    let active = true;
    api.sourceLocation(source).then((result) => { if (active) setParameters({ path: result.path }, { replace: true }); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "原文件不可用"); });
    return () => { active = false; };
  }, [parameters, setParameters]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.getLibrary(directory, query, controller.signal)
      .then((data) => { if (!controller.signal.aborted) { setLibrary(data); } })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "无法读取知识库"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [directory, query, revision]);
  useEffect(() => {
    const controller = new AbortController();
    setPreview(null); setPreviewError(null); setEditing(false);
    if (!selected || selected.kind !== "file") { setPreviewLoading(false); return; }
    setPreviewLoading(true);
    api.previewOriginal(selected.path, controller.signal).then((result) => {
      if (!controller.signal.aborted) { setPreview(result); setDraft(result.content ?? ""); }
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setPreviewError(caught instanceof Error ? caught.message : "无法预览原文件");
    }).finally(() => { if (!controller.signal.aborted) setPreviewLoading(false); });
    return () => controller.abort();
  }, [selected?.path, selected?.modified_at, revision]);
  const allowNavigation = () => !dirty || window.confirm("当前修改尚未保存，确认放弃修改？");
  const openDirectory = (path: string) => {
    if (!allowNavigation()) return;
    setError(null); setSelectedPath(""); setDirectory(path); setQuery("");
    setParameters(path ? { directory: path } : {}, { replace: true });
  };
  const openEntry = (entry: LibraryEntry) => {
    if (entry.kind === "directory") openDirectory(entry.path);
    else if (allowNavigation()) {
      setError(null); setQuery(""); setParameters({ path: entry.path }, { replace: true }); setSelectedPath(entry.path);
    }
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null);
    try { await operation(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const refresh = () => {
    if (!allowNavigation()) return;
    void run(async () => {
      const result = await api.refreshLibrary();
      setRevision((value) => value + 1);
      if (result.errors.length) setError(result.errors.map((item) => `${item.path}：${item.message}`).join("；"));
      else setNotice(`已刷新目录，更新 ${result.updated.length} 个文件。`);
    });
  };
  const importFiles = async (files: FileList | null) => {
    if (!files?.length || !allowNavigation()) return;
    const targetDirectory = directory;
    const batch = Array.from(files);
    await run(async () => {
      let completed = 0;
      const failures = [];
      for (const file of batch) {
        const id = createTask("import", `导入 ${file.name}`, "正在保存原文件并建立来源索引");
        updateTask(id, { status: "running" });
        try {
          await api.importFile(file, false, targetDirectory); completed++;
          updateTask(id, { status: "completed", detail: "原文件已保存，可手动构建图谱" });
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : "导入失败";
          failures.push(`${file.name}：${message}`); updateTask(id, { status: "failed", detail: message });
        }
      }
      setRevision((value) => value + 1); setNotice(`已导入 ${completed} 个文件。`);
      if (failures.length) setError(failures.join("；"));
    });
    if (fileInput.current) fileInput.current.value = "";
  };
  const build = (path: string) => void run(async () => {
    await api.buildLibraryEntry(path); await refreshServerTasks();
    setNotice("已加入后台构建队列，可在构建状态页查看进度。");
  });
  const remove = (entry: LibraryEntry) => {
    if (!window.confirm(`确认删除“${entry.path}”${entry.kind === "directory" ? "及其全部内容" : ""}？原文件移入回收站，并清理对应图谱与向量索引。`)) return;
    void run(async () => {
      await api.deleteLibraryEntry(entry.path);
      if (selectedPath === entry.path || selectedPath.startsWith(`${entry.path}/`)) {
        setSelectedPath("");
        const next = directory === entry.path || directory.startsWith(`${entry.path}/`) ? parentPath(entry.path) : directory;
        setDirectory(next); setParameters(next ? { directory: next } : {}, { replace: true });
      } else if (directory === entry.path || directory.startsWith(`${entry.path}/`)) {
        const next = parentPath(entry.path);
        setDirectory(next); setParameters(next ? { directory: next } : {}, { replace: true });
      }
      setRevision((value) => value + 1); setNotice("已删除，原文件保存在回收站。");
    });
  };
  const save = () => {
    if (!selected?.source_id || !preview?.content_hash) return;
    void run(async () => {
      await api.updateDocumentContent(selected.source_id!, draft, preview.content_hash);
      setEditing(false); setRevision((value) => value + 1);
      setNotice("原文件已保存，处理产物已更新，可手动重建图谱。");
    });
  };
  const resize = (index: number, delta: number, initial = widths) => {
    const available = (layout.current?.clientWidth ?? 1000) - 16;
    const minimums = [140, 180, 260].map((value) => value / available * 100);
    const change = Math.max(minimums[index] - initial[index], Math.min(delta, initial[index + 1] - minimums[index + 1]));
    const next = [...initial]; next[index] += change; next[index + 1] -= change; setWidths(next);
  };
  const separator = (index: number) => <div className="library-resizer" role="separator"
    aria-label={index === 0 ? "调整目录与文件列表宽度" : "调整文件列表与预览宽度"}
    aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(widths[index])} tabIndex={0}
    onKeyDown={(event) => {
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); resize(index, event.key === "ArrowLeft" ? -2 : 2); }
    }}
    onPointerDown={(event) => {
      event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { index, x: event.clientX, widths: [...widths] };
    }}
    onPointerMove={(event) => {
      if (drag.current && layout.current) resize(drag.current.index, (event.clientX - drag.current.x) / (layout.current.clientWidth - 16) * 100, drag.current.widths);
    }}
    onPointerUp={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }} />;
  return <section className="library-page">
    <header className="library-page-header"><h1>知识库</h1><div className="library-page-actions">
      <label className="search-field"><Search size={17} /><input aria-label="按名称或路径搜索" placeholder="按名称或路径搜索…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <button className="button button--secondary" disabled={busy || building} onClick={refresh}><RefreshCw size={16} className={busy ? "spin" : ""} /><span>刷新</span></button>
    </div></header>
    <input ref={fileInput} type="file" multiple hidden onChange={(event) => void importFiles(event.target.files)} />
    {error && <ErrorNotice message={error} />}{notice && <InfoNotice message={notice} />}
    <div ref={layout} className="library-layout" style={{ gridTemplateColumns: `${widths[0]}fr 8px ${widths[1]}fr 8px ${widths[2]}fr` }}>
      <aside className="library-pane library-directories" aria-label="知识目录">
        <header className="library-pane-header"><h2>存储目录</h2><details ref={directoryMenu} className="library-entry-menu library-directory-menu">
          <summary aria-label="目录操作" title="目录操作"><Plus size={17} /></summary><div>
            <button disabled={busy || building} onClick={() => { directoryMenu.current!.open = false; setAction({ kind: "create", entryKind: "directory" }); }}><FolderPlus size={14} />新建文件夹</button>
            <button disabled={busy || building} onClick={() => { directoryMenu.current!.open = false; setAction({ kind: "create", entryKind: "file" }); }}><FilePlus2 size={14} />新建文档</button>
            <button disabled={busy || building} onClick={() => { directoryMenu.current!.open = false; fileInput.current?.click(); }}><Upload size={14} />导入文件</button>
          </div></details></header>
        <div className="library-tree"><button className={`library-tree-row library-tree-root ${!directory && !selectedPath ? "is-active" : ""}`}
          onClick={() => openDirectory("")}><FolderOpen size={17} /><span>根目录</span></button>
          {library && <DirectoryTree entries={library.tree} directory={directory} selected={selectedPath} onOpen={openEntry} />}
          {loading && !library && <LoadingState label="读取目录…" />}
        </div><footer className="library-root-path" title={library?.root}>{library?.root ?? "原文件目录"}</footer>
      </aside>
      {separator(0)}
      <section className="library-pane library-files" aria-label="目录文件">
        <header className="library-pane-header"><nav className="library-breadcrumb" aria-label="目录位置">
          <button onClick={() => openDirectory("")}>根目录</button>
          {directory.split("/").filter(Boolean).map((part, index, parts) => <span key={index}><span>/</span><button onClick={() => openDirectory(parts.slice(0, index + 1).join("/"))}>{part}</button></span>)}
          {query && <span>/ 搜索结果</span>}
        </nav><button className="icon-button" aria-label="构建当前目录的图谱" title="构建当前目录的图谱" disabled={busy || building} onClick={() => build(directory)}><Network size={17} /></button></header>
        <div className="library-file-list" aria-busy={loading}>
          {library?.entries.map((entry) => <article key={entry.path} className={`library-file-row ${selectedPath === entry.path ? "is-active" : ""}`}>
            <button className="library-file-select" onClick={() => openEntry(entry)} title={entry.path}>
              {entry.kind === "directory" ? <Folder size={20} /> : <FileText size={20} />}
              <span><strong>{entry.name}</strong><small>{formatSize(entry.size)} · {new Date(entry.modified_at * 1000).toLocaleDateString("zh-CN", { month: "short", day: "numeric" })}</small>{query && <small>{parentPath(entry.path) || "根目录"}</small>}</span>
            </button><details className="library-entry-menu"><summary aria-label={`管理 ${entry.name}`}><MoreHorizontal size={18} /></summary><div>
              <button disabled={busy || building} onClick={() => setAction({ kind: "rename", entry })}><Pencil size={14} />重命名</button>
              <button disabled={busy || building} onClick={() => setAction({ kind: "move", entry })}><Folder size={14} />移动</button>
              <button disabled={busy || building || (entry.kind === "file" && !entry.supported)} onClick={() => build(entry.path)}><Network size={14} />构建图谱</button>
              <button className="text-danger" disabled={busy || building} onClick={() => remove(entry)}><Trash2 size={14} />删除</button>
            </div></details>
          </article>)}
          {!loading && !library?.entries.length && <div className="empty-compact"><FolderOpen size={28} /><strong>{query ? "没有匹配的文件" : "此目录为空"}</strong><span>{query ? "试试其他名称或路径。" : "导入文件，或新建文档与文件夹。"}</span></div>}
        </div><footer className="library-list-footer">{library?.entries.length ?? 0} 个条目</footer>
      </section>
      {separator(1)}
      <section className={`library-pane library-preview ${expanded ? "is-expanded" : ""}`} aria-label="原文件预览">
        <header className="library-pane-header library-preview-header"><div className="library-preview-heading"><div className="library-preview-title">
          <h2 title={selected?.name}>{selected?.name ?? "文件预览"}</h2>
          {selected && <span>{formatSize(selected.size)}</span>}
          <button className="icon-button" aria-label={expanded ? "退出展开预览" : "展开预览"} title={expanded ? "退出展开预览" : "展开预览"} onClick={() => setExpanded((value) => !value)}>{expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}</button>
        </div><p title={selected?.path}>{selected ? `根目录 / ${selected.path}` : "选择文件查看完整内容"}</p></div>
        {selected && <div className="library-preview-tools"><div className="library-preview-tabs">
          <button aria-pressed={!editing} onClick={() => { if (allowNavigation()) { setDraft(preview?.content ?? ""); setEditing(false); } }}>预览</button>
          <button aria-pressed={editing} disabled={!preview?.editable || !selected.source_id}
            title={preview?.editable ? selected.source_id ? "编辑原文件" : "刷新后可编辑此文件" : "此格式请下载后使用本地应用编辑"}
            onClick={() => setEditing(true)}>编辑</button>
        </div><a className="icon-button" href={api.originalUrl(selected.path, true)} aria-label="下载原文件" title="下载原文件"><Download size={16} /></a>
          {selected.supported && <button className="icon-button" aria-label="构建此文件的图谱" title="构建此文件的图谱" disabled={busy || building} onClick={() => build(selected.path)}><Network size={17} /></button>}
        </div>}
        </header>
        <div className={`library-preview-body ${editing ? "is-editing" : ""}`}>
          {!selected ? <div className="empty-state"><BookOpen size={32} /><h3>选择一份资料</h3><p>在目录或文件列表中选择文件，查看原文件内容。</p></div>
            : previewLoading ? <LoadingState label="正在读取原文件…" />
            : previewError ? <div className="empty-state"><FileText size={28} /><h3>预览暂不可用</h3><p>{previewError}</p><a className="text-link" href={api.originalUrl(selected.path, true)}>下载原文件查看</a></div>
            : editing ? <textarea className="library-editor" aria-label="编辑原文件" spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)} />
            : preview?.mode === "markdown" ? <MarkdownPreview content={preview.content ?? ""} />
            : preview?.mode === "text" ? <pre className="library-text-preview">{preview.content}</pre>
            : preview?.mode === "html" ? <iframe title={`原文件 ${selected.name}`} sandbox="" srcDoc={preview.html} />
            : preview?.mode === "pdf" ? <iframe title={`原文件 ${selected.name}`} src={api.originalUrl(selected.path)} />
            : preview?.mode === "image" ? <img className="library-image-preview" src={api.originalUrl(selected.path)} alt={selected.name} />
            : <div className="empty-state"><FileText size={30} /><h3>使用原文件查看</h3><p>此格式暂不支持网页内预览。</p><a className="button button--secondary" href={api.originalUrl(selected.path, true)}><Download size={16} />下载原文件</a></div>}
        </div>
        {editing && <footer className="library-edit-footer"><span>{dirty ? "尚未保存" : "已保存"}</span><button className="button button--secondary" disabled={busy} onClick={() => { setDraft(preview?.content ?? ""); setEditing(false); }}>撤销</button><button className="button button--primary" disabled={busy || !dirty} onClick={save}><Save size={15} />保存</button></footer>}
      </section>
    </div>
    {action && <EntryDialog action={action} directory={directory} onClose={() => setAction(null)} onComplete={(path, message, kind) => {
      if (action.kind === "create") {
        setQuery("");
        if (kind === "directory") { setDirectory(path); setSelectedPath(""); setParameters({ directory: path }, { replace: true }); }
        else { setSelectedPath(path); setParameters({ path }, { replace: true }); }
      } else {
        const previous = action.entry.path;
        if (selectedPath === previous || selectedPath.startsWith(`${previous}/`)) {
          const next = path + selectedPath.slice(previous.length);
          setSelectedPath(next); setParameters({ path: next }, { replace: true });
        } else if (directory === previous || directory.startsWith(`${previous}/`)) {
          const next = path + directory.slice(previous.length);
          setDirectory(next); setParameters({ directory: next }, { replace: true });
        }
      }
      setNotice(message); setRevision((value) => value + 1);
    }} />}
  </section>;
}
