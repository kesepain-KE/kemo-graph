"""Optional live progress for standard queries; existing response bodies stay intact."""

from uuid import UUID
from fastapi import APIRouter, Request
from core.query_progress import QueryProgressStore, bind_trace
from .errors import success_response

router = APIRouter()


@router.get("/query/progress/{progress_id}")
def get_query_progress(progress_id: UUID, request: Request):
    return success_response(request.app.state.query_progress.get(str(progress_id)))


class QueryProgressMiddleware:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in {
            f"/api/v1/query/{mode}" for mode in ("graph", "rag", "hybrid", "answer", "global")
        }:
            return await self.app(scope, receive, send)
        raw = dict(scope.get("headers", [])).get(b"x-kemo-progress-id", b"")
        try:
            key = str(UUID(raw.decode("ascii")))
        except (ValueError, UnicodeError):
            return await self.app(scope, receive, send)
        trace = self.store.start(key)
        if trace is None:
            return await self.app(scope, receive, send)

        async def report(message):
            if message["type"] == "http.response.start":
                trace.finish(failed=message["status"] >= 400)
            await send(message)

        with bind_trace(trace):
            try:
                await self.app(scope, receive, report)
            except BaseException:
                trace.finish(failed=True)
                raise


def install_query_progress(app):
    store = QueryProgressStore()
    app.state.query_progress = store
    app.add_middleware(QueryProgressMiddleware, store=store)
