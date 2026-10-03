"""
reconciliation/parsing.py — reading the export, and typing its cells.
=============================================================================
The Reconciliation Export is a workbook of text: every one of its 204 columns
arrives as a string, including the dates and the money. This module turns a
file into a list of ``{column: python value}`` dicts, and nothing else — no
database, no classification, no side effects — so the reader can be tested
against the two real exports in ``dummy_tempelate/reconciliation/`` on its own.

Two things about the source format are worth stating plainly, because both
have bitten this import before:

* **Dates are day-first.** ``05-06-2026`` is 5 June, not 6 May. Parsing it the
  American way does not fail, it silently produces a date a month out, which
  is the worst kind of wrong. Only day-first patterns are tried, then ISO,
  then an Excel serial number.

* **The heading row is not always the first row.** The export sometimes carries
  a title or a filter line above it, so the header is found by looking for the
  row that matches the most known column names within the first eight, rather
  than by assuming row 1.
"""
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from . import schema

# How far down to look for the heading row.
HEADER_SEARCH_DEPTH = 8

# The sheet to prefer when a workbook has several.
PREFERRED_SHEET_HINT = "reconcil"

# Excel's day zero. 1899-12-30 rather than 12-31 because of the deliberate
# 1900-leap-year bug Excel keeps for Lotus compatibility.
_EXCEL_EPOCH = dt.date(1899, 12, 30)
_MAX_EXCEL_SERIAL = 2958466  # 31-12-9999

_DAY_FIRST_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%d-%m-%y", "%d/%m/%y",
)
_ISO_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


class ReadError(Exception):
    """The file could not be read — shown to the user as-is, so say why."""


# ── cell parsers ─────────────────────────────────────────────────────────────

def parse_date(value):
    """A ``date``, or None. Day-first; see the module docstring."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_excel_serial(value)

    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None

    for fmt in _DAY_FIRST_FORMATS + _ISO_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # A date that arrived as a bare number in a text cell.
    try:
        serial = float(text)
    except ValueError:
        return None
    return _from_excel_serial(serial)


def _from_excel_serial(serial):
    if not (0 < serial < _MAX_EXCEL_SERIAL):
        return None
    try:
        return _EXCEL_EPOCH + dt.timedelta(days=float(serial))
    except (OverflowError, ValueError):
        return None


def parse_decimal(value):
    """A ``Decimal``, or None. Tolerates thousands separators and a rupee sign."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    text = str(value).replace(",", "").replace(" ", "").replace("₹", "").strip()
    if not text or text in {"-", "--"}:
        return None
    # Accounting parentheses: (1,234) is minus 1234.
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative else number


def parse_int(value):
    """An ``int``, or None. Rounds, because the export writes 13.0 for a
    state code often enough to matter."""
    number = parse_decimal(value)
    if number is None:
        return None
    try:
        return int(number.to_integral_value(rounding="ROUND_HALF_UP"))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def parse_text(value):
    """Trimmed text, or None for an empty cell. Never the string "None"."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d-%m-%Y")
    text = str(value).strip()
    return text or None


def parse_for(column, value):
    """Type one cell the way its column says it should be typed."""
    if column in schema.DATE_COLUMNS:
        return parse_date(value)
    if column in schema.INT_COLUMNS:
        return parse_int(value)
    if column in schema.DECIMAL_COLUMNS:
        return parse_decimal(value)
    return parse_text(value)


def map_row(raw):
    """One raw ``{heading: cell}`` dict into ``{column: typed value}``.

    Every one of the 204 columns is present in the result, set to None when the
    file does not carry it. A heading the schema does not recognise is ignored:
    it means the export gained a column since ``schema.py`` was generated,
    which is a reason to regenerate the schema, not to fail the import.
    """
    return {column: parse_for(column, raw.get(column)) for column in schema.ALL_COLUMNS}


# ── workbook / csv readers ───────────────────────────────────────────────────

def pick_sheet(workbook):
    """The sheet whose name mentions "reconcil", else the first worksheet."""
    for name in workbook.sheetnames:
        if PREFERRED_SHEET_HINT in name.casefold():
            return workbook[name]
    if not workbook.sheetnames:
        raise ReadError("No worksheet was found inside the file.")
    return workbook[workbook.sheetnames[0]]


def read_xlsx(path):
    """``[{heading: cell}, ...]`` from an .xlsx file.

    Read-only mode streams the sheet rather than building the whole object
    graph — a 10,000-row export with 204 columns is two million cells, and
    loading them all as objects first is a needless spike.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = pick_sheet(workbook)
        grid = []
        for row in sheet.iter_rows(values_only=True):
            grid.append(list(row))
        return assemble_rows(grid)
    finally:
        workbook.close()


def read_csv(path):
    """``[{heading: cell}, ...]`` from a .csv file.

    ``utf-8-sig`` so a BOM written by Excel does not end up glued to the first
    heading, which would make ``EntityGstin`` an unrecognised column.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        grid = [list(row) for row in csv.reader(handle)]
    return assemble_rows(grid)


def read_file(path, filename=None):
    """Dispatch on extension. Raises ReadError with something a user can act on."""
    name = (filename or str(path)).casefold()
    if name.endswith(".xls"):
        raise ReadError(
            "The legacy .xls format isn't supported — please save as .xlsx and try again."
        )
    if name.endswith(".csv"):
        return read_csv(path)
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return read_xlsx(path)
    raise ReadError("Please choose a .xlsx or .csv file.")


def find_header_row(grid):
    """Index of the heading row, by how many known column names it carries."""
    known = {column.casefold() for column in schema.ALL_COLUMNS}
    best_index, best_hits = 0, 0
    for index in range(min(HEADER_SEARCH_DEPTH, len(grid))):
        hits = sum(
            1 for cell in grid[index]
            if cell is not None and str(cell).strip().casefold() in known
        )
        if hits > best_hits:
            best_index, best_hits = index, hits
    return best_index, best_hits


def assemble_rows(grid):
    """Find the heading row, then key every row below it by that heading text."""
    if not grid:
        return []

    header_index, hits = find_header_row(grid)
    if hits == 0:
        raise ReadError(
            "No recognised column headings were found in the first "
            f"{HEADER_SEARCH_DEPTH} rows. Is this the Reconciliation Export sheet?"
        )

    headers = [("" if cell is None else str(cell).strip()) for cell in grid[header_index]]

    rows = []
    for cells in grid[header_index + 1:]:
        row = {}
        has_value = False
        for position, heading in enumerate(headers):
            if not heading:
                continue
            cell = cells[position] if position < len(cells) else None
            if cell is not None and str(cell).strip():
                has_value = True
            row[heading] = cell
        # A wholly blank row is padding below the data, not a document.
        if has_value:
            rows.append(row)
    return rows


def count_known_headers(headings):
    """How many of the file's headings the schema recognises — shown after an
    upload so a partly-matching file is obvious before it is imported."""
    known = {column.casefold() for column in schema.ALL_COLUMNS}
    return sum(1 for heading in headings if str(heading).strip().casefold() in known)
