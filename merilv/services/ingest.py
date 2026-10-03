"""Spreadsheet ingestion for SAP extracts and master-data files.

SAP transaction exports are not stable: header names differ between transaction
variants, columns get reordered, and the header row is rarely the first row.
Rather than demand a fixed template, every reader here locates its header row by
content and resolves columns through alias lists — the same tolerance the legacy
JavaScript had, collected into one place.

Supported inputs: ``.xlsx`` / ``.xlsm`` via openpyxl, legacy ``.xls`` via
xlrd, and ``.csv`` / ``.txt``.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from openpyxl import load_workbook

MAX_HEADER_SCAN_ROWS = 30

#: Terms that plausibly name a material column, ordered by specificity.
MATERIAL_TERMS = (
    "material",
    "matnr",
    "article",
    "product",
    "sku",
    "part",
    "item",
    "code",
    "model",
    "identifier",
    "mat",
)

# Column header constants — avoids duplicating string literals across parsers.
COL_MATERIAL_CODE = "Material Code"
COL_SHIP_TO_PARTY = "Ship-to party"
COL_SOLD_TO_PARTY = "Sold-to party"
COL_BILLING_DOCUMENT = "billing document"
COL_SUPPLY_NODE = "Supply Node Code"
COL_RECEIVING_PARENT_NODE = "Receiving Parent Node Code"
COL_RECEIVING_NODE = "Receiving Node Code"
COL_INTRANSIT_COLOR_ZONE = "Intransit Color Zone"


class IngestError(ValueError):
    """Raised when a file cannot be understood well enough to proceed."""


@dataclass
class SheetData:
    """A parsed spreadsheet: the header row plus everything after it."""

    headers: list[str]
    rows: list[list[Any]]
    header_row_index: int = 0

    def as_dicts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self.rows:
            record: dict[str, Any] = {}
            for index, header in enumerate(self.headers):
                if header:
                    record[header] = row[index] if index < len(row) else None
            out.append(record)
        return out


@dataclass
class IngestResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0
    headers: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.records)


# ─── Low-level readers ───────────────────────────────────────────────────────
def _decode_csv(raw: bytes) -> list[list[Any]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover — latin-1 accepts any byte sequence
        text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if "\t" in sample else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


#: Rows scanned when judging a sheet. Enough to tell data from a stray note,
#: cheap enough to do for every sheet in the book.
_SHEET_SCORE_ROWS = 40


def _sheet_has_data(grid: list[list[Any]]) -> bool:
    return any(
        cell is not None and str(cell).strip()
        for row in grid[:_SHEET_SCORE_ROWS] for cell in row
    )


def looks_like_barcodes(grid: list[list[Any]]) -> int:
    """How many of a sheet's first rows look like scanned barcodes.

    Used as a ``prefer`` scorer by :func:`parse_scan_file`. Recognises both
    shapes the client's scanners produce: bracketed GS1 (``(01)…(10)…(21)``)
    and the unbracketed fixed-position strings MLSIPL's guns emit.
    """
    hits = 0
    for row in grid[:_SHEET_SCORE_ROWS]:
        for cell in row:
            text = _cell_text(cell)
            if not text:
                continue
            if text.count("(") >= 3 and ")" in text:
                hits += 1
                break
            # Unbracketed: a long digit-heavy token is how the MLSIPL exports
            # come out (010081176702088610T3203272311240801…).
            if len(text) >= 40 and sum(c.isdigit() for c in text) >= 30:
                hits += 1
                break
    return hits


def _read_xlsx_grid(raw: bytes, prefer=None) -> list[list[Any]]:
    """The grid of the sheet that actually holds the data.

    NOT ``workbook.active``, which is what this used to be. `active` is the tab
    that happened to be selected when the file was last saved — an irrelevant
    act that was silently deciding what got imported.

    A real example from the client's Stock Validator files: a scan workbook with
    the barcodes on Sheet1 (22 rows) and a leftover MLSIPL extract on Sheet2
    (31 rows), saved with Sheet2 selected. The validator read the leftover tab,
    found no barcodes, and reported every row "Incomplete Barcode" at 0%
    reconciliation — with nothing on screen to say it had read the wrong sheet.

    Note that sheet SIZE cannot decide this: the wrong tab was the bigger one.
    Only the caller knows what it is looking for, so it may pass ``prefer`` — a
    function scoring a grid for its own purpose (see :func:`looks_like_barcodes`).
    A sheet the scorer recognises beats one it does not; past that, and for every
    caller that passes no scorer, the active sheet wins as before.
    """
    workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        sheets = workbook.worksheets
        if not sheets:
            return []
        active_title = workbook.active.title if workbook.active is not None else None

        best_grid: list[list[Any]] = []
        best_key = None
        for sheet in sheets:
            grid = [list(row) for row in sheet.iter_rows(values_only=True)]
            if not _sheet_has_data(grid):
                continue
            score = prefer(grid) if prefer is not None else 0
            key = (score, 1 if sheet.title == active_title else 0)
            if best_key is None or key > best_key:
                best_key = key
                best_grid = grid
        if best_grid:
            return best_grid

        # Every sheet empty: hand back the active one so the caller still raises
        # its own "file is empty" error rather than an IndexError in here.
        sheet = workbook.active
        return [list(row) for row in sheet.iter_rows(values_only=True)] if sheet else []
    finally:
        workbook.close()


def _read_xls_grid(raw: bytes) -> list[list[Any]]:
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise IngestError("Could not read the legacy .xls file: xlrd is not installed.") from exc

    try:
        book = xlrd.open_workbook(file_contents=raw)
    except Exception as exc:
        raise IngestError(f"Could not read the legacy .xls file: {exc}") from exc

    sheet = book.sheet_by_index(0)

    def value_at(row_index: int, column_index: int) -> Any:
        cell = sheet.cell(row_index, column_index)
        if cell.ctype == xlrd.XL_CELL_EMPTY:
            return None
        if cell.ctype == xlrd.XL_CELL_DATE:
            parsed = xlrd.xldate_as_datetime(cell.value, book.datemode)
            if parsed.time() == datetime.min.time():
                return parsed.date()
            return parsed
        return cell.value

    return [
        [value_at(row_index, column_index) for column_index in range(sheet.ncols)]
        for row_index in range(sheet.nrows)
    ]


def _looks_like_legacy_xls(raw: bytes) -> bool:
    return raw.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1")


def read_grid(uploaded_file: Any, filename: str = "", prefer=None) -> list[list[Any]]:
    """Read any supported file into a raw grid of cell values."""
    name = (filename or getattr(uploaded_file, "name", "") or "").lower()

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    raw = uploaded_file.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    if name.endswith((".csv", ".txt", ".tsv")):
        return _decode_csv(raw)

    if name.endswith(".xls") or _looks_like_legacy_xls(raw):
        return _read_xls_grid(raw)

    try:
        return _read_xlsx_grid(raw, prefer=prefer)
    except Exception as exc:  # openpyxl raises a variety of types for bad files
        raise IngestError(f"Could not read the spreadsheet: {exc}") from exc


def _cell_text(value: Any) -> str:
    """One cell as text, without Excel's numeric formatting leaking in.

    Codes in these sheets are strings — plant `WGJ7` and plant `9100` are the
    same kind of thing — but Excel stores the digit-only ones as numbers, and
    openpyxl hands them back as `int` or `float` depending on how the cell was
    written. `str(9100.0)` is "9100.0", which matches no plant in the priority
    list, no material in the master, and no row in another dump. The failure is
    silent: the code simply never matches.

    So a float that is exactly a whole number renders without the decimal.
    Genuine decimals are unaffected — 2.5 stays "2.5" — and quantities do not
    come through here at all; they go through `number_at`.

    Note this deliberately does NOT strip leading zeros. `0002100099` is a real
    customer code in these files, and Excel already destroys the zeros when it
    decides a cell is numeric; making that worse here is not the fix.
    """
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def find_header_row(grid: Sequence[Sequence[Any]], required: Sequence[Sequence[str]]) -> int:
    """Locate the header row.

    ``required`` is a list of term groups; a row qualifies when it contains at
    least one term from *every* group. Groups are tried in order and the first
    that matches wins, which lets callers pass a strict rule followed by looser
    fallbacks.
    """
    limit = min(len(grid), MAX_HEADER_SCAN_ROWS)
    for index in range(limit):
        joined = " | ".join(_cell_text(cell).lower() for cell in grid[index])
        if not joined.strip(" |"):
            continue
        if all(any(term in joined for term in group) for group in required):
            return index
    return -1


def load_sheet(
    uploaded_file: Any,
    required: Sequence[Sequence[str]],
    fallbacks: Sequence[Sequence[Sequence[str]]] = (),
    filename: str = "",
) -> SheetData:
    """Read a file and split it into headers plus data rows."""

    def _has_the_header(grid: list[list[Any]]) -> int:
        """Score a sheet by whether it carries the header we are looking for.

        Without this the ACTIVE sheet wins (see _read_xlsx_grid), and active
        is just whichever tab was selected when the file was last saved.
        Reported from the floor: a customer master exported from this system,
        opened to check a count with a couple of scratch cells on a new tab,
        and re-uploaded — the new tab was active and two cells wide, so the
        import read that and answered "could not find a header row" about a
        file whose 992-row Customer Master sheet was sitting right there.

        Every master import states the columns it needs, so the sheet that
        actually has them is knowable here rather than guessed. Preferred over
        a fallback match, which is in turn preferred over neither.
        """
        if find_header_row(grid, required) >= 0:
            return 2
        for group in fallbacks:
            if find_header_row(grid, group) >= 0:
                return 1
        return 0

    grid = read_grid(uploaded_file, filename, prefer=_has_the_header)
    if not grid:
        raise IngestError("The file is empty.")

    header_index = find_header_row(grid, required)
    for fallback in fallbacks:
        if header_index >= 0:
            break
        header_index = find_header_row(grid, fallback)

    if header_index < 0:
        raise IngestError(
            "Could not find a header row. Expected a row containing "
            + " and ".join("/".join(group) for group in required)
            + ". No sheet in this file has one — check the column names, and "
            "that the data is not on a tab that was left empty."
        )

    headers = [_cell_text(cell) for cell in grid[header_index]]
    rows = [list(row) for row in grid[header_index + 1 :] if any(_cell_text(cell) for cell in row)]
    return SheetData(headers=headers, rows=rows, header_row_index=header_index)


# ─── Column resolution ───────────────────────────────────────────────────────
class ColumnResolver:
    """Finds column positions from alias lists, case- and space-insensitively."""

    def __init__(self, headers: Sequence[str]):
        self.headers = list(headers)
        self._lower = [h.strip().lower() for h in self.headers]

    def exact(self, *names: str) -> int:
        """First column whose header equals one of ``names``."""
        for name in names:
            target = name.strip().lower()
            for index, header in enumerate(self._lower):
                if header == target:
                    return index
        return -1

    def contains(self, *terms: str, exclude: Sequence[str] = ()) -> int:
        """First column whose header contains one of ``terms``."""
        for term in terms:
            needle = term.strip().lower()
            for index, header in enumerate(self._lower):
                if needle in header and not any(bad in header for bad in exclude):
                    return index
        return -1

    def any_of(self, *specs: Sequence[str]) -> int:
        """First column containing *all* terms of any one spec."""
        for spec in specs:
            for index, header in enumerate(self._lower):
                if all(term.lower() in header for term in spec):
                    return index
        return -1

    def resolve(self, *strategies) -> int:
        """Run resolution strategies in order, returning the first hit."""
        for strategy in strategies:
            result = strategy()
            if result >= 0:
                return result
        return -1


def cell(row: Sequence[Any], index: int, default: Any = "") -> Any:
    if index < 0 or index >= len(row):
        return default
    value = row[index]
    return default if value is None else value


def text_at(row: Sequence[Any], index: int, default: str = "") -> str:
    value = cell(row, index, None)
    return _cell_text(value) or default


def number_at(row: Sequence[Any], index: int, default: float = 0.0) -> float:
    """Read a numeric cell, tolerating thousands separators and stray symbols."""
    value = cell(row, index, None)
    if value is None or value == "":
        return default
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    if not text or text in ("-", ".", "-."):
        return default
    try:
        return float(text)
    except (ValueError, InvalidOperation):
        return default


def date_at(row: Sequence[Any], index: int) -> date | None:
    value = cell(row, index, None)
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ─── YVL06 delivery dump ─────────────────────────────────────────────────────
#: SAP pads header names with spaces, so keys are matched after trimming.
DELIVERY_ALIASES: dict[str, tuple[str, ...]] = {
    "delivery_no": ("Delivery No.", "Delivery", "Delivery No", "Delivery Number"),
    "item_no": ("Item", "Item No.", "Pos.", "Pos"),
    "material": ("Material", "Material No.", COL_MATERIAL_CODE),
    "batch": ("Batch", "Batch No.", "Lot"),
    "plant": ("Receiving plant", "Plant", "Shipping Point/Receiving Pt"),
    "location": ("Storage Location", "Location", "Stor. Location"),
    "qty": ("Delivery quantity", "Quantity", "Delivery Qty", "Del. Qty"),
    "delivery_date": (
        "Delivery Date", "Delivery date", "Planned GI date", "Actual GI date", "GI Date", "Created on",
    ),
    "ship_to_code": (COL_SHIP_TO_PARTY, "Ship-to pt"),
    "ship_to_name": ("Ship-to pt.name", "Ship-to party name"),
    "bill_to_code": (COL_SOLD_TO_PARTY, "Sold-to pt", "Customer", "Customer Code"),
    "bill_to_name": ("Name 1", "Name", "Customer Name", "Sold-to pt.name", "Sold-To Pt.Name"),
    "ref_document": (
        "Reference Document", "Reference document", "Reference Doc", "Sales document", "Sales Document", "Order No.",
    ),
    "customer_po_no": ("Customer reference", "Customer Reference", "Customer PO", "PO No", "PO Number"),
    "company_code": ("Company Code", "CompanyCode"),
    "discharging_place": ("Discharging Place", "Port of Discharge"),
    "final_destination": ("Country of Final Destination", "Final Destination"),
    "country_of_origin": ("Country of Origin",),
    "port_of_loading": ("Port of Loading",),
    "ean": ("EAN", "EAN/UPC", "GTIN"),
}


def parse_delivery_dump(uploaded_file: Any, filename: str = "", aggregate: bool = False) -> IngestResult:
    """Parse a YVL06 delivery extract into delivery-line records.

    Rows without a delivery number, material or positive quantity are dropped —
    SAP exports routinely carry subtotal and footer rows.
    """
    sheet = load_sheet(
        uploaded_file,
        required=[("delivery",), ("material",)],
        fallbacks=[[("material",), ("quantity", "qty")]],
        filename=filename,
    )
    resolver = ColumnResolver(sheet.headers)
    columns = {field_name: resolver.exact(*names) for field_name, names in DELIVERY_ALIASES.items()}

    # Fall back to substring matching for the fields we cannot do without.
    if columns["delivery_no"] < 0:
        columns["delivery_no"] = resolver.contains("delivery no", "delivery")
    if columns["material"] < 0:
        columns["material"] = resolver.contains(*MATERIAL_TERMS)
    if columns["qty"] < 0:
        columns["qty"] = resolver.contains("delivery quantity", "quantity", "qty")

    missing = [name for name in ("delivery_no", "material", "qty") if columns[name] < 0]
    if missing:
        raise IngestError(
            "The delivery dump is missing required columns: "
            + ", ".join(name.replace("_", " ") for name in missing)
            + "."
        )

    result = IngestResult(headers=sheet.headers)
    for sequence, row in enumerate(sheet.rows):
        delivery_no = text_at(row, columns["delivery_no"])
        material = text_at(row, columns["material"])
        qty = number_at(row, columns["qty"])
        if not delivery_no or not material or qty <= 0:
            result.skipped += 1
            continue

        record: dict[str, Any] = {
            "sequence": sequence,
            "delivery_no": delivery_no,
            "item_no": text_at(row, columns["item_no"]),
            "material": material.upper(),
            "batch": text_at(row, columns["batch"]).upper(),
            "plant": text_at(row, columns["plant"]),
            "location": text_at(row, columns["location"]),
            "company_code": text_at(row, columns["company_code"]),
            "ean": text_at(row, columns["ean"]),
            "required_qty": qty,
            "delivery_date": date_at(row, columns["delivery_date"]),
            "ship_to_code": text_at(row, columns["ship_to_code"]),
            "ship_to_name": text_at(row, columns["ship_to_name"]),
            "bill_to_code": text_at(row, columns["bill_to_code"]),
            "bill_to_name": text_at(row, columns["bill_to_name"]),
            "ref_document": text_at(row, columns["ref_document"]),
            "customer_po_no": text_at(row, columns["customer_po_no"]),
            "port_of_loading": text_at(row, columns["port_of_loading"]),
            "discharging_place": text_at(row, columns["discharging_place"]),
            "final_destination": text_at(row, columns["final_destination"]),
            "country_of_origin": text_at(row, columns["country_of_origin"]),
        }
        record["customer_name"] = record["bill_to_name"] or record["ship_to_name"]
        result.records.append(record)

    if aggregate:
        result.records = _aggregate_delivery_lines(result.records)
    if result.skipped:
        result.warnings.append(f"{result.skipped} row(s) skipped — no delivery number, material or quantity.")
    if not result.records:
        raise IngestError("No usable delivery lines were found in the file.")
    return result


def _aggregate_delivery_lines(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge repeated delivery/item/material/batch rows, summing their quantities.

    The item number is part of the key, and must stay that way. Serials are
    assigned against a line's open quantity, so a line is what SAP will accept
    a serial for — and SAP accepts them per delivery *item*. Two items of one
    delivery that happen to share a material and batch (900001 x2 and 900026 x2,
    say) are two separate commitments of two units each; merging them into one
    four-unit line would put all four serials under the first item number and
    leave the second with none, which is rejected on upload.

    What is left to merge is a genuine duplicate: the same delivery item split
    across repeated rows by a column this parser does not read. Dumps with no
    Item column at all key on a blank item and so aggregate exactly as before.
    """
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["delivery_no"], record["item_no"], record["material"], record["batch"])
        existing = merged.get(key)
        if existing is None:
            record = dict(record)
            record["source_items"] = [record["item_no"]] if record["item_no"] else []
            merged[key] = record
        else:
            existing["required_qty"] += record["required_qty"]
            if record["item_no"] and record["item_no"] not in existing["source_items"]:
                existing["source_items"].append(record["item_no"])
    return list(merged.values())


