"""Web 进程内的轻量每日维护调度器。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Protocol

from .config import AppConfig
from .logger import DailyTSVLogger


LOGGER = logging.getLogger(__name__)


class MaintenanceService(Protocol):
    """调度器依赖的最小知识库服务接口。"""

    settings: AppConfig

    def generate_group_summaries(self) -> dict[str, Any]: ...

    def cleanup_recycle(self) -> dict[str, Any]: ...


class MaintenanceScheduler:
    """按本地时间每日触发群总结和回收站清理。"""

    def __init__(
        self,
        service_factory: Callable[[], MaintenanceService],
        *,
        poll_interval_seconds: float = 30.0,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds 必须大于 0")
        self._service_factory = service_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_run_date: date | None = None

    @property
    def is_running(self) -> bool:
        """后台线程是否仍在运行。"""

        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def is_executing(self) -> bool:
        """是否正在执行当日维护，供安全重启预检使用。"""

        return self._run_lock.locked()

    def start(self) -> bool:
        """幂等启动 daemon 线程；首次启动返回 ``True``。"""

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False

            service = self._service_factory()
            LOGGER.info(
                "维护调度器已启动：每日 %s 生成群总结并清理过期回收站文件"
                "（保留 %s 天）",
                service.settings.summary_trigger_time,
                service.settings.recycle_life_days,
            )
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="kemo-graph-maintenance",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        """通知后台线程退出；未启动时返回 ``False``。"""

        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return False
            self._stop_event.set()

        thread.join(timeout=max(timeout, 0.0))
        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
        return True

    def run_pending(self, now: datetime | None = None) -> dict[str, Any] | None:
        """触发到期任务；未到配置时间或当日已执行时返回 ``None``。"""

        service = self._service_factory()
        current = now or self._clock()
        trigger_time = service.settings.summary_trigger_time
        if current.strftime("%H:%M") != trigger_time:
            return None

        run_date = current.date()
        with self._run_lock:
            if self._last_run_date == run_date:
                return None
            # 在执行前登记，避免一次失败在同一分钟内被轮询反复触发。
            self._last_run_date = run_date

        LOGGER.info("开始执行每日维护任务：%s %s", run_date.isoformat(), trigger_time)
        result: dict[str, Any] = {
            "run_date": run_date.isoformat(),
            "trigger_time": trigger_time,
            "summary": None,
            "cleanup": None,
            "errors": {},
        }
        try:
            result["summary"] = service.generate_group_summaries()
        except Exception as exc:
            result["errors"]["summary"] = str(exc)
            LOGGER.exception("每日群总结任务执行失败")
            _log_scheduler(service.settings, "scheduler_error", "task=summary", "ERROR")

        try:
            result["cleanup"] = service.cleanup_recycle()
        except Exception as exc:
            result["errors"]["cleanup"] = str(exc)
            LOGGER.exception("每日回收站清理任务执行失败")
            _log_scheduler(service.settings, "scheduler_error", "task=cleanup", "ERROR")

        LOGGER.info("每日维护任务执行完成：%s", result)
        return result

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_pending()
            except Exception:
                # 配置在运行中被写坏等轮询级异常不应终止调度线程。
                LOGGER.exception("维护调度器轮询失败，将在下一轮重试")
            self._stop_event.wait(self._poll_interval_seconds)


def _log_scheduler(
    settings: AppConfig,
    action: str,
    detail: str,
    level: str = "INFO",
) -> None:
    try:
        DailyTSVLogger(
            settings.resolve_log_dir(),
            settings.log_level,
        ).log("scheduler", action, detail, level=level)
    except Exception:
        pass
