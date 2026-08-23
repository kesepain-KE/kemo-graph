"""kemo-graph application version discovery and safe Git self-update support."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import total_ordering
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = "version.json"
DEFAULT_REPOSITORY = "https://github.com/kesepain-KE/kemo-graph"
DEFAULT_VERSION_URL = (
    "https://raw.githubusercontent.com/kesepain-KE/kemo-graph/main/version.json"
)
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

ProgressCallback = Callable[[float, str], None]
FetchJSON = Callable[[str, float], Mapping[str, Any]]
CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """Base application update error."""


class UpdateSourceError(UpdateError):
    """The GitHub update source could not be read or validated."""


class UpdateBlockedError(UpdateError):
    """The installation is valid but unsafe to update in its current state."""


class UpdatePermissionError(UpdateError):
    """The caller is not permitted to apply an application update."""


@total_ordering
@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        normalized = str(value).strip()
        match = _SEMVER_RE.fullmatch(normalized)
        if match is None:
            raise ValueError(f"无效的 SemVer 版本号：{value}")
        prerelease = tuple((match.group("prerelease") or "").split("."))
        build = tuple((match.group("build") or "").split("."))
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease if prerelease != ("",) else (),
            build if build != ("",) else (),
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        core_self = (self.major, self.minor, self.patch)
        core_other = (other.major, other.minor, other.patch)
        if core_self != core_other:
            return core_self < core_other
        return _compare_prerelease(self.prerelease, other.prerelease) < 0


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if left == right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    return -1 if len(left) < len(right) else 1


def read_local_version(project_root: Path | str | None = None) -> str:
    root = Path(project_root or PROJECT_ROOT).resolve()
    try:
        payload = json.loads((root / VERSION_FILE).read_text(encoding="utf-8"))
        version = str(payload["version"]).strip()
        SemanticVersion.parse(version)
        return version
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise UpdateSourceError(f"无法读取本地 version.json：{exc}") from exc


class ApplicationUpdater:
    """Checks GitHub and applies a fast-forward update to a clean Git checkout."""

    _PROTECTED_EXACT = frozenset({".env", "config/config.json"})
    _PROTECTED_PREFIXES = (
        "data/",
        "external/",
        "log/",
        "output/",
        "tmp/",
        "update/runtime/",
        "kemo-graph-storage/",
    )

    def __init__(
        self,
        project_root: Path | str | None = None,
        *,
        version_url: str = DEFAULT_VERSION_URL,
        repository_url: str = DEFAULT_REPOSITORY,
        remote: str = DEFAULT_REMOTE,
        branch: str = DEFAULT_BRANCH,
        request_timeout: float = 10.0,
        fetch_json: FetchJSON | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.project_root = Path(project_root or PROJECT_ROOT).resolve()
        self.version_url = version_url
        self.repository_url = repository_url
        self.remote = remote
        self.branch = branch
        self.request_timeout = request_timeout
        self._fetch_json = fetch_json or _fetch_json
        self._command_runner = command_runner or _run_command
        self.update_dir = self.project_root / "update" / "runtime"
        self.state_path = self.update_dir / "state.json"
        self._lock = threading.RLock()
        migrate_legacy_runtime(self.project_root)

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            current_version = read_local_version(self.project_root)
            preflight = self._preflight()
            latest_version = state.get("latest_version")
            update_available = bool(state.get("update_available"))
            force_update_available = bool(state.get("force_update_available"))
            if latest_version:
                try:
                    latest = SemanticVersion.parse(str(latest_version))
                    current = SemanticVersion.parse(current_version)
                    update_available = latest > current
                    force_update_available = latest == current
                except ValueError:
                    # ``check`` reports malformed remote data.  Keep status
                    # readable and retain the last known relation until then.
                    pass
            state.update(
                {
                    "current_version": current_version,
                    "installation_mode": preflight["installation_mode"],
                    "worktree_clean": not preflight["dirty_files"],
                    "dirty_files": preflight["dirty_files"],
                    "update_available": update_available,
                    "force_update_available": force_update_available,
                    "can_apply": update_available
                    and not preflight["blocking_reasons"]
                    and not state.get("error")
                    and state.get("phase") not in {"checking", "updating", "failed"},
                    "can_force_apply": force_update_available
                    and not preflight["blocking_reasons"]
                    and not state.get("error")
                    and state.get("phase") not in {"checking", "updating", "failed"},
                    "blocking_reasons": preflight["blocking_reasons"],
                    "repository_url": self.repository_url,
                    "version_url": self.version_url,
                }
            )
            return state

    def check(self) -> dict[str, Any]:
        with self._lock:
            self._write_state({**self._read_state(), "phase": "checking", "error": None})
            try:
                payload = self._fetch_json(self.version_url, self.request_timeout)
                latest_text = str(payload.get("version", "")).strip()
                latest = SemanticVersion.parse(latest_text)
                current_text = read_local_version(self.project_root)
                current = SemanticVersion.parse(current_text)
                preflight = self._preflight()
                state = {
                    "current_version": current_text,
                    "latest_version": latest_text,
                    "update_available": latest > current,
                    "force_update_available": latest == current,
                    "installation_mode": preflight["installation_mode"],
                    "checked_at": _now_iso(),
                    "worktree_clean": not preflight["dirty_files"],
                    "dirty_files": preflight["dirty_files"],
                    "can_apply": latest > current and not preflight["blocking_reasons"],
                    "can_force_apply": latest == current and not preflight["blocking_reasons"],
                    "blocking_reasons": preflight["blocking_reasons"],
                    "phase": "idle",
                    "restart_required": bool(
                        self._read_state().get("restart_required", False)
                    ),
                    "error": None,
                    "repository_url": self.repository_url,
                    "version_url": self.version_url,
                }
                self._write_state(state)
                return state
            except UpdateError as exc:
                self._record_error(exc)
                raise
            except (ValueError, TypeError, KeyError) as exc:
                error = UpdateSourceError(f"远端 version.json 格式无效：{exc}")
                self._record_error(error)
                raise error from exc
            except Exception as exc:
                error = UpdateSourceError(f"检查 GitHub 更新失败：{exc}")
                self._record_error(error)
                raise error from exc

    def apply(
        self,
        *,
        progress: ProgressCallback | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        callback = progress or (lambda _value, _detail: None)
        with self._lock:
            checked = self.check()
            forced_same_version = bool(force and checked.get("force_update_available"))
            if not checked["update_available"] and not forced_same_version:
                message = (
                    "当前已是最新版本"
                    if checked.get("force_update_available")
                    else "本地版本高于远端版本，未执行降级"
                )
                return {**checked, "updated": False, "forced": False, "message": message}
            if not (checked["can_apply"] or (forced_same_version and checked["can_force_apply"])):
                reasons = "；".join(checked["blocking_reasons"]) or "当前安装不可更新"
                raise UpdateBlockedError(reasons)

            self._write_state({**checked, "phase": "updating", "error": None})
            original_config: bytes | None = None
            config_path = self.project_root / "config" / "config.json"
            old_head = ""
            merged = False
            try:
                old_head = self._git_output(["git", "rev-parse", "HEAD"]).strip()
                if not old_head:
                    raise UpdateError("无法确定当前 Git 提交，已停止更新")
                callback(0.05, "从 GitHub 获取最新代码")
                self._git(["git", "fetch", "--prune", self.remote, self.branch])
                target = f"{self.remote}/{self.branch}"
                ancestor = self._command_runner(
                    ["git", "merge-base", "--is-ancestor", "HEAD", target],
                    self.project_root,
                )
                if ancestor.returncode != 0:
                    raise UpdateBlockedError("本地分支与远端 main 已分叉，无法安全快进更新")

                changed_during_fetch = self._dirty_files()
                if changed_during_fetch:
                    raise UpdateBlockedError(
                        "获取更新期间工作区发生变化，已停止安装："
                        + "、".join(changed_during_fetch[:10])
                    )

                remote_version = self._read_remote_version(target)
                if SemanticVersion.parse(remote_version) < SemanticVersion.parse(
                    checked["latest_version"]
                ):
                    raise UpdateSourceError("GitHub main 分支版本低于远端 version.json")

                callback(0.22, "备份并保护用户配置")
                if config_path.is_file():
                    original_config = config_path.read_bytes()
                    self._backup_config(original_config)
                    committed = self._git_bytes("HEAD:config/config.json")
                    _write_bytes_atomic(config_path, committed)

                callback(0.36, "快进应用新版本")
                self._git(["git", "merge", "--ff-only", target])
                merged = True

                callback(0.52, "合并新版默认配置与用户配置")
                if original_config is not None:
                    self._merge_user_config(config_path, original_config)

                callback(0.65, "安装 Python 依赖")
                requirements = self.project_root / "requirements.txt"
                if requirements.is_file():
                    self._run(
                        [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
                    )

                frontend = self.project_root / "web" / "frontend"
                if (frontend / "package.json").is_file():
                    npm = shutil.which("npm") or shutil.which("npm.cmd")
                    if npm is None:
                        raise UpdateError("未找到 npm，无法构建 Web 前端")
                    callback(0.78, "安装 Web 前端依赖")
                    self._run([npm, "ci"], cwd=frontend)
                    callback(0.9, "构建 Web 前端")
                    self._run([npm, "run", "build"], cwd=frontend)

                current_version = read_local_version(self.project_root)
                completed = {
                    **checked,
                    "current_version": current_version,
                    "latest_version": current_version,
                    "update_available": False,
                    "force_update_available": False,
                    "can_apply": False,
                    "can_force_apply": False,
                    "worktree_clean": True,
                    "dirty_files": [],
                    "blocking_reasons": [],
                    "phase": "completed",
                    "restart_required": True,
                    "updated": True,
                    "forced": forced_same_version,
                    "previous_commit": old_head,
                    "current_commit": self._git_output(["git", "rev-parse", "HEAD"]).strip(),
                    "finished_at": _now_iso(),
                    "error": None,
                }
                self._write_state(completed)
                callback(1.0, "更新完成，请重启 kemo-graph")
                return completed
            except Exception as exc:
                rollback_error: Exception | None = None
                if merged and old_head:
                    # Only reset when no new program files appeared during the
                    # update.  This prevents a concurrent user edit from being
                    # overwritten while still recovering dependency/build failures.
                    try:
                        if not self._dirty_files():
                            self._git(["git", "reset", "--hard", old_head])
                        else:
                            rollback_error = UpdateBlockedError(
                                "更新失败后检测到新的程序文件修改，未自动回滚 Git 提交"
                            )
                    except Exception as rollback_exc:
                        rollback_error = rollback_exc
                if original_config is not None:
                    try:
                        # Restore the user's config after a possible Git reset;
                        # resetting first would otherwise overwrite it.
                        _write_bytes_atomic(config_path, original_config)
                    except OSError as config_exc:
                        rollback_error = rollback_error or config_exc
                error = exc if isinstance(exc, UpdateError) else UpdateError(str(exc))
                if rollback_error is not None:
                    error = UpdateError(f"{error}；自动回滚失败：{rollback_error}")
                self._record_error(error)
                raise error from exc if error is not exc else exc

    def _preflight(self) -> dict[str, Any]:
        if not (self.project_root / ".git").exists():
            return {
                "installation_mode": "source",
                "dirty_files": [],
                "blocking_reasons": ["当前不是 Git 安装，暂不支持自动应用更新"],
            }
        try:
            dirty = self._dirty_files()
        except UpdateError as exc:
            return {
                "installation_mode": "git",
                "dirty_files": [],
                "blocking_reasons": [str(exc)],
            }
        reasons = []
        if dirty:
            reasons.append("工作区包含未提交的程序文件修改")
        return {
            "installation_mode": "git",
            "dirty_files": dirty,
            "blocking_reasons": reasons,
        }

    def _dirty_files(self) -> list[str]:
        result = self._run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )
        records = result.stdout.split("\0")
        dirty: list[str] = []
        skip_next = False
        for record in records:
            if not record:
                continue
            if skip_next:
                skip_next = False
                continue
            if len(record) < 4:
                continue
            status = record[:2]
            path = record[3:].replace("\\", "/")
            if status[0] in {"R", "C"}:
                skip_next = True
            if not self._is_protected(path):
                dirty.append(path)
        return sorted(set(dirty))

    def _is_protected(self, path: str) -> bool:
        normalized = path.strip().lstrip("./").replace("\\", "/")
        return normalized in self._PROTECTED_EXACT or normalized.startswith(
            self._PROTECTED_PREFIXES
        )

    def _read_remote_version(self, target: str) -> str:
        raw = self._git_output(["git", "show", f"{target}:{VERSION_FILE}"])
        try:
            version = str(json.loads(raw)["version"]).strip()
            SemanticVersion.parse(version)
            return version
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise UpdateSourceError(f"远端分支 version.json 无效：{exc}") from exc

    def _backup_config(self, content: bytes) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.update_dir / "backups" / f"config-{stamp}.json"
        _write_bytes_atomic(target, content)

    def _merge_user_config(self, path: Path, original: bytes) -> None:
        try:
            defaults = json.loads(path.read_text(encoding="utf-8"))
            user = json.loads(original.decode("utf-8"))
            if not isinstance(defaults, dict) or not isinstance(user, dict):
                raise ValueError("配置根节点必须是对象")
            merged = _deep_merge(defaults, user)
            _write_json_atomic(path, merged)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise UpdateError(f"合并用户配置失败：{exc}") from exc

    def _read_state(self) -> dict[str, Any]:
        base = {
            "current_version": read_local_version(self.project_root),
            "latest_version": None,
            "update_available": False,
            "force_update_available": False,
            "installation_mode": "git" if (self.project_root / ".git").exists() else "source",
            "checked_at": None,
            "worktree_clean": False,
            "dirty_files": [],
            "can_apply": False,
            "can_force_apply": False,
            "blocking_reasons": [],
            "phase": "idle",
            "restart_required": False,
            "error": None,
            "repository_url": self.repository_url,
            "version_url": self.version_url,
        }
        if not self.state_path.is_file():
            return base
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                base.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
        return base

    def _write_state(self, state: Mapping[str, Any]) -> None:
        _write_json_atomic(self.state_path, dict(state))

    def _record_error(self, error: Exception) -> None:
        state = self._read_state()
        state.update({"phase": "failed", "error": str(error), "failed_at": _now_iso()})
        self._write_state(state)

    def _git(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self._run(command)

    def _git_output(self, command: list[str]) -> str:
        return self._run(command).stdout

    def _git_bytes(self, revision_path: str) -> bytes:
        # Route this read through the injected command runner as well.  The
        # updater's tests and deployment wrappers can then observe every Git
        # operation, while the production runner still returns UTF-8 text.
        result = self._command_runner(
            ["git", "show", revision_path],
            self.project_root,
        )
        if result.returncode != 0:
            message = str(result.stderr or result.stdout or "")[-2000:].strip()
            raise UpdateError(f"Git 读取失败：{message}")
        output = result.stdout
        if isinstance(output, bytes):
            return output
        return str(output or "").encode("utf-8")

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self._command_runner(command, cwd or self.project_root)
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()[-2000:]
            raise UpdateError(f"命令执行失败（{command[0]}）：{output}")
        return result


def _fetch_json(url: str, timeout: float) -> Mapping[str, Any]:
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "kemo-graph-updater"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise UpdateSourceError(f"无法读取 GitHub version.json：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise UpdateSourceError("GitHub version.json 根节点必须是 JSON 对象")
    return payload


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=environment,
    )


def _deep_merge(defaults: Mapping[str, Any], user: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in user.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_bytes_atomic(path, data)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_legacy_runtime(project_root: Path | str | None = None) -> Path:
    """Move legacy ``.update`` runtime files into the public update package.

    Existing installations may already contain update state, restart logs, or
    configuration backups.  Migration is best-effort and never overwrites a
    newer file in ``update/runtime``.
    """

    root = Path(project_root or PROJECT_ROOT).resolve()
    legacy_dir = root / ".update"
    runtime_dir = root / "update" / "runtime"
    if not legacy_dir.is_dir():
        return runtime_dir

    runtime_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(legacy_dir.rglob("*")):
        if not source.is_file():
            continue
        try:
            relative = source.relative_to(legacy_dir)
            target = runtime_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                continue
            os.replace(source, target)
        except OSError:
            # Runtime migration must not prevent the application from starting.
            continue

    try:
        for directory in sorted(
            (item for item in legacy_dir.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            directory.rmdir()
        legacy_dir.rmdir()
    except OSError:
        pass
    return runtime_dir


__all__ = [
    "ApplicationUpdater",
    "DEFAULT_REPOSITORY",
    "DEFAULT_VERSION_URL",
    "SemanticVersion",
    "UpdateBlockedError",
    "UpdateError",
    "UpdatePermissionError",
    "UpdateSourceError",
    "migrate_legacy_runtime",
    "read_local_version",
]
