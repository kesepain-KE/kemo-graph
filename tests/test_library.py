import yaml
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from api.schemas import APIResponse
from core import library
from core.config import AppConfig
from core.db import connect_sources, initialize_databases
from core.document_preview import preview_document
from core.ingestor import FileMapStore, Ingestor
from core.knowledge_base import KnowledgeBaseService, DocumentContentConflictError, DocumentImportError
from core.rag_engine import RAGEngine
from core.search_cache import SearchCache, SearchCacheCleanupError, SearchCacheError, compute_state_hash, make_cache_key


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
    assert preview["mode"] == "office" and preview["format"] == "docx"
    assert not preview["editable"]
    assert "html" not in preview and "content" not in preview
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    with TestClient(app) as client:
        metadata = client.get("/api/v1/library/preview", params={"path": result["source_path"]})
        assert metadata.json()["data"] == preview
        response = client.get("/api/v1/library/original", params={"path": result["source_path"]})
        assert response.status_code == 200 and response.content == original_bytes
    assert original.read_bytes() == original_bytes


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


def test_new_file_import_failure_rolls_back_original_and_derived_files(service):
    with patch.object(FileMapStore, "upsert", side_effect=OSError("disk full")):
        with pytest.raises(DocumentImportError):
            library.create_entry(service, "", "new.md", "file")

    assert not (library.original_root(service) / "new.md").exists()
    assert not list(service.external_dir.glob("*.md"))
    assert library.source_rows(service) == []
    assert FileMapStore(service.external_dir / "file_map.json").list() == []

    created = library.create_entry(service, "", "new.md", "file")
    assert created["path"] == "new.md"
    assert len(library.source_rows(service)) == 1


@pytest.mark.parametrize("operation", ["single", "batch", "library"])
def test_delete_reports_cache_failure_and_allows_separate_cleanup(service, tmp_path, operation):
    document = imported(service, tmp_path)
    cache = SearchCache(service.paths, service.settings)
    state = compute_state_hash(service.paths, service.settings)
    key = make_cache_key("secret", state, {}, query_mode="rag")
    cache.set(key, "secret", state, {}, {"results": [{"content": "private"}]}, query_mode="rag")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    with TestClient(app) as client:
        with patch.object(SearchCache, "clear", side_effect=SearchCacheError("database locked")):
            if operation == "single":
                response = client.delete(f"/api/v1/documents/{document['source_id']}")
            elif operation == "batch":
                response = client.post("/api/v1/documents/delete-batch", json={"source_ids": [document["source_id"]]})
            else:
                response = client.request("DELETE", "/api/v1/library/entries", json={"path": document["source_path"]})

        assert response.status_code == 503, response.text
        payload = response.json()
        assert payload["ok"] is False and payload["data"] is None
        assert payload["error"]["code"] == "SEARCH_CACHE_CLEANUP_FAILED"
        assert "已经生效" in payload["error"]["message"]
        result = payload["error"]["result"]
        assert APIResponse.model_validate(payload).error.result == result
        assert result["search_cache_deleted"] is None
        assert "database locked" in result["search_cache_error"]
        if operation == "batch":
            assert result["deleted"] == 1 and result["failed"] == 0
        else:
            assert result["deleted_source_id"] == document["source_id"]
        assert service.list_documents(status="active")["documents"] == []
        assert not (library.original_root(service) / document["source_path"]).exists()
        assert cache.list()["total"] == 1
        assert any("search_cache_cleanup_failed" in log.read_text(encoding="utf-8")
                   for log in (tmp_path / "log").rglob("*.tsv"))

        cleanup = client.delete("/api/v1/search/cache")
        assert cleanup.status_code == 200
        assert cleanup.json()["data"]["deleted"] == 1
        assert cache.list()["total"] == 0


