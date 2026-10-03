"""Excel export builders for Meril-V.

Every workbook the module hands to a user is produced here, so that the one
corporate header style used across this codebase (see ``vendorlink.views``) is
applied in exactly one place and the view layer never has to touch openpyxl.
Builders return an :class:`io.BytesIO` rewound to position zero, ready to pass
straight to ``HttpResponse``; nothing here imports Django, so a workbook can be
built and inspected in a unit test without a request.

**The padded column headers in :func:`export_outbound` are deliberate.** The
"Serial Format" workbook is not a report — it is uploaded verbatim into SAP,
whose template matches columns by their full fixed-width name. Trimming the
trailing spaces, which is the first thing any tidy-up pass wants to do, makes
SAP reject the file with an unhelpful "field not found". :data:`OUTBOUND_BASE_HEADERS`
is therefore reproduced character for character and must stay that way; the
padding is data, not whitespace.

openpyxl is the only spreadsheet dependency used here. pandas is still listed in
``requirements.txt`` for other apps but is a dead weight for this module, and
routing exports through a DataFrame would lose the styling anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .validation import STOCK_DAMAGE, STOCK_EXPIRED, STOCK_NEAR_EXPIRY, STOCK_SHORT_EXPIRY

#: Corporate blue, matching the vendorlink exports.
HEADER_COLOR = "0076C8"

#: Excel caps sheet names at 31 characters and rejects a handful of punctuation.
SHEET_TITLE_LIMIT = 31
_INVALID_TITLE_CHARS = re.compile(r"[\[\]:*?/\\]")

DEFAULT_WIDTH = 20
MIN_WIDTH = 10
MAX_WIDTH = 60
#: Only the first slice of rows is measured for auto-width. A 50k-row export
#: should not spend most of its time deciding how wide column C ought to be.
AUTO_WIDTH_SAMPLE = 200

#: Above this many body cells a sheet is written without the per-cell border
#: grid. openpyxl charges for every style assignment, so on a 32,299-row master
#: the grid alone costs more than the rest of the export put together — and the
#: grid is cosmetic, while the export existing at all is not. Every sheet the
#: warehouse actually reads by eye (a delivery note, a validation run, a BT
#: receipt) is far below this and still gets the full grid.
MAX_GRID_CELLS = 120_000

SAVED_DATE_FORMAT = "%d-%m-%Y"
SAVED_TIME_FORMAT = "%H:%M:%S"


@dataclass
class SheetSpec:
    """One worksheet: a title, a header row, and the data beneath it.

    ``widths`` is optional — when omitted the column widths are measured from
    the content, which is right often enough that most callers can ignore it.
    """

    title: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    widths: list[int] | None = None


# ─── Workbook plumbing ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class _HeaderStyle:
    fill: PatternFill
    font: Font
    alignment: Alignment
    border: Border


def _header_style() -> _HeaderStyle:
    """Build fresh style objects for one workbook.

    openpyxl binds a style object into a workbook's shared style table the first
    time it is assigned, so styles are created per build rather than held as
    module constants shared between unrelated workbooks.
    """
    thin = Side(style="thin", color="D0D0D0")
    return _HeaderStyle(
        fill=PatternFill("solid", fgColor=HEADER_COLOR),
        font=Font(bold=True, color="FFFFFF", size=11),
        alignment=Alignment(horizontal="center", vertical="center", wrap_text=True),
        border=Border(left=thin, right=thin, top=thin, bottom=thin),
    )


def _safe_title(title: str) -> str:
    cleaned = _INVALID_TITLE_CHARS.sub(" ", str(title or "")).strip()
    return (cleaned or "Sheet1")[:SHEET_TITLE_LIMIT]


def _unique_title(title: str, used: set[str]) -> str:
    """Excel refuses duplicate sheet names, so collisions get a numeric suffix."""
    base = _safe_title(title)
    candidate, counter = base, 2
    while candidate.lower() in used:
        suffix = f" ({counter})"
        candidate = base[: SHEET_TITLE_LIMIT - len(suffix)] + suffix
        counter += 1
    used.add(candidate.lower())
    return candidate


def _cell_value(value: Any) -> Any:
    """Coerce a value into something openpyxl will accept.

    openpyxl raises on unknown types and on timezone-aware datetimes, both of
    which arrive routinely from ORM rows and JSON payloads. Normalising once
    here spares every call site from defensive ``str()`` calls.

    ``None`` stays ``None`` so the cell is genuinely EMPTY rather than holding
    an empty string. The two look identical in Excel, and openpyxl reads both
    back as ``None``, so the difference is invisible from almost everywhere —
    but it is not invisible in the file: an empty string is written as a typed
    string cell (``inlineStr``), an untouched cell as nothing at all. That
    matters on the sheets SAP ingests, where a numeric column carrying a
    zero-length string is not the same input as a column left blank. The
    invoice sheet's COST is exactly such a column.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # before int — bool is a subclass of int
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):  # before date — datetime is a subclass of date
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float, date, time)):
        return value
    return str(value)


