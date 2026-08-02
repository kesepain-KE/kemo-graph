"""No-argument terminal entry point for checking and applying updates."""

from __future__ import annotations

import json
from typing import Any

from update import ApplicationUpdater, UpdateError


def _envelope(*, data: dict[str, Any] | None, error: dict[str, str] | None) -> str:
    return json.dumps(
        {"ok": error is None, "data": data, "error": error},
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    """Check GitHub and install an available update when it is safe to do so."""

    try:
        result = ApplicationUpdater().apply()
        print(_envelope(data=result, error=None))
        return 0
    except UpdateError as exc:
        print(
            _envelope(
                data=None,
                error={"code": type(exc).__name__, "message": str(exc)},
            )
        )
        return 1
    except Exception as exc:  # Keep the public entry point machine-readable.
        print(
            _envelope(
                data=None,
                error={"code": "UPDATE_FAILED", "message": str(exc)},
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
