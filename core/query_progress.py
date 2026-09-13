"""Transient, bounded query-stage telemetry; never stores questions or results."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import threading
from time import monotonic

LABELS = {
    "cache": "缓存检查", "cache_hit": "缓存命中", "llm": "LLM 优化",
    "split": "关键词与拆分", "normalize": "问题规范化", "embedding": "查询向量化", "graph": "图谱检索",
    "vector": "向量召回", "lexical": "关键词召回", "chunks": "切片聚合",
    "rerank": "重排序", "entities": "实体召回", "communities": "节点群召回",
    "answer": "生成回答",
}
_current = ContextVar("query_progress", default=None)


class QueryTrace:
    def __init__(self):
        self.created = monotonic()
        self.status = "running"
        self.steps = {}
        self.lock = threading.RLock()

    def set_step(self, key, status):
        with self.lock:
            if key not in LABELS or self.status != "running":
                return
            step = self.steps.setdefault(key, {"id": key, "label": LABELS[key]})
            step["status"] = status

    def finish(self, failed=False):
        with self.lock:
            self.status = "failed" if failed else "completed"
            for step in self.steps.values():
                if step["status"] == "running":
                    step["status"] = "failed" if failed else "completed"

    def snapshot(self):
        with self.lock:
            return {"available": True, "status": self.status, "steps": deepcopy(list(self.steps.values()))}


class QueryProgressStore:
    def __init__(self, max_entries=128, ttl=600):
        self.records = {}
        self.lock = threading.Lock()
        self.max_entries, self.ttl = max_entries, ttl

    def _expire(self):
        now = monotonic()
        for key in [key for key, value in self.records.items() if now - value.created > self.ttl]:
            del self.records[key]

    def start(self, key):
        with self.lock:
            self._expire()
            if key in self.records:
                return None  # Never overwrite another request's trace.
            if len(self.records) >= self.max_entries:
                finished = next((key for key, value in self.records.items() if value.status != "running"), None)
                if finished is None:
                    return None  # Telemetry capacity must not block a query.
                del self.records[finished]
            trace = QueryTrace()
            self.records[key] = trace
            return trace

    def get(self, key):
        with self.lock:
            self._expire()
            trace = self.records.get(key)
            return trace.snapshot() if trace else {"available": False, "steps": []}


@contextmanager
def bind_trace(trace):
    token = _current.set(trace)
    try:
        yield
    finally:
        _current.reset(token)


def step_status(key, status):
    trace = _current.get()
    if trace is not None:
        trace.set_step(key, status)


@contextmanager
def query_step(key):
    step_status(key, "running")
    try:
        yield
    except Exception:
        step_status(key, "failed")
        raise
    else:
        step_status(key, "completed")


def tracked_step(key):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with query_step(key):
                return function(*args, **kwargs)
        return wrapped
    return decorate