# ─── SAP stock dump (validator) ──────────────────────────────────────────────
def parse_system_dump(uploaded_file: Any, filename: str = "", require_serial: bool = False) -> IngestResult:
    """Parse a SAP stock extract (YSRL / MB52 style) into inventory rows."""
    sheet = load_sheet(
        uploaded_file,
        required=[MATERIAL_TERMS, ("batch", "qty", "quantity", "desc", "plant", "billing", "lot", "unrestricted")],
        fallbacks=[[MATERIAL_TERMS], [("batch", "lot"), ("serial", "sno", "ser")]],
        filename=filename,
    )
    resolver = ColumnResolver(sheet.headers)

    columns = {
        "material": resolver.resolve(
            lambda: resolver.exact("Material", COL_MATERIAL_CODE, "Material No."),
            lambda: resolver.contains(*MATERIAL_TERMS),
        ),
        "batch": resolver.contains("batch", "lot"),
        "serial": resolver.contains("serial", "serial no", "serial number", "sno", "equipment"),
        "qty": resolver.contains("unrestricted", "qty", "quantity", "count", "stock"),
        "plant": resolver.contains("plant", "werks", "site"),
        "location": resolver.contains("sloc", "stor", "str. loc", "loc"),
        "expiry": resolver.contains("sled", "bbd", "expiry", "expiration"),
        "ageing": resolver.contains("ageing", "aging", "inv. age"),
        "description": resolver.resolve(
            lambda: resolver.contains("material description", "description", "desc", "text"),
            lambda: resolver.contains("name"),
        ),
        "customer_name": resolver.resolve(
            lambda: resolver.any_of(("cust", "name")),
            lambda: resolver.contains("name 1", "name"),
        ),
        "customer_code": resolver.resolve(
            lambda: resolver.any_of(("cust", "code"), ("cust", "no"), ("cust", "id")),
            lambda: resolver.contains("sold-to", "ship-to", "payer", "party", "kunnr"),
            lambda: resolver.contains("customer", exclude=("name",)),
        ),
        "billing_document": resolver.contains(COL_BILLING_DOCUMENT, "billing doc", "bill. doc", "bill doc", "invoice"),
        "billing_date": resolver.contains("billing date", "billing dt", "bill. date", "bill date", "invoice date"),
    }

    # Material is mandatory; the legacy code fell back to column A.
    if columns["material"] < 0:
        columns["material"] = 0
    if columns["qty"] < 0 and columns["material"] < 0:
        raise IngestError("The stock dump needs at least a material column.")
    if require_serial and columns["serial"] < 0:
        raise IngestError("This entity tracks serials — the stock dump must include a serial column.")

    result = IngestResult(headers=sheet.headers)
    for row in sheet.rows:
        material = text_at(row, columns["material"])
        if not material:
            result.skipped += 1
            continue
        qty = number_at(row, columns["qty"], default=1.0) if columns["qty"] >= 0 else 1.0
        if qty <= 0:
            result.skipped += 1
            continue
        result.records.append(
            {
                "material": material,
                "batch": text_at(row, columns["batch"]),
                "serial": text_at(row, columns["serial"]),
                "qty": qty,
                "plant": text_at(row, columns["plant"], "-"),
                "location": text_at(row, columns["location"], "-"),
                "expiry": text_at(row, columns["expiry"], "-"),
                "ageing": text_at(row, columns["ageing"]),
                "description": text_at(row, columns["description"]),
                "customer_name": text_at(row, columns["customer_name"]),
                "customer_code": text_at(row, columns["customer_code"]),
                "billing_document": text_at(row, columns["billing_document"]),
                "billing_date": text_at(row, columns["billing_date"]),
            }
        )

    if not result.records:
        raise IngestError("No usable stock rows were found in the file.")
    if result.skipped:
        result.warnings.append(f"{result.skipped} row(s) skipped — no material or non-positive quantity.")
    return result


