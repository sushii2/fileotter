"""Fileotter's in-house conversion engine.

Importing this package registers every converter module. To add a format, write a
module with ``@converter(("src", "dst"))`` functions and import it below.
"""

from . import documents, images, tabular  # noqa: F401  (imported for registration side effects)
from .registry import (
    ConversionError,
    convert,
    format_of,
    normalize_format,
    supported_conversions,
    supported_targets,
)

__all__ = [
    "ConversionError",
    "convert",
    "format_of",
    "normalize_format",
    "supported_conversions",
    "supported_targets",
]
