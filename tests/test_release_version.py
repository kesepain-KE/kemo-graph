"""Release metadata must agree with the authoritative root version.json."""

import json
from pathlib import Path
import re

from update import read_local_version

ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_matches_application_version():
    from markitdown import __version__ as markitdown_version

    version = read_local_version(ROOT)
    assert markitdown_version == version
    assert json.loads((ROOT / "web/frontend/package.json").read_text(encoding="utf-8"))["version"] == version
    lock = json.loads((ROOT / "web/frontend/package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == lock["packages"][""]["version"] == version
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', metadata, re.MULTILINE).group(1) == version


def test_documentation_matches_application_version():
    version = read_local_version(ROOT)
    for name in ("README.md", "README.en.md"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert f"version-{version}-" in content
        assert f"**{version}**" in content
    assert f"**当前版本**：`{version}`" in (ROOT / "api.md").read_text(encoding="utf-8")
    assert f"## {version} " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"**{version}**" in (ROOT / "markitdown/README.md").read_text(encoding="utf-8")


def test_api_and_web_openapi_use_root_version(tmp_path):
    from api import create_app
    from core.config import AppConfig
    from start_web import create_app as create_web_app
    config = tmp_path / "config.json"
    config.write_text(AppConfig().model_dump_json(), encoding="utf-8")
    for factory in (create_app, create_web_app):
        app = factory(config_path=config, data_dir=tmp_path / "data", external_dir=tmp_path / "docs")
        assert app.version == read_local_version(ROOT)
        assert app.openapi()["info"]["version"] == read_local_version(ROOT)
