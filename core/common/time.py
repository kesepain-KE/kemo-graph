from datetime import datetime, timezone
import time


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)
