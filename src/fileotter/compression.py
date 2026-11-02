"""Fileotter's in-house compression engine: archives, images, PDFs, and Excel workbooks."""

from __future__ import annotations

import gzip
import io
import shutil
import tarfile
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter

ARCHIVE_FORMATS = {
    "zip": ".zip",
    "tar.gz": ".tar.gz",
    "tar.bz2": ".tar.bz2",
    "tar.xz": ".tar.xz",
    "gz": ".gz",
}
_TAR_MODES = {"tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz"}


class CompressionError(Exception):
    """Raised when compression or extraction fails or is unsafe."""


def _resolve_inputs(paths: list[Path]) -> list[Path]:
    resolved = [p.expanduser().resolve() for p in paths]
    missing = [str(p) for p in resolved if not p.exists()]
    if missing:
        raise CompressionError(f"Not found: {', '.join(missing)}")
    if not resolved:
        raise CompressionError("No input paths given")
    return resolved


def _check_output(dst: Path, overwrite: bool) -> Path:
    dst = dst.expanduser().resolve()
    if dst.exists() and not overwrite:
        raise CompressionError(f"Output already exists: {dst} (pass overwrite=True to replace it)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def compress(paths: list[Path], fmt: str = "zip", output: Path | None = None, overwrite: bool = False) -> Path:
    """Compress files and/or directories into an archive. ``gz`` accepts exactly one file."""
    fmt = fmt.lower().lstrip(".")
    if fmt not in ARCHIVE_FORMATS:
        raise CompressionError(f"Unsupported archive format {fmt!r}. Use one of: {', '.join(ARCHIVE_FORMATS)}")
    inputs = _resolve_inputs(paths)
    default_name = inputs[0].name if len(inputs) == 1 else "archive"
    dst = _check_output(output or inputs[0].parent / f"{default_name}{ARCHIVE_FORMATS[fmt]}", overwrite)

    if fmt == "gz":
        if len(inputs) != 1 or not inputs[0].is_file():
            raise CompressionError("gz compresses a single file; use zip or tar.gz for folders or multiple files")
        with inputs[0].open("rb") as f_in, gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    elif fmt == "zip":
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for p in inputs:
                if p.is_dir():
                    for child in sorted(p.rglob("*")):
                        if child.resolve() != dst:
                            zf.write(child, child.relative_to(p.parent))
                else:
                    zf.write(p, p.name)
    else:
        with tarfile.open(dst, _TAR_MODES[fmt]) as tf:
            for p in inputs:
                tf.add(p, arcname=p.name, filter=lambda ti: None if Path(ti.name).name == dst.name else ti)
    return dst


def extract(archive: Path, dest: Path | None = None) -> Path:
    """Extract an archive, rejecting members that would escape ``dest``."""
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise CompressionError(f"Archive not found: {archive}")
    name = archive.name
    stem = next(
        (name[: -len(ext)] for ext in sorted(ARCHIVE_FORMATS.values(), key=len, reverse=True) if name.endswith(ext)),
        archive.stem,
    )
    dest = (dest or archive.parent / stem).expanduser().resolve()

    if zipfile.is_zipfile(archive):
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not (dest / member).resolve().is_relative_to(dest):
                    raise CompressionError(f"Unsafe path in archive: {member}")
            zf.extractall(dest)
    elif tarfile.is_tarfile(archive):
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(dest, filter="data")  # blocks absolute paths, '..', and unsafe links
            except tarfile.FilterError as exc:
                raise CompressionError(f"Unsafe path in archive: {exc}") from exc
    elif name.endswith(".gz"):
        dest = _check_output(dest, overwrite=False)
        with gzip.open(archive, "rb") as f_in, dest.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        raise CompressionError(f"Unrecognized archive format: {name}")
    return dest


def compress_image(
    src: Path, quality: int = 75, max_dimension: int | None = None, output: Path | None = None, overwrite: bool = False
) -> Path:
    """Re-encode an image smaller: lossy quality for jpg/webp, optimized encoding for png."""
    if not 1 <= quality <= 95:
        raise CompressionError("quality must be between 1 and 95")
    src = src.expanduser().resolve()
    if not src.is_file():
        raise CompressionError(f"Image not found: {src}")
    dst = _check_output(output or src.with_name(f"{src.stem}.compressed{src.suffix}"), overwrite)
    if dst == src:
        raise CompressionError("Output path is the same as the source image")

    with Image.open(src) as img:
        fmt = img.format
        if max_dimension:
            img.thumbnail((max_dimension, max_dimension))
        if fmt == "JPEG":
            img.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
        elif fmt == "WEBP":
            img.save(dst, "WEBP", quality=quality, method=6)
        elif fmt == "PNG":
            img.save(dst, "PNG", optimize=True)
        else:
            img.save(dst, fmt)
    return dst


def _source_and_output(src: Path, suffixes: set[str], output: Path | None, overwrite: bool) -> tuple[Path, Path]:
    src = src.expanduser().resolve()
    if not src.is_file():
        raise CompressionError(f"File not found: {src}")
    if src.suffix.lower() not in suffixes:
        raise CompressionError(f"Expected a {'/'.join(sorted(suffixes))} file, got {src.name}")
    dst = _check_output(output or src.with_name(f"{src.stem}.compressed{src.suffix}"), overwrite)
    if dst == src:
        raise CompressionError("Output path is the same as the source file")
    return src, dst


def _shrink_raster(img: Image.Image, max_dimension: int | None) -> Image.Image:
    if max_dimension and max(img.size) > max_dimension:
        img = img.copy()
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return img


def compress_pdf(
    src: Path,
    quality: int = 70,
    max_dimension: int | None = 2000,
    output: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Shrink a PDF: downsample/re-encode embedded images, dedupe objects, deflate page streams.

    Lossy for images only; text, vectors, and fonts are untouched. Images with transparency are
    left as-is (re-encoding them as JPEG would drop the alpha channel). An image is only replaced
    when the re-encoded version is smaller.
    """
    if not 1 <= quality <= 95:
        raise CompressionError("quality must be between 1 and 95")
    src, dst = _source_and_output(src, {".pdf"}, output, overwrite)

    reader = PdfReader(src)
    if reader.is_encrypted:
        raise CompressionError(f"{src.name} is password-protected")
    writer = PdfWriter(clone_from=reader)

    for page in writer.pages:
        for image_file in page.images:
            try:
                img = image_file.image
                if img is None or img.mode not in ("RGB", "L", "CMYK"):
                    continue  # skip alpha / palette / bilevel images
                candidate = _shrink_raster(img, max_dimension)
                probe = io.BytesIO()
                candidate.save(probe, "JPEG", quality=quality, optimize=True)
                if probe.tell() < len(image_file.data):
                    image_file.replace(candidate, quality=quality)
            except Exception:  # a single odd image (inline, exotic filter) must not fail the whole file
                continue
        page.compress_content_streams()

    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    with dst.open("wb") as f:
        writer.write(f)
    return dst


def compress_xlsx(
    src: Path,
    quality: int = 75,
    max_dimension: int | None = 2000,
    output: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Shrink an Excel workbook (.xlsx/.xlsm) without touching its data.

    An xlsx is a zip of XML parts. This rewrites every part at maximum deflate and re-encodes
    embedded images in ``xl/media/`` (JPEG at ``quality``, PNG losslessly optimized, both optionally
    downscaled; on-sheet display size is unchanged). Cells, formulas, charts, styles, and macros
    are copied byte-for-byte, so nothing is lost except image detail.
    """
    if not 1 <= quality <= 95:
        raise CompressionError("quality must be between 1 and 95")
    src, dst = _source_and_output(src, {".xlsx", ".xlsm"}, output, overwrite)
    if not zipfile.is_zipfile(src):
        raise CompressionError(f"{src.name} is not a valid Excel workbook (not a zip container)")

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.startswith("xl/media/"):
                data = _recompress_media(data, quality, max_dimension)
            zout.writestr(info.filename, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return dst


def _recompress_media(data: bytes, quality: int, max_dimension: int | None) -> bytes:
    """Re-encode an embedded image in its own format; return the original bytes if not smaller."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            if fmt not in ("JPEG", "PNG"):
                return data
            img = _shrink_raster(img, max_dimension)
            out = io.BytesIO()
            if fmt == "JPEG":
                img.convert("RGB" if img.mode not in ("RGB", "L") else img.mode).save(
                    out, "JPEG", quality=quality, optimize=True, progressive=True
                )
            else:
                img.save(out, "PNG", optimize=True)
    except Exception:
        return data
    return out.getvalue() if out.tell() < len(data) else data
