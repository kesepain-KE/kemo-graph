"""Terminal entry point for checking, inspecting and applying updates."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from update import ApplicationUpdater, UpdateBlockedError, UpdateError


def _envelope(*, data: dict[str, Any] | None, error: dict[str, str] | None) -> str:
    return json.dumps(
        {"ok": error is None, "data": data, "error": error},
        ensure_ascii=False,
        indent=2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update.py",
        description="检查、查看或应用 kemo-graph 更新",
    )
    parser.add_argument(
        "--dirty",
        "--changes",
        dest="dirty",
        action="store_true",
        help="只列出未提交的工作区更改，不执行更新",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "存在未提交的程序文件修改时先备份再继续更新；"
            "本地与远端同版本时也重新安装"
        ),
    )
    return parser


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


def _blocked_hint(message: str) -> str | None:
    """为可自行解除的阻塞补一条可执行提示。"""

    if "未提交" not in message:
        return None
    return (
        "运行 python update.py --dirty 查看未提交的文件；"
        "确认要备份这些修改并继续更新时运行 python update.py --force"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Check GitHub and install an available update when it is safe to do so."""

    args = _parser().parse_args(argv)
    try:
        updater = ApplicationUpdater()
        if args.dirty:
            print(_envelope(data=updater.changes(), error=None))
            return 0
        checked = updater.check()
        if checked.get("force_update_available"):
            if args.force or _confirm_force_update(
                str(checked.get("current_version") or ""),
                str(checked.get("latest_version") or ""),
            ):
                result = updater.apply(force=True)
            else:
                result = {
                    **checked,
                    "updated": False,
                    "forced": False,
                    "message": "用户取消同版本强制更新",
                }
        else:
            result = updater.apply(force=args.force)
        print(_envelope(data=result, error=None))
        return 0
    except UpdateBlockedError as exc:
        error: dict[str, str] = {"code": type(exc).__name__, "message": str(exc)}
        hint = _blocked_hint(str(exc))
        if hint is not None:
            error["hint"] = hint
        print(_envelope(data=None, error=error))
        return 1
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