def _auto_widths(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[int]:
    widths: list[int] = []
    for index, header in enumerate(headers):
        longest = len(str(header))
        for row in rows[:AUTO_WIDTH_SAMPLE]:
            if index < len(row):
                # An empty cell measures as nothing, not as the four
                # characters in "None" — otherwise a column that is blank by
                # design comes out four characters wide for no reason.
                cell = _cell_value(row[index])
                longest = max(longest, len(str(cell)) if cell is not None else 0)
        widths.append(max(MIN_WIDTH, min(MAX_WIDTH, longest + 2)))
    return widths


def _write_sheet(workbook: Workbook, spec: SheetSpec, style: _HeaderStyle, used_titles: set[str]) -> None:
    worksheet = workbook.create_sheet(_unique_title(spec.title, used_titles))
    headers = [str(header) for header in spec.headers]

    if headers:
        worksheet.append(headers)
        for column in range(1, len(headers) + 1):
            cell = worksheet.cell(row=1, column=column)
            cell.fill = style.fill
            cell.font = style.font
            cell.alignment = style.alignment
            cell.border = style.border
        worksheet.freeze_panes = "A2"

    rows = spec.rows
    column_count = len(headers)
    # Rough upper bound — rows may be ragged, but this only picks a strategy so
    # it does not need to be exact.
    body_cells = len(rows) * (column_count or 1)
    draw_grid = body_cells <= MAX_GRID_CELLS

    # Row 3.12 — every export gets a visible grid and a real date format,
    # rather than Excel's locale-dependent default for a bare `date` object.
    #
    # Two things here are deliberate and were both load-bearing bugs before:
    #
    # 1. The row is addressed by an explicit counter. The previous version read
    #    the row it had just appended back out with ``worksheet[worksheet.max_row]``
    #    on every iteration, which is O(rows) per row and therefore O(rows²)
    #    overall. Measured: 250 rows wrote at 14,021 cells/sec but 10,000 rows
    #    managed only 996, and the 32,299-row Material master (1.16M cells) never
    #    finished at all — over an hour, holding a worker the whole time.
    #
    # 2. Nothing is touched cell-by-cell unless it actually needs it. A cell only
    #    gets a ``worksheet.cell()`` lookup when it carries a date needing a
    #    number format, so a master sheet of plain strings costs one isinstance
    #    check per value and no style traffic at all.
    row_index = 1 if headers else 0
    for row in rows:
        row_index += 1
        worksheet.append([_cell_value(value) for value in row])

        if draw_grid:
            for column in range(1, column_count + 1):
                worksheet.cell(row=row_index, column=column).border = style.border

        for column, original in enumerate(row, start=1):
            if isinstance(original, datetime):
                worksheet.cell(row=row_index, column=column).number_format = "DD-MM-YYYY HH:MM"
            elif isinstance(original, date):
                worksheet.cell(row=row_index, column=column).number_format = "DD-MM-YYYY"

    widths = list(spec.widths) if spec.widths else _auto_widths(headers, spec.rows)
    for index in range(len(headers)):
        width = widths[index] if index < len(widths) else DEFAULT_WIDTH
        worksheet.column_dimensions[get_column_letter(index + 1)].width = width


def build_workbook(sheets: Sequence[SheetSpec]) -> BytesIO:
    """Render sheet specs into an in-memory ``.xlsx``.

    The generic entry point every other builder funnels through, so the header
    fill, bold white font, frozen header row and column widths are guaranteed to
    be identical across every download the module produces.
    """
    specs = list(sheets) or [SheetSpec(title="Sheet1")]
    workbook = Workbook()
    # Workbook() ships with one blank sheet; drop it so titles come only from specs.
    workbook.remove(workbook.active)

    style = _header_style()
    used_titles: set[str] = set()
    for spec in specs:
        _write_sheet(workbook, spec, style, used_titles)

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


# ─── Record access ───────────────────────────────────────────────────────────
def _field(record: Any, *names: str, default: Any = "") -> Any:
    """Read the first present attribute or mapping key.

    Callers pass dataclasses (:class:`~merilv.services.validation.ResultRow`),
    plain dicts and light view-models interchangeably, and the same value is
    spelled differently depending on which legacy file it came from — so every
    column declares its aliases and the first hit wins.
    """
    for name in names:
        if isinstance(record, Mapping):
            value = record.get(name)
        else:
            value = getattr(record, name, None)
        if value is not None:
            return value
    return default


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _humanise(value: Any) -> str:
    """Turn an internal status constant into something a warehouse lead reads.

    The engine's vocabulary is snake_case (``variance_partial``, ``near_expiry``)
    which is right for code and wrong for a printed sheet.
    """
    text = str(value or "").strip()
    return text.replace("_", " ").title() if text else ""


# ─── Validation export ───────────────────────────────────────────────────────
#: Display label paired with the aliases that supply it.
VALIDATION_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Raw Scan", ("raw_scan", "raw")),
    ("Material", ("material",)),
    ("Description", ("material_description", "description")),
    # Row 17 - the other two material-master fields, kept beside Description so
    # the four material columns read as one block. Appending them at the end
    # would have put them after Detail, a long prose column nobody scrolls past.
    ("Material Group", ("material_group",)),
    ("PH Level", ("ph_level",)),
    ("Phy. Batch", ("physical_batch", "batch")),
    ("Phy. Serial", ("physical_serial", "serial")),
    ("Scan Qty", ("scan_qty", "qty")),
    ("Consumed", ("consumed_qty",)),
    ("Sys Batch", ("system_batch",)),
    ("Sys Serial", ("system_serial",)),
    ("Available Batches", ("available_batches",)),
    ("Available Serials", ("available_serials",)),
    ("Plant", ("system_plant", "plant")),
    ("S.Loc", ("system_location", "location")),
    ("Sys Expiry", ("system_expiry", "expiry")),
    ("Stock Status", ("stock_status",)),
    ("Target S.Loc", ("target_location",)),
    ("Status", ("status",)),
    ("Detail", ("detail",)),
)