def test_delete_all_stops_before_next_batch_when_cache_cleanup_fails(service):
    initialize_databases(service.data_dir, service.settings)
    connection = connect_sources(service.paths)
    try:
        with connection:
            connection.executemany(
                "INSERT INTO sources(source_id,original_path,relative_path,path_hash,content_hash) VALUES(?,?,?,?,?)",
                [(str(index), f"original-{index}", f"document-{index:04}.md", str(index), "content")
                 for index in range(1001)],
            )
    finally:
        connection.close()

    with patch.object(SearchCache, "clear", side_effect=SearchCacheError("database locked")):
        with pytest.raises(SearchCacheCleanupError) as caught:
            service.delete_all_documents()

    result = caught.value.result
    assert result["requested"] == 1001
    assert result["deleted"] == len(result["documents"]) == 1000
    assert result["failed"] == 0 and result["unprocessed"] == 1
    assert result["search_cache_deleted"] is None
    assert service.list_documents(status="active")["pagination"]["total"] == 1


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


def test_refresh_removes_unregistered_processed_files_and_stale_mappings(service):
    ingestor = Ingestor(service.data_dir, service.external_dir, settings=service.settings)
    orphan = service.external_dir / "orphan.md"
    orphan.write_text("unmapped processed content", encoding="utf-8")
    stale = service.external_dir / "stale.md"
    stale.write_text("processed content without its original", encoding="utf-8")
    ingestor.file_map.upsert(str(service.external_dir.parent / "missing.docx"), "stale.md")
    ingestor.file_map.upsert("upload://already-removed.txt", "already-removed.md")
    result = library.refresh_library(service)
    assert set(result["deleted"]) == {"orphan.md", "stale.md", "already-removed.md"}
    assert not orphan.exists() and not stale.exists()
    assert ingestor.file_map.list() == []
    assert service.list_documents(status="active")["documents"] == []
    assert ingestor.scan_sources().new_source_ids == []


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


def test_delete_removes_original_and_processed_file_without_recreating_it(service, tmp_path):
    document = imported(service, tmp_path)
    library.delete_entry(service, document["source_path"])
    assert not (library.original_root(service) / document["source_path"]).exists()
    assert not (service.external_dir / document["markdown_relative_path"]).exists()
    assert not (service.external_dir.parent / "recycle").exists()
    assert library.refresh_library(service)["updated"] == []
    assert service.list_documents(status="active")["documents"] == []



def test_failed_file_deletion_leaves_original_and_source_active(service, tmp_path):
    document = imported(service, tmp_path)
    with patch.object(Ingestor, "_delete_source_file", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            service.delete_document(document["source_id"])
    assert library.original_for_source(service, document["source_id"]).read_bytes() == (tmp_path / "sample.txt").read_bytes()
    assert (service.external_dir / document["markdown_relative_path"]).is_file()


def test_delete_directory_removes_indexed_files_and_unindexed_attachments(service, tmp_path):
    library.create_entry(service, "", "attachments", "directory")
    document = imported(service, tmp_path, directory="attachments")
    folder = library.original_root(service) / "attachments"
    (folder / "image.png").write_bytes(b"attachment")
    result = library.delete_entry(service, "attachments")
    assert result["sources_deleted"] == 1
    assert not folder.exists()
    assert not (service.external_dir / document["markdown_relative_path"]).exists()
    assert not (service.external_dir.parent / "recycle").exists()
    assert service.list_documents(status="active")["documents"] == []


def test_delete_can_retry_index_cleanup_after_files_are_removed(service, tmp_path):
    document = imported(service, tmp_path)
    with patch.object(Ingestor, "_delete_source_data", side_effect=OSError("index unavailable")):
        with pytest.raises(OSError, match="index unavailable"):
            service.delete_document(document["source_id"])
    assert not (library.original_root(service) / document["source_path"]).exists()
    assert not (service.external_dir / document["markdown_relative_path"]).exists()
    assert len(service.list_documents(status="active")["documents"]) == 1
    result = service.delete_document(document["source_id"])
    assert result["deleted_source_id"] == document["source_id"]
    assert service.list_documents(status="active")["documents"] == []



@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "a/../../outside", "a\\b", "a//b"])
def test_library_paths_cannot_escape_or_alias_the_root(service, path):
    with pytest.raises(ValueError):
        library.resolve_path(service, path, must_exist=False)


