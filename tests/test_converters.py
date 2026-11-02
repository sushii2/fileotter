import csv
import json

import pytest
from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

from fileotter.converters import ConversionError, convert, normalize_format, supported_conversions


@pytest.fixture
def sample_csv(tmp_path):
    path = tmp_path / "people.csv"
    path.write_text("name,age\nAda,36\nLinus,54\n", encoding="utf-8")
    return path


def test_normalize_format_aliases():
    assert normalize_format(".JPEG") == "jpg"
    assert normalize_format("Word") == "docx"
    assert normalize_format("excel") == "xlsx"


def test_csv_xlsx_roundtrip(sample_csv, tmp_path):
    xlsx = convert(sample_csv, "xlsx")
    ws = load_workbook(xlsx).active
    assert [c.value for c in ws[1]] == ["name", "age"]

    back = convert(xlsx, "csv", tmp_path / "back.csv")
    with back.open(newline="") as f:
        assert list(csv.reader(f)) == [["name", "age"], ["Ada", "36"], ["Linus", "54"]]


def test_csv_to_json(sample_csv):
    data = json.loads(convert(sample_csv, "json").read_text())
    assert data == [{"name": "Ada", "age": "36"}, {"name": "Linus", "age": "54"}]


def test_docx_tables_to_xlsx(tmp_path):
    src = tmp_path / "report.docx"
    doc = Document()
    doc.add_paragraph("Quarterly numbers")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Q", "Revenue"
    table.cell(1, 0).text, table.cell(1, 1).text = "Q1", "100"
    doc.save(src)

    ws = load_workbook(convert(src, "excel")).worksheets[0]
    assert [[c.value for c in row] for row in ws.iter_rows()] == [["Q", "Revenue"], ["Q1", "100"]]


def test_image_to_pdf_and_jpg(tmp_path):
    src = tmp_path / "pic.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(src)
    assert len(PdfReader(convert(src, "pdf")).pages) == 1
    with Image.open(convert(src, "jpeg")) as img:
        assert img.format == "JPEG" and img.size == (20, 10)


def test_refuses_overwrite_and_unknown_pairs(sample_csv):
    convert(sample_csv, "json")
    with pytest.raises(ConversionError, match="already exists"):
        convert(sample_csv, "json")
    convert(sample_csv, "json", overwrite=True)
    with pytest.raises(ConversionError, match="No converter"):
        convert(sample_csv, "mp3")


def test_requested_headline_conversions_registered():
    table = supported_conversions()
    assert "docx" in table["pdf"]
    assert {"xlsx", "csv"} <= set(table["docx"])