VALIDATION_HEADERS = [label for label, _ in VALIDATION_COLUMNS]
#: One width per column, in the same order. Adding a column above without a
#: width here silently shifts every width after it onto the wrong column, so
#: the two are asserted to stay the same length.
VALIDATION_WIDTHS = [
    34, 16, 34, 18, 12, 16, 20, 10, 11, 16, 20, 24, 30, 8, 10, 14, 15, 14, 17, 46,
]
assert len(VALIDATION_WIDTHS) == len(VALIDATION_HEADERS), (
    "VALIDATION_WIDTHS must have one entry per VALIDATION_COLUMNS entry - "
    f"{len(VALIDATION_WIDTHS)} widths for {len(VALIDATION_HEADERS)} columns"
)

#: Columns rendered as prose rather than as raw engine constants.
_READABLE_COLUMNS = frozenset({"Stock Status", "Status"})

#: Everything the "Expiry" tab collects — the two conventions disagree on which
#: of near/short is tighter, so both are always included alongside expired.
EXPIRY_STOCK_STATUSES = frozenset({STOCK_EXPIRED, STOCK_NEAR_EXPIRY, STOCK_SHORT_EXPIRY})


def _validation_row(row: Any) -> list[Any]:
    cells: list[Any] = []
    for label, aliases in VALIDATION_COLUMNS:
        value = _field(row, *aliases)
        # The available-batch/serial fields are lists. Writing them straight out
        # put a Python repr in the cell — ["B-REAL", "B100"] complete with
        # brackets and quotes, and a bare [] on every row that had no
        # alternatives. Joined instead, so the column reads as data and an empty
        # one reads as empty.
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value if str(item).strip())
        cells.append(_humanise(value) if label in _READABLE_COLUMNS else value)
    return cells


