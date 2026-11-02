"""Tabular converters: CSV, TSV, Excel (xlsx), JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook

from .registry import ConversionError, converter

Rows = list[list[str]]


def _read_delimited(src: Path, delimiter: str) -> Rows:
    with src.open(newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f, delimiter=delimiter)]


def _write_delimited(rows: Rows, dst: Path, delimiter: str) -> None:
    with dst.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=delimiter).writerows(rows)


def _read_xlsx(src: Path) -> Rows:
    """Reads the first worksheet (computed values, not formulas)."""
    wb = load_workbook(src, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _write_xlsx(rows: Rows, dst: Path) -> None:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(dst)


def _rows_to_records(rows: Rows) -> list[dict[str, str]]:
    if not rows:
        return []
    header, *body = rows
    return [dict(zip(header, row, strict=False)) for row in body]


def _records_to_rows(src: Path) -> Rows:
    data = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ConversionError("JSON must be an array of objects to convert to a table")
    header: list[str] = []
    for record in data:
        header.extend(k for k in record if k not in header)
    return [header] + [["" if r.get(k) is None else str(r.get(k)) for k in header] for r in data]


_READERS = {
    "csv": lambda p: _read_delimited(p, ","),
    "tsv": lambda p: _read_delimited(p, "\t"),
    "xlsx": _read_xlsx,
    "json": _records_to_rows,
}
_WRITERS = {
    "csv": lambda rows, p: _write_delimited(rows, p, ","),
    "tsv": lambda rows, p: _write_delimited(rows, p, "\t"),
    "xlsx": _write_xlsx,
    "json": lambda rows, p: p.write_text(json.dumps(_rows_to_records(rows), indent=2), encoding="utf-8"),
}


def _make(src_fmt: str, dst_fmt: str) -> None:
    @converter((src_fmt, dst_fmt))
    def _convert(src: Path, dst: Path) -> None:
        _WRITERS[dst_fmt](_READERS[src_fmt](src), dst)


for _s in _READERS:
    for _d in _WRITERS:
        if _s != _d:
            _make(_s, _d)
