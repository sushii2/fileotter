"""Converter registry: maps (source_format, target_format) pairs to converter functions.

Every converter has the signature ``(src: Path, dst: Path) -> None`` and must write
exactly one file at ``dst``. Register new converters with the ``@converter`` decorator.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

Converter = Callable[[Path, Path], None]

_REGISTRY: dict[tuple[str, str], Converter] = {}

# User-facing names and alternate extensions normalized to one canonical format id.
ALIASES: dict[str, str] = {
    "jpeg": "jpg",
    "tif": "tiff",
    "word": "docx",
    "excel": "xlsx",
    "text": "txt",
    "markdown": "md",
}


class ConversionError(Exception):
    """Raised when a conversion is unsupported or fails."""


def normalize_format(fmt: str) -> str:
    """Normalize ``".JPEG"``, ``"jpeg"``, ``"Word"`` etc. to a canonical format id."""
    key = fmt.strip().lower().lstrip(".")
    return ALIASES.get(key, key)


def format_of(path: Path) -> str:
    """Return the canonical format id for a file based on its extension."""
    if not path.suffix:
        raise ConversionError(f"Cannot detect format of {path}: file has no extension")
    return normalize_format(path.suffix)


def converter(*pairs: tuple[str, str]) -> Callable[[Converter], Converter]:
    """Register a function as the converter for one or more (source, target) pairs."""

    def decorator(fn: Converter) -> Converter:
        for src, dst in pairs:
            key = (normalize_format(src), normalize_format(dst))
            if key in _REGISTRY:
                raise ValueError(f"Duplicate converter for {key[0]} -> {key[1]}")
            _REGISTRY[key] = fn
        return fn

    return decorator


def get_converter(src_fmt: str, dst_fmt: str) -> Converter:
    key = (normalize_format(src_fmt), normalize_format(dst_fmt))
    try:
        return _REGISTRY[key]
    except KeyError:
        targets = supported_targets(key[0])
        hint = f" Supported targets for {key[0]}: {', '.join(targets)}." if targets else ""
        raise ConversionError(f"No converter for {key[0]} -> {key[1]}.{hint}") from None


def supported_targets(src_fmt: str) -> list[str]:
    src = normalize_format(src_fmt)
    return sorted(dst for (s, dst) in _REGISTRY if s == src)


def supported_conversions() -> dict[str, list[str]]:
    """Return ``{source_format: [target_formats...]}`` for every registered converter."""
    table: dict[str, list[str]] = {}
    for src, dst in _REGISTRY:
        table.setdefault(src, []).append(dst)
    return {src: sorted(dsts) for src, dsts in sorted(table.items())}


def convert(src: Path, target_format: str, output: Path | None = None, overwrite: bool = False) -> Path:
    """Convert ``src`` to ``target_format``.

    The output defaults to ``src`` with its extension swapped. Refuses to overwrite an
    existing file unless ``overwrite`` is True, and never writes over the source file.
    """
    src = src.expanduser().resolve()
    if not src.is_file():
        raise ConversionError(f"Source file not found: {src}")

    dst_fmt = normalize_format(target_format)
    fn = get_converter(format_of(src), dst_fmt)

    dst = output.expanduser().resolve() if output else src.with_suffix(f".{dst_fmt}")
    if dst == src:
        raise ConversionError("Output path is the same as the source file")
    if dst.exists() and not overwrite:
        raise ConversionError(f"Output already exists: {dst} (pass overwrite=True to replace it)")
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        fn(src, dst)
    except ConversionError:
        raise
    except Exception as exc:  # surface library errors as a single, readable error type
        raise ConversionError(f"Failed to convert {src.name} to {dst_fmt}: {exc}") from exc
    return dst