def _stock_status(row: Any) -> str:
    return str(_field(row, "stock_status")).strip().lower()


def _summary_rows(
    run_name: str,
    summary: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
) -> list[list[Any]]:
    """Flatten the KPI block, keeping the caller's ordering.

    A mapping preserves insertion order and a sequence of pairs is accepted for
    callers that build the KPIs incrementally; either way the run identity is
    stamped on top so a downloaded file can be traced back to its run.
    """
    pairs: list[tuple[str, Any]] = [
        ("Run", run_name),
        ("Generated", datetime.now().strftime("%d-%m-%Y %H:%M")),
    ]
    if isinstance(summary, Mapping):
        pairs.extend(summary.items())
    elif summary:
        pairs.extend((str(key), value) for key, value in summary)
    return [[str(key), value] for key, value in pairs]


def export_validation(
    run_name: str,
    rows: Sequence[Any],
    summary: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
) -> BytesIO:
    """Build the four-tab reconciliation workbook.

    The Damage and Expiry tabs are filtered views of the same rows rather than
    separate queries: the warehouse works one tab per physical action pile, and
    a row that appears on a filtered tab must be byte-identical to its entry on
    the master tab or the piles stop reconciling. Both tabs are written even when
    empty, so the workbook has the same shape every time.
    """
    results = [_validation_row(row) for row in rows]
    damage = [_validation_row(row) for row in rows if _stock_status(row) == STOCK_DAMAGE]
    expiry = [_validation_row(row) for row in rows if _stock_status(row) in EXPIRY_STOCK_STATUSES]

    return build_workbook(
        [
            SheetSpec("Validation Results", VALIDATION_HEADERS, results, VALIDATION_WIDTHS),
            SheetSpec("Damage", VALIDATION_HEADERS, damage, VALIDATION_WIDTHS),
            SheetSpec("Expiry", VALIDATION_HEADERS, expiry, VALIDATION_WIDTHS),
            SheetSpec("Summary", ["Metric", "Value"], _summary_rows(run_name, summary), [38, 26]),
        ]
    )


# ─── Outbound "Serial Format" export ─────────────────────────────────────────
#: SAP upload column names, padded to 30 characters. See the module docstring —
#: the trailing spaces are part of the field name and must not be stripped.
OUTBOUND_BASE_HEADERS: tuple[str, ...] = (
    "Delivery No                   ",
    "Item                          ",
    "Material                      ",
    "Plant                         ",
    "Location                      ",
    "Batch                         ",
    "Serial                        ",
)

#: Also padded and also verbatim — but to 28 characters, not 30. The legacy
#: template is inconsistent here and SAP matches what the template says, so the
#: odd one out is correct.
OUTBOUND_CUSTOMER_HEADER = "Customer Name               "

OUTBOUND_BOX_HEADER = "Box No."
OUTBOUND_BOX_DIM_HEADERS: tuple[str, ...] = (
    "Box L(cm)",
    "Box W(cm)",
    "Box H(cm)",
    "Phy. Wt(kg)",
    "Vol. Divisor",
    "Vol. Wt(kg)",
)
#: Appended to every outbound export, padding-free — SAP ignores the tail
#: columns and they exist for the warehouse's own audit trail.
OUTBOUND_TAIL_HEADERS: tuple[str, ...] = ("Saved Date", "Saved Time", "Scan String")

