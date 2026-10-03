"""
reconciliation/exports.py — Excel output, and the preview of it.
=============================================================================
Two exports, both real .xlsx files:

* **Filing summary** — the dashboard pivot, laid out exactly as the report the
  finance team already reconciles against: a letterhead per vendor, the vendor
  / segment / section labels merged down their blocks, a coloured subtotal band
  at each level, and a Grand Total.

* **Records** — whatever the current filters show, one row per document, under
  the source workbook's own 204 headings plus the derived classification
  columns. Because every stored column keeps its original heading, this is a
  straight pass-through: a file exported here can be corrected and uploaded
  straight back.

Both can be **previewed before downloading**. The preview is HTML built from
this module's own ``PALETTE`` and the same rows the workbook writer walks, so
what the preview shows and what the file contains cannot drift into different
layouts or different colours.

── On the colours ──────────────────────────────────────────────────────────
The layout is taken from the legacy report (``GST_Filing_Summary_*.xls``),
which was an HTML table served as ``.xls``. Its palette is kept in spirit but
not literally: that file bands every GSTR-1 subtotal in pure ``#FFFF00``, and
since those are the most numerous rows in the sheet, most of the page arrives
as saturated yellow with black text on top. It is legible for one row and
exhausting for two hundred.

Here the same four levels are tinted instead of filled, and the tint gets
stronger as the subtotal gets broader — section, segment, vendor, grand — so
the hierarchy is something you can see at a glance rather than something you
have to read. Every fill stays light enough for dark text to sit on it
comfortably, which is the actual requirement: this is a document people read
across, not a chart.
"""
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import schema

# The derived columns, in the order the export writes them after the 204.
EXPORT_DERIVED = ("Presence", "ReasonRemark", "Segment", "Gstr1Section", "Health", "MaxGap")

EXPORT_COLUMNS = tuple(schema.ALL_COLUMNS) + EXPORT_DERIVED


# ── the palette, shared by the workbooks and their previews ─────────────────
#
# Fills are light; text is always the same near-black, so nothing depends on a
# viewer distinguishing two similar mid-tones. The four subtotal levels
# escalate deliberately: a section band is barely tinted, a grand total is the
# strongest thing on the sheet.

PALETTE = {
    "text":          "1E2D40",
    "text_soft":     "4A5C6E",
    "border":        "D6DEE8",
    "border_strong": "AFC0D4",

    "header":        "EEF3FA",   # the column heading row
    "letterhead":    "FFFFFF",

    "gstr1_total":   "FFF8E1",   # innermost — the lightest
    "segment_total": "FDEADC",
    "entity_total":  "E4F1DD",
    "grand_total":   "D6E8F7",   # outermost — the strongest

    # Health tints for the Reason Remark column of the records export.
    "health_ok":     "EAF6EC",
    "health_warn":   "FFF6E0",
    "health_bad":    "FDEAEA",

    "accent":        "2E6FD8",   # sheet tab
}

# CSS colours for the on-screen preview, keyed the same way.
CSS = {key: f"#{value}" for key, value in PALETTE.items()}

_THIN = Side(style="thin", color=PALETTE["border"])
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_HEAD_SIDE = Side(style="thin", color=PALETTE["border_strong"])
_HEAD_BORDER = Border(left=_THIN, right=_THIN, top=_HEAD_SIDE, bottom=_HEAD_SIDE)

PIVOT_HEADERS = (
    "EntityGstin", "Segment", "GSTR1", "Reason Remarks", "DocumentType",
    "DocumentNumber", "GST", "EINV", "EWB",
)

# Whole rupees, no decimals — matching the source report's own number style.
MONEY_FORMAT = "#,##0;-#,##0"
# The records export keeps paise: it is the file someone corrects and re-uploads.
AMOUNT_FORMAT = "#,##0.00;-#,##0.00"
COUNT_FORMAT = "#,##0"
DATE_FORMAT = "DD-MM-YYYY"

BAND_FILL = {
    "gstr1_total": PALETTE["gstr1_total"],
    "segment_total": PALETTE["segment_total"],
    "entity_total": PALETTE["entity_total"],
    "grand_total": PALETTE["grand_total"],
}

