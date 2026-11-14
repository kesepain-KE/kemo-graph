import {
  BookOpen, ChevronDown, ChevronRight, ClipboardPaste, Copy, Download, FilePlus2, FileText, Folder,
  FolderOpen, FolderPlus, Maximize2, Minimize2, MoreHorizontal, Network, Pencil,
  Plus, RefreshCw, Save, Scissors, Search, Trash2, Upload, X,
} from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiClientError } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { LibraryContextMenu, type LibraryMenuItem } from "../components/LibraryContextMenu";
import { MarkdownPreview } from "../components/MarkdownPreview";
import { useRuntimeTasks } from "../context/RuntimeTasksContext";
import type { LibraryData, LibraryEntry, OriginalPreview, ProcessedDocument } from "../types/api";
import "../styles/library.css";

const OfficePreview = lazy(() => import("../components/OfficePreview"));

const parentPath = (path: string) => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
const within = (path: string, parent: string) => path === parent || path.startsWith(`${parent}/`);
const basename = (path: string) => path.slice(path.lastIndexOf("/") + 1);
function findEntry(entries: LibraryEntry[], path: string): LibraryEntry | undefined {
  for (const entry of entries) {
    if (entry.path === path) return entry;
    const child = entry.children && findEntry(entry.children, path);
    if (child) return child;
  }
}
function expandTo(current: Set<string>, path: string, includeSelf = true): Set<string> {
  const next = new Set(current);
  const parts = path.split("/").filter(Boolean);
  for (let index = 1; index <= parts.length - (includeSelf ? 0 : 1); index++) {
    next.add(parts.slice(0, index).join("/"));
  }
  return next;
}
function formatSize(bytes: number | null) {
  if (bytes === null) return "文件夹";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
function isTextControl(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox']"));
}

type FileArea = "tree" | "list";
type FileClipboard = { mode: "copy" | "cut"; entry: LibraryEntry; sourceDirectory: string };
type InlineAction =
  | { kind: "create"; entryKind: "file" | "directory"; directory: string; area: "list" }
  | { kind: "rename"; entry: LibraryEntry; area: FileArea };
type MenuTarget = {
  x: number; y: number; entry: LibraryEntry | null; directory: string; area: FileArea; anchor: HTMLElement;
};

function InlineEntryName({ action, busy, onSubmit, onCancel }: {
  action: InlineAction; busy: boolean;
  onSubmit: (name: string) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState(action.kind === "rename" ? action.entry.name
    : action.entryKind === "file" ? "新建文档.md" : "新建目录");
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    const extension = name.lastIndexOf(".");
    const file = action.kind === "create" ? action.entryKind === "file" : action.entry.kind === "file";
    input.current?.setSelectionRange(0, file && extension > 0 ? extension : name.length);
    // This form is remounted for each new editing target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <form className="library-inline-name" onClick={(event) => event.stopPropagation()}
    onDoubleClick={(event) => event.stopPropagation()}
    onSubmit={async (event) => {
      event.preventDefault();
      if (busy) return;
      setError(null);
      try { await onSubmit(name); }
      catch (caught) { setError(caught instanceof Error ? caught.message : "操作失败"); }
    }}>
    <input ref={input} aria-label={action.kind === "rename" ? "重命名" : action.entryKind === "file" ? "新文件名称" : "新目录名称"}
      required maxLength={160} value={name} disabled={busy} autoComplete="off"
      onChange={(event) => setName(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault(); event.stopPropagation();
          if (!busy) onCancel();
        }
      }} />
    {error ? <small className="text-danger" role="alert">{error}</small>
      : <small>{busy ? "正在保存…" : "Enter 确认 · Esc 取消"}</small>}
  </form>;
}

function DirectoryTree({ entries, directory, selected, expanded, cutPath, inline, busy, onOpen, onMenu, onSubmit, onCancel }: {
  entries: LibraryEntry[]; directory: string; selected: string; expanded: Set<string>; cutPath?: string;
  inline: InlineAction | null; busy: boolean;
  onOpen: (entry: LibraryEntry) => void; onMenu: (event: MouseEvent<HTMLElement>, entry: LibraryEntry) => void;
  onSubmit: (name: string) => Promise<void>; onCancel: () => void;
}) {
  const render = (items: LibraryEntry[], depth = 0) => items.map((entry) => {
    const folder = entry.kind === "directory";
    const open = expanded.has(entry.path);
    const active = selected ? selected === entry.path : directory === entry.path;
    const naming = inline?.kind === "rename" && inline.area === "tree" && inline.entry.path === entry.path;
    const className = `library-tree-row ${active ? "is-active" : ""} ${cutPath === entry.path ? "is-cut" : ""}`;
    const icon = <>
      <span className="library-tree-chevron">{folder ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}</span>
      {folder ? open ? <FolderOpen size={16} /> : <Folder size={16} /> : <FileText size={16} />}
    </>;
    return <div key={entry.path}>
      {naming && inline ? <div className={`${className} is-naming`} style={{ "--tree-depth": depth } as CSSProperties}>
        {icon}<InlineEntryName action={inline} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
      </div> : <button type="button" className={className}
        style={{ "--tree-depth": depth } as CSSProperties} title={entry.path}
        aria-label={entry.name} aria-pressed={active} aria-expanded={folder ? open : undefined}
        onClick={() => onOpen(entry)} onContextMenu={(event) => onMenu(event, entry)}>
        {icon}<span>{entry.name}</span>
      </button>}
      {folder && open && entry.children ? render(entry.children, depth + 1) : null}
    </div>;
  });
  return <>{render(entries)}</>;
}

function DirectoryBreadcrumb({ directory, searching, onOpen }: {
  directory: string; searching: boolean; onOpen: (path: string) => void;
}) {
  const parts = directory.split("/").filter(Boolean);
  const collapsed = parts.length > 3;
  const menu = useRef<HTMLDetailsElement>(null);
  const segment = (part: string, index: number) => <span key={index}>
    <span aria-hidden="true">/</span><button type="button" title={parts.slice(0, index + 1).join("/")}
      aria-current={index === parts.length - 1 && !searching ? "page" : undefined}
      onClick={() => onOpen(parts.slice(0, index + 1).join("/"))}>{part}</button>
  </span>;
  return <nav className="library-breadcrumb" aria-label="目录位置">
    <button type="button" onClick={() => onOpen("")} aria-current={!directory && !searching ? "page" : undefined}>根目录</button>
    {collapsed && <><span aria-hidden="true">/</span><details ref={menu} className="library-breadcrumb-more"
      onKeyDown={(event) => { if (event.key === "Escape") { menu.current!.open = false; event.stopPropagation(); } }}>
      <summary aria-label="展开上级目录" title="展开上级目录">…</summary>
      <div>{parts.slice(0, -2).map((part, index) => <button key={index} type="button"
        title={parts.slice(0, index + 1).join("/")} onClick={() => {
          menu.current!.open = false; onOpen(parts.slice(0, index + 1).join("/"));
        }}>{part}</button>)}</div>
    </details></>}
    {parts.map((part, index) => !collapsed || index >= parts.length - 2 ? segment(part, index) : null)}
    {searching && <span>/ 搜索结果</span>}
  </nav>;
}

function PasteConflictDialog({ clipboard, onCancel, onKeepBoth }: {
  clipboard: FileClipboard; onCancel: () => void; onKeepBoth: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  return <dialog ref={dialog} className="library-dialog" aria-labelledby="library-conflict-title"
    onCancel={(event) => { event.preventDefault(); onCancel(); }}>
    <header><h2 id="library-conflict-title">目标位置已有同名项目</h2>
      <button type="button" className="icon-button" aria-label="取消粘贴" onClick={onCancel}><X size={18} /></button></header>
    <p>“{clipboard.entry.name}”已存在。保留两者会自动为粘贴的项目添加“副本”名称，已有内容不会被覆盖。</p>
    <footer><button type="button" className="button button--secondary" onClick={onCancel}>取消</button>
      <button type="button" className="button button--primary" onClick={onKeepBoth}>保留两者</button></footer>
  </dialog>;
}

export function DocumentsPage() {
  const [parameters, setParameters] = useSearchParams();
  const initialPath = parameters.get("path") ?? "";
  const initialDirectory = parameters.get("directory") ?? (initialPath ? parentPath(initialPath) : "");
  const [directory, setDirectory] = useState(initialDirectory);
  const [selectedPath, setSelectedPath] = useState(initialPath);
  const [expandedPaths, setExpandedPaths] = useState(() => expandTo(new Set(), initialDirectory));
  const [library, setLibrary] = useState<LibraryData | null>(null);
  const [query, setQuery] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<OriginalPreview | null>(null);
  const [processed, setProcessed] = useState<ProcessedDocument | null>(null);
  const [previewMode, setPreviewMode] = useState<"original" | "processed">("original");
  const [previewRevision, setPreviewRevision] = useState(0);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [inline, setInline] = useState<InlineAction | null>(null);
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const [clipboard, setClipboard] = useState<FileClipboard | null>(null);
  const [conflict, setConflict] = useState<{ clipboard: FileClipboard; directory: string } | null>(null);
  const [trackedBuild, setTrackedBuild] = useState<{ id: string; title: string } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [widths, setWidths] = useState([21, 30, 49]);
  const fileInput = useRef<HTMLInputElement>(null);
  const importDirectory = useRef(directory);
  const layout = useRef<HTMLDivElement>(null);
  const treePane = useRef<HTMLElement>(null);
  const listPane = useRef<HTMLElement>(null);
  const drag = useRef<{ index: number; x: number; widths: number[] } | null>(null);
  const view = useRef({ directory, selectedPath, dirty: false });
  const { tasks, createTask, updateTask, refreshServerTasks } = useRuntimeTasks();
  const selected = library && findEntry(library.tree, selectedPath);
  const selectedFile = selected?.kind === "file" ? selected : null;
  const dirty = editing && draft !== (preview?.content ?? "");
  view.current = { directory, selectedPath, dirty };
  const trackedTask = trackedBuild && tasks.find((task) => task.serverJobId === trackedBuild.id);
  const buildStatus = trackedTask?.status ?? "queued";
  const entries = library?.directory === directory ? library.entries : [];

  useEffect(() => {
    const path = parameters.get("path") ?? "";
    const nextDirectory = parameters.get("directory") ?? (path ? parentPath(path) : "");
    setSelectedPath(path);
    setDirectory(nextDirectory);
    setExpandedPaths((current) => expandTo(
      expandTo(current, nextDirectory, false), path && path !== nextDirectory ? parentPath(path) : "",
    ));
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
      .then((data) => {
        if (controller.signal.aborted) return;
        setLibrary(data);
        const path = view.current.selectedPath;
        if (path && !findEntry(data.tree, path)) {
          setSelectedPath("");
          setParameters(data.directory ? { directory: data.directory } : {}, { replace: true });
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught instanceof Error ? caught.message : "无法读取知识库");
        if (caught instanceof ApiClientError && caught.status === 404 && directory) {
          const parent = parentPath(directory);
          setSelectedPath(""); setDirectory(parent);
          setParameters(parent ? { directory: parent } : {}, { replace: true });
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [directory, query, revision]);
  useEffect(() => {
    const controller = new AbortController();
    setPreview(null); setProcessed(null); setPreviewError(null); setEditing(false);
    if (!selectedFile) { setPreviewLoading(false); return; }
    setPreviewLoading(true);
    const request = previewMode === "processed"
      ? selectedFile.source_id ? api.getProcessedDocument(selectedFile.source_id, controller.signal)
        : Promise.reject(new Error("尚未生成预处理文件，请先刷新或执行重新预处理并构建。"))
      : api.previewOriginal(selectedFile.path, controller.signal);
    request.then((result) => {
      if (controller.signal.aborted) return;
      if (previewMode === "processed") setProcessed(result as ProcessedDocument);
      else { setPreview(result as OriginalPreview); setDraft((result as OriginalPreview).content ?? ""); }
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setPreviewError(caught instanceof Error ? caught.message : "无法读取文件");
    }).finally(() => { if (!controller.signal.aborted) setPreviewLoading(false); });
    return () => controller.abort();
    // Unrelated directory mutations must not discard an unsaved original.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFile?.path, selectedFile?.modified_at, selectedFile?.source_id, previewMode, previewRevision]);
  useEffect(() => {
    if (buildStatus === "completed" || buildStatus === "failed") {
      setRevision((value) => value + 1);
      if (previewMode === "processed") setPreviewRevision((value) => value + 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackedBuild?.id, buildStatus]);

  const allowNavigation = () => {
    if (dirty && !window.confirm("当前修改尚未保存，确认放弃修改？")) return false;
    if (dirty) setEditing(false);
    return true;
  };
  const navigateDirectory = (path: string, selectFolder = false, expandSelf = true) => {
    if (!allowNavigation()) return false;
    setError(null); setMenu(null); setInline(null); setPreviewMode("original");
    setSelectedPath(selectFolder ? path : ""); setDirectory(path); setQuery("");
    setExpandedPaths((current) => expandTo(current, path, expandSelf));
    setParameters(selectFolder ? { directory: path, path } : path ? { directory: path } : {}, { replace: true });
    return true;
  };
  const selectEntry = (entry: LibraryEntry, mode: "original" | "processed" = "original") => {
    if ((selectedPath !== entry.path || mode !== previewMode) && !allowNavigation()) return false;
    if (inline && (inline.kind === "create" || inline.entry.path !== entry.path)) setInline(null);
    setError(null); setSelectedPath(entry.path); setPreviewMode(mode);
    setParameters({ directory, path: entry.path }, { replace: true });
    return true;
  };
  const openTreeEntry = (entry: LibraryEntry) => {
    if (entry.kind === "file") { selectEntry(entry); return; }
    const open = expandedPaths.has(entry.path);
    const same = directory === entry.path || selectedPath === entry.path;
    if (!open || !same) {
      if (!navigateDirectory(entry.path, true, false)) return;
    }
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (open) next.delete(entry.path); else next.add(entry.path);
      return next;
    });
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null);
    try { await operation(); }
    catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
      setRevision((value) => value + 1);
    } finally { setBusy(false); }
  };
  const applyPathChange = (previous: string, path: string) => {
    setExpandedPaths((current) => new Set(Array.from(current, (item) => within(item, previous) ? path + item.slice(previous.length) : item)));
    setClipboard((current) => {
      if (!current || !within(current.entry.path, previous)) return current;
      const next = path + current.entry.path.slice(previous.length);
      return { ...current, sourceDirectory: parentPath(next), entry: { ...current.entry, path: next, name: basename(next) } };
    });
    const current = view.current;
    const nextDirectory = within(current.directory, previous) ? path + current.directory.slice(previous.length) : current.directory;
    const nextSelection = within(current.selectedPath, previous) ? path + current.selectedPath.slice(previous.length) : current.selectedPath;
    if (nextDirectory !== current.directory || nextSelection !== current.selectedPath) {
      setDirectory(nextDirectory); setSelectedPath(nextSelection);
      setParameters(nextSelection ? { directory: nextDirectory, path: nextSelection }
        : nextDirectory ? { directory: nextDirectory } : {}, { replace: true });
    }
  };
  const refresh = () => {
    if (!allowNavigation()) return;
    void run(async () => {
      const result = await api.refreshLibrary();
      setRevision((value) => value + 1); setPreviewRevision((value) => value + 1);
      if (result.errors.length) setError(result.errors.map((item) => `${item.path}：${item.message}`).join("；"));
      else setNotice(`已刷新目录，更新 ${result.updated.length} 个文件。`);
    });
  };
  const importFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const targetDirectory = importDirectory.current;
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
  const build = (path: string, reprocess = false) => void run(async () => {
    const result = await api.buildLibraryEntry(path, reprocess);
    setTrackedBuild({ id: result.job_id, title: `${reprocess ? "重新预处理并构建" : "重新构建"} · ${basename(path) || "根目录"}` });
    await refreshServerTasks();
  });
  const remove = (entry: LibraryEntry) => {
    if (!window.confirm(`确认永久删除“${entry.name}”${entry.kind === "directory" ? "及其全部内容" : ""}？原文件、预处理文件及对应图谱与向量索引将一并删除，此操作不可恢复。`)) return;
    void run(async () => {
      await api.deleteLibraryEntry(entry.path);
      setClipboard((current) => current && within(current.entry.path, entry.path) ? null : current);
      const current = view.current;
      const next = within(current.directory, entry.path) ? parentPath(entry.path) : current.directory;
      const selection = within(current.selectedPath, entry.path) ? "" : current.selectedPath;
      setDirectory(next); setSelectedPath(selection);
      setParameters(selection ? { directory: next, path: selection } : next ? { directory: next } : {}, { replace: true });
      setRevision((value) => value + 1); setNotice("已永久删除文件及关联索引。");
    });
  };
  const beginCreate = (path: string, entryKind: "file" | "directory") => {
    if (!navigateDirectory(path)) return;
    setInline({ kind: "create", entryKind, directory: path, area: "list" });
  };
  const beginRename = (entry: LibraryEntry, area: FileArea) => {
    if (!allowNavigation()) return;
    setInline({ kind: "rename", entry, area });
  };
  const submitName = async (name: string) => {
    if (!inline) return;
    const action = inline;
    setBusy(true); setError(null); setNotice(null);
    try {
      if (action.kind === "create") {
        const result = await api.createLibraryEntry(action.directory, name, action.entryKind);
        if (view.current.directory === action.directory) {
          setSelectedPath(result.path);
          setParameters({ directory: action.directory, path: result.path }, { replace: true });
        }
        setNotice(`已创建${action.entryKind === "file" ? "文件" : "目录"}。`);
      } else {
        const destination = [parentPath(action.entry.path), name].filter(Boolean).join("/");
        if (destination !== action.entry.path) {
          const result = await api.moveLibraryEntry(action.entry.path, destination);
          applyPathChange(action.entry.path, result.path);
        }
        setNotice("重命名完成，来源与预处理映射保持不变。");
      }
      setInline(null); setRevision((value) => value + 1);
    } finally { setBusy(false); }
  };
  const copyEntry = (entry: LibraryEntry, mode: "copy" | "cut") => {
    setClipboard({ mode, entry, sourceDirectory: parentPath(entry.path) }); setNotice(null);
  };
  const paste = (target: string, intent = clipboard, keepBoth = false) => {
    if (!intent) return;
    if (intent.mode === "cut" && within(selectedPath, intent.entry.path) && !allowNavigation()) return;
    void run(async () => {
      let result;
      try {
        result = await api.transferLibraryEntry(intent.entry.path, target, intent.mode, keepBoth ? "keep_both" : "error");
      } catch (caught) {
        if (caught instanceof ApiClientError && caught.code === "LIBRARY_ENTRY_EXISTS") {
          setConflict({ clipboard: intent, directory: target }); return;
        }
        throw caught;
      }
      if (intent.mode === "cut") {
        setClipboard((current) => current === intent ? null : current);
        applyPathChange(intent.entry.path, result.path);
      }
      if (view.current.directory === target && !view.current.dirty) {
        setSelectedPath(result.path); setPreviewMode("original");
        setParameters({ directory: target, path: result.path }, { replace: true });
      }
      setRevision((value) => value + 1);
      setNotice(intent.mode === "copy" ? "已复制到目标目录，可继续粘贴。" : "已移动到目标目录。");
    });
  };
  const cancelInline = () => {
    const area = inline?.area;
    setInline(null);
    (area === "tree" ? treePane : listPane).current?.focus();
  };
  const openMenu = (event: MouseEvent<HTMLElement>, entry: LibraryEntry | null, area: FileArea) => {
    if (isTextControl(event.target)) return;
    event.preventDefault(); event.stopPropagation();
    if (entry && !selectEntry(entry)) return;
    const anchor = event.currentTarget.matches("button") ? event.currentTarget
      : event.currentTarget.querySelector<HTMLElement>(".library-file-select") ?? event.currentTarget;
    setMenu({ x: event.clientX, y: event.clientY, entry, directory: entry?.kind === "directory" ? entry.path : directory, area, anchor });
  };
  const closeMenu = (restoreFocus = false) => {
    if (restoreFocus && menu?.anchor.isConnected) menu.anchor.focus();
    setMenu(null);
  };
  const shortcuts = (event: KeyboardEvent, area: FileArea) => {
    if (event.defaultPrevented || isTextControl(event.target)) return;
    const entry = menu ? menu.entry : selected;
    const command = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (event.key === "Escape") {
      event.preventDefault(); setMenu(null);
      if (inline && !busy) cancelInline(); else setClipboard(null);
      return;
    }
    if (busy) return;
    if (command && (key === "c" || key === "x") && entry) {
      event.preventDefault(); copyEntry(entry, key === "c" ? "copy" : "cut"); closeMenu(true);
    } else if (command && key === "v" && clipboard) {
      event.preventDefault(); paste(directory); closeMenu(true);
    } else if (event.key === "Delete" && entry) {
      event.preventDefault(); closeMenu(true); remove(entry);
    } else if (event.key === "F2" && entry) {
      event.preventDefault(); closeMenu(true); beginRename(entry, area);
    } else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      event.preventDefault();
      const anchor = event.target as HTMLElement, bounds = anchor.getBoundingClientRect();
      setMenu({ x: bounds.left + 16, y: bounds.bottom, entry: entry || null, directory: entry?.kind === "directory" ? entry.path : directory, area, anchor });
    }
  };
  const menuGroups: LibraryMenuItem[][] = menu ? [
    !menu.entry || menu.entry.kind === "directory" ? [
      { label: "新建文件", icon: FilePlus2, disabled: busy, action: () => beginCreate(menu.directory, "file") },
      { label: "新建目录", icon: FolderPlus, disabled: busy, action: () => beginCreate(menu.directory, "directory") },
      { label: "导入文件", icon: Upload, disabled: busy, action: () => {
        importDirectory.current = menu.directory; fileInput.current?.click();
      } },
    ] : [],
    menu.entry?.kind === "file" ? [
      { label: "打开文件", icon: FileText, action: () => { if (selectEntry(menu.entry!)) setExpanded(true); } },
      { label: "打开预处理后的文件", icon: BookOpen, disabled: !menu.entry.source_id,
        title: !menu.entry.source_id ? "尚未生成预处理文件，请先刷新或重新预处理并构建" : undefined,
        action: () => { selectEntry(menu.entry!, "processed"); } },
    ] : [],
    [
      ...(menu.entry ? [
        { label: "复制", icon: Copy, shortcut: "Ctrl+C", disabled: busy, action: () => copyEntry(menu.entry!, "copy") },
        { label: "剪切", icon: Scissors, shortcut: "Ctrl+X", disabled: busy, action: () => copyEntry(menu.entry!, "cut") },
      ] : []),
      ...(!menu.entry || menu.entry.kind === "directory" ? [
        { label: "粘贴", icon: ClipboardPaste, shortcut: "Ctrl+V", disabled: busy || !clipboard, action: () => paste(menu.directory) },
      ] : []),
    ],
    menu.entry ? [
      { label: "重命名", icon: Pencil, shortcut: "F2", disabled: busy, action: () => beginRename(menu.entry!, menu.area) },
      { label: "删除", icon: Trash2, shortcut: "Delete", danger: true, disabled: busy, action: () => remove(menu.entry!) },
    ] : [],
    menu.entry ? [
      { label: `重新构建此${menu.entry.kind === "directory" ? "目录" : "文件"}`, icon: Network,
        disabled: busy || (menu.entry.kind === "file" && !menu.entry.supported), action: () => build(menu.entry!.path) },
      { label: "重新预处理并构建", icon: RefreshCw,
        disabled: busy || (menu.entry.kind === "file" && !menu.entry.supported), action: () => build(menu.entry!.path, true) },
    ] : [],
  ] : [];
  const save = () => {
    if (!selectedFile?.source_id || !preview?.content_hash) return;
    void run(async () => {
      await api.updateDocumentContent(selectedFile.source_id!, draft, preview.content_hash);
      setEditing(false); setRevision((value) => value + 1); setPreviewRevision((value) => value + 1);
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
      <button type="button" className="button button--secondary" disabled={busy} onClick={refresh}><RefreshCw size={16} className={busy ? "spin" : ""} /><span>刷新</span></button>
    </div></header>
    <input ref={fileInput} type="file" multiple hidden onChange={(event) => void importFiles(event.target.files)} />
    {error && <ErrorNotice message={error} />}{notice && <InfoNotice message={notice} />}
    {trackedBuild && <div className={`library-build-feedback is-${buildStatus}`} role="status">
      <Network size={17} /><div><strong>{trackedBuild.title} · {{ queued: "等待中", running: "处理中", completed: "已完成", failed: "失败", interrupted: "待核查" }[buildStatus]}</strong>
        <span>{trackedTask?.detail ?? "已加入后台队列，可以继续浏览目录。"}{buildStatus === "running" && trackedTask?.progress !== undefined ? ` · ${Math.round(trackedTask.progress * 100)}%` : ""}</span></div>
      <Link to="/build-status">查看构建状态</Link><button type="button" className="icon-button" aria-label="隐藏构建提示" onClick={() => setTrackedBuild(null)}><X size={15} /></button>
    </div>}
    <div ref={layout} className="library-layout" style={{ gridTemplateColumns: `${widths[0]}fr 8px ${widths[1]}fr 8px ${widths[2]}fr` }}>
      <aside ref={treePane} className="library-pane library-directories" aria-label="知识目录" tabIndex={0}
        onKeyDown={(event) => shortcuts(event, "tree")} onContextMenu={(event) => openMenu(event, null, "tree")}>
        <header className="library-pane-header"><h2>存储目录</h2>
          <button type="button" className="icon-button" aria-label="目录操作" title="目录操作" onClick={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            setMenu({ x: bounds.right, y: bounds.bottom, entry: null, directory, area: "tree", anchor: event.currentTarget });
          }}><Plus size={17} /></button></header>
        <div className="library-tree">
          {library && <DirectoryTree entries={library.tree} directory={directory} selected={selectedPath}
            expanded={expandedPaths} cutPath={clipboard?.mode === "cut" ? clipboard.entry.path : undefined}
            inline={inline} busy={busy} onOpen={openTreeEntry} onMenu={(event, entry) => openMenu(event, entry, "tree")}
            onSubmit={submitName} onCancel={cancelInline} />}
          {loading && !library && <LoadingState label="读取目录…" />}
        </div><footer className="library-root-path" title={library?.root}>{library?.root ?? "原文件目录"}</footer>
      </aside>
      {separator(0)}
      <section ref={listPane} className="library-pane library-files" aria-label="目录文件" tabIndex={0}
        onKeyDown={(event) => shortcuts(event, "list")} onContextMenu={(event) => openMenu(event, null, "list")}>
        <header className="library-pane-header"><DirectoryBreadcrumb directory={directory} searching={Boolean(query)} onOpen={navigateDirectory} />
          <button type="button" className="icon-button" aria-label="构建当前目录的图谱" title="构建当前目录的图谱" disabled={busy} onClick={() => build(directory)}><Network size={17} /></button></header>
        <div className="library-file-list" aria-busy={loading}>
          {inline?.kind === "create" && inline.directory === directory && <article className="library-file-row is-naming">
            <div className="library-file-select">{inline.entryKind === "directory" ? <FolderPlus size={20} /> : <FilePlus2 size={20} />}
              <InlineEntryName key={`create-${inline.entryKind}-${inline.directory}`} action={inline} busy={busy} onSubmit={submitName} onCancel={cancelInline} /></div>
          </article>}
          {entries.map((entry) => <article key={entry.path} data-path={entry.path}
            className={`library-file-row ${selectedPath === entry.path ? "is-active" : ""} ${clipboard?.mode === "cut" && clipboard.entry.path === entry.path ? "is-cut" : ""}`}
            onContextMenu={(event) => openMenu(event, entry, "list")}>
            {inline?.kind === "rename" && inline.area === "list" && inline.entry.path === entry.path
              ? <div className="library-file-select">{entry.kind === "directory" ? <Folder size={20} /> : <FileText size={20} />}
                <InlineEntryName action={inline} busy={busy} onSubmit={submitName} onCancel={cancelInline} /></div>
              : <><button type="button" className="library-file-select" onClick={() => {
                if (entry.kind === "directory") navigateDirectory(entry.path); else selectEntry(entry);
              }}
                title={entry.path} aria-label={entry.name} aria-pressed={selectedPath === entry.path}>
                {entry.kind === "directory" ? <Folder size={20} /> : <FileText size={20} />}
                <span><strong>{entry.name}</strong><small>{formatSize(entry.size)} · {new Date(entry.modified_at * 1000).toLocaleDateString("zh-CN", { month: "short", day: "numeric" })}</small>{query && <small>{parentPath(entry.path) || "根目录"}</small>}</span>
              </button><button type="button" className="library-file-more" aria-label={`管理 ${entry.name}`}
                onClick={(event) => {
                  if (!selectEntry(entry)) return;
                  const bounds = event.currentTarget.getBoundingClientRect();
                  setMenu({ x: bounds.right, y: bounds.bottom, entry, directory: entry.kind === "directory" ? entry.path : directory, area: "list", anchor: event.currentTarget });
                }}><MoreHorizontal size={18} /></button></>}
          </article>)}
          {!loading && !entries.length && inline?.kind !== "create" && <div className="empty-compact"><FolderOpen size={28} /><strong>{query ? "没有匹配的文件" : "此目录为空"}</strong><span>{query ? "试试其他名称或路径。" : "右键新建、导入或粘贴文件。"}</span></div>}
        </div><footer className="library-list-footer"><span>{entries.length} 个条目</span>
          {clipboard && <span className="library-clipboard" role="status" title={`${clipboard.mode === "copy" ? "复制" : "剪切"}：${clipboard.entry.path}`}>
            {clipboard.mode === "copy" ? <Copy size={12} /> : <Scissors size={12} />}
            <span>{clipboard.mode === "copy" ? "已复制" : "待移动"}：{clipboard.entry.name}</span>
            <button type="button" aria-label="取消文件剪贴板" title="取消复制或剪切（Esc）" onClick={() => setClipboard(null)}><X size={13} /></button>
          </span>}
        </footer>
      </section>
      {separator(1)}
      {expanded && <button type="button" className="library-preview-backdrop" aria-label="关闭展开预览"
        onClick={() => setExpanded(false)} />}
      <section className={`library-pane library-preview ${expanded ? "is-expanded" : ""}`} aria-label="文件内容预览">
        <header className="library-pane-header library-preview-header"><div className="library-preview-heading"><div className="library-preview-title">
          <h2 title={selectedFile?.name}>{selectedFile?.name ?? "文件预览"}</h2>
          {selectedFile && <span>{previewMode === "processed" ? processed ? formatSize(processed.size) : "—" : formatSize(selectedFile.size)}</span>}
          <button type="button" className="icon-button" aria-label={expanded ? "退出展开预览" : "展开预览"} title={expanded ? "退出展开预览" : "展开预览"} onClick={() => setExpanded((value) => !value)}>{expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}</button>
        </div><p title={previewMode === "processed" ? processed?.processed_path : selectedFile?.path}>
          {selectedFile ? previewMode === "processed" ? processed?.relative_path ?? "正在定位预处理文件…" : `根目录 / ${selectedFile.path}` : "选择文件查看完整内容"}
        </p></div>
          {selectedFile && <div className="library-preview-tools"><div className="library-preview-tabs">
            <button type="button" aria-pressed={!editing} onClick={() => { if (allowNavigation()) { setDraft(preview?.content ?? ""); setEditing(false); } }}>预览</button>
            <button type="button" aria-pressed={editing} disabled={busy || previewMode !== "original" || !preview?.editable || !selectedFile.source_id}
              title={previewMode === "processed" ? "处理稿可在构建状态页编辑" : preview?.editable ? selectedFile.source_id ? "编辑原文件" : "刷新后可编辑此文件" : "此格式请下载后使用本地应用编辑"}
              onClick={() => setEditing(true)}>编辑</button>
          </div><span className="library-preview-source">{previewMode === "processed" ? "预处理 Markdown" : "原始文件"}</span>
            {previewMode === "processed" && <button type="button" className="library-source-back" onClick={() => setPreviewMode("original")}>查看原文件</button>}
            <a className="icon-button" href={api.originalUrl(selectedFile.path, true)} aria-label="下载原文件" title="下载原文件"><Download size={16} /></a>
            {selectedFile.supported && <button type="button" className="icon-button" aria-label="构建此文件的图谱" title="构建此文件的图谱" disabled={busy} onClick={() => build(selectedFile.path)}><Network size={17} /></button>}
          </div>}
        </header>
        <div className={`library-preview-body ${editing ? "is-editing" : ""}`}>
          {!selectedFile ? <div className="empty-state"><BookOpen size={32} /><h3>选择文件预览</h3></div>
            : previewLoading ? <LoadingState label={previewMode === "processed" ? "正在读取预处理文件…" : "正在读取原文件…"} />
            : previewError ? <div className="empty-state"><FileText size={28} /><h3>预览暂不可用</h3><p>{previewError}</p>
              {previewMode === "original" && <a className="text-link" href={api.originalUrl(selectedFile.path, true)}>下载原文件查看</a>}</div>
            : previewMode === "processed" ? <MarkdownPreview content={processed?.content ?? ""} />
            : editing ? <textarea className="library-editor" aria-label="编辑原文件" spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)} />
            : preview?.mode === "markdown" ? <MarkdownPreview content={preview.content ?? ""} />
            : preview?.mode === "text" ? <pre className="library-text-preview">{preview.content}</pre>
            : preview?.mode === "html" ? <iframe title={`原文件 ${selectedFile.name}`} sandbox="" srcDoc={preview.html} />
            : preview?.mode === "pdf" || preview?.mode === "office" ? <Suspense fallback={<LoadingState label="正在加载文档阅读器…" />}>
              <OfficePreview key={`${selectedFile.path}:${selectedFile.modified_at}`} name={selectedFile.name}
                url={api.originalUrl(selectedFile.path)} format={preview.format ?? "pdf"} />
            </Suspense>
            : preview?.mode === "image" ? <img className="library-image-preview" src={api.originalUrl(selectedFile.path)} alt={selectedFile.name} />
            : <div className="empty-state"><FileText size={30} /><h3>暂不支持预览</h3><a className="button button--secondary" href={api.originalUrl(selectedFile.path, true)}><Download size={16} />下载原文件</a></div>}
        </div>
        {editing && <footer className="library-edit-footer"><span>{dirty ? "尚未保存" : "已保存"}</span><button type="button" className="button button--secondary" disabled={busy} onClick={() => { setDraft(preview?.content ?? ""); setEditing(false); }}>撤销</button><button type="button" className="button button--primary" disabled={busy || !dirty} onClick={save}><Save size={15} />保存</button></footer>}
      </section>
    </div>
    {menu && <LibraryContextMenu key={`${menu.x}:${menu.y}:${menu.entry?.path ?? ""}`}
      x={menu.x} y={menu.y} groups={menuGroups} onClose={closeMenu} onShortcut={(event) => shortcuts(event, menu.area)} />}
    {conflict && <PasteConflictDialog clipboard={conflict.clipboard} onCancel={() => setConflict(null)}
      onKeepBoth={() => { const pending = conflict; setConflict(null); paste(pending.directory, pending.clipboard, true); }} />}
  </section>;
}