_OUTBOUND_BASE_ALIASES: tuple[tuple[str, ...], ...] = (
    ("delivery_no", "delivery"),
    ("item_no", "item"),
    ("material",),
    ("plant",),
    ("location", "storage_location", "sloc"),
    ("batch",),
    ("serial", "serial_only", "serial_identity"),
)


def _saved_stamp(scan: Any) -> tuple[str, str]:
    """Split a scan's timestamp into the separate date and time columns.

    SAP wants them apart, and the source may be a datetime, a bare date, two
    pre-split strings, or one already-formatted string depending on whether the
    scan came from the database, an offline queue or a legacy file.
    """
    stamp = _field(scan, "created_at", "saved_at", "scanned_at", "timestamp", default=None)
    if isinstance(stamp, datetime):
        naive = stamp.replace(tzinfo=None) if stamp.tzinfo else stamp
        return naive.strftime(SAVED_DATE_FORMAT), naive.strftime(SAVED_TIME_FORMAT)
    if isinstance(stamp, date):
        return stamp.strftime(SAVED_DATE_FORMAT), ""

    saved_date = str(_field(scan, "saved_date") or "")
    saved_time = str(_field(scan, "saved_time") or "")
    if saved_date or saved_time:
        return saved_date, saved_time

    text = str(stamp or "").strip()
    if not text:
        return "", ""
    parts = text.replace("T", " ").split(" ", 1)
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def _box_dimension_cells(scan: Any) -> list[Any]:
    """Carton dimensions, deriving volumetric weight when it was not stored.

    Zeros are written as blanks: an unmeasured carton and a zero-centimetre
    carton mean very different things to the freight desk.
    """
    length = _number(_field(scan, "box_length_cm", "length_cm"))
    width = _number(_field(scan, "box_width_cm", "width_cm"))
    height = _number(_field(scan, "box_height_cm", "height_cm"))
    physical = _number(_field(scan, "physical_weight_kg", "physical_weight", "weight_kg"))
    divisor = _number(_field(scan, "volumetric_divisor", "box_divisor", "divisor"))
    volumetric = _number(_field(scan, "volumetric_weight_kg", "volumetric_weight", "vol_weight"))
    if not volumetric and divisor:
        volumetric = round(length * width * height / divisor, 2)
    return [value or "" for value in (length, width, height, physical, divisor, volumetric)]


def export_outbound(
    delivery_no: str,
    scans: Sequence[Any],
    *,
    include_customer: bool = False,
    include_box: bool = False,
    include_box_dims: bool = False,
) -> BytesIO:
    """Build the SAP "Serial Format" upload workbook for one delivery.

    ``delivery_no`` is the fallback for the first column: a session may span
    several deliveries, in which case each scan carries its own number, but a
    single-delivery export should still be labelled when the scans do not.

    The optional blocks are opt-in because SAP rejects columns it was not
    configured for — they are only enabled for the entities whose upload variant
    expects them.
    """
    headers: list[str] = list(OUTBOUND_BASE_HEADERS)
    widths: list[int] = [16, 10, 18, 10, 12, 18, 24]

    if include_customer:
        headers.append(OUTBOUND_CUSTOMER_HEADER)
        widths.append(34)
    if include_box:
        headers.append(OUTBOUND_BOX_HEADER)
        widths.append(12)
    if include_box_dims:
        headers.extend(OUTBOUND_BOX_DIM_HEADERS)
        widths.extend([11, 11, 11, 13, 14, 12])

    headers.extend(OUTBOUND_TAIL_HEADERS)
    widths.extend([13, 12, 48])

    rows: list[list[Any]] = []
    for scan in scans:
        cells: list[Any] = [_field(scan, *aliases) for aliases in _OUTBOUND_BASE_ALIASES]
        cells[0] = cells[0] or delivery_no
        if include_customer:
            # The customer CODE, not the name. SAP keys on the code — a name
            # is free text that differs between the dump and the master by a
            # trailing "Ltd." or a hyphen, and the upload then matches nothing.
            # The name is kept as the fallback for a row that carries no code,
            # so an older dump still exports something rather than a blank.
            #
            # The HEADER deliberately still reads "Customer Name": SAP matches
            # the template's field names verbatim, padding included (see the
            # module docstring), so renaming it would break the upload this
            # change exists to fix.
            cells.append(
                _field(
                    scan,
                    "customer_code", "bill_to_code", "ship_to_code",
                    "customer_name", "bill_to_name", "ship_to_name",
                )
            )
        if include_box:
            cells.append(_field(scan, "box", "box_no", "box_code"))
        if include_box_dims:
            cells.extend(_box_dimension_cells(scan))
        saved_date, saved_time = _saved_stamp(scan)
        cells.extend([saved_date, saved_time, _field(scan, "raw_scan", "scan_string", "raw")])
        rows.append(cells)

    return build_workbook([SheetSpec("Serial Format", headers, rows, widths)])