HEALTH_FILL = {
    "ok": PALETTE["health_ok"],
    "warn": PALETTE["health_warn"],
    "bad": PALETTE["health_bad"],
}

# How many rows a preview shows before it stops. Enough to judge the layout and
# spot a wrong column; not so many that the browser has to lay out the whole
# export twice.
PREVIEW_ROWS = 40


# ═══════════════════════════════════════════════════════════════════════════
#  Records export
# ═══════════════════════════════════════════════════════════════════════════

def _column_format(column):
    """The number format for one export column, or None for text."""
    if column in schema.DATE_COLUMNS:
        return DATE_FORMAT
    if column in schema.DECIMAL_COLUMNS or column == "MaxGap":
        return AMOUNT_FORMAT
    if column in schema.INT_COLUMNS:
        return COUNT_FORMAT
    return None


def _column_width(column):
    """Wide enough for the heading, wider for the fields people actually read."""
    if column in ("ReasonRemark", "EntityName", "GSTBillToTradeName",
                  "EINVBillToTradeName", "EWBBillToTradeName", "GSTBillToLegalName"):
        return 34
    if column in ("EINVIrn", "GSTIrn", "EWBIrn"):
        return 30
    if column in schema.DATE_COLUMNS:
        return 13
    if column in schema.DECIMAL_COLUMNS or column == "MaxGap":
        return 15
    return min(28, max(12, len(column) + 2))


def records_workbook(queryset):
    """A styled workbook of the filtered records.

    ``write_only`` mode: a 10,000-row export across 210 columns is two million
    cells, and building them as objects first would hold the whole sheet in
    memory before a byte is written.

    That mode is also why the number formats are set on the **columns** rather
    than the cells. Styling every cell individually costs about nine seconds
    and doubles the file; a column carries its format to every cell in it for
    free. The header row is the only row built cell by cell, because it is the
    only row that needs its own look.
    """
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Reconciliation Export")

    sheet.sheet_properties.tabColor = PALETTE["accent"]
    # The heading row stays put while you scroll 3,700 rows, and the first four
    # columns — vendor, name, document number, type — stay put while you scroll
    # 210 of them. Without this the export is a maze.
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(EXPORT_COLUMNS))}1"

    for index, column in enumerate(EXPORT_COLUMNS, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = _column_width(column)
        number_format = _column_format(column)
        if number_format:
            sheet.column_dimensions[letter].number_format = number_format

    header_font = Font(bold=True, color=PALETTE["text"], size=11)
    header_fill = PatternFill("solid", fgColor=PALETTE["header"])
    heading = []
    for column in EXPORT_COLUMNS:
        cell = WriteOnlyCell(sheet, value=column)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = _HEAD_BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=False)
        heading.append(cell)
    sheet.append(heading)

    # The one column worth tinting per row: a filing team scans Reason Remark
    # and needs Clear / Watch / Exception to separate without being read.
    remark_index = EXPORT_COLUMNS.index("ReasonRemark")
    fills = {health: PatternFill("solid", fgColor=colour)
             for health, colour in HEALTH_FILL.items()}

    for record in queryset.iterator(chunk_size=500):
        row = [getattr(record, column, None) for column in EXPORT_COLUMNS]
        fill = fills.get(record.Health)
        if fill is not None:
            cell = WriteOnlyCell(sheet, value=row[remark_index])
            cell.fill = fill
            row[remark_index] = cell
        sheet.append(row)

    return workbook


# ═══════════════════════════════════════════════════════════════════════════
#  Filing summary export
# ═══════════════════════════════════════════════════════════════════════════

def pivot_workbook(rows, letterheads, title="3Way Reco"):
    """The filing summary, laid out as the source report lays it out.

    A vendor's block opens with its own letterhead — name and period, centred
    across the table — then the column headings, then its rows, ending in the
    coloured subtotal bands. ``rows`` comes from ``pivot.build_pivot``.
    """
    from . import pivot

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.sheet_properties.tabColor = PALETTE["accent"]
    sheet.sheet_view.showGridLines = False

    widths = (20, 12, 14, 38, 14, 13, 16, 16, 16)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    line = 1
    last_entity = None

    for row in rows:
        if row.kind == pivot.DETAIL and row.entity != last_entity:
            line = _write_letterhead(sheet, line, row.entity, letterheads)
            last_entity = row.entity

        line = _write_pivot_row(sheet, line, row)

        if row.kind == pivot.ENTITY_TOTAL:
            last_entity = None  # the next vendor gets its own letterhead
            line += 1           # a blank line between vendors

    return workbook


