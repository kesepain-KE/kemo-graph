"""No-argument terminal entry point for checking and applying updates."""

from __future__ import annotations

import json
import sys
from typing import Any

from update import ApplicationUpdater, UpdateError


def _envelope(*, data: dict[str, Any] | None, error: dict[str, str] | None) -> str:
    return json.dumps(
        {"ok": error is None, "data": data, "error": error},
        ensure_ascii=False,
        indent=2,
    )


def _confirm_force_update(current: str, latest: str) -> bool:
    print(
        f"本地版本 {current} 与远端版本 {latest} 相同。是否强制重新执行更新？[y/N] ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = input().strip().casefold()
    except EOFError:
        return False
    return answer in {"y", "yes", "是", "确认"}


def main() -> int:
    """Check GitHub and install an available update when it is safe to do so."""

    try:
        updater = ApplicationUpdater()
        checked = updater.check()
        if checked.get("force_update_available"):
            if not _confirm_force_update(
                str(checked.get("current_version") or ""),
                str(checked.get("latest_version") or ""),
            ):
                result = {
                    **checked,
                    "updated": False,
                    "forced": False,
                    "message": "用户取消同版本强制更新",
                }
            else:
                result = updater.apply(force=True)
        else:
            result = updater.apply()
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
