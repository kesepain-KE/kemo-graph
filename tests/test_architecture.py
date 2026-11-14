"""Architecture acceptance checks for the migrated package boundaries."""
import shutil

from tools.check_architecture import ROOT, check_architecture


def test_architecture_boundaries():
    assert check_architecture() == []


def test_architecture_rejects_upward_dependency(tmp_path):
    for name in ("core", "api", "provider"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    assert check_architecture(tmp_path) == []
    (tmp_path / "core/common/dependency_probe.py").write_text(
        "from core.service import KnowledgeBaseService\n", encoding="utf-8"
    )
    assert any(
        "core.common.dependency_probe -> core.service (upward dependency)" in violation
        for violation in check_architecture(tmp_path)
    )
