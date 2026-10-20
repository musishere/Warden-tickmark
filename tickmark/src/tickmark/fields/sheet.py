"""Read a worksheet without any schema: the header row and every row's cells, as received."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from tickmark.fields.types import clean_text
from tickmark.models import Cell


class SampleFormatError(Exception):
    pass


@dataclass
class SheetRow:
    excel_row: int
    values: list  # Python values as openpyxl read them (datetime, float, str, None)


@dataclass
class Sheet:
    title: str
    header_row: int
    headers: list[Cell]  # one per column: letter + header text as written
    rows: list[SheetRow]  # every row below the header, blank ones included

    def cells(self, row: SheetRow) -> list[Cell]:
        """The row as JSON-safe cells, each with its column letter and header."""
        return [Cell(col=h.col, header=h.header, value=json_value(v), kind=kind(v))
                for h, v in zip(self.headers, padded(row.values, len(self.headers)), strict=True)]


def padded(values: list, width: int) -> list:
    return list(values) + [None] * (width - len(values))


def kind(v) -> str:
    if v is None or v == "":
        return "empty"
    return type(v).__name__


def json_value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def is_blank(values: list) -> bool:
    return all(v in (None, "") or (isinstance(v, str) and not v.strip()) for v in values)


def read_sheet(path: str | Path, sheet: str | None = None) -> Sheet:
    path = Path(path)
    if not path.is_file():
        raise SampleFormatError(f"file not found: {path}")
    try:
        wb = load_workbook(path, data_only=True)
    except (InvalidFileException, OSError, KeyError) as e:
        raise SampleFormatError(f"not a readable .xlsx file: {path}") from e
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
    except KeyError as e:
        raise SampleFormatError(f"sheet not found: {sheet}") from e

    all_rows = [(r[0].row, [c.value for c in r]) for r in ws.iter_rows()]
    first = next((i for i, (_, values) in enumerate(all_rows) if any(v not in (None, "") for v in values)), None)
    if first is None:
        raise SampleFormatError("sheet is empty")
    header_row, header_values = all_rows[first]
    width = max(len(values) for _, values in all_rows)
    headers = [Cell(col=get_column_letter(i + 1), header=clean_text(v) or None,
                    value=json_value(v), kind=kind(v))
               for i, v in enumerate(padded(header_values, width))]
    return Sheet(ws.title, header_row, headers, [SheetRow(n, values) for n, values in all_rows[first + 1:]])