def _write_letterhead(sheet, line, entity, letterheads):
    info = letterheads.get(entity, {})
    name = info.get("name") or entity or "—"
    period = info.get("period") or ""

    sheet.merge_cells(start_row=line, start_column=1, end_row=line, end_column=9)
    cell = sheet.cell(row=line, column=1, value=name)
    cell.font = Font(bold=True, size=16, color=PALETTE["text"])
    cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[line].height = 26
    line += 1

    sheet.merge_cells(start_row=line, start_column=1, end_row=line, end_column=9)
    cell = sheet.cell(row=line, column=1, value=f"3Way Reco M/o {period}".strip())
    cell.font = Font(bold=True, size=12, color=PALETTE["text_soft"])
    cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[line].height = 20
    line += 1

    for index, heading in enumerate(PIVOT_HEADERS, start=1):
        cell = sheet.cell(row=line, column=index, value=heading)
        cell.font = Font(bold=True, color=PALETTE["text"])
        cell.fill = PatternFill("solid", fgColor=PALETTE["header"])
        cell.border = _HEAD_BORDER
        cell.alignment = Alignment(
            horizontal="right" if index >= 6 else "left", vertical="center")
    sheet.row_dimensions[line].height = 20
    return line + 1


def _write_pivot_row(sheet, line, row):
    from . import pivot

    fill = BAND_FILL.get(row.kind)

    if row.kind == pivot.DETAIL:
        _merged_label(sheet, line, 1, row.entity, row.entity_span, bold=True)
        _merged_label(sheet, line, 2, row.segment, row.segment_span)
        _merged_label(sheet, line, 3, row.gstr1, row.gstr1_span)
        _text_cell(sheet, line, 4, row.reason)
        _text_cell(sheet, line, 5, row.doc_type)
    else:
        # Starts after whatever vertical merges from the rows above already
        # own — see PivotRow.label_start_column. Writing into one of those is
        # what makes openpyxl refuse with "MergedCell value is read-only".
        start = row.label_start_column
        end = start + row.label_colspan - 1
        if end > start:
            sheet.merge_cells(start_row=line, start_column=start,
                              end_row=line, end_column=end)
        cell = sheet.cell(row=line, column=start, value=row.label)
        cell.font = Font(bold=True, color=PALETTE["text"])
        cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in range(start, end + 1):
            target = sheet.cell(row=line, column=column)
            target.border = _BORDER
            target.fill = PatternFill("solid", fgColor=fill)

    _number_cell(sheet, line, 6, row.doc_count, fill, COUNT_FORMAT)
    _number_cell(sheet, line, 7, row.gst, fill, MONEY_FORMAT)
    _number_cell(sheet, line, 8, row.einv, fill, MONEY_FORMAT)
    _number_cell(sheet, line, 9, row.ewb, fill, MONEY_FORMAT)
    return line + 1


def _merged_label(sheet, line, column, value, span, bold=False):
    """A label cell that may be merged down over the rows it covers.

    ``span`` of 0 means an earlier row's merge already owns this cell, so
    nothing is written — writing into a merged range is what produces the
    "cannot overwrite a merged cell" errors.
    """
    if span <= 0:
        return
    if span > 1:
        sheet.merge_cells(start_row=line, start_column=column,
                          end_row=line + span - 1, end_column=column)
    cell = sheet.cell(row=line, column=column, value=value)
    cell.alignment = Alignment(vertical="center", wrap_text=False)
    cell.border = _BORDER
    cell.font = Font(bold=bold, color=PALETTE["text"])


def _text_cell(sheet, line, column, value):
    cell = sheet.cell(row=line, column=column, value=value)
    cell.border = _BORDER
    cell.alignment = Alignment(vertical="center")
    cell.font = Font(color=PALETTE["text"])


def _number_cell(sheet, line, column, value, fill, number_format):
    cell = sheet.cell(row=line, column=column, value=value)
    cell.border = _BORDER
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.number_format = number_format
    cell.font = Font(bold=bool(fill), color=PALETTE["text"])
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
