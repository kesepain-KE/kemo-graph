"""转换层的产品入口、异常语义和无扩展名文件回归。"""

import yaml
from pathlib import Path
import sys
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from api import create_app
from core.config import AppConfig
from core.service.errors import (
    DocumentImportPathError,
    SUPPORTED_IMPORT_SUFFIXES,
)
from markitdown import DocumentConverter, DocumentConverterResult, MarkItDown, supported_suffixes
from provider.tools import document_tools


@pytest.fixture
def client(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(AppConfig(log_dir=str(tmp_path / "log")).model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    app = create_app(
        config_path=config,
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "content" / "markdown",
    )
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("limit", ["max_input_bytes", "max_output_chars"])
def test_converter_limits_reach_api_as_413(client, monkeypatch, limit):
    monkeypatch.setattr(document_tools._MARKITDOWN, limit, 8)
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": ("limits.txt", b"This document exceeds the converter limit.")},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert client.get("/api/v1/documents").json()["data"]["pagination"]["total"] == 0


def test_missing_converter_dependency_reaches_api_as_422(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": ("document.pdf", b"%PDF-1.4\n")},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "CONVERSION_FAILED"
    assert "pdfplumber" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("filename", "status", "code"),
    [("../unsafe.txt", 400, "INVALID_PATH"), ("program.exe", 415, "UNSUPPORTED_FORMAT")],
)
def test_invalid_input_is_not_reported_as_conversion_failure(client, filename, status, code):
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": (filename, b"ordinary text")},
    )
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_adapter_rejects_unsafe_paths_with_domain_error(tmp_path):
    with pytest.raises(DocumentImportPathError):
        document_tools.import_document(tmp_path / ".." / "outside.txt", tmp_path / "output")


def test_format_registry_drives_product_policy_and_custom_instances(tmp_path):
    assert SUPPORTED_IMPORT_SUFFIXES == supported_suffixes() | {""}
    assert document_tools.SUPPORTED_DOCUMENT_SUFFIXES is SUPPORTED_IMPORT_SUFFIXES

    class CustomConverter(DocumentConverter):
        extensions = frozenset({".custom"})

        def convert(self, source, info):
            return DocumentConverterResult(text_content="Custom document content")

    converter = MarkItDown()
    converter.register_converter(CustomConverter())
    assert converter.supported_suffixes() == supported_suffixes() | {".custom"}
    source = tmp_path / "document.custom"
    source.write_text("input", encoding="utf-8")
    assert converter.convert(source).file_extension == ".custom"


def test_extensionless_upload_refresh_and_original_preview(client):
    content = b"Original document with no filename extension.\nKnowledge graph material."
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": ("notes", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    document = response.json()["data"]
    assert document["detected_format"] == "txt"
    original = Path(document["original_path"])
    assert original.name == "notes" and original.read_bytes() == content
    preview = client.get("/api/v1/library/preview", params={"path": document["source_path"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["content"] == content.decode()
    assert preview.json()["data"]["mode"] == "text"
    assert preview.json()["data"]["editable"] is False

    sibling = original.with_name("another-note")
    sibling.write_text("Another original document without an extension.", encoding="utf-8")
    listed = client.get("/api/v1/library").json()["data"]
    assert next(entry for entry in listed["entries"] if entry["name"] == sibling.name)["supported"]
    refreshed = client.post("/api/v1/library/refresh", json={"path": sibling.name})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["data"]["updated"] == [sibling.name]
    assert refreshed.json()["data"]["errors"] == []
    assert client.get("/api/v1/documents").json()["data"]["pagination"]["total"] == 2


def test_extensionless_unsupported_binary_is_not_treated_as_text(client):
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": ("image", bytes.fromhex("89504e470d0a1a0a") + bytes(256))},
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_extensionless_pdf_original_is_inline_but_download_remains_attachment(client):
    from test_markitdown_core import _minimal_pdf

    content = _minimal_pdf("Original PDF without a filename extension")
    response = client.post(
        "/api/v1/import?ingest=false",
        files={"file": ("document", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    document = response.json()["data"]
    assert document["detected_format"] == "pdf"
    preview = client.get("/api/v1/library/preview", params={"path": document["source_path"]})
    assert preview.json()["data"]["mode"] == "pdf"
    for endpoint in ("/api/v1/library/original", f"/api/v1/documents/{document['source_id']}/original"):
        original = client.get(endpoint, params={"path": document["source_path"]})
        assert original.content == content
        assert original.headers["content-type"] == "application/pdf"
        assert original.headers["content-disposition"].startswith("inline;")
    download = client.get(
        "/api/v1/library/original", params={"path": document["source_path"], "download": True},
    )
    assert download.content == content
    assert download.headers["content-disposition"].startswith("attachment;")


def _macro_document(suffix):
    """真实 OOXML 宏容器的 main-part Content-Type，不能只重命名后缀。"""
    normal = BytesIO()
    if suffix == ".docm":
        from docx import Document

        document = Document()
        document.add_heading("Macro document heading", 1)
        document.add_paragraph("Macro document body")
        document.save(normal)
        old = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        new = b"application/vnd.ms-word.document.macroEnabled.main+xml"
    elif suffix == ".pptm":
        from pptx import Presentation

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "Macro document heading"
        slide.placeholders[1].text = "Macro document body"
        presentation.save(normal)
        old = b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
        new = b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["Macro document heading", "Macro document body"])
        workbook.save(normal)
        old = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
        new = b"application/vnd.ms-excel.sheet.macroEnabled.main+xml"
    output = BytesIO()
    with ZipFile(normal) as source, ZipFile(output, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "[Content_Types].xml":
                assert old in data
                data = data.replace(old, new)
            target.writestr(entry, data)
    return output.getvalue()


@pytest.mark.parametrize("suffix", [".docm", ".pptm", ".xlsm"])
def test_macro_enabled_office_import_preview_and_adapter(client, tmp_path, suffix):
    content = _macro_document(suffix)
    response = client.post(
        "/api/v1/import?ingest=false", files={"file": (f"macro{suffix}", content)},
    )
    assert response.status_code == 200, response.text
    document = response.json()["data"]
    assert document["detected_format"] == suffix[1:]
    original = Path(document["original_path"])
    assert original.read_bytes() == content
    preview = client.get("/api/v1/library/preview", params={"path": document["source_path"]})
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()["data"]
    if suffix in {".docm", ".pptm"}:
        assert preview_data["mode"] == "office"
        assert preview_data["format"] == suffix[1:]
        assert client.get("/api/v1/library/original", params={"path": document["source_path"]}).content == content
    else:
        assert "Macro document body" in preview_data["html"]
    source = tmp_path / f"macro{suffix}"
    source.write_bytes(content)
    convert = {
        ".docm": document_tools.convert_docx,
        ".pptm": document_tools.convert_pptx,
        ".xlsm": document_tools.convert_spreadsheet,
    }[suffix]
    assert "Macro document heading" in convert(source)
    assert "Macro document body" in convert(source)


@pytest.mark.parametrize("dependency", ["mammoth", "markdownify"])
def test_docm_requires_mammoth_pipeline_without_python_docx_fallback(client, monkeypatch, dependency):
    content = _macro_document(".docm")
    monkeypatch.setitem(sys.modules, dependency, None)
    response = client.post(
        "/api/v1/import?ingest=false", files={"file": ("macro.docm", content)},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "CONVERSION_FAILED"
    assert dependency in error["message"] and "DOCM" in error["message"]
    assert client.get("/api/v1/documents").json()["data"]["pagination"]["total"] == 0


def test_docx_keeps_structured_fallback_when_mammoth_is_missing(tmp_path, monkeypatch):
    from docx import Document

    source = tmp_path / "normal.docx"
    document = Document()
    document.add_paragraph("Native DOCX fallback remains available")
    document.save(source)
    monkeypatch.setitem(sys.modules, "mammoth", None)
    result = MarkItDown().convert(source)
    assert result.converter == "DocxPythonConverter"
    assert "Native DOCX fallback" in result.text_content


def test_docm_mammoth_failure_keeps_its_diagnostic_instead_of_using_docx(tmp_path, monkeypatch):
    import mammoth

    source = tmp_path / "macro.docm"
    source.write_bytes(_macro_document(".docm"))

    def fail_conversion(*args, **kwargs):
        raise ValueError("broken macro document")

    monkeypatch.setattr(mammoth, "convert_to_html", fail_conversion)
    with pytest.raises(document_tools.DocumentConversionError, match="无法转换 DOCM.*broken macro document"):
        document_tools.convert_docx(source)
