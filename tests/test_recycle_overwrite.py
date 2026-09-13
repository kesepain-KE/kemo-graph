"""Both document deletion paths use the same safe recycle replacement."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.ingestor import IngestError
from core.ingestor._delete import _move_source_to_recycle
from core.config import AppConfig
from core.knowledge_base import KnowledgeBaseService
from core.knowledge_documents import KnowledgeDocumentsMixin


@pytest.fixture(params=[_move_source_to_recycle, KnowledgeDocumentsMixin._move_source_to_recycle])
def recycle(request, tmp_path):
    external = tmp_path / "markdown"
    external.mkdir()
    owner = SimpleNamespace(external_dir=external, settings=SimpleNamespace(recycle_life_days=7))
    return lambda name: request.param(owner, name)


@pytest.mark.parametrize("existing", ["both", "document", "metadata", "neither"])
def test_replaces_existing_file_and_refreshes_metadata(recycle, tmp_path, existing):
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("new content", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    metadata = destination.with_name("doc.md.meta.json")
    if existing in ("both", "document"):
        destination.write_text("old content", encoding="utf-8")
    if existing in ("both", "metadata"):
        metadata.write_text('{"recycled_at": "old"}', encoding="utf-8")
    assert recycle("doc.md") == "recycle/doc.md"
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "new content"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    assert data["original_path"] == "doc.md"
    assert data["recycled_at"] != "old"
    assert data["expires_at"] > data["recycled_at"]
    assert not list(destination.parent.glob(".recycle-replace-*"))


def test_metadata_failure_restores_both_copies(recycle, tmp_path):
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("active", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    destination.write_text("previous", encoding="utf-8")
    metadata = destination.with_name("doc.md.meta.json")
    metadata.write_text("previous metadata", encoding="utf-8")
    with patch("core.ingestor._delete._write_json_atomic", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            recycle("doc.md")
    assert source.read_text(encoding="utf-8") == "active"
    assert destination.read_text(encoding="utf-8") == "previous"
    assert metadata.read_text(encoding="utf-8") == "previous metadata"
    assert not list(destination.parent.glob(".recycle-replace-*"))


def test_failed_move_restores_existing_recycle_copy(recycle, tmp_path):
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("active", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    destination.write_text("previous", encoding="utf-8")
    with patch("core.ingestor._delete.shutil.move", side_effect=PermissionError("locked")):
        with pytest.raises(PermissionError):
            recycle("doc.md")
    assert source.read_text(encoding="utf-8") == "active"
    assert destination.read_text(encoding="utf-8") == "previous"


def test_backup_cleanup_failure_is_deferred_after_new_copy_is_committed(recycle, tmp_path):
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("new content", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.parent.mkdir()
    destination.write_text("previous", encoding="utf-8")
    metadata = destination.with_name("doc.md.meta.json")
    metadata.write_text('{"recycled_at": "old"}', encoding="utf-8")

    with patch(
        "core.ingestor._delete.shutil.rmtree",
        side_effect=PermissionError("backup locked"),
    ):
        assert recycle("doc.md") == "recycle/doc.md"

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "new content"
    assert json.loads(metadata.read_text(encoding="utf-8"))["original_path"] == "doc.md"
    backups = list(destination.parent.glob(".recycle-replace-*"))
    assert len(backups) == 1
    assert (backups[0] / "document").read_text(encoding="utf-8") == "previous"


def test_delete_document_marks_source_deleted_when_backup_cleanup_is_deferred(tmp_path):
    external = tmp_path / "markdown"
    service = KnowledgeBaseService(
        settings=AppConfig(),
        data_dir=tmp_path / "data",
        external_dir=external,
    )
    uploaded = service.upload_file("# active", "doc.md")
    recycle_file = external.parent / "recycle" / "doc.md"
    recycle_file.parent.mkdir(parents=True)
    recycle_file.write_text("old", encoding="utf-8")

    with patch(
        "core.ingestor._delete.shutil.rmtree",
        side_effect=PermissionError("backup locked"),
    ):
        result = service.delete_document(str(uploaded["source_id"]))

    assert result["deleted_source_id"] == uploaded["source_id"]
    assert service.list_documents(status="active")["pagination"]["total"] == 0
    assert not (external / "doc.md").exists()
    assert recycle_file.read_text(encoding="utf-8") == "# active"


def test_does_not_replace_a_directory(recycle, tmp_path):
    source = tmp_path / "markdown" / "doc.md"
    source.write_text("active", encoding="utf-8")
    destination = tmp_path / "recycle" / "doc.md"
    destination.mkdir(parents=True)
    with pytest.raises(IngestError):
        recycle("doc.md")
    assert source.is_file()
    assert destination.is_dir()


def test_rejects_path_outside_source_root(recycle, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("do not move", encoding="utf-8")
    with pytest.raises(IngestError):
        recycle("../outside.md")
    assert outside.is_file()
