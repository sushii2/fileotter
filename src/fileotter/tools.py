"""Strands tools that expose Fileotter's conversion and compression engines to the agent.

Tools are thin adapters: validate/translate arguments, call the engine, and return a short
human-readable result. Engine errors are returned as text so the model can recover.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from strands import tool

from . import compression
from .converters import ConversionError, convert, supported_conversions
from .inspection import InspectionError
from .inspection import inspect_file as _inspect


def _path(raw: str) -> Path:
    """Turn a path as a person types or pastes it into a ``Path``.

    Handles ``~``, surrounding quotes, ``file://`` URLs, and the backslash-escaped spaces that
    macOS terminals insert when a file is dragged in (``Quarterly\\ Report.pdf``).
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if text.startswith("file://"):
        text = unquote(urlparse(text).path)
    path = Path(text).expanduser()
    if "\\" in text and not path.exists():
        unescaped = Path(re.sub(r"\\(.)", r"\1", text)).expanduser()
        if unescaped.exists():
            return unescaped
    return path


def _out(raw: str | None) -> Path | None:
    return _path(raw) if raw else None


def _bytes(path: Path) -> int:
    path = path.expanduser()
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _size(path: Path) -> str:
    n = float(_bytes(path))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


LIST_LIMIT = 100


@tool
def list_files(directory: str = ".", pattern: str = "*", recursive: bool = False) -> str:
    """List files in a folder, newest first, with size and age. Read-only and never asks permission.

    Use it to work out which file the user means by "this file", "it", "the pdf", or "the photo I
    just downloaded", and to find files by name or type (e.g. pattern="*.pdf").

    Args:
        directory: Folder to list. Defaults to the current working directory.
        pattern: Glob pattern for names, e.g. "*.pdf" or "*report*" (no {a,b} braces; call once per type).
        recursive: Also search subfolders.
    """
    root = _path(directory)
    if not root.is_dir():
        return f"Error: not a folder: {root}"
    try:
        found = root.rglob(pattern) if recursive else root.glob(pattern)
        entries = [p for p in found if not any(part.startswith(".") for part in p.relative_to(root).parts)]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        return f"Error: could not list {root}: {exc}"
    if not entries:
        return f"No matches for {pattern!r} in {root}"
    now = time.time()
    lines = [f"{root}  ({len(entries)} match{'es' if len(entries) != 1 else ''}, newest first)"]
    for p in entries[:LIST_LIMIT]:
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            lines.append(f"  {rel}/")
        else:
            lines.append(f"  {rel}  {_size(p)}  {_age(now - p.stat().st_mtime)}")
    if len(entries) > LIST_LIMIT:
        lines.append(f"  … {len(entries) - LIST_LIMIT} more; narrow the pattern")
    return "\n".join(lines)


def _age(seconds: float) -> str:
    for unit, span in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= span:
            return f"{int(seconds // span)}{unit} ago"
    return "just now"


@tool
def list_supported_conversions() -> str:
    """List every file conversion Fileotter supports, as JSON mapping source format to target formats.

    Call this before converting when you are unsure whether a conversion path exists.
    """
    return json.dumps(supported_conversions(), indent=2)


@tool
def convert_file(source_path: str, target_format: str, output_path: str | None = None, overwrite: bool = False) -> str:
    """Convert a file to another format using Fileotter's built-in converters.

    Args:
        source_path: Path to the file to convert.
        target_format: Target format or extension, e.g. "docx", "xlsx", "csv", "pdf", "png".
        output_path: Optional output file path. Defaults to the source path with the new extension.
        overwrite: Replace the output file if it already exists. Only set this when the user agreed.
    """
    try:
        dst = convert(_path(source_path), target_format, _out(output_path), overwrite)
    except ConversionError as exc:
        return f"Error: {exc}"
    return f"Converted {source_path} -> {dst} ({_size(dst)})"


@tool
def compress_files(
    paths: list[str], archive_format: str = "zip", output_path: str | None = None, overwrite: bool = False
) -> str:
    """Compress one or more files or folders into an archive.

    Args:
        paths: Files and/or directories to include.
        archive_format: One of "zip", "tar.gz", "tar.bz2", "tar.xz", "gz" (gz: single file only).
        output_path: Optional archive path. Defaults to next to the first input.
        overwrite: Replace the archive if it already exists. Only set this when the user agreed.
    """
    try:
        inputs = [_path(p) for p in paths]
        dst = compression.compress(inputs, archive_format, _out(output_path), overwrite)
        before = sum(_bytes(p) for p in inputs)
    except compression.CompressionError as exc:
        return f"Error: {exc}"
    after = dst.stat().st_size
    ratio = f"{100 * (1 - after / before):.0f}% smaller" if before else "n/a"
    return f"Created {dst} ({_size(dst)}, {ratio})"


