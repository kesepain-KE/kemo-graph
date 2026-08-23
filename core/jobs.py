"""持久化维护任务队列；供 Web 运行气泡、API 和日志追踪共用。"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import AppConfig, load_config
from .db import DatabasePaths, connect_sources, initialize_databases
from update import ApplicationUpdater


ServiceFactory = Callable[[], Any]
UpdaterFactory = Callable[[], ApplicationUpdater]
SUPPORTED_JOB_KINDS = frozenset(
    {
        "ingest",
        "organize_graph",
        "rebuild_knowledge_base",
        "rebuild_all",
        "summarize",
        "cleanup_recycle",
        "update",
    }
)


class JobNotFoundError(LookupError):
    """请求的维护任务不存在。"""


class MaintenanceJobManager:
    def __init__(
        self,
        service_factory: ServiceFactory,
        *,
        data_dir: Path | str | None = None,
        settings: AppConfig | None = None,
        updater_factory: UpdaterFactory | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths: DatabasePaths = initialize_databases(data_dir, self.settings)
        self._service_factory = service_factory
        self._updater_factory = updater_factory or ApplicationUpdater
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._thread: threading.Thread | None = None
        self._guard = threading.RLock()
        self._stop_event = threading.Event()

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._mark_abandoned_jobs()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker,
                name="kemo-graph-maintenance-jobs",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._guard:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._queue.put(None)
        thread.join(timeout=5)
        with self._guard:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def submit(self, kind: str, **options: Any) -> dict[str, Any]:
        normalized = str(kind).strip().casefold()
        if normalized not in SUPPORTED_JOB_KINDS:
            raise ValueError(f"不支持的任务类型：{kind}")
        self.start()
        job_id = str(uuid4())
        now = _now_iso()
        detail = _initial_detail(normalized)
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                """
                INSERT INTO maintenance_jobs (
                    job_id, kind, status, progress, detail,
                    created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, ?, ?, ?)
                """,
                (job_id, normalized, detail, now, now),
            )
            _insert_event(connection, job_id, "INFO", "任务已进入后台队列")
            connection.commit()
        finally:
            connection.close()
        with self._guard:
            self._requests[job_id] = (normalized, dict(options))
        self._queue.put(job_id)
        self._prune_history()
        return self.get(job_id)

    def list(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_limit = limit or self.settings.maintenance_job_history_limit
        if not 1 <= selected_limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        parameters: list[Any] = []
        where = ""
        if status is not None:
            normalized_status = status.strip().casefold()
            if normalized_status not in {"queued", "running", "completed", "failed"}:
                raise ValueError("status 必须为 queued、running、completed 或 failed")
            where = "WHERE status = ?"
            parameters.append(normalized_status)
        parameters.append(selected_limit)
        connection = connect_sources(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT * FROM maintenance_jobs {where}
                ORDER BY created_at DESC, job_id DESC LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            return [_job_payload(connection, row, include_events=True) for row in rows]
        finally:
            connection.close()

    def get(self, job_id: str) -> dict[str, Any]:
        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"维护任务不存在：{job_id}")
            return _job_payload(connection, row, include_events=True)
        finally:
            connection.close()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._guard:
                request = self._requests.pop(job_id, None)
            if request is None:
                self._fail(job_id, "任务执行参数已丢失")
                continue
            kind, options = request
            self._run(job_id, kind, options)

    def _run(self, job_id: str, kind: str, options: dict[str, Any]) -> None:
        self._set_running(job_id)

        def progress(value: float, detail: str) -> None:
            self._set_progress(job_id, value, detail)

        try:
            if kind == "update":
                result = self._updater_factory().apply(
                    progress=progress,
                    force=bool(options.get("force", False)),
                )
            else:
                service = self._service_factory()
                result = self._run_service_job(service, kind, options, progress)
            self._complete(job_id, result)
        except Exception as exc:
            self._fail(job_id, str(exc) or type(exc).__name__)

    @staticmethod
    def _run_service_job(
        service: Any,
        kind: str,
        options: dict[str, Any],
        progress: Callable[[float, str], None],
    ) -> Any:
        if kind == "ingest":
            progress(0.05, "扫描文档并构建 Graph/RAG")
            result = service.ingest(
                paths=options.get("paths"),
                mode=options.get("mode", "both"),
            )
            if int(result.get("failed", 0)):
                raise RuntimeError(f"文档整理有 {result['failed']} 个失败项")
        elif kind == "organize_graph":
            progress(0.05, "扫描重叠节点与关系候选")
            result = service.organize_graph(
                use_llm=bool(options.get("use_llm", True)),
                summarize=bool(options.get("summarize", True)),
            )
        elif kind == "rebuild_knowledge_base":
            result = service.rebuild_knowledge_base(progress=progress)
        elif kind == "rebuild_all":
            result = service.rebuild_all(progress=progress)
        elif kind == "summarize":
            progress(0.1, "重新生成节点群总结")
            result = service.generate_group_summaries(
                force=bool(options.get("force", False))
            )
        elif kind == "cleanup_recycle":
            progress(0.2, "清理回收站")
            result = service.cleanup_recycle(
                force=bool(options.get("force", False))
            )
        else:  # pragma: no cover - submit 已校验
            raise ValueError(f"未知任务类型：{kind}")
        return result

    def _set_running(self, job_id: str) -> None:
        now = _now_iso()
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                """
                UPDATE maintenance_jobs
                SET status = 'running', progress = MAX(progress, 0.01),
                    detail = '后台任务正在执行', started_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )
            _insert_event(connection, job_id, "INFO", "后台任务开始执行")
            connection.commit()
        finally:
            connection.close()

    def _set_progress(self, job_id: str, value: float, detail: str) -> None:
        progress = max(0.0, min(0.99, float(value)))
        message = str(detail).strip() or "处理中"
        now = _now_iso()
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                """
                UPDATE maintenance_jobs
                SET progress = MAX(progress, ?), detail = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (progress, message, now, job_id),
            )
            _insert_event(connection, job_id, "INFO", message)
            connection.commit()
        finally:
            connection.close()

    def _complete(self, job_id: str, result: Any) -> None:
        now = _now_iso()
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                """
                UPDATE maintenance_jobs
                SET status = 'completed', progress = 1, detail = '任务已完成',
                    result_json = ?, error = NULL, finished_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (encoded, now, now, job_id),
            )
            _insert_event(connection, job_id, "INFO", "任务成功完成")
            connection.commit()
        finally:
            connection.close()
        self._prune_history()

    def _fail(self, job_id: str, error: str) -> None:
        now = _now_iso()
        message = str(error)[:4000]
        connection = connect_sources(self.paths)
        try:
            connection.execute(
                """
                UPDATE maintenance_jobs
                SET status = 'failed', detail = '任务执行失败', error = ?,
                    finished_at = ?, updated_at = ? WHERE job_id = ?
                """,
                (message, now, now, job_id),
            )
            _insert_event(connection, job_id, "ERROR", message)
            connection.commit()
        finally:
            connection.close()
        self._prune_history()

    def _mark_abandoned_jobs(self) -> None:
        connection = connect_sources(self.paths)
        try:
            rows = connection.execute(
                """
                SELECT job_id FROM maintenance_jobs
                WHERE status IN ('queued', 'running')
                """
            ).fetchall()
            now = _now_iso()
            for row in rows:
                job_id = str(row["job_id"])
                connection.execute(
                    """
                    UPDATE maintenance_jobs
                    SET status = 'failed', detail = '服务重启，任务已中断',
                        error = '服务进程在任务完成前退出', finished_at = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, now, job_id),
                )
                _insert_event(connection, job_id, "ERROR", "服务重启，任务已中断")
            connection.commit()
        finally:
            connection.close()

    def _prune_history(self) -> None:
        limit = self.settings.maintenance_job_history_limit
        connection = connect_sources(self.paths)
        try:
            stale = connection.execute(
                """
                SELECT job_id FROM maintenance_jobs
                WHERE status IN ('completed', 'failed')
                ORDER BY created_at DESC, job_id DESC
                LIMIT -1 OFFSET ?
                """,
                (limit,),
            ).fetchall()
            if stale:
                connection.executemany(
                    "DELETE FROM maintenance_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in stale],
                )
                connection.commit()
        finally:
            connection.close()


def list_jobs(
    data_dir: Path | str | None = None,
    *,
    settings: AppConfig | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    active = settings or load_config()
    manager = MaintenanceJobManager(
        lambda: None,
        data_dir=data_dir,
        settings=active,
    )
    return manager.list(limit=limit)


def get_job(
    job_id: str,
    data_dir: Path | str | None = None,
    *,
    settings: AppConfig | None = None,
) -> dict[str, Any]:
    active = settings or load_config()
    manager = MaintenanceJobManager(
        lambda: None,
        data_dir=data_dir,
        settings=active,
    )
    return manager.get(job_id)


def _job_payload(
    connection: Any,
    row: Any,
    *,
    include_events: bool,
) -> dict[str, Any]:
    result: Any = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = row["result_json"]
    payload = {
        "job_id": str(row["job_id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "progress": float(row["progress"] or 0.0),
        "detail": str(row["detail"] or ""),
        "result": result,
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "updated_at": row["updated_at"],
    }
    if include_events:
        payload["events"] = [
            {
                "event_id": str(event["event_id"]),
                "level": str(event["level"]),
                "message": str(event["message"]),
                "created_at": event["created_at"],
            }
            for event in connection.execute(
                """
                SELECT * FROM maintenance_job_events
                WHERE job_id = ? ORDER BY created_at, event_id
                """,
                (row["job_id"],),
            ).fetchall()
        ]
    return payload


def _insert_event(connection: Any, job_id: str, level: str, message: str) -> None:
    connection.execute(
        """
        INSERT INTO maintenance_job_events (
            event_id, job_id, level, message, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (str(uuid4()), job_id, level, str(message)[:4000], _now_iso()),
    )


def _initial_detail(kind: str) -> str:
    return {
        "ingest": "等待整理文档",
        "organize_graph": "等待整理知识图谱",
        "rebuild_knowledge_base": "等待重建变化文档",
        "rebuild_all": "等待全项目影子重建",
        "summarize": "等待生成节点群总结",
        "cleanup_recycle": "等待清理回收站",
        "update": "等待下载并安装应用更新",
    }[kind]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "JobNotFoundError",
    "MaintenanceJobManager",
    "SUPPORTED_JOB_KINDS",
    "get_job",
    "list_jobs",
]