def test_api_original_download_and_nested_import_round_trip(service, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
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
    preview = preview_document(pptx)
    assert preview["mode"] == "office" and preview["format"] == "pptx"
    assert "html" not in preview and "content" not in preview
    # The browser SDK reads the full original, including every slide, rather
    # than a server-generated linear HTML transcript.
    from zipfile import ZipFile
    with ZipFile(pptx) as archive:
        assert "第一页" in archive.read("ppt/slides/slide1.xml").decode()
        assert "最后一页" in archive.read("ppt/slides/slide2.xml").decode()


def test_portable_store_uses_its_own_original_directory(tmp_path):
    settings = AppConfig(log_dir=str(tmp_path / "log"), portable_stores={"enabled": True, "allowed_roots": []})
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
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


def test_copy_paste_preserves_bytes_and_creates_an_independent_mapping_on_refresh(service, tmp_path):
    library.create_entry(service, "", "A", "directory")
    library.create_entry(service, "", "B", "directory")
    original = imported(service, tmp_path, directory="A")
    result = library.transfer_entry(service, "A/sample.txt", "B", "copy")
    assert result["path"] == "B/sample.txt"
    root = library.original_root(service)
    assert (root / result["path"]).read_bytes() == (root / "A/sample.txt").read_bytes()
    with pytest.raises(library.LibraryNameConflictError):
        library.transfer_entry(service, "A/sample.txt", "B", "copy")
    second = library.transfer_entry(service, "A/sample.txt", "B", "copy", conflict="keep_both")
    assert second["path"] == "B/sample - 副本.txt"
    assert (root / "B/sample.txt").read_bytes() == (root / "A/sample.txt").read_bytes()
    assert library.refresh_library(service)["errors"] == []
    rows = {row["library_path"]: row for row in library.source_rows(service)}
    assert len({row["source_id"] for row in rows.values()}) == 3
    assert rows["A/sample.txt"]["source_id"] == original["source_id"]
    assert len({row["relative_path"] for row in rows.values()}) == 3


def test_recursive_copy_and_cut_include_unsupported_files_and_keep_moved_source_ids(service, tmp_path):
    library.create_entry(service, "", "A", "directory")
    library.create_entry(service, "A", "nested", "directory")
    library.create_entry(service, "", "B", "directory")
    library.create_entry(service, "", "C", "directory")
    original = imported(service, tmp_path, directory="A/nested")
    root = library.original_root(service)
    (root / "A/nested/archive.bin").write_bytes(b"\x00\xff")
    library.transfer_entry(service, "A", "B", "copy")
    assert (root / "B/A/nested/archive.bin").read_bytes() == b"\x00\xff"
    library.transfer_entry(service, "A", "C", "cut")
    assert not (root / "A").exists()
    assert (root / "C/A/nested/sample.txt").is_file()
    assert (root / "B/A/nested/sample.txt").is_file()
    record = next(row for row in library.source_rows(service) if row["source_id"] == original["source_id"])
    assert record["library_path"] == "C/A/nested/sample.txt"
    assert record["relative_path"] == original["markdown_relative_path"]
    mapping = FileMapStore(service.external_dir / "file_map.json").get_by_original(str(root / record["library_path"]))
    assert mapping.markdown_path == original["markdown_relative_path"]


@pytest.mark.parametrize("mode", ["copy", "cut"])
@pytest.mark.parametrize("destination", ["A", "A/child"])
def test_paste_rejects_directory_cycles_without_changing_files(service, mode, destination):
    library.create_entry(service, "", "A", "directory")
    library.create_entry(service, "A", "child", "directory")
    with pytest.raises(ValueError, match="自身及其子目录"):
        library.transfer_entry(service, "A", destination, mode, conflict="keep_both")
    assert list((library.original_root(service) / "A").iterdir()) == [library.original_root(service) / "A/child"]


def test_failed_copy_cleans_only_its_new_destination(service, tmp_path):
    original = imported(service, tmp_path)
    library.create_entry(service, "", "B", "directory")
    root = library.original_root(service)
    content = (root / "sample.txt").read_bytes()
    def fail_after_write(reader, writer):
        writer.write(b"partial")
        raise OSError("disk full")
    with patch.object(library.shutil, "copyfileobj", side_effect=fail_after_write):
        with pytest.raises(OSError, match="disk full"):
            library.transfer_entry(service, "sample.txt", "B", "copy")
    assert not (root / "B/sample.txt").exists()
    assert library.original_for_source(service, original["source_id"]).read_bytes() == content
    assert len(library.source_rows(service)) == 1


def test_copy_does_not_remove_or_overwrite_a_destination_created_after_preflight(service, tmp_path):
    imported(service, tmp_path)
    library.create_entry(service, "", "B", "directory")
    root = library.original_root(service)
    real_open = Path.open
    target = root / "B/sample.txt"
    def raced_open(path, mode="r", *args, **kwargs):
        if path == target and mode == "xb":
            with real_open(path, "wb") as writer:
                writer.write(b"another writer")
        return real_open(path, mode, *args, **kwargs)
    with patch.object(Path, "open", raced_open):
        with pytest.raises(library.LibraryNameConflictError):
            library.transfer_entry(service, "sample.txt", "B", "copy")
    assert target.read_bytes() == b"another writer"


def test_copy_rejects_nested_links_before_creating_a_destination(service, tmp_path):
    library.create_entry(service, "", "A", "directory")
    library.create_entry(service, "", "B", "directory")
    root = library.original_root(service)
    outside = tmp_path / "private.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (root / "A/link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("需要创建符号链接的系统权限")
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        library.transfer_entry(service, "A", "B", "copy")
    assert not (root / "B/A").exists()
    assert outside.read_text(encoding="utf-8") == "outside"


def test_directory_browsing_remains_available_while_graph_write_lock_is_held(service):
    from threading import Event, Thread
    from core.locks import get_knowledge_base_lock
    library.create_entry(service, "", "A", "directory")
    locked, release = Event(), Event()
    def building():
        with get_knowledge_base_lock(service.data_dir):
            locked.set()
            release.wait(5)
    thread = Thread(target=building)
    thread.start()
    assert locked.wait(1)
    try:
        assert library.list_library(service)["entries"][0]["name"] == "A"
        with pytest.raises(DocumentContentConflictError, match="正在构建或修改"):
            library.create_entry(service, "", "B", "directory")
    finally:
        release.set()
        thread.join(timeout=2)
    assert not (library.original_root(service) / "B").exists()


def test_transfer_api_reports_conflicts_missing_sources_and_access_denied(service, tmp_path):
    imported(service, tmp_path)
    library.create_entry(service, "", "B", "directory")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    with TestClient(app) as client:
        payload = {"path": "sample.txt", "directory": "B", "mode": "copy"}
        assert client.post("/api/v1/library/transfer", json=payload).status_code == 200
        conflict = client.post("/api/v1/library/transfer", json=payload)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "LIBRARY_ENTRY_EXISTS"
        assert client.post("/api/v1/library/transfer", json={**payload, "conflict": "keep_both"}).status_code == 200
        assert client.post("/api/v1/library/transfer", json={**payload, "path": "missing.txt"}).status_code == 404
        assert client.post("/api/v1/library/transfer", json={**payload, "directory": "../outside"}).status_code == 422
        assert client.post("/api/v1/library/transfer", json={**payload, "mode": "overwrite"}).status_code == 422
        with patch.object(library, "transfer_entry", side_effect=PermissionError("denied")):
            response = client.post("/api/v1/library/transfer", json=payload)
            assert response.status_code == 403 and response.json()["error"]["code"] == "FILE_ACCESS_DENIED"


def test_library_background_rebuild_and_force_reprocess_have_distinct_content(service, tmp_path):
    import time
    document = imported(service, tmp_path)
    processed = service.external_dir / document["markdown_relative_path"]
    processed.write_text("# Manual processing revision", encoding="utf-8")
    Ingestor(service.data_dir, service.external_dir, settings=service.settings).scan_sources()
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    read = []
    with TestClient(app) as client, patch.object(
        Ingestor, "_update_graph_for_source", side_effect=lambda record, text: read.append(text)
    ), patch.object(Ingestor, "_update_rag_for_source"):
        for reprocess in (False, True):
            response = client.post("/api/v1/library/build", json={"path": document["source_path"], "reprocess": reprocess})
            assert response.status_code == 200, response.text
            job_id = response.json()["data"]["job_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert job["status"] == "completed", job
            assert job["result"]["graph_updated"] == job["result"]["rag_updated"] == 1
            assert ("Manual processing revision" in read[-1]) is not reprocess
        assert library.original_for_source(service, document["source_id"]).read_text(encoding="utf-8") == "原文件正文\n第二行"
