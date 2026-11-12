from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import library
from core.config import AppConfig
from core.document_preview import preview_document
from core.ingestor import FileMapStore, Ingestor
from core.knowledge_base import KnowledgeBaseService, DocumentContentConflictError
from core.rag_engine import RAGEngine


@pytest.fixture
def service(tmp_path):
    settings = AppConfig(log_dir=str(tmp_path / "log"))
    return KnowledgeBaseService(settings=settings, data_dir=tmp_path / "data", external_dir=tmp_path / "content" / "markdown")


def imported(service, tmp_path, name="sample.txt", directory=""):
    path = tmp_path / name
    path.write_text("原文件正文\n第二行", encoding="utf-8")
    return service.import_document(path, ingest_after_import=False, directory=directory)


def test_original_bytes_survive_upload_staging_and_preview_ignores_markdown(service, tmp_path):
    from docx import Document
    path = tmp_path / "样本.docx"
    document = Document()
    document.add_heading("原文标题", 1)
    document.add_paragraph("原文段落")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "原文表格"
    document.save(path)
    original_bytes = path.read_bytes()
    result = service.import_document(path, ingest_after_import=False)
    path.unlink()
    original = library.original_for_source(service, result["source_id"])
    assert original.read_bytes() == original_bytes
    (service.external_dir / result["markdown_relative_path"]).write_text("处理产物已变化", encoding="utf-8")
    preview = preview_document(original)
    assert preview["mode"] == "html"
    assert all(text in preview["html"] for text in ("原文标题", "原文段落", "原文表格"))
    assert "处理产物已变化" not in preview["html"]


def test_real_recursive_tree_and_name_path_search(service, tmp_path):
    library.create_entry(service, "", "用户", "directory")
    library.create_entry(service, "用户", "技术调研", "directory")
    document = imported(service, tmp_path, directory="用户/技术调研")
    data = library.list_library(service, "用户/技术调研")
    assert data["entries"][0]["path"] == "用户/技术调研/sample.txt"
    assert data["tree"][0]["children"][0]["children"][0]["source_id"] == document["source_id"]
    assert library.list_library(service, search="技术调研/sample")["entries"][0]["name"] == "sample.txt"
    assert list(service.external_dir.glob("*.md"))
    assert not (service.external_dir / "用户").exists()


def test_folder_move_preserves_source_identity_and_processing_paths(service, tmp_path):
    library.create_entry(service, "", "research", "directory")
    document = imported(service, tmp_path, directory="research")
    before = service.list_documents()["documents"][0]
    library.relocate_entry(service, "research", "papers")
    after = service.list_documents()["documents"][0]
    assert after["source_id"] == before["source_id"]
    assert after["relative_path"] == before["relative_path"]
    assert after["content_hash"] == before["content_hash"]
    assert after["source_path"] == "papers/sample.txt"
    assert library.original_for_source(service, document["source_id"]).is_file()
    scan = Ingestor(service.data_dir, service.external_dir, settings=service.settings).scan_sources()
    assert scan.new_source_ids == []
    assert scan.newly_deleted_source_ids == []
    source = RAGEngine(service.data_dir, settings=service.settings)._load_source_paths({document["source_id"]})
    assert source[document["source_id"]]["relative_path"] == "papers/sample.txt"
    assert source[document["source_id"]]["original_path"].endswith("sample.txt")


