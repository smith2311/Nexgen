"""
reconciliation/previews.py — see the file before you take it.
=============================================================================
Both exports can be previewed, and the preview is the workbook: same rows, same
layout, same colours, built from ``exports.PALETTE`` rather than from a second
set of values chosen to look similar. Change a fill in ``exports.py`` and the
preview changes with it; there is no way to make one pretty and leave the other
behind.

The preview exists because both files are expensive to be wrong about. The
filing summary goes to whoever signs the return, and noticing there that a
vendor is missing is much cheaper than noticing it in their inbox. The records
export is 210 columns wide, and "did the filters actually do what I meant"
is not a question a 1.5 MB download answers quickly.

The markup is self-contained: it carries its own scoped ``<style>`` block, so
it renders the same wherever it is dropped — the preview modal, an email, a
paste into a spreadsheet — without depending on the page's stylesheet.
"""
import datetime as dt
from decimal import Decimal

from django.utils.html import escape

from . import pivot, schema
from .exports import CSS, EXPORT_COLUMNS, PREVIEW_ROWS

# ── cell chrome ─────────────────────────────────────────────────────────────
#
# Emitted once as a scoped <style> block rather than inline on every cell. The
# records preview is 40 rows across 210 columns — 8,400 cells — and repeating
# the same 180-character style string on each of them made the fragment 1.5 MB
# before this. Class names cut it by roughly twenty times.
#
# The block travels *inside* the returned HTML, not in the page's stylesheet,
# for two reasons: the fragment stays self-contained (paste it into an email or
# a spreadsheet and it keeps its look), and the values still come from
# exports.PALETTE, so the preview cannot drift from the workbook it previews.

STYLE_BLOCK = f"""<style>
.rcpv {{ border-collapse: collapse; background: #fff; }}
.rcpv td, .rcpv th {{
    padding: 6px 10px;
    border: 1px solid {CSS['border']};
    font-family: Calibri, Arial, sans-serif;
    font-size: 13px;
    color: {CSS['text']};
    white-space: nowrap;
}}
.rcpv th {{
    background: {CSS['header']};
    border-color: {CSS['border_strong']};
    font-weight: 700;
    text-align: left;
    position: sticky;
    top: 0;
    z-index: 2;
}}
.rcpv .n {{
    text-align: right;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-variant-numeric: tabular-nums;
}}
.rcpv .wrap {{ white-space: normal; }}
.rcpv .mid {{ vertical-align: middle; }}
.rcpv .b {{ font-weight: 700; }}
.rcpv .lbl {{ text-align: center; font-weight: 700; }}
.rcpv .lead td {{
    border: none;
    background: {CSS['letterhead']};
    text-align: center;
    font-weight: 700;
}}
.rcpv .lead-name td {{ font-size: 19px; padding: 18px 6px 4px; }}
.rcpv .lead-period td {{ font-size: 14px; padding: 0 6px 12px; color: {CSS['text_soft']}; }}
.rcpv .lv-gstr1 td   {{ background: {CSS['gstr1_total']};   font-weight: 700; }}
.rcpv .lv-segment td {{ background: {CSS['segment_total']}; font-weight: 700; }}
.rcpv .lv-entity td  {{ background: {CSS['entity_total']};  font-weight: 700; }}
.rcpv .lv-grand td   {{ background: {CSS['grand_total']};   font-weight: 700; }}
.rcpv .h-ok   {{ background: {CSS['health_ok']}; }}
.rcpv .h-warn {{ background: {CSS['health_warn']}; }}
.rcpv .h-bad  {{ background: {CSS['health_bad']}; }}
</style>"""

_BAND_CLASS = {
    pivot.GSTR1_TOTAL: "lv-gstr1",
    pivot.SEGMENT_TOTAL: "lv-segment",
    pivot.ENTITY_TOTAL: "lv-entity",
    pivot.GRAND_TOTAL: "lv-grand",
}

_HEALTH_CLASS = {"ok": "h-ok", "warn": "h-warn", "bad": "h-bad"}


def _group_indian(digits):
    """``123456789`` → ``12,34,56,789``: last three, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def _rupees(value):
    """Whole rupees, Indian grouping, an em dash for nothing — the number
    format the workbook's money cells carry."""
    if value is None:
        return "&mdash;"
    rounded = int(Decimal(value).to_integral_value(rounding="ROUND_HALF_UP"))
    if rounded == 0:
        return "&mdash;"
    sign = "-" if rounded < 0 else ""
    return f"{sign}{_group_indian(str(abs(rounded)))}"


def _count(value):
    try:
        return _group_indian(str(int(value)))
    except (TypeError, ValueError):
        return escape(str(value))


