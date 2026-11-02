"""Document converters: PDF, Word (docx), plain text, markdown."""

from __future__ import annotations

import csv
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from pypdf import PdfReader

from .registry import ConversionError, converter


def _pdf_pages(src: Path) -> list[str]:
    reader = PdfReader(src)
    if reader.is_encrypted:
        raise ConversionError(f"{src.name} is password-protected")
    return [page.extract_text() or "" for page in reader.pages]


def _docx_paragraphs(src: Path) -> list[str]:
    return [p.text for p in Document(src).paragraphs]


def _docx_tables(src: Path) -> list[list[list[str]]]:
    return [[[cell.text for cell in row.cells] for row in table.rows] for table in Document(src).tables]


@converter(("pdf", "docx"))
def pdf_to_docx(src: Path, dst: Path) -> None:
    """Text-layer extraction: one Word section per PDF page. Layout and images are not kept."""
    doc = Document()
    pages = _pdf_pages(src)
    for i, text in enumerate(pages):
        for line in text.splitlines():
            doc.add_paragraph(line)
        if i < len(pages) - 1:
            doc.add_page_break()
    doc.save(dst)


@converter(("pdf", "txt"), ("pdf", "md"))
def pdf_to_text(src: Path, dst: Path) -> None:
    dst.write_text("\n\n".join(_pdf_pages(src)), encoding="utf-8")


@converter(("docx", "txt"), ("docx", "md"))
def docx_to_text(src: Path, dst: Path) -> None:
    dst.write_text("\n".join(_docx_paragraphs(src)), encoding="utf-8")


@converter(("txt", "docx"), ("md", "docx"))
def text_to_docx(src: Path, dst: Path) -> None:
    doc = Document()
    for line in src.read_text(encoding="utf-8").splitlines():
        doc.add_paragraph(line)
    doc.save(dst)


@converter(("docx", "xlsx"))
def docx_to_xlsx(src: Path, dst: Path) -> None:
    """Each table in the Word document becomes its own worksheet."""
    tables = _docx_tables(src)
    if not tables:
        raise ConversionError(f"{src.name} contains no tables to export to Excel")
    wb = Workbook()
    wb.remove(wb.active)
    for i, rows in enumerate(tables, start=1):
        ws = wb.create_sheet(f"Table {i}")
        for row in rows:
            ws.append(row)
    wb.save(dst)


@converter(("docx", "csv"))
def docx_to_csv(src: Path, dst: Path) -> None:
    """Exports the first table in the Word document."""
    tables = _docx_tables(src)
    if not tables:
        raise ConversionError(f"{src.name} contains no tables to export to CSV")
    with dst.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(tables[0])