# ─── Generic export ──────────────────────────────────────────────────────────
def export_generic(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> BytesIO:
    """Single-sheet export for master data, history lists and ad-hoc tables."""
    return build_workbook([SheetSpec(title, list(headers), [list(row) for row in rows])])


# ─── Filenames ───────────────────────────────────────────────────────────────
#: Names the warehouse already recognises. "Serial Format" in particular is what
#: the SAP team greps their inbox for, so it is not up for renaming.
FILENAME_TEMPLATES: dict[str, str] = {
    "outbound": "Serial Format - {identifier}.xlsx",
    "serial_format": "Serial Format - {identifier}.xlsx",
    "validation": "MerilV_Validation_{identifier}.xlsx",
    "consolidation": "MerilV_Consolidated_{identifier}.xlsx",
    "discrepancy": "MerilV_Discrepancy_{identifier}.xlsx",
    "history": "MerilV_History_{identifier}.xlsx",
    "extractor": "MerilV_Extract_{identifier}.xlsx",
    "master": "MerilV_Master_{identifier}.xlsx",
    "bt_plan": "MerilV_BT_Plan_{identifier}.xlsx",
    "bt_receipt": "MerilV_BT_Receipt_{identifier}.xlsx",
}
DEFAULT_FILENAME_TEMPLATE = "MerilV_{kind}_{identifier}.xlsx"

#: Delivery numbers and run names reach this function straight from user input,
#: so anything a filesystem or a Content-Disposition header would choke on goes.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def filename_for(kind: str, identifier: Any = "") -> str:
    """Build the download filename for an export."""
    key = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    template = FILENAME_TEMPLATES.get(key, DEFAULT_FILENAME_TEMPLATE)
    safe_kind = _ILLEGAL_FILENAME_CHARS.sub("-", str(kind or "Export")).strip() or "Export"
    safe_identifier = _ILLEGAL_FILENAME_CHARS.sub("-", str(identifier or "")).strip()
    if not safe_identifier:
        safe_identifier = datetime.now().strftime("%Y%m%d-%H%M")
    return template.format(kind=safe_kind, identifier=safe_identifier)


__all__ = [
    "AUTO_WIDTH_SAMPLE",
    "DEFAULT_FILENAME_TEMPLATE",
    "EXPIRY_STOCK_STATUSES",
    "FILENAME_TEMPLATES",
    "HEADER_COLOR",
    "MAX_GRID_CELLS",
    "OUTBOUND_BASE_HEADERS",
    "OUTBOUND_BOX_DIM_HEADERS",
    "OUTBOUND_BOX_HEADER",
    "OUTBOUND_CUSTOMER_HEADER",
    "OUTBOUND_TAIL_HEADERS",
    "VALIDATION_COLUMNS",
    "VALIDATION_HEADERS",
    "VALIDATION_WIDTHS",
    "SheetSpec",
    "build_workbook",
    "export_generic",
    "export_outbound",
    "export_validation",
    "filename_for",
]
