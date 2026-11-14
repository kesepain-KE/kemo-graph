"""用真实 PDF 验证表格、正文顺序与无表格输出，fixture 不依赖制 PDF 库。"""

import pytest

from markitdown import MarkItDown
from markitdown.converters.pdf import PdfConverter


def _pdf(pages: list[str]) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>", b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for commands in pages:
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        stream = commands.encode("ascii")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
    children = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{children}] /Count {len(page_ids)} >>".encode()
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def _text(x, y, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 12 Tf {x} {y} Td ({escaped}) Tj ET\n"


def _table(left, top, rows: list[list[str]], width=240, height=40) -> str:
    columns = max(map(len, rows))
    bottom = top - len(rows) * height
    commands = ""
    for column in range(columns + 1):
        x = left + column * width / columns
        commands += f"{x} {bottom} m {x} {top} l S\n"
    for row in range(len(rows) + 1):
        y = top - row * height
        commands += f"{left} {y} m {left + width} {y} l S\n"
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            for line, content in enumerate(value.split("\n")):
                commands += _text(
                    left + column * width / columns + 6,
                    top - row * height - 16 - line * 12,
                    content,
                )
    return commands


def _convert(tmp_path, pages, *, extract_tables=True):
    path = tmp_path / "sample.pdf"
    path.write_bytes(_pdf(pages))
    converter = PdfConverter()
    converter.extract_tables = extract_tables
    return MarkItDown(converters=[converter]).convert(path).text_content


def test_pdf_table_preserves_cells_and_surrounding_prose(tmp_path):
    page = (
        _text(72, 720, "Before the table.")
        + _table(72, 650, [["Node|Name", "Weight"], ["Graph\nitem", "3"], ["Empty", ""]])
        + _text(400, 630, "Beside the table.")
        + _text(72, 480, "After the table.")
    )
    output = _convert(tmp_path, [page])
    assert "| Node\\|Name | Weight |" in output
    assert "| --- | --- |" in output
    assert "| Graph<br>item | 3 |" in output
    assert "| Empty |  |" in output
    assert output.index("Before") < output.index("| Node") < output.index("After")
    assert "Beside the table." in output
    assert output.count("Graph") == output.count("Node") == 1
    assert "None" not in output


def test_pdf_multiple_tables_are_ordered_and_tail_survives(tmp_path):
    page = (
        _text(72, 720, "Introduction.")
        + _table(72, 650, [["Left node", "Value"], ["alpha", "1"]], width=210)
        + _table(330, 650, [["Right node", "Value"], ["beta", "2"]], width=210)
        + _text(72, 530, "Between table groups.")
        + _table(72, 470, [["Next node", "Value"], ["gamma", "3"]])
        + _text(72, 350, "Final tail.")
    )
    output = _convert(tmp_path, [page])
    markers = ["Introduction", "| Left node", "| Right node", "Between", "| Next node", "Final tail"]
    positions = [output.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert output.count("| --- | --- |") == 3
    assert all(output.count(term) == 1 for term in ("alpha", "beta", "gamma"))


@pytest.mark.parametrize(
    "rows",
    [
        [["One row", "Not a table"]],
        [["One column"], ["Still prose"]],
        [["A", "B"], ["1", "2"]],
    ],
)
def test_pdf_fake_table_keeps_original_text_path(tmp_path, rows):
    page = _text(72, 720, "Before.") + _table(72, 650, rows) + _text(72, 480, "After.")
    output = _convert(tmp_path, [page])
    assert output == _convert(tmp_path, [page], extract_tables=False)
    assert "| ---" not in output
    assert "Before." in output and "After." in output


def test_pdf_table_switch_keeps_plain_text_extraction(tmp_path):
    page = _table(72, 650, [["Node", "Weight"], ["Graph", "3"]])
    output = _convert(tmp_path, [page], extract_tables=False)
    assert "Node Weight" in output and "Graph 3" in output
    assert "| ---" not in output


def test_pdf_without_tables_matches_recorded_pre_change_bytes(tmp_path):
    pages = [
        _text(72, 760, "Repeated report header")
        + _text(72, 720, f"Chapter {index}")
        + _text(72, 680, f"Part {index}: hy-")
        + _text(72, 666, f"phenated text {index}.")
        + _text(72, 30, "Repeated report footer")
        for index in range(1, 4)
    ]
    expected = (
        "## 第 1 页\n\nChapter 1\nPart 1: hyphenated text 1.\n\n"
        "## 第 2 页\n\nChapter 2\nPart 2: hyphenated text 2.\n\n"
        "## 第 3 页\n\nChapter 3\nPart 3: hyphenated text 3.\n"
    ).encode("utf-8")
    assert _convert(tmp_path, pages).encode("utf-8") == expected


def test_pdf_table_pages_still_remove_repeated_headers_and_footers(tmp_path):
    pages = [
        _text(72, 760, "Repeated report header")
        + _text(72, 720, f"Chapter {index}")
        + _table(72, 650, [[f"Node {index}", "Weight"], [f"Graph {index}", "3"]])
        + _text(72, 30, "Repeated report footer")
        for index in range(1, 4)
    ]
    output = _convert(tmp_path, pages)
    assert "Repeated report" not in output
    assert output.count("| --- | --- |") == 3
    assert all(f"| Graph {index} | 3 |" in output for index in range(1, 4))
