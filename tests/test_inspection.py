import json
import tarfile

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image

from fileotter.inspection import InspectionError, detect_format, inspect_file


def test_detects_real_type_and_warns_on_wrong_extension(tmp_path):
    fake = tmp_path / "photo.pdf"
    Image.new("RGB", (30, 20)).save(fake, format="PNG")
    info = inspect_file(fake)
    assert info["detected_format"] == "png"
    assert "Rename it to .png" in info["warning"]
    assert info["details"]["width"] == 30
    assert "jpg" in info["convertible_to"]


def test_pdf_details_flag_scanned_pdf(tmp_path):
    scanned = tmp_path / "scan.pdf"
    Image.new("RGB", (200, 200), "white").save(scanned)  # image-only PDF: no text layer
    info = inspect_file(scanned)
    assert info["details"]["pages"] == 1
    assert info["details"]["has_text_layer"] is False
    assert "scanned" in info["details"]["note"]
    assert info["compression_tool"] == "compress_pdf"


def test_office_formats_detected_from_zip_contents(tmp_path):
    xlsx = tmp_path / "book.xlsx"
    wb = Workbook()
    wb.active.append(["a", "b"])
    wb.create_sheet("Second")
    wb.save(xlsx)
    info = inspect_file(xlsx)
    assert info["detected_format"] == "xlsx"
    assert [s["name"] for s in info["details"]["sheets"]] == ["Sheet", "Second"]
    assert info["compression_tool"] == "compress_xlsx"

    docx = tmp_path / "doc.docx"
    doc = Document()
    doc.add_paragraph("three little words")
    doc.add_table(rows=1, cols=1)
    doc.save(docx)
    details = inspect_file(docx)["details"]
    assert details["words"] == 3 and details["tables"] == 1


def test_text_formats(tmp_path):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("id,name\n1,a\n2,b\n")
    details = inspect_file(csv_path)["details"]
    assert details == {"columns": 2, "header": ["id", "name"], "data_rows": 2}

    json_path = tmp_path / "rows.json"
    json_path.write_text(json.dumps([{"a": 1}, {"a": 2}]))
    assert inspect_file(json_path)["details"] == {"top_level": "list", "items": 2, "tabular": True}

    script = tmp_path / "tool.py"
    script.write_text("print('hi')\n")
    info = inspect_file(script)
    assert info["detected_format"] == "py" and "warning" not in info


def test_tar_gz_not_flagged_as_mismatch(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(tmp_path / "f.txt", arcname="f.txt")
    info = inspect_file(archive)
    assert detect_format(archive) == "tar.gz"
    assert "warning" not in info
    assert info["details"]["files"] == 1


def test_corrupt_file_reports_details_error(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not really a pdf")
    info = inspect_file(broken)
    assert info["detected_format"] == "pdf"
    assert "details_error" in info


def test_missing_file(tmp_path):
    with pytest.raises(InspectionError, match="not found"):
        inspect_file(tmp_path / "nope.txt")
