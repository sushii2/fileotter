"""Image converters via Pillow: png, jpg, webp, gif, bmp, tiff, and image -> pdf."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .registry import converter

RASTER_FORMATS = ["png", "jpg", "webp", "gif", "bmp", "tiff"]
_PIL_NAME = {"jpg": "JPEG", "tiff": "TIFF"}
# Formats that cannot store an alpha channel.
_NO_ALPHA = {"jpg", "bmp", "pdf"}


def _make(src_fmt: str, dst_fmt: str) -> None:
    @converter((src_fmt, dst_fmt))
    def _convert(src: Path, dst: Path) -> None:
        with Image.open(src) as img:
            if dst_fmt in _NO_ALPHA and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(dst, format=_PIL_NAME.get(dst_fmt, dst_fmt.upper()))


for _s in RASTER_FORMATS:
    for _d in [*RASTER_FORMATS, "pdf"]:
        if _s != _d:
            _make(_s, _d)
