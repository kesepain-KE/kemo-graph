"""符号链接边界：知识库不得借链接把文档写到库外或从库外读入。

这类防护平时看不见，但它是「知识库目录 = 数据边界」这一前提的实现方式；
一旦被重构无声移除，读写会穿透到 external_dir 之外。Windows 默认不允许
普通用户创建符号链接，因此这些用例会在该平台自动跳过，由 Linux CI 覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import AppConfig
from core.library import create_entry, original_root, resolve_path, relocate_entry
from core.ingestor import IngestError
from core.ingestor._delete import _move_source_to_recycle
from core.knowledge_base import DocumentContentConflictError, KnowledgeBaseService
from core.knowledge_documents import KnowledgeDocumentsMixin


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:  # Windows 无特权 / 文件系统不支持
        pytest.skip(f"当前平台不允许创建符号链接：{exc}")


@pytest.fixture
def service(tmp_path: Path) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        settings=AppConfig(log_dir=str(tmp_path / "log")),
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "markdown",
    )


def _imported(service: KnowledgeBaseService, tmp_path: Path) -> dict:
    path = tmp_path / "doc.txt"
    path.write_text("# Knowledge\n\nA relates to B.", encoding="utf-8")
    return service.import_document(path, ingest_after_import=False)


@pytest.fixture(params=[_move_source_to_recycle, KnowledgeDocumentsMixin._move_source_to_recycle])
def recycle(request, tmp_path: Path):
    external = tmp_path / "markdown"
    external.mkdir()
    owner = SimpleNamespace(
        external_dir=external, settings=SimpleNamespace(recycle_life_days=7)
    )
    return lambda name: request.param(owner, name)


def test_resolve_path_accepts_plain_directories(service: KnowledgeBaseService) -> None:
    create_entry(service, "", "plain", "directory")
    assert resolve_path(service, "plain") == original_root(service) / "plain"
    assert resolve_path(service, "") == original_root(service)


def test_resolve_path_rejects_symlinked_project(service: KnowledgeBaseService, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "doc.md").write_text("outside the knowledge base", encoding="utf-8")
    original_root(service).mkdir(parents=True, exist_ok=True)
    _symlink(original_root(service) / "linked", outside, directory=True)

    with pytest.raises(ValueError):
        resolve_path(service, "linked")


def test_relocate_refuses_symlinked_target_file(service: KnowledgeBaseService, tmp_path: Path) -> None:
    document = _imported(service, tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("protected content", encoding="utf-8")
    _symlink(original_root(service) / "linked.md", outside)

    with pytest.raises((ValueError, DocumentContentConflictError)):
        relocate_entry(service, document["source_path"], "linked.md")

    # 外部目标未被跟随写入，原文档也仍在原处。
    assert outside.read_text(encoding="utf-8") == "protected content"
    assert (service.external_dir / document["markdown_relative_path"]).is_file()


def test_relocate_refuses_symlinked_project_target(service: KnowledgeBaseService, tmp_path: Path) -> None:
    document = _imported(service, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_root(service).mkdir(parents=True, exist_ok=True)
    _symlink(original_root(service) / "linked", outside, directory=True)

    with pytest.raises(ValueError):
        relocate_entry(service, document["source_path"], "linked/doc.txt")

    assert list(outside.iterdir()) == []
    assert (service.external_dir / document["markdown_relative_path"]).is_file()


def test_recycle_refuses_symlinked_destination(recycle, tmp_path: Path) -> None:
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("active content", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("protected content", encoding="utf-8")
    _symlink(destination, outside)

    with pytest.raises(IngestError, match="符号链接"):
        recycle("doc.md")

    assert outside.read_text(encoding="utf-8") == "protected content"
    assert source.read_text(encoding="utf-8") == "active content"


def test_recycle_refuses_symlinked_metadata(recycle, tmp_path: Path) -> None:
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("active content", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"protected": true}', encoding="utf-8")
    _symlink(destination.with_name("doc.md.meta.json"), outside)

    with pytest.raises(IngestError):
        recycle("doc.md")

    assert json.loads(outside.read_text(encoding="utf-8")) == {"protected": True}
    assert source.read_text(encoding="utf-8") == "active content"
