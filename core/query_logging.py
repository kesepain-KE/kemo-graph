"""Shared query lifecycle logging for Web, HTTP API and CLI retrieval."""

from functools import wraps
from time import perf_counter
from uuid import uuid4


def trace_query(function):
    @wraps(function)
    def traced(self, query_mode, query, params, execute, *, force):
        query_id = uuid4().hex[:12]
        detail = f"query_id={query_id}, mode={query_mode}, query_chars={len(str(query))}, force={force}"
        # Do not persist question bodies, model answers, headers or credentials.
        self.owner._log_event("query_start", detail)
        started = perf_counter()
        try:
            result = function(self, query_mode, query, params, execute, force=force)
        except Exception as exc:
            self.owner._log_event("query_failed", f"{detail}, error={type(exc).__name__}", round((perf_counter() - started) * 1000), level="ERROR")
            raise
        self.owner._log_event("query_complete", detail, round((perf_counter() - started) * 1000))
        return result
    return traced