# ─── Scan file ───────────────────────────────────────────────────────────────
_SCAN_HEADER_KEYWORDS = ("scan", "barcode", "material", "serial")
_GS1_MARKERS = ("(01)", "(240)", "(10)")


def _find_scan_header(grid: list[list[Any]]) -> int:
    """Locate the header row in a scan file, or -1 if headerless."""
    header_index = -1
    for index in range(min(len(grid), MAX_HEADER_SCAN_ROWS)):
        joined = " | ".join(_cell_text(c).lower() for c in grid[index])
        if any(kw in joined for kw in _SCAN_HEADER_KEYWORDS):
            header_index = index
            break
    first_cell = _cell_text(grid[0][0]) if grid[0] else ""
    if any(marker in first_cell for marker in _GS1_MARKERS):
        return -1
    return header_index


def _resolve_scan_columns(
    headers: list[str], use_scan_qty: bool,
) -> dict[str, int]:
    """Resolve all column positions for a headed scan sheet."""
    resolver = ColumnResolver(headers)
    scan_col = resolver.resolve(
        lambda: resolver.contains("scan", "barcode"),
        lambda: -1,
    )
    material_col = resolver.resolve(
        lambda: resolver.exact(
            "Sap Code", "SAP Code", "Material", COL_MATERIAL_CODE, "Material No.",
            "Item Code", "SKU",
        ),
        lambda: resolver.any_of(("sap", "code")),
        lambda: resolver.contains(
            "material", "sku", "item code",
            exclude=("desc", "description", "text", "name"),
        ),
        lambda: resolver.contains("material", "sku", "item code"),
    )
    batch_col = resolver.contains("batch", "lot")
    serial_col = resolver.contains("serial", "serial no", "serial number", "sno")
    if scan_col < 0 and material_col < 0:
        scan_col = 0
    plant_col = resolver.resolve(
        lambda: resolver.exact("Plant", "Werks"),
        lambda: resolver.contains("plant", exclude=("r-", "recv", "receiv", "name", "region")),
        lambda: resolver.contains("plant", exclude=("name", "region")),
    )
    condition_col = resolver.contains("condition", "status")
    if condition_col < 0 and scan_col >= 0 and len(headers) > scan_col + 1:
        neighbour = scan_col + 1
        claimed = {material_col, batch_col, serial_col, plant_col}
        if not headers[neighbour].strip() and neighbour not in claimed:
            condition_col = neighbour
    return {
        "scan": scan_col,
        "material": material_col,
        "batch": batch_col,
        "serial": serial_col,
        "plant": plant_col,
        "condition": condition_col,
        "qty": resolver.contains("qty", "quantity", "count") if use_scan_qty else -1,
        "description": resolver.contains("desc", "text", "material name", "spec", "detail", "title"),
        "customer_name": resolver.contains("name", "client", "buyer", "account"),
        "customer_code": resolver.resolve(
            lambda: resolver.any_of(("cust", "code"), ("cust", "no"), ("cust", "id")),
            lambda: resolver.contains("party", "sold-to"),
            lambda: resolver.contains("customer", exclude=("name",)),
        ),
    }