def test_failed_index_update_restores_original_path_and_source_index(service, tmp_path):
    document = imported(service, tmp_path)
    mapping = (service.external_dir / "file_map.json").read_bytes()
    with patch.object(FileMapStore, "_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            library.relocate_entry(service, "sample.txt", "renamed.txt")
    assert library.original_for_source(service, document["source_id"]).name == "sample.txt"
    assert not (library.original_root(service) / "renamed.txt").exists()
    assert (service.external_dir / "file_map.json").read_bytes() == mapping


def test_move_can_reuse_a_deleted_original_path_without_duplicate_mappings(service, tmp_path):
    deleted = imported(service, tmp_path, name="previous.txt")
    service.delete_document(deleted["source_id"])
    current = imported(service, tmp_path, name="current.txt")
    library.relocate_entry(service, "current.txt", "previous.txt")
    refreshed = library.refresh_library(service)
    assert refreshed["errors"] == []
    sources = service.list_documents(status="active")["documents"]
    assert [(row["source_id"], row["source_path"]) for row in sources] == [(current["source_id"], "previous.txt")]
    mappings = FileMapStore(service.external_dir / "file_map.json").list()
    identities = [mapping.original_path for mapping in mappings]
    assert len(identities) == len(set(identities))


def test_original_edits_refresh_derived_file_and_reject_stale_writes(service, tmp_path):
    document = imported(service, tmp_path)
    content = service.get_document_content(document["source_id"])
    assert content["content"] == (tmp_path / "sample.txt").read_bytes().decode("utf-8")
    service.update_document_content(document["source_id"], "新的原文件内容", expected_content_hash=content["content_hash"])
    assert library.original_for_source(service, document["source_id"]).read_text(encoding="utf-8") == "新的原文件内容"
    assert "新的原文件内容" in (service.external_dir / document["markdown_relative_path"]).read_text(encoding="utf-8")
    with pytest.raises(DocumentContentConflictError):
        service.update_document_content(document["source_id"], "过期编辑", expected_content_hash=content["content_hash"])
    assert service.list_documents()["documents"][0]["graph_status"] == "pending"


def test_refresh_discovers_disk_changes_and_cleans_deleted_sources(service, tmp_path):
    root = library.original_root(service)
    (root / "nested").mkdir(parents=True)
    file = root / "nested" / "external.txt"
    file.write_text("directly created", encoding="utf-8")
    assert library.refresh_library(service)["updated"] == ["nested/external.txt"]
    source_id = service.list_documents()["documents"][0]["source_id"]
    file.write_text("updated outside the app", encoding="utf-8")
    assert library.refresh_library(service)["updated"] == ["nested/external.txt"]
    assert library.refresh_library(service)["updated"] == []
    assert service.list_documents()["documents"][0]["source_id"] == source_id
    file.unlink()
    assert library.refresh_library(service)["deleted"] == ["nested/external.txt"]
    assert service.list_documents(status="active")["documents"] == []


def test_build_selects_original_directory_and_reads_its_processed_file(service, tmp_path):
    library.create_entry(service, "", "selected", "directory")
    document = imported(service, tmp_path, directory="selected")
    imported(service, tmp_path, name="unselected.txt")
    read = []
    with patch.object(Ingestor, "_update_graph_for_source", side_effect=lambda record, text: read.append((record.source_id, text))), patch.object(Ingestor, "_update_rag_for_source"):
        result = service.ingest(paths=["selected"])
    assert result["graph_updated"] == 1
    assert read[0][0] == document["source_id"]
    assert "原文件正文" in read[0][1]


def test_delete_recycles_original_and_prevents_refresh_from_restoring_it(service, tmp_path):
    document = imported(service, tmp_path)
    library.delete_entry(service, document["source_path"])
    assert not (library.original_root(service) / document["source_path"]).exists()
    recycled = list((service.external_dir.parent / "recycle" / "originals").rglob("sample.txt"))
    assert recycled[0].read_text(encoding="utf-8") == "原文件正文\n第二行"
    assert library.refresh_library(service)["updated"] == []
    assert service.list_documents(status="active")["documents"] == []


def test_failed_recycling_restores_original_and_leaves_source_active(service, tmp_path):
    document = imported(service, tmp_path)
    with patch.object(Ingestor, "_move_source_to_recycle", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            service.delete_document(document["source_id"])
    assert library.original_for_source(service, document["source_id"]).read_bytes() == (tmp_path / "sample.txt").read_bytes()
    assert (service.external_dir / document["markdown_relative_path"]).is_file()


def test_original_files_and_unindexed_folders_follow_recycle_expiration(service, tmp_path):
    import json
    document = imported(service, tmp_path)
    library.delete_entry(service, document["source_path"])
    folder = library.original_root(service) / "attachments"
    folder.mkdir()
    (folder / "image.png").write_bytes(b"preserved attachment")
    library.delete_entry(service, "attachments")
    recycle = service.external_dir.parent / "recycle"
    for metadata in recycle.rglob("*.meta.json"):
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload["expires_at"] = "2000-01-01T00:00:00+00:00"
        metadata.write_text(json.dumps(payload), encoding="utf-8")
    assert service.cleanup_recycle()["deleted"] == 3
    assert not list(recycle.rglob("sample.txt"))
    assert not list(recycle.rglob("image.png"))


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "a/../../outside", "a\\b", "a//b"])
def test_library_paths_cannot_escape_or_alias_the_root(service, path):
    with pytest.raises(ValueError):
        library.resolve_path(service, path, must_exist=False)


