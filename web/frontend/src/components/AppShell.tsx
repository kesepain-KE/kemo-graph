import {
  Activity,
  CheckCircle2,
  CircleAlert,
  Database,
  FileText,
  History,
  LoaderCircle,
  Network,
  Search,
  Settings2,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "../api/api";
import { useRuntimeTasks, type RuntimeTask } from "../context/RuntimeTasksContext";

const navItems = [
  { to: "/documents", label: "文档管理", icon: FileText },
  { to: "/graph", label: "知识图谱", icon: Network },
  { to: "/search", label: "知识检索", icon: Search },
  { to: "/settings", label: "系统配置", icon: Settings2 },
  { to: "/status", label: "运行状态", icon: Activity },
];

const pageMeta: Record<string, { title: string; eyebrow: string }> = {
  "/documents": { title: "文档管理", eyebrow: "Sources / Ingest" },
  "/graph": { title: "知识图谱", eyebrow: "Explore / Visualize" },
  "/search": { title: "知识检索", eyebrow: "Graph / RAG / Hybrid" },
  "/settings": { title: "系统配置", eyebrow: "Runtime / Providers" },
  "/status": { title: "运行状态", eyebrow: "Health / Metrics" },
};

function BrandMark() {
  return (
    <div className="brand-mark">
      <img src="/kemo-graph-logo.png" alt="kemo-graph" />
    </div>
  );
}

function formatTaskTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function taskStatusLabel(task: RuntimeTask): string {
  if (task.status === "queued") return "等待中";
  if (task.status === "running") return "处理中";
  if (task.status === "completed") return "已完成";
  if (task.status === "interrupted") return "待核查";
  return "失败";
}

function TaskStatusIcon({ task }: { task: RuntimeTask }) {
  if (task.status === "queued" || task.status === "running") {
    return <LoaderCircle className="spin" size={16} />;
  }
  if (task.status === "completed") return <CheckCircle2 size={16} />;
  if (task.status === "interrupted") return <CircleAlert size={16} />;
  return <XCircle size={16} />;
}

export function AppShell() {
  const location = useLocation();
  const meta = pageMeta[location.pathname] ?? pageMeta["/graph"];
  const [serviceState, setServiceState] = useState<"online" | "offline" | "checking">(
    "checking",
  );
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const taskCenterRef = useRef<HTMLDivElement>(null);
  const { tasks, activeTasks, clearFinished } = useRuntimeTasks();
  const hasAttentionTask = tasks.some(
    (task) => task.status === "failed" || task.status === "interrupted",
  );

  useEffect(() => {
    let active = true;
    api
      .status()
      .then(() => active && setServiceState("online"))
      .catch(() => active && setServiceState("offline"));
    return () => {
      active = false;
    };
  }, [location.pathname]);

  useEffect(() => {
    if (!taskPanelOpen) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (!taskCenterRef.current?.contains(event.target as Node)) {
        setTaskPanelOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTaskPanelOpen(false);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [taskPanelOpen]);

  return (
    <div className="app-shell">
      <aside className="nav-rail">
        <NavLink className="brand" to="/graph" aria-label="kemo-graph 首页">
          <BrandMark />
          <span className="brand-name">
            <strong>kemo-graph</strong>
            <small>Knowledge Base</small>
          </span>
        </NavLink>

        <nav className="nav-links" aria-label="主导航">
          <span className="nav-heading">Navigation</span>
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}
              aria-label={label}
              title={label}
            >
              <Icon size={20} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

      </aside>

      <div className={`app-workspace ${location.pathname === "/settings" ? "is-settings" : ""}`}>
        <header className="topbar">
          <div>
            <p className="eyebrow">{meta.eyebrow}</p>
            <h1>{meta.title}</h1>
          </div>
          <div className="topbar-actions">
            <div className="runtime-task-center" ref={taskCenterRef}>
              <button
                type="button"
                className={`runtime-task-pill ${activeTasks.length ? "is-running" : hasAttentionTask ? "is-attention" : "is-idle"}`}
                aria-expanded={taskPanelOpen}
                aria-haspopup="dialog"
                onClick={() => setTaskPanelOpen((open) => !open)}
                title="查看文档导入与知识库整理任务"
              >
                {activeTasks.length ? <LoaderCircle className="spin" size={15} /> : <History size={15} />}
                <span>{activeTasks.length ? `${activeTasks.length} 项处理中` : "运行记录"}</span>
              </button>

              {taskPanelOpen ? (
                <section className="runtime-task-popover" role="dialog" aria-label="运行任务与日志">
                  <header>
                    <span>
                      <strong>运行任务</strong>
                      <small>服务端持久保存，刷新或切换页面后仍可继续追踪</small>
                    </span>
                    <button
                      type="button"
                      disabled={!tasks.some((task) => task.status !== "queued" && task.status !== "running")}
                      onClick={clearFinished}
                    >
                      清除记录
                    </button>
                  </header>

                  <div className="runtime-task-list">
                    {!tasks.length ? (
                      <div className="runtime-task-empty">
                        <History size={22} />
                        <span>当前没有文档处理记录</span>
                      </div>
                    ) : null}
                    {tasks.map((task) => (
                      <article className={`runtime-task-item is-${task.status}`} key={task.id}>
                        <span className="runtime-task-item__icon"><TaskStatusIcon task={task} /></span>
                        <div>
                          <div className="runtime-task-item__title">
                            <strong>{task.title}</strong>
                            <b>{taskStatusLabel(task)}</b>
                          </div>
                          <p>{task.detail}</p>
                          {typeof task.progress === "number" ? (
                            <div className="runtime-task-progress" aria-label={`任务进度 ${Math.round(task.progress * 100)}%`}>
                              <span style={{ width: `${Math.max(2, task.progress * 100)}%` }} />
                              <b>{Math.round(task.progress * 100)}%</b>
                            </div>
                          ) : null}
                          <details>
                            <summary>{formatTaskTime(task.updatedAt)} · 查看过程</summary>
                            <ol>
                              {task.events.map((event) => (
                                <li key={event.id}>
                                  <time>{formatTaskTime(event.timestamp)}</time>
                                  <span>{event.message}</span>
                                </li>
                              ))}
                            </ol>
                          </details>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
            <div className={`service-pill is-${serviceState}`}>
              <span />
              {serviceState === "online"
                ? "本地服务在线"
                : serviceState === "offline"
                  ? "服务未连接"
                  : "正在检查"}
            </div>
            <div className="database-pill">
              <Database size={15} />
              <span>Local Knowledge Base</span>
            </div>
          </div>
        </header>

        <main className="page-stage">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