def parse_scan_file(uploaded_file: Any, filename: str = "", use_scan_qty: bool = True) -> IngestResult:
    """Parse a handheld-scanner export.

    These files are frequently headerless — a bare column of barcodes — so a
    header row is only assumed when one is actually recognisable.
    """
    grid = read_grid(uploaded_file, filename, prefer=looks_like_barcodes)
    if not grid:
        raise IngestError("The scan file is empty.")

    header_index = _find_scan_header(grid)

    if header_index >= 0:
        headers = [_cell_text(c) for c in grid[header_index]]
        data_rows = grid[header_index + 1 :]
        cols = _resolve_scan_columns(headers, use_scan_qty)
    else:
        headers = []
        data_rows = grid
        cols = {
            "scan": 0, "condition": 1,
            "material": -1, "batch": -1, "serial": -1, "plant": -1,
            "qty": -1, "description": -1, "customer_name": -1, "customer_code": -1,
        }

    result = IngestResult(headers=headers)
    for row in data_rows:
        raw = text_at(row, cols["scan"]) if cols["scan"] >= 0 else ""
        material = text_at(row, cols["material"]) if cols["material"] >= 0 else ""
        if not raw and not material:
            result.skipped += 1
            continue
        result.records.append(
            {
                "raw": raw,
                "material": material,
                "batch": text_at(row, cols["batch"]) if cols["batch"] >= 0 else "",
                "serial": text_at(row, cols["serial"]) if cols["serial"] >= 0 else "",
                "plant": text_at(row, cols["plant"]),
                "condition": text_at(row, cols["condition"]),
                "qty": number_at(row, cols["qty"], default=0.0) if cols["qty"] >= 0 else None,
                "description": text_at(row, cols["description"]),
                "customer_name": text_at(row, cols["customer_name"]),
                "customer_code": text_at(row, cols["customer_code"]),
            }
        )

    if not result.records:
        raise IngestError("No barcodes were found in the scan file.")
    return result