@tool
def extract_archive(archive_path: str, destination: str | None = None) -> str:
    """Extract a .zip, .tar.gz, .tar.bz2, .tar.xz, or .gz archive.

    Args:
        archive_path: Path to the archive.
        destination: Optional directory (or output file for .gz). Defaults to next to the archive.
    """
    try:
        dst = compression.extract(_path(archive_path), _out(destination))
    except compression.CompressionError as exc:
        return f"Error: {exc}"
    return f"Extracted {archive_path} -> {dst}"


@tool
def compress_image(
    image_path: str,
    quality: int = 75,
    max_dimension: int | None = None,
    output_path: str | None = None,
    overwrite: bool = False,
) -> str:
    """Shrink an image by re-encoding it, optionally downscaling it.

    Args:
        image_path: Path to a jpg, png, or webp image.
        quality: 1-95, lower is smaller (applies to jpg and webp).
        max_dimension: Optional longest-side pixel limit; aspect ratio is preserved.
        output_path: Optional output path. Defaults to "<name>.compressed.<ext>".
        overwrite: Replace the output if it already exists. Only set this when the user agreed.
    """
    try:
        src = _path(image_path)
        dst = compression.compress_image(src, quality, max_dimension, _out(output_path), overwrite)
    except (compression.CompressionError, OSError) as exc:
        return f"Error: {exc}"
    return f"Compressed {image_path} ({_size(src)}) -> {dst} ({_size(dst)})"


def _shrink_report(src: Path, dst: Path) -> str:
    before, after = _bytes(src), _bytes(dst)
    change = f"{100 * (1 - after / before):.0f}% smaller" if after < before else "no size reduction possible"
    return f"Compressed {src} ({_size(src)}) -> {dst} ({_size(dst)}, {change})"


@tool
def inspect_file(path: str) -> str:
    """Inspect a file: its real format (detected from content, not extension), size, contents summary
    (pages, sheets, rows, dimensions, text layer, ...), what it can be converted to, and which
    compression tool fits it. Call this first when a file's type or contents are unclear.

    Args:
        path: Path to the file.
    """
    try:
        return json.dumps(_inspect(_path(path)), indent=2, default=str)
    except InspectionError as exc:
        return f"Error: {exc}"


@tool
def compress_pdf(
    pdf_path: str,
    quality: int = 70,
    max_dimension: int | None = 2000,
    output_path: str | None = None,
    overwrite: bool = False,
) -> str:
    """Shrink a PDF by downsampling and re-encoding its embedded images and deduplicating objects.
    Text, fonts, and vector graphics are untouched; only image detail is reduced.

    Args:
        pdf_path: Path to the PDF.
        quality: JPEG quality for images, 1-95. ~70 is a good default; ~40 for email-sized output.
        max_dimension: Longest-side pixel limit for embedded images (2000 keeps print quality;
            1200 is fine for screen). None keeps original resolution.
        output_path: Optional output path. Defaults to "<name>.compressed.pdf".
        overwrite: Replace the output if it already exists. Only set this when the user agreed.
    """
    try:
        src = _path(pdf_path)
        dst = compression.compress_pdf(src, quality, max_dimension, _out(output_path), overwrite)
    except compression.CompressionError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # malformed PDFs surface as pypdf errors
        return f"Error: could not compress {pdf_path}: {exc}"
    return _shrink_report(src, dst)


@tool
def compress_xlsx(
    xlsx_path: str,
    quality: int = 75,
    max_dimension: int | None = 2000,
    output_path: str | None = None,
    overwrite: bool = False,
) -> str:
    """Shrink an Excel workbook (.xlsx/.xlsm) by recompressing its internal parts at maximum level
    and re-encoding embedded images. Cells, formulas, charts, styles, and macros are preserved exactly.

    Args:
        xlsx_path: Path to the workbook.
        quality: JPEG quality for embedded photos, 1-95 (PNGs are optimized losslessly).
        max_dimension: Longest-side pixel limit for embedded images. None keeps original resolution.
        output_path: Optional output path. Defaults to "<name>.compressed.xlsx".
        overwrite: Replace the output if it already exists. Only set this when the user agreed.
    """
    try:
        src = _path(xlsx_path)
        dst = compression.compress_xlsx(src, quality, max_dimension, _out(output_path), overwrite)
    except compression.CompressionError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # corrupt workbooks surface as zipfile errors
        return f"Error: could not compress {xlsx_path}: {exc}"
    return _shrink_report(src, dst)


ALL_TOOLS = [
    list_files,
    inspect_file,
    list_supported_conversions,
    convert_file,
    compress_files,
    extract_archive,
    compress_image,
    compress_pdf,
    compress_xlsx,
]