def _cell_text(value):
    """One record cell, formatted the way the workbook's column format shows it."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, Decimal):
        quantised = value.quantize(Decimal("0.01"))
        whole, _, fraction = str(abs(quantised)).partition(".")
        sign = "-" if quantised < 0 else ""
        return f"{sign}{_group_indian(whole)}.{fraction}"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return escape(str(value))


# ═══════════════════════════════════════════════════════════════════════════
#  Filing summary
# ═══════════════════════════════════════════════════════════════════════════

def summary_preview(rows, letterheads, limit=None):
    """``{"html": ..., "shown": n, "total": n, "truncated": bool}``.

    Cut at a vendor boundary rather than mid-block when it has to be cut: half
    a vendor with no subtotal under it is not a preview of anything, it is a
    different report.
    """
    total = len(rows)
    limit = limit or PREVIEW_ROWS * 2
    shown_rows = rows
    truncated = False

    if total > limit:
        cut = limit
        for index in range(limit, -1, -1):
            if rows[index - 1].kind == pivot.ENTITY_TOTAL:
                cut = index
                break
        shown_rows = rows[:cut] if cut else rows[:limit]
        truncated = True

    # A merge that would run past the cut has to be shortened, or the browser
    # renders a rowspan into rows that are not there and the table collapses.
    spans = _clamped_spans(shown_rows)

    parts = [STYLE_BLOCK, '<table class="rcpv" style="width:100%">']
    last_entity = None

    for index, row in enumerate(shown_rows):
        if row.kind == pivot.DETAIL and row.entity != last_entity:
            parts.append(_letterhead_html(row.entity, letterheads))
            last_entity = row.entity
        parts.append(_pivot_row_html(row, spans[index]))
        if row.kind == pivot.ENTITY_TOTAL:
            last_entity = None

    parts.append("</table>")

    return {
        "html": "".join(parts),
        "shown": len(shown_rows),
        "total": total,
        "truncated": truncated,
    }


def _clamped_spans(rows):
    """Row spans clipped to the rows actually being rendered."""
    count = len(rows)
    clamped = []
    for index, row in enumerate(rows):
        room = count - index
        clamped.append((
            min(row.entity_span, room),
            min(row.segment_span, room),
            min(row.gstr1_span, room),
        ))
    return clamped


def _letterhead_html(entity, letterheads):
    info = letterheads.get(entity, {})
    name = escape(info.get("name") or entity or "—")
    period = escape(info.get("period") or "")
    headings = ("EntityGstin", "Segment", "GSTR1", "Reason Remarks", "DocumentType",
                "DocumentNumber", "GST", "EINV", "EWB")
    return (
        f'<tr class="lead lead-name"><td colspan="9">{name}</td></tr>'
        f'<tr class="lead lead-period"><td colspan="9">3Way Reco M/o {period}</td></tr>'
        "<tr>"
        + "".join(
            f'<th class="n">{escape(h)}</th>' if i >= 5 else f"<th>{escape(h)}</th>"
            for i, h in enumerate(headings))
        + "</tr>"
    )


def _pivot_row_html(row, spans):
    entity_span, segment_span, gstr1_span = spans
    cells = []

    if row.kind == pivot.DETAIL:
        if entity_span:
            cells.append(f'<td rowspan="{entity_span}" class="mid b">'
                         f"{escape(row.entity)}</td>")
        if segment_span:
            cells.append(f'<td rowspan="{segment_span}" class="mid">'
                         f"{escape(row.segment)}</td>")
        if gstr1_span:
            cells.append(f'<td rowspan="{gstr1_span}" class="mid">'
                         f"{escape(row.gstr1)}</td>")
        cells.append(f'<td class="wrap">{escape(row.reason)}</td>')
        cells.append(f"<td>{escape(row.doc_type)}</td>")
        row_class = ""
    else:
        cells.append(f'<td colspan="{row.label_colspan}" class="lbl">'
                     f"{escape(row.label)}</td>")
        row_class = f' class="{_BAND_CLASS[row.kind]}"'

    cells.append(f'<td class="n">{_count(row.doc_count)}</td>')
    for value in (row.gst, row.einv, row.ewb):
        cells.append(f'<td class="n">{_rupees(value)}</td>')

    return f"<tr{row_class}>" + "".join(cells) + "</tr>"


# ═══════════════════════════════════════════════════════════════════════════
#  Records
# ═══════════════════════════════════════════════════════════════════════════

_RIGHT_ALIGNED = set(schema.DECIMAL_COLUMNS) | set(schema.INT_COLUMNS) | {"MaxGap"}


def _records_table(rows, read):
    """The 210-column table, for anything that can be read by column name.

    ``read(row, column)`` is the only thing that differs between a stored
    record (attributes) and a staged one (dict keys), so it is the only thing
    parameterised. The alternative — two renderers — is two places for the
    import preview and the export preview to stop looking alike.
    """
    head = "".join(
        f'<th class="n">{escape(column)}</th>' if column in _RIGHT_ALIGNED
        else f"<th>{escape(column)}</th>"
        for column in EXPORT_COLUMNS
    )

    body = []
    shown = 0
    for row in rows:
        shown += 1
        tint = _HEALTH_CLASS.get(read(row, "Health"))
        cells = []
        for column in EXPORT_COLUMNS:
            classes = []
            if column in _RIGHT_ALIGNED:
                classes.append("n")
            if column == "ReasonRemark" and tint:
                classes.append(tint)
            attribute = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{attribute}>{_cell_text(read(row, column))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    html = (
        STYLE_BLOCK
        + '<table class="rcpv">'
        + f"<thead><tr>{head}</tr></thead>"
        + f'<tbody>{"".join(body)}</tbody></table>'
    )
    return html, shown


def records_preview(queryset, total, limit=PREVIEW_ROWS):
    """The records export, as far as the first ``limit`` rows.

    All 210 columns are rendered, not a chosen few: the point of previewing a
    210-column file is to scroll across and check the columns are the ones you
    expect, which a tidy five-column summary cannot answer.
    """
    html, shown = _records_table(
        queryset[:limit], lambda row, column: getattr(row, column, None))
    return {
        "html": html,
        "shown": shown,
        "total": total,
        "columns": len(EXPORT_COLUMNS),
        "truncated": total > shown,
    }


def staged_preview(staged_rows, total, limit=PREVIEW_ROWS):
    """The same table, over rows that have been read but not yet imported.

    This is the import side of the same question: the summary table on the
    upload step shows eight columns because that is what you judge a
    *classification* on, but "did all 204 of my columns land where I think they
    did" needs all 204 of them.
    """
    html, shown = _records_table(
        staged_rows[:limit], lambda row, column: row.get(column))
    return {
        "html": html,
        "shown": shown,
        "total": total,
        "columns": len(EXPORT_COLUMNS),
        "truncated": total > shown,
    }