def test_api_original_download_and_nested_import_round_trip(service, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(service.settings.model_dump_json(), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    with TestClient(app) as client:
        assert client.post("/api/v1/library/entries", json={"directory": "", "name": "files", "kind": "directory"}).status_code == 200
        response = client.post("/api/v1/import?ingest=false&directory=files", files={"file": ("source.txt", b"original body", "text/plain")})
        assert response.status_code == 200, response.text
        document = response.json()["data"]
        response = client.get(f"/api/v1/documents/{document['source_id']}/original")
        assert response.content == b"original body"
        assert "attachment" in response.headers["content-disposition"]
        response = client.patch("/api/v1/library/entries", json={"path": "files/source.txt", "destination": "files/renamed.txt"})
        assert response.status_code == 200, response.text
        assert client.get(f"/api/v1/documents/{document['source_id']}/source").json()["data"]["path"] == "files/renamed.txt"
        duplicate = client.post("/api/v1/import?ingest=false&directory=files", files={"file": ("renamed.txt", b"must not overwrite", "text/plain")})
        assert duplicate.status_code == 409
        assert client.get(f"/api/v1/documents/{document['source_id']}/original").content == b"original body"


def test_office_preview_covers_all_sheets_and_slides(tmp_path):
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches
    workbook = Workbook()
    workbook.active["A1"] = "第一张表"
    workbook.create_sheet("second")["A1"] = "最后一张表"
    xlsx = tmp_path / "sample.xlsx"
    workbook.save(xlsx)
    preview = preview_document(xlsx)["html"]
    assert "第一张表" in preview and "最后一张表" in preview
    presentation = Presentation()
    for label in ("第一页", "最后一页"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = label
    pptx = tmp_path / "sample.pptx"
    presentation.save(pptx)
    preview = preview_document(pptx)["html"]
    assert "第一页" in preview and "最后一页" in preview


def test_portable_store_uses_its_own_original_directory(tmp_path):
    settings = AppConfig(log_dir=str(tmp_path / "log"), portable_stores={"enabled": True, "allowed_roots": []})
    config = tmp_path / "config.json"
    config.write_text(settings.model_dump_json(), encoding="utf-8")
    app = create_app(config_path=config, data_dir=tmp_path / "default-data", external_dir=tmp_path / "default-content" / "markdown")
    root = str(tmp_path / "portable")
    with TestClient(app) as client:
        assert client.post("/api/v1/stores/initialize", json={"store_root": root, "scope": "knowledge.user"}).status_code == 200
        created = client.post("/api/v1/stores/library/create", json={"store_root": root, "directory": "", "name": "notes.md", "kind": "file"})
        assert created.status_code == 200, created.text
        moved = client.post("/api/v1/stores/library/move", json={"store_root": root, "path": "notes.md", "destination": "renamed.md"})
        assert moved.status_code == 200, moved.text
        listed = client.post("/api/v1/stores/library/list", json={"store_root": root})
        assert listed.json()["data"]["result"]["entries"][0]["path"] == "renamed.md"
        assert (Path(root) / "kemo-graph-storage" / "content" / "originals" / "renamed.md").is_file()
        assert not library.original_root(app.state.kemo_context).exists()
