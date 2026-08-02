import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api } from "../api/api";
import type { MaintenanceJob } from "../types/api";

export type RuntimeTaskKind =
  | "import"
  | "ingest"
  | "organize_graph"
  | "rebuild_knowledge_base"
  | "rebuild_all"
  | "summarize"
  | "cleanup_recycle"
  | "update";
export type RuntimeTaskStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "interrupted";

export type RuntimeTaskEvent = {
  id: string;
  timestamp: string;
  message: string;
};

export type RuntimeTask = {
  id: string;
  kind: RuntimeTaskKind;
  title: string;
  status: RuntimeTaskStatus;
  detail: string;
  createdAt: string;
  updatedAt: string;
  events: RuntimeTaskEvent[];
  serverJobId?: string;
  progress?: number;
};

type RuntimeTaskPatch = Partial<Pick<RuntimeTask, "status" | "detail">>;

type RuntimeTasksValue = {
  tasks: RuntimeTask[];
  activeTasks: RuntimeTask[];
  createTask: (kind: RuntimeTaskKind, title: string, detail: string) => string;
  updateTask: (taskId: string, patch: RuntimeTaskPatch, event?: string) => void;
  clearFinished: () => void;
  refreshServerTasks: () => Promise<void>;
};

const STORAGE_KEY = "kemo-graph.runtime-tasks.v1";
const DISMISSED_STORAGE_KEY = "kemo-graph.dismissed-server-tasks.v1";
const MAX_TASKS = 30;

const RuntimeTasksContext = createContext<RuntimeTasksValue | null>(null);

function uniqueId(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function loadStoredTasks(): RuntimeTask[] {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const now = new Date().toISOString();
    return parsed
      .filter((task): task is RuntimeTask => (
        Boolean(task)
        && typeof task === "object"
        && typeof (task as RuntimeTask).id === "string"
        && typeof (task as RuntimeTask).title === "string"
        && Array.isArray((task as RuntimeTask).events)
      ))
      .slice(0, MAX_TASKS)
      .map((task) => {
        if (task.status !== "queued" && task.status !== "running") return task;
        return {
          ...task,
          status: "interrupted",
          detail: "页面曾被刷新，无法继续跟踪该请求；请检查文档状态与服务日志。",
          updatedAt: now,
          events: [
            ...task.events,
            {
              id: uniqueId("event"),
              timestamp: now,
              message: "浏览器会话中断，任务状态已标记为待核查。",
            },
          ],
        };
      });
  } catch {
    return [];
  }
}

function loadDismissedServerTasks(): string[] {
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(DISMISSED_STORAGE_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
}

function serverTaskTitle(job: MaintenanceJob): string {
  return {
    ingest: "文档整理",
    organize_graph: "知识图谱整理",
    rebuild_knowledge_base: "变化文档知识库重建",
    rebuild_all: "全项目重建",
    summarize: "节点群总结",
    cleanup_recycle: "回收站清理",
    update: "kemo-graph 应用更新",
  }[job.kind];
}

function mapServerTask(job: MaintenanceJob): RuntimeTask {
  return {
    id: `server-${job.job_id}`,
    serverJobId: job.job_id,
    kind: job.kind,
    title: serverTaskTitle(job),
    status: job.status,
    detail: job.error || job.detail,
    progress: job.progress,
    createdAt: job.created_at,
    updatedAt: job.updated_at,
    events: (job.events ?? []).map((event) => ({
      id: event.event_id,
      timestamp: event.created_at,
      message: event.message,
    })),
  };
}

export function RuntimeTasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<RuntimeTask[]>(loadStoredTasks);
  const [dismissedServerTasks, setDismissedServerTasks] = useState<string[]>(loadDismissedServerTasks);

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(tasks));
    } catch {
      // 浏览器禁用存储时仍保留当前会话内的任务状态。
    }
  }, [tasks]);

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem(DISMISSED_STORAGE_KEY, JSON.stringify(dismissedServerTasks));
    } catch {
      // 无本地存储时仅在当前会话隐藏记录。
    }
  }, [dismissedServerTasks]);

  const refreshServerTasks = useCallback(async () => {
    try {
      const response = await api.getJobs(MAX_TASKS);
      const dismissed = new Set(dismissedServerTasks);
      const serverTasks = response.jobs
        .filter((job) => !dismissed.has(job.job_id))
        .map(mapServerTask);
      setTasks((current) => {
        const localTasks = current.filter((task) => !task.serverJobId);
        return [...serverTasks, ...localTasks]
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
          .slice(0, MAX_TASKS);
      });
    } catch {
      // 后端离线时保留最后一次成功同步的任务与本地上传记录。
    }
  }, [dismissedServerTasks]);

  useEffect(() => {
    void refreshServerTasks();
    const timer = globalThis.setInterval(() => void refreshServerTasks(), 2_500);
    const refreshOnFocus = () => void refreshServerTasks();
    globalThis.addEventListener?.("focus", refreshOnFocus);
    return () => {
      globalThis.clearInterval(timer);
      globalThis.removeEventListener?.("focus", refreshOnFocus);
    };
  }, [refreshServerTasks]);

  const createTask = useCallback(
    (kind: RuntimeTaskKind, title: string, detail: string): string => {
      const now = new Date().toISOString();
      const taskId = uniqueId("task");
      const task: RuntimeTask = {
        id: taskId,
        kind,
        title,
        status: "queued",
        detail,
        createdAt: now,
        updatedAt: now,
        events: [
          {
            id: uniqueId("event"),
            timestamp: now,
            message: detail,
          },
        ],
      };
      setTasks((current) => [task, ...current].slice(0, MAX_TASKS));
      return taskId;
    },
    [],
  );

  const updateTask = useCallback(
    (taskId: string, patch: RuntimeTaskPatch, event?: string) => {
      const now = new Date().toISOString();
      setTasks((current) => current.map((task) => {
        if (task.id !== taskId) return task;
        return {
          ...task,
          ...patch,
          updatedAt: now,
          events: event
            ? [
                ...task.events,
                { id: uniqueId("event"), timestamp: now, message: event },
              ].slice(-20)
            : task.events,
        };
      }));
    },
    [],
  );

  const clearFinished = useCallback(() => {
    const finishedServerIds = tasks
      .filter((task) => task.serverJobId && task.status !== "queued" && task.status !== "running")
      .map((task) => task.serverJobId as string);
    if (finishedServerIds.length) {
      setDismissedServerTasks((dismissed) => [
        ...new Set([...dismissed, ...finishedServerIds]),
      ].slice(-200));
    }
    setTasks((current) => current.filter(
      (task) => task.status === "queued" || task.status === "running",
    ));
  }, [tasks]);

  const activeTasks = useMemo(
    () => tasks.filter((task) => task.status === "queued" || task.status === "running"),
    [tasks],
  );
  const value = useMemo(
    () => ({ tasks, activeTasks, createTask, updateTask, clearFinished, refreshServerTasks }),
    [activeTasks, clearFinished, createTask, refreshServerTasks, tasks, updateTask],
  );

  return (
    <RuntimeTasksContext.Provider value={value}>
      {children}
    </RuntimeTasksContext.Provider>
  );
}

export function useRuntimeTasks(): RuntimeTasksValue {
  const value = useContext(RuntimeTasksContext);
  if (!value) throw new Error("useRuntimeTasks 必须在 RuntimeTasksProvider 内使用");
  return value;
}
