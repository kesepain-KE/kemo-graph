from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import start
from api.routes import _is_loopback_host
from update import (
    ApplicationUpdater,
    SemanticVersion,
    UpdateSourceError,
    migrate_legacy_runtime,
)


def _project(tmp_path: Path, version: str = "1.0.0") -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    return root


def _runner(stdout: str = ""):
    def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return run


def test_semver_precedence_and_validation() -> None:
    assert SemanticVersion.parse("1.2.0") > SemanticVersion.parse("1.1.9")
    assert SemanticVersion.parse("1.0.0") > SemanticVersion.parse("1.0.0-rc.1")
    assert SemanticVersion.parse("1.0.0-rc.2") > SemanticVersion.parse("1.0.0-rc.1")
    assert SemanticVersion.parse("1.0.0+build.2") == SemanticVersion.parse(
        "1.0.0+build.1"
    )
    with pytest.raises(ValueError):
        SemanticVersion.parse("v1")


def test_check_finds_new_version_and_persists_state(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".git").mkdir()
    updater = ApplicationUpdater(
        root,
        fetch_json=lambda _url, _timeout: {"version": "1.1.0"},
        command_runner=_runner(),
    )

    status = updater.check()

    assert status["current_version"] == "1.0.0"
    assert status["latest_version"] == "1.1.0"
    assert status["update_available"] is True
    assert status["can_apply"] is True
    assert json.loads(
        (root / "update" / "runtime" / "state.json").read_text(encoding="utf-8")
    )[
        "latest_version"
    ] == "1.1.0"


def test_legacy_runtime_is_migrated_without_overwriting(tmp_path: Path) -> None:
    root = _project(tmp_path)
    legacy = root / ".update"
    legacy.mkdir()
    (legacy / "state.json").write_text('{"phase":"idle"}', encoding="utf-8")

    runtime = migrate_legacy_runtime(root)

    assert runtime == root / "update" / "runtime"
    assert (runtime / "state.json").read_text(encoding="utf-8") == '{"phase":"idle"}'
    assert not legacy.exists()


def test_remote_failure_is_not_reported_as_up_to_date(tmp_path: Path) -> None:
    root = _project(tmp_path)

    def fail(_url: str, _timeout: float):
        raise RuntimeError("offline")

    updater = ApplicationUpdater(root, fetch_json=fail)
    with pytest.raises(UpdateSourceError, match="检查 GitHub 更新失败"):
        updater.check()
    assert updater.status()["phase"] == "failed"
    assert updater.status()["error"]


def test_runtime_and_user_config_do_not_make_worktree_dirty(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".git").mkdir()
    porcelain = (
        " M config/config.json\0?? data/index.bin\0?? external/a.md\0"
        "?? log/a.tsv\0?? output/a.json\0?? tmp/a.tmp\0"
        "?? update/runtime/state.json\0"
    )
    updater = ApplicationUpdater(root, command_runner=_runner(porcelain))

    status = updater.status()

    assert status["dirty_files"] == []
    assert status["worktree_clean"] is True


def test_program_changes_block_update(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".git").mkdir()
    updater = ApplicationUpdater(
        root,
        command_runner=_runner(" M core/rag_engine.py\0?? tests/new_test.py\0"),
    )
    status = updater.status()
    assert status["dirty_files"] == ["core/rag_engine.py", "tests/new_test.py"]
    assert status["blocking_reasons"] == ["工作区包含未提交的程序文件修改"]


def test_user_config_wins_while_new_defaults_are_added(tmp_path: Path) -> None:
    root = _project(tmp_path)
    config = root / "config" / "config.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"rag": {"chunk_size": 1000, "new_option": True}, "theme": "white"}),
        encoding="utf-8",
    )
    updater = ApplicationUpdater(root)
    old_user = json.dumps(
        {"rag": {"chunk_size": 600}, "api_key": "secret"}
    ).encode()

    updater._merge_user_config(config, old_user)

    merged = json.loads(config.read_text(encoding="utf-8"))
    assert merged == {
        "rag": {"chunk_size": 600, "new_option": True},
        "theme": "white",
        "api_key": "secret",
    }


def test_cli_version_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert start.main(["version"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["version"] == "1.2.0"


def test_update_apply_loopback_guard() -> None:
    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("192.168.1.10") is False
