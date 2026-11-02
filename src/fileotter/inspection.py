"""Fileotter's file inspector: detect a file's real type from its bytes and summarize its contents.

Used by the agent before converting or compressing, so it can pick the right tool, warn about
mismatched extensions, and set expectations (e.g. a scanned PDF has no text to put into Word).
"""

from __future__ import annotations

import csv
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from .converters import normalize_format, supported_targets

_SNIFF_BYTES = 8192


class InspectionError(Exception):
    """Raised when a file cannot be inspected."""


def detect_format(path: Path) -> str:
    """Return the canonical format id from the file's magic bytes, falling back to its extension."""
    with path.open("rb") as f:
        head = f.read(_SNIFF_BYTES)

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _detect_zip_flavor(path)
    if head.startswith(b"\x1f\x8b"):
        return "tar.gz" if _is_tar(path) else "gz"
    if head.startswith(b"BZh"):
        return "tar.bz2" if _is_tar(path) else "bz2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "tar.xz" if _is_tar(path) else "xz"
    if head[257:262] == b"ustar":
        return "tar"

    ext = normalize_format(path.suffix) if path.suffix else ""
    if _looks_like_text(head):
        return ext or "txt"  # text formats (csv, json, md, source code, ...) are identified by extension
    return ext or "unknown"


def _detect_zip_flavor(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return "zip"
    if "xl/workbook.xml" in names:
        return "xlsm" if "xl/vbaProject.bin" in names else "xlsx"
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return "zip"


def _is_tar(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except (OSError, tarfile.TarError):
        return False


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut off at the sniff boundary is still text.
        return exc.start >= len(head) - 3
    return True


def inspect_file(path: Path) -> dict[str, Any]:
    """Return a JSON-serializable summary of a file: real type, size, contents, and options."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise InspectionError(f"File not found: {path}")

    detected = detect_format(path)
    ext = normalize_format(path.suffix) if path.suffix else None
    info: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "extension": ext,
        "detected_format": detected,
    }
    if ext and ext != detected and not detected.endswith(f".{ext}"):  # "tar.gz" matches a .gz suffix
        info["warning"] = (
            f"Extension says .{ext} but the content is {detected}. Rename it to .{detected} before converting."
        )

    describe = _DESCRIBERS.get(detected)
    if describe:
        try:
            info["details"] = describe(path)
        except Exception as exc:  # corrupt or unusual file: still return what we know
            info["details_error"] = f"Could not read contents: {exc}"

    info["convertible_to"] = supported_targets(detected)
    info["compression_tool"] = _COMPRESSION_TOOL.get(detected, "compress_files")
    return info


def _describe_pdf(path: Path) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        return {"encrypted": True}
    pages = reader.pages
    sample = pages[: min(3, len(pages))]
    has_text = any((p.extract_text() or "").strip() for p in sample)
    image_count = sum(len(p.images) for p in sample)
    meta = reader.metadata or {}
    details: dict[str, Any] = {
        "encrypted": False,
        "pages": len(pages),
        "has_text_layer": has_text,
        "images_in_first_pages": image_count,
        "title": meta.get("/Title"),
        "author": meta.get("/Author"),
    }
    if not has_text:
        details["note"] = "No extractable text in the first pages: likely scanned. Text conversion will be empty."
    return details


def _describe_docx(path: Path) -> dict[str, Any]:
    from docx import Document

    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs]
    return {
        "paragraphs": len(paragraphs),
        "words": sum(len(t.split()) for t in paragraphs),
        "tables": len(doc.tables),
        "images": len(doc.inline_shapes),
    }


def _describe_xlsx(path: Path) -> dict[str, Any]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    try:
        sheets = [{"name": ws.title, "rows": ws.max_row, "columns": ws.max_column} for ws in wb.worksheets]
    finally:
        wb.close()
    with zipfile.ZipFile(path) as zf:
        media = [i for i in zf.infolist() if i.filename.startswith("xl/media/")]
    return {
        "sheets": sheets,
        "embedded_images": len(media),
        "embedded_images_bytes": sum(i.file_size for i in media),
        "has_macros": path.suffix.lower() == ".xlsm",
    }


def _describe_image(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as img:
        return {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "has_transparency": img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info,
            "frames": getattr(img, "n_frames", 1),
        }


def _describe_delimited(delimiter: str):
    def describe(path: Path) -> dict[str, Any]:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, [])
            rows = sum(1 for _ in reader)
        return {"columns": len(header), "header": header[:20], "data_rows": rows}

    return describe


def _describe_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    details: dict[str, Any] = {"top_level": type(data).__name__}
    if isinstance(data, list):
        details["items"] = len(data)
        details["tabular"] = all(isinstance(r, dict) for r in data)
    elif isinstance(data, dict):
        details["keys"] = list(data)[:20]
    return details


def _describe_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0), "words": len(text.split())}


def _describe_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
    return {"files": len(infos), "uncompressed_bytes": sum(i.file_size for i in infos)}


def _describe_tar(path: Path) -> dict[str, Any]:
    with tarfile.open(path) as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
    return {"files": len(members), "uncompressed_bytes": sum(m.size for m in members)}


_DESCRIBERS = {
    "pdf": _describe_pdf,
    "docx": _describe_docx,
    "xlsx": _describe_xlsx,
    "xlsm": _describe_xlsx,
    **{fmt: _describe_image for fmt in ("png", "jpg", "gif", "webp", "bmp", "tiff")},
    "csv": _describe_delimited(","),
    "tsv": _describe_delimited("\t"),
    "json": _describe_json,
    "txt": _describe_text,
    "md": _describe_text,
    "zip": _describe_zip,
    **{fmt: _describe_tar for fmt in ("tar", "tar.gz", "tar.bz2", "tar.xz")},
}

_COMPRESSION_TOOL = {
    "pdf": "compress_pdf",
    "xlsx": "compress_xlsx",
    "xlsm": "compress_xlsx",
    "jpg": "compress_image",
    "png": "compress_image",
    "webp": "compress_image",
}
