import os

import pytest
from PIL import Image

from fileotter.tools import _path, compress_image, inspect_file, list_files


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "Quarterly Report.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / ".hidden").write_text("secret")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.pdf").write_bytes(b"%PDF-1.4\n")
    os.utime(tmp_path / "notes.txt", (1, 1))  # oldest
    return tmp_path


@pytest.mark.parametrize(
    "typed",
    [
        "{d}/Quarterly Report.pdf",
        "{d}/Quarterly\\ Report.pdf",  # macOS drag-and-drop escaping
        "'{d}/Quarterly Report.pdf'",
        ' "{d}/Quarterly Report.pdf" ',
        "file://{d}/Quarterly%20Report.pdf",
    ],
)
def test_paths_as_people_paste_them(folder, typed):
    assert _path(typed.format(d=folder)) == folder / "Quarterly Report.pdf"


def test_tilde_expands(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _path("~/x.pdf") == tmp_path / "x.pdf"


def test_list_files_newest_first_skips_hidden(folder, monkeypatch):
    monkeypatch.chdir(folder)
    out = list_files()
    assert "Quarterly Report.pdf" in out and "sub/" in out
    assert ".hidden" not in out
    assert out.index("Quarterly Report.pdf") < out.index("notes.txt")


def test_list_files_pattern_and_recursive(folder):
    flat = list_files(str(folder), "*.pdf")
    assert "Quarterly Report.pdf" in flat and "deep.pdf" not in flat and "notes.txt" not in flat
    assert "sub/deep.pdf" in list_files(str(folder), "*.pdf", recursive=True)
    assert "No matches" in list_files(str(folder), "*.docx")
    assert list_files(str(folder / "nope")).startswith("Error:")


def test_tools_accept_escaped_paths(tmp_path):
    src = tmp_path / "My Photo.png"
    Image.new("RGB", (40, 30), "red").save(src)
    escaped = str(src).replace(" ", "\\ ")
    assert '"detected_format": "png"' in inspect_file(escaped)
    assert compress_image(escaped).startswith("Compressed")
    assert (tmp_path / "My Photo.compressed.png").exists()
