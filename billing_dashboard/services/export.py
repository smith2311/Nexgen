"""
billing_dashboard/services/export.py

"Download All Export" — streams the (optionally filtered) billing rows back to
Excel. Emits the promoted columns first, then every remaining column captured
in ``raw`` so the export is a faithful, complete copy of the stored data.
"""
from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill


def _month_cell(o):
    """Tracker's MONTH format, e.g. "1 Apr'26" (seq + month label + 2-digit
    calendar year of that month)."""
    if not o.fin_month_seq or not o.fin_month_label:
        return ""
    yy = (o.billing_date.year % 100) if o.billing_date else 0
    return f"{o.fin_month_seq} {o.fin_month_label}'{yy:02d}"


def _raw(key):
    return lambda o: (o.raw or {}).get(key, "")


# PH Level 01 family code → the 3 reporting buckets the stakeholder reviews.
# Checked first because the granular ``lob`` value is frequently just the raw
# SAP Product-Hierarchy abbreviation (e.g. "SynAb", "MCS", "Mes") when the SKU
# isn't in the LOB master — that text never contains "suture"/"mechanical"/
# "biosurg", so the substring check below alone would miss it.
_PH1_FAMILY_BUCKETS = {
    "sut": "Sutures",
    "surgical suture": "Sutures",
    "mcs": "Mechanical",
    "mes": "Mechanical",
    "mechanical device": "Mechanical",
    "bio": "Biosurgery",
}


def _lob_bucket(o):
    """Biosurgery/Sutures/Mechanical stay as the 3 named reporting lines;
    anything that doesn't match one of those families shows its actual
    granular LOB value instead of a generic "Other" catch-all."""
    ph1 = (o.ph_level01_desc or "").strip().lower()
    bucket = _PH1_FAMILY_BUCKETS.get(ph1)
    if bucket:
        return bucket
    s = (o.lob or "").strip().lower()
    if "biosurg" in s:
        return "Biosurgery"
    if "suture" in s:
        return "Sutures"
    if "mechanical" in s:
        return "Mechanical"
    return o.lob or "Unclassified"


# The refined "Data" sheet — exactly the 30 columns (and order) the stakeholder
# reviews. Each entry is (header label, accessor) where accessor is a model
# field name or a callable(obj) → value.
_PROMOTED = [
    ("Sales Org.", "sales_org"),
    ("Billing Doc.", "billing_doc"),
    ("Billing Date", "billing_date"),
    ("Billing Type", "billing_type"),
    ("SO/STO Itm", "so_sto_item"),
    ("SO/STO Dt", "so_sto_date"),
    ("Sold-to party", "sold_to_party"),
    ("Soldto-Name", "sold_to_name"),
    ("Billing Category", "billing_category"),
    ("Material", "material"),
    ("Description", "description"),
    ("Sales unit", "sales_unit"),
    ("Billed qty", "billed_qty"),
    ("Plant", "plant"),
    ("Invoice Value in LC", "invoice_value_lc"),
    ("Distr. Channel", "distr_channel"),
    ("Document cat.", "document_category"),
    ("Payer-country", "payer_country"),
    ("SO/STO", "so_sto"),
    ("Batch", _raw("batch")),
    ("Material Group", "material_group"),
    ("Plant Region", "plant_region"),
    ("Exch.rate-acct.", "exchange_rate"),
    ("Country", "country"),
    ("Subs/Non Sub.", lambda o: "Subsidiary" if o.is_subsidiary else "Non Subsidiary"),
    ("Region", "region"),
    ("LOB", _lob_bucket),
    ("VALUE IN LAKH", "value_lakhs"),
    ("MONTH", _month_cell),
    ("Push Report - Devision", "push_division"),
]

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def _cell_value(obj, accessor):
    val = accessor(obj) if callable(accessor) else getattr(obj, accessor)
    if val is None:
        return ""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, bool):
        return "Yes" if val else "No"
    return val


def build_workbook(queryset):
    """Return a BytesIO .xlsx of the queryset (promoted cols + all raw cols)."""
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Data")

    # Discover the union of raw keys (extra SAP columns not promoted).
    raw_keys = []
    seen = set()
    for raw in queryset.values_list("raw", flat=True).iterator(chunk_size=2000):
        for k in (raw or {}):
            if k not in seen:
                seen.add(k)
                raw_keys.append(k)

    headers = [h for h, _ in _PROMOTED] + [f"raw:{k}" for k in raw_keys]
    header_cells = []
    for h in headers:
        c = openpyxl.cell.WriteOnlyCell(ws, value=h)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = Alignment(horizontal="center")
        header_cells.append(c)
    ws.append(header_cells)

    for obj in queryset.iterator(chunk_size=2000):
        row = [_cell_value(obj, field) for _, field in _PROMOTED]
        raw = obj.raw or {}
        row.extend(raw.get(k, "") for k in raw_keys)
        ws.append(row)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