# ─── Master data ─────────────────────────────────────────────────────────────
def parse_material_master(uploaded_file: Any, filename: str = "") -> IngestResult:
    """Parse a material master export into scan-code / SAP-code / GTIN rows."""
    sheet = load_sheet(
        uploaded_file,
        required=[MATERIAL_TERMS],
        fallbacks=[[("scan", "sap", "gtin", "ean")]],
        filename=filename,
    )
    return _material_master_from_sheet(sheet)


def parse_material_master_grid(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> IngestResult:
    """Same column resolution as :func:`parse_material_master`, for a sheet that
    is already split into headers/rows — e.g. one tab of a multi-sheet workbook,
    where the generic file-based ``load_sheet`` (single active sheet, header-row
    search) does not apply."""
    return _material_master_from_sheet(SheetData(headers=list(headers), rows=[list(r) for r in rows]))


_YMM061_SKIP = frozenset(("material_code", "material_description", "material_group"))


def _resolve_ymm061_columns(resolver: ColumnResolver) -> dict[str, int]:
    from merilv.models import MaterialMaster
    return {
        field_name: resolver.exact(header)
        for header, field_name in MaterialMaster.YMM061_COLUMNS
        if field_name not in _YMM061_SKIP
    }


def _material_master_from_sheet(sheet: SheetData) -> IngestResult:
    resolver = ColumnResolver(sheet.headers)

    scan_col = resolver.resolve(
        lambda: resolver.exact("Scan Code", "ScanCode", "Scancode"),
        lambda: resolver.contains("scan code", "scancode"),
        lambda: resolver.exact("Material"),
        lambda: resolver.contains(*MATERIAL_TERMS),
        lambda: 0,
    )
    sap_col = resolver.resolve(
        lambda: resolver.exact("SAP Code", "SAPCode", "Sap Code"),
        lambda: resolver.contains("sap code", "sapcode"),
        lambda: resolver.exact(COL_MATERIAL_CODE),
        lambda: resolver.contains("material code"),
        # A sheet that already has its own dedicated "Scan Code" column (label
        # code != SAP code) usually also carries a plain "Material" column for
        # the true SAP code — only reached when "Material" wasn't already
        # claimed as scan_col (guarded by the collision check just below).
        lambda: resolver.exact("Material"),
    )
    if sap_col == scan_col:
        sap_col = -1
    gtin_col = resolver.contains("gtin", "ean", "upc")
    desc_col = resolver.contains("description", "desc", "material name", "text")
    group_col = resolver.contains("material group", "group", "matkl")

    ymm_cols = _resolve_ymm061_columns(resolver)

    result = IngestResult(headers=sheet.headers)
    for row in sheet.rows:
        scan_code = text_at(row, scan_col)
        sap_code = text_at(row, sap_col) if sap_col >= 0 else ""
        if not scan_code and not sap_code:
            result.skipped += 1
            continue
        raw_row = {header: _cell_text(cell(row, index, "")) for index, header in enumerate(sheet.headers) if header}
        record = {
            "scan_code": (scan_code or sap_code).strip().upper(),
            "material_code": (sap_code or scan_code).strip().upper(),
            "material_description": text_at(row, desc_col),
            "gtin": text_at(row, gtin_col),
            "material_group": text_at(row, group_col),
            "raw_data": raw_row,
        }
        for field_name, column in ymm_cols.items():
            if column >= 0:
                record[field_name] = text_at(row, column)
        result.records.append(record)

    if not result.records:
        raise IngestError("No material rows were found in the file.")
    return result


def parse_customer_master(uploaded_file: Any, filename: str = "") -> IngestResult:
    """Parse a customer master export."""
    sheet = load_sheet(
        uploaded_file,
        required=[("customer", "sold-to", "ship-to", "kunnr", "party")],
        fallbacks=[[("name",)]],
        filename=filename,
    )
    return _customer_master_from_sheet(sheet)


def parse_customer_master_grid(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> IngestResult:
    """Same column resolution as :func:`parse_customer_master`, for a sheet that
    is already split into headers/rows."""
    return _customer_master_from_sheet(SheetData(headers=list(headers), rows=[list(r) for r in rows]))


_CUSTOMER_SIMPLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "company_code": ("company code",),
    "plant": ("plant",),
    "courier_partner": ("courier partner", "courier name", "courier"),
    "courier_number": ("courier number", "courier no"),
    "city": ("city",),
    "state": ("state", "region"),
    "country": ("country",),
    "pincode": ("pincode", "postal code", "pin code", "zip"),
    "gstin": ("gstin", "gst no", "gst"),
    "email": ("email", "e-mail"),
    "mobile": ("mobile", "telephone", "phone"),
    "contact_person": ("contact person", "contact"),
    "address": ("address", "street"),
}


def _merge_customer_plant(existing: dict[str, Any], new_plant: str) -> None:
    """Merge a new plant value into an already-seen customer record."""
    old_plants = {p.strip() for p in (existing.get("plant") or "").split(",") if p.strip()}
    if new_plant not in old_plants:
        old_plants.add(new_plant)
        existing["plant"] = ",".join(sorted(old_plants))


def _note_plant_courier(record: dict[str, Any], plant: str, name: str, number: str) -> None:
    """Record "this customer uses this courier out of this plant".

    ``CustomerMaster.customer_code`` is globally unique, so one customer is one
    row and cannot hold a courier per plant. A sheet that lists the same
    customer once per plant — which is how the business supplies it — therefore
    had every repeat DROPPED, keeping only the first row's courier. Reported as
    courier 19MAC at plant 3082 showing "none customers" while the sheet listed
    plenty: the customers were real, their 3082 rows were never stored.

    ``CustomerCourierMapping`` is the model for exactly this, keyed on
    (customer_code, entity, plant), and ``masters.courier_usage`` has always
    read it as a second link source — nothing ever wrote it outside the one-off
    legacy import. These ride along on the customer record and are unpacked by
    ``masters._load_customers``.
    """
    name = (name or "").strip()
    if not name:
        # Without a courier there is nothing to say: a plant on its own is
        # already covered by the customer's own default.
        return
    # One mapping per plant, never a comma list.
    #
    # A sheet exported from this system carries the merged value back in a
    # single cell — "3044,3082,3182" — because that is how CustomerMaster
    # stores several plants for one customer. Stored verbatim it matches no
    # courier row at all, since a courier belongs to ONE plant, so the
    # mapping would be written and still count nothing.
    rows = record.setdefault("_plant_couriers", [])
    known = {row["plant"].lower() for row in rows}
    for part in (plant or "").split(","):
        part = part.strip()
        if not part or part.lower() in known:
            continue
        known.add(part.lower())
        rows.append({
            "plant": part,
            "courier_name": name,
            "courier_number": (number or "").strip(),
        })


def _resolve_customer_columns(resolver: ColumnResolver) -> tuple[int, int, dict[str, int]]:
    """Resolve code_col, name_col, and simple column positions."""
    code_col = resolver.resolve(
        lambda: resolver.exact("Customer Code", "Customer", COL_SOLD_TO_PARTY, COL_SHIP_TO_PARTY, "Kunnr"),
        lambda: resolver.any_of(("customer", "code"), ("cust", "code"), ("cust", "no")),
        lambda: resolver.contains("sold-to", "ship-to", "party", "kunnr"),
        lambda: resolver.contains("customer", exclude=("name",)),
        lambda: 0,
    )
    name_col = resolver.resolve(
        lambda: resolver.exact("Customer Name", "Name 1", "Name"),
        lambda: resolver.any_of(("customer", "name"), ("cust", "name")),
        lambda: resolver.contains("name 1", "name"),
    )
    columns = {key: resolver.contains(*terms) for key, terms in _CUSTOMER_SIMPLE_COLUMNS.items()}
    return code_col, name_col, columns


def _customer_master_from_sheet(sheet: SheetData) -> IngestResult:
    resolver = ColumnResolver(sheet.headers)
    code_col, name_col, columns = _resolve_customer_columns(resolver)
    plant_col = columns.get("plant", -1)

    result = IngestResult(headers=sheet.headers)
    seen: dict[str, dict[str, Any]] = {}
    for row in sheet.rows:
        code = text_at(row, code_col)
        if not code:
            result.skipped += 1
            continue

        canon = code.strip().upper()
        if canon in seen:
            if plant_col >= 0:
                plant_val = text_at(row, plant_col)
                if plant_val:
                    _merge_customer_plant(seen[canon], plant_val)
                    # The row is still not kept as a customer — the code is
                    # unique and the first row is the default — but its courier
                    # is no longer thrown away with it.
                    _note_plant_courier(
                        seen[canon], plant_val,
                        text_at(row, columns.get("courier_partner", -1)),
                        text_at(row, columns.get("courier_number", -1)),
                    )
            continue

        raw_row = {header: _cell_text(cell(row, index, "")) for index, header in enumerate(sheet.headers) if header}
        record: dict[str, Any] = {
            "customer_code": canon,
            "raw_data": raw_row,
        }
        if name_col >= 0:
            record["customer_name"] = text_at(row, name_col)
        for key, index in columns.items():
            if index >= 0:
                record[key] = text_at(row, index)
        # The first row gets a mapping too, so the table holds the whole
        # picture rather than "every plant except the one that happened to be
        # listed first".
        if plant_col >= 0:
            _note_plant_courier(
                record, text_at(row, plant_col),
                text_at(row, columns.get("courier_partner", -1)),
                text_at(row, columns.get("courier_number", -1)),
            )
        seen[canon] = record
        result.records.append(record)

    if not result.records:
        raise IngestError("No customer rows were found in the file.")
    if result.skipped:
        result.warnings.append(f"{result.skipped} row(s) skipped — missing customer code.")
    return result


def _resolve_simple_positions(
    headers: Sequence[str],
    columns: Mapping[str, Sequence[str]],
    key_field: str,
) -> dict[str, int]:
    """Resolve column positions with exact-then-contains fallback and collision dedup."""
    resolver = ColumnResolver(headers)
    positions = {name: resolver.exact(*aliases) for name, aliases in columns.items()}
    for name, aliases in columns.items():
        if positions[name] < 0:
            positions[name] = resolver.contains(*aliases)
    if positions[key_field] < 0:
        positions[key_field] = 0
    claimed: set[int] = set()
    for name in list(positions):
        index = positions[name]
        if index < 0:
            continue
        if index in claimed:
            positions[name] = -1
        else:
            claimed.add(index)
    return positions


def parse_simple_master_grid(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    columns: Mapping[str, Sequence[str]],
    key: str = "",
    dedupe: Sequence[str] = (),
) -> IngestResult:
    """Parse a flat code/name(/parent) master from an already-split sheet.

    The structural masters — company, sales org, plant, storage location and the
    two type tables — all have that same shape, so they share one parser driven
    by a ``{field: (header aliases…)}`` spec instead of six near-identical ones.
    ``key`` names the field a row cannot be missing (the first field by default);
    rows without it are skipped.

    ``dedupe`` names the fields that together identify a row, defaulting to just
    ``key``. Storage locations need ``("code", "parent")``: their codes repeat
    across plants (every plant has a ``DMG1``), and deduplicating on the code
    alone silently collapsed a 300-row sheet to 29.
    """
    key_field = key or next(iter(columns))
    positions = _resolve_simple_positions(headers, columns, key_field)
    identity = tuple(field for field in (dedupe or (key_field,)) if positions.get(field, -1) >= 0)

    result = IngestResult(headers=list(headers))
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key_value = text_at(row, positions[key_field])
        marker = tuple(text_at(row, positions[field]).strip().lower() for field in identity)
        if not key_value or marker in seen:
            result.skipped += 1
            continue
        seen.add(marker)
        # A key only for a column the sheet actually has, making a narrow sheet
        # a PARTIAL update rather than a wipe.
        #
        # `positions[field]` is -1 for an absent column and `text_at(row, -1)`
        # returns "", so emitting every field unconditionally meant a carton
        # sheet without a SKU Name column blanked the SKU Name on every carton,
        # and a plant sheet without a Sales Org column CLEARED the sales-org
        # link on every plant. Same fault fixed for couriers in
        # `_courier_master_from_sheet` and for customers in
        # `_customer_master_from_sheet`; the rule is:
        #
        #     absent COLUMN              -> no opinion, leave the stored value
        #     present column, empty CELL -> a deliberate clear, so clear it
        #
        # The key field is always emitted — it is what the row is matched on.
        result.records.append({
            field: text_at(row, index)
            for field, index in positions.items()
            if index >= 0 or field == key_field
        })

    if result.skipped:
        result.warnings.append(
            f"{result.skipped} row(s) skipped — missing or duplicate "
            + " + ".join(field.replace("_", " ") for field in identity) + "."
        )
    return result


def parse_simple_master(
    uploaded_file: Any,
    columns: Mapping[str, Sequence[str]],
    dedupe: Sequence[str] = (),
    filename: str = "",
    key: str = "",
) -> IngestResult:
    """File-based counterpart to :func:`parse_simple_master_grid`."""
    key_field = key or next(iter(columns))
    sheet = load_sheet(
        uploaded_file,
        required=[tuple(term.lower() for term in columns[key_field])],
        fallbacks=[[tuple(term.lower() for term in aliases)] for aliases in columns.values()],
        filename=filename,
    )
    result = parse_simple_master_grid(
        sheet.headers, sheet.rows, columns, key=key_field, dedupe=dedupe,
    )
    if not result.records:
        raise IngestError("No usable rows were found in the file.")
    return result


def parse_courier_master(uploaded_file: Any, filename: str = "") -> IngestResult:
    """Parse a courier master export."""
    sheet = load_sheet(
        uploaded_file,
        required=[("courier",)],
        fallbacks=[[("name",)], [("vehicle",)]],
        filename=filename,
    )
    return _courier_master_from_sheet(sheet)


def _resolve_courier_columns(resolver: ColumnResolver) -> dict[str, int]:
    """Resolve all column positions for a courier master sheet."""
    name_col = resolver.resolve(
        lambda: resolver.exact("Courier Name", "Courier", "Name"),
        lambda: resolver.contains("courier name", "courier"),
        lambda: 0,
    )
    plant_col = resolver.contains("plant")
    entity_col = resolver.contains("entity", "business unit", "company code")
    number_col = resolver.resolve(
        lambda: resolver.exact("Courier Number", "Courier No", "Number"),
        lambda: resolver.contains("courier number", "courier no", "contact number", "number"),
    )
    vehicle_type_col = resolver.contains("vehicle type", "veh type", "vehicle category")

    vehicle_col = -1
    for candidate in (
        resolver.exact("Vehicle Number", "Vehicle No", "Vehicle #"),
        resolver.contains("vehicle number", "vehicle no"),
        resolver.contains("vehicle"),
    ):
        if candidate >= 0 and candidate != vehicle_type_col:
            vehicle_col = candidate
            break

    return {
        "name": name_col,
        "plant": plant_col,
        "entity": entity_col,
        "number": number_col,
        "vehicle_type": vehicle_type_col,
        "vehicle": vehicle_col,
        "gstin": resolver.contains("gstin", "gst no", "gst number", "gst"),
        "mode": resolver.contains("mode of transit", "transit mode", "mode of transport"),
        "active": resolver.contains("active", "status"),
    }


_COURIER_OPTIONAL_FIELDS = (
    ("plant", "plant"),
    ("entity_code", "entity"),
    ("courier_number", "number"),
    ("vehicle_number", "vehicle"),
    ("vehicle_type", "vehicle_type"),
    ("gstin", "gstin"),
    ("mode_of_transit", "mode"),
)

_INACTIVE_VALUES = frozenset(("no", "0", "false", "inactive", "n"))


def _courier_identity(name: str, code: str, plant: str, entity: str) -> tuple[str, ...]:
    """Dedupe key: courier code within a plant, falling back to name."""
    if code:
        return ("code", code.lower(), plant.lower(), entity.lower())
    return ("name", name.lower(), entity.lower())


def _courier_master_from_sheet(sheet: SheetData) -> IngestResult:
    resolver = ColumnResolver(sheet.headers)
    cols = _resolve_courier_columns(resolver)

    result = IngestResult(headers=sheet.headers)
    seen: set[tuple[str, ...]] = set()
    missing = repeated = 0
    for row in sheet.rows:
        name = text_at(row, cols["name"]).strip()
        if not name:
            missing += 1
            result.skipped += 1
            continue
        code = text_at(row, cols["number"]).strip() if cols["number"] >= 0 else ""
        plant = text_at(row, cols["plant"]).strip() if cols["plant"] >= 0 else ""
        entity = text_at(row, cols["entity"]).strip() if cols["entity"] >= 0 else ""
        key = _courier_identity(name, code, plant, entity)
        if key in seen:
            repeated += 1
            result.skipped += 1
            continue
        seen.add(key)
        record: dict[str, Any] = {"courier_name": name}
        for field_name, col_key in _COURIER_OPTIONAL_FIELDS:
            if cols[col_key] >= 0:
                record[field_name] = text_at(row, cols[col_key])
        if cols["active"] >= 0:
            active_text = text_at(row, cols["active"]).strip().lower()
            record["is_active"] = active_text not in _INACTIVE_VALUES
        result.records.append(record)

    if not result.records:
        raise IngestError("No courier rows were found in the file.")
    if missing:
        result.warnings.append(f"{missing} row(s) skipped — no courier name.")
    if repeated:
        result.warnings.append(
            f"{repeated} row(s) skipped — the same courier code appears twice for one "
            "plant (rows with no code are matched on name instead)."
        )
    return result


# ─── Branch transfer ─────────────────────────────────────────────────────────
BT_PLAN_REQUIRED = (
    "Unique Number",
    COL_SUPPLY_NODE,
    "Node Code",
    "Storage Location",
    COL_RECEIVING_PARENT_NODE,
    COL_RECEIVING_NODE,
    "SKU Code",
    "Quantity",
    COL_INTRANSIT_COLOR_ZONE,
    "Mode",
)


def _row_data(sheet: SheetData, row: Sequence[Any]) -> dict[str, str]:
    """Preserve a source row's visible values for audit/export context."""
    return {header: _cell_text(cell(row, index, "")) for index, header in enumerate(sheet.headers) if header}


def _parse_bt_template(sheet: SheetData) -> IngestResult:
    """Parse the legacy ten-column BT plan template."""
    resolver = ColumnResolver(sheet.headers)
    columns = {name: resolver.exact(name) for name in BT_PLAN_REQUIRED}
    missing = [name for name, index in columns.items() if index < 0]
    if missing:
        raise IngestError("The BT plan is missing required columns: " + ", ".join(missing) + ".")

    result = IngestResult(headers=sheet.headers)
    for sequence, row in enumerate(sheet.rows):
        sku = text_at(row, columns["SKU Code"])
        quantity = number_at(row, columns["Quantity"])
        if not sku or quantity <= 0:
            result.skipped += 1
            continue
        result.records.append(
            {
                "sequence": sequence,
                "unique_number": text_at(row, columns["Unique Number"]),
                "supply_node_code": text_at(row, columns[COL_SUPPLY_NODE]),
                "node_code": text_at(row, columns["Node Code"]),
                "storage_location": text_at(row, columns["Storage Location"]),
                "receiving_parent_node_code": text_at(row, columns[COL_RECEIVING_PARENT_NODE]),
                "receiving_node_code": text_at(row, columns[COL_RECEIVING_NODE]),
                "sku_code": sku.upper(),
                "quantity": quantity,
                "intransit_color_zone": text_at(row, columns[COL_INTRANSIT_COLOR_ZONE]),
                "mode": text_at(row, columns["Mode"]),
                "raw_data": _row_data(sheet, row),
            }
        )

    if not result.records:
        raise IngestError("No usable BT plan lines were found.")
    return result


def _parse_bt_yvl06(sheet: SheetData) -> IngestResult:
    """Accept a SAP/YVL06 transfer dump as a Branch Transfer plan.

    The legacy BT template remains the first-class format, but warehouse users
    often have the actual SAP delivery extract instead. Branch Transfer matches
    on material only, so a YVL06 line can safely become one BT line: Material ->
    SKU, Delivery/Reference -> unique number, Storage Location -> storage, and
    Receiving plant -> receiver.
    """
    resolver = ColumnResolver(sheet.headers)
    delivery_col = resolver.resolve(
        lambda: resolver.exact("Delivery No.", "Delivery", "Delivery No", "Delivery Number"),
        lambda: resolver.contains("delivery no", "reference document", COL_BILLING_DOCUMENT),
    )
    material_col = resolver.exact("Material", "Material No.", COL_MATERIAL_CODE)
    qty_col = resolver.resolve(
        lambda: resolver.exact("Delivery quantity", "Quantity", "Delivery Qty", "Del. Qty"),
        lambda: resolver.contains("delivery quantity", "quantity", "qty"),
    )
    if delivery_col < 0 or material_col < 0 or qty_col < 0:
        raise IngestError(
            "The YVL06/BT dump needs Delivery/Reference, Material and Delivery quantity columns."
        )

    item_col = resolver.exact("Item", "Item No.", "Reference item", "Reference Item", "Pos.", "Pos")
    storage_col = resolver.exact("Storage Location", "Location", "Stor. Location")
    receiving_col = resolver.resolve(
        lambda: resolver.exact("Receiving plant", "Receiving Plant", COL_RECEIVING_NODE),
        lambda: resolver.contains("receiving plant", "receiving node"),
    )
    supply_col = resolver.resolve(
        lambda: resolver.exact("Shipping Point/Receiving Pt", "Shipping Point", COL_SUPPLY_NODE),
        lambda: resolver.contains("shipping point", "supply node"),
    )
    parent_col = resolver.exact(COL_RECEIVING_PARENT_NODE, COL_SHIP_TO_PARTY, "Ship-to pt", COL_SOLD_TO_PARTY)
    mode_col = resolver.exact("Mode", "Delivery Type")
    zone_col = resolver.exact(COL_INTRANSIT_COLOR_ZONE, "Color Zone", "Zone")

    result = IngestResult(headers=sheet.headers)
    seen: set[tuple[str, str, str, str]] = set()
    for sequence, row in enumerate(sheet.rows):
        material = text_at(row, material_col).upper()
        quantity = number_at(row, qty_col)
        unique_number = text_at(row, delivery_col)
        if not unique_number or not material or quantity <= 0:
            result.skipped += 1
            continue

        item_no = text_at(row, item_col)
        receiving_node = text_at(row, receiving_col)
        storage = text_at(row, storage_col)
        key = (unique_number, item_no, material, receiving_node)
        if key in seen:
            result.skipped += 1
            continue
        seen.add(key)

        result.records.append(
            {
                "sequence": sequence,
                "unique_number": unique_number if not item_no else f"{unique_number}/{item_no}",
                "supply_node_code": text_at(row, supply_col),
                "node_code": text_at(row, supply_col),
                "storage_location": storage,
                "receiving_parent_node_code": text_at(row, parent_col),
                "receiving_node_code": receiving_node,
                "sku_code": material,
                "quantity": quantity,
                "intransit_color_zone": text_at(row, zone_col),
                "mode": text_at(row, mode_col) or "BT",
                "raw_data": _row_data(sheet, row),
            }
        )

    if not result.records:
        raise IngestError("No usable YVL06/BT transfer lines were found.")
    result.warnings.append(
        "Loaded as a YVL06/BT transfer dump; branch-transfer picking will match scanned barcodes on material."
    )
    if result.skipped:
        result.warnings.append(f"{result.skipped} row(s) skipped — duplicates, missing material/reference or non-positive quantity.")
    return result


def parse_bt_plan(uploaded_file: Any, filename: str = "") -> IngestResult:
    """Parse a branch-transfer plan.

    Supports the legacy ten-column BT template and SAP/YVL06 transfer dumps.
    """
    sheet = load_sheet(
        uploaded_file,
        required=[("sku code", "sku"), ("quantity", "qty")],
        fallbacks=[[("material",), ("delivery", "reference document", COL_BILLING_DOCUMENT), ("quantity", "qty")]],
        filename=filename,
    )
    try:
        return _parse_bt_template(sheet)
    except IngestError as template_error:
        try:
            return _parse_bt_yvl06(sheet)
        except IngestError:
            raise template_error


# ─── ZVA05 sales-order dump ──────────────────────────────────────────────────
def parse_zva05(uploaded_file: Any, filename: str = "") -> IngestResult:
    """Parse a ZVA05 open-order extract for auto-assign mode."""
    sheet = load_sheet(
        uploaded_file,
        required=[("material",), ("qty", "quantity")],
        filename=filename,
    )
    resolver = ColumnResolver(sheet.headers)
    material_col = resolver.resolve(
        lambda: resolver.exact("Material"),
        lambda: resolver.contains(*MATERIAL_TERMS),
    )
    qty_col = resolver.resolve(
        lambda: resolver.exact("Confirmed Qty", "Confirmed Quantity", "Order quantity"),
        lambda: resolver.contains("confirmed qty", "confirmed quantity", "order quantity"),
    )
    if material_col < 0 or qty_col < 0:
        raise IngestError("The ZVA05 dump needs a Material column and a confirmed/order quantity column.")

    customer_col = resolver.resolve(
        lambda: resolver.exact("Customer", "Sold-to Party"),
        lambda: resolver.contains("customer", "sold-to"),
    )
    doc_col = resolver.resolve(
        lambda: resolver.exact("Sales Document"),
        lambda: resolver.contains("sales document", "sales doc"),
    )
    item_col = resolver.exact("Item")

    result = IngestResult(headers=sheet.headers)
    seen: set[tuple[str, str, str, str]] = set()
    for sequence, row in enumerate(sheet.rows):
        material = text_at(row, material_col)
        qty = number_at(row, qty_col)
        if not material or qty <= 0:
            result.skipped += 1
            continue
        customer = text_at(row, customer_col)
        document = text_at(row, doc_col)
        item = text_at(row, item_col)
        key = (material.upper(), customer, document, item)
        if key in seen:
            result.skipped += 1
            continue
        seen.add(key)
        raw_row = {header: _cell_text(cell(row, index, "")) for index, header in enumerate(sheet.headers) if header}
        result.records.append(
            {
                "sequence": sequence,
                "material": material.upper(),
                "required_qty": qty,
                "customer": customer,
                "sales_document": document,
                "item_no": item,
                "raw_data": raw_row,
            }
        )

    if not result.records:
        raise IngestError("No usable ZVA05 order lines were found.")
    if result.skipped:
        result.warnings.append(f"{result.skipped} row(s) skipped — duplicates or non-positive quantity.")
    return result


__all__ = [
    "ColumnResolver",
    "IngestError",
    "IngestResult",
    "SheetData",
    "date_at",
    "find_header_row",
    "load_sheet",
    "number_at",
    "parse_bt_plan",
    "parse_courier_master",
    "parse_customer_master",
    "parse_customer_master_grid",
    "parse_delivery_dump",
    "parse_material_master",
    "parse_material_master_grid",
    "parse_scan_file",
    "parse_simple_master",
    "parse_simple_master_grid",
    "parse_system_dump",
    "parse_zva05",
    "read_grid",
    "text_at",
]
