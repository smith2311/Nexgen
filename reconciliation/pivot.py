"""
reconciliation/pivot.py — the filing-summary pivot.
=============================================================================
Reproduces the structure of the "Dashboard" pivot in the source workbook,
computed live from whatever is in the database:

    EntityGstin → Segment → GSTR1 → Reason Remarks → DocumentType
    values: count of documents, and the GST / E-Invoice / E-Way Bill totals
    with a subtotal band at the GSTR1, Segment and Entity levels, then a
    Grand Total.

The reference sheet itself turned out to be a stale, disconnected pivot cache —
sourced from a deleted tab with a different GSTIN and a different row count —
so only its *shape* is reused here. Every number below is computed from live
records.

SQL Server got this shape from ``GROUP BY ROLLUP``. Postgres has ROLLUP too,
but going through raw SQL would put the dashboard's filters on a second,
hand-written code path beside the one the records grid uses, and those two
drift. Instead one ordinary grouped query returns the detail rows and the
subtotal bands are folded in as the rows are walked — the same output, built
from the same queryset the rest of the module filters.

``build_pivot`` returns structured rows, not markup. The screen renders them
as HTML and the export writes them into a real .xlsx; sharing the computation
means the two can differ in styling but never in numbers.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Max, Min, Sum

ZERO = Decimal("0")

# Row kinds, in increasing order of how much they aggregate.
DETAIL = "detail"
GSTR1_TOTAL = "gstr1_total"
SEGMENT_TOTAL = "segment_total"
ENTITY_TOTAL = "entity_total"
GRAND_TOTAL = "grand_total"

# Which kinds a merged cell at each level is allowed to span. A GSTR1 label
# covers only its own detail rows; a Segment label carries on through the GSTR1
# subtotal bands beneath it; an Entity label carries on through those and the
# Segment bands too. Getting this wrong makes the vendor's name stop after its
# first subtotal instead of spanning its whole block.
_GSTR1_SPANS = (DETAIL,)
_SEGMENT_SPANS = (DETAIL, GSTR1_TOTAL)
_ENTITY_SPANS = (DETAIL, GSTR1_TOTAL, SEGMENT_TOTAL)


@dataclass
class PivotRow:
    kind: str
    entity: str = ""
    segment: str = ""
    gstr1: str = ""
    reason: str = ""
    doc_type: str = ""
    doc_count: int = 0
    gst: Decimal = ZERO
    einv: Decimal = ZERO
    ewb: Decimal = ZERO
    # 0 means "this row is covered by a merge that started above it".
    entity_span: int = 0
    segment_span: int = 0
    gstr1_span: int = 0

    @property
    def label(self):
        """The text a subtotal band shows in its merged left-hand cell."""
        if self.kind == GSTR1_TOTAL:
            return f"{self.gstr1 or '—'} Total"
        if self.kind == SEGMENT_TOTAL:
            return f"{self.segment or '—'} Total"
        if self.kind == ENTITY_TOTAL:
            return f"{self.entity or '—'} Total"
        if self.kind == GRAND_TOTAL:
            return "Grand Total"
        return ""

    @property
    def label_colspan(self):
        """How many of the five label columns the band's merged cell covers."""
        return {
            GSTR1_TOTAL: 3,
            SEGMENT_TOTAL: 4,
            ENTITY_TOTAL: 5,
            GRAND_TOTAL: 5,
        }.get(self.kind, 0)

    @property
    def label_start_column(self):
        """Which of the five label columns the band's merged cell actually
        begins in, 1-based.

        HTML works this out for itself: a row sitting under a ``rowspan`` has
        those columns already occupied, so a ``colspan`` cell in it simply
        starts at the first free one. A spreadsheet does not — writing into a
        cell another merge already owns is an error — so the same arithmetic is
        stated here, and the export reads it off.

        It is always the mirror of ``label_colspan``, because every band ends at
        column 5: a GSTR1 band sits under both the vendor and segment merges and
        starts at 3, a Segment band sits under the vendor merge and starts at 2,
        and the Entity and Grand bands start at 1 with nothing above them.
        """
        return {
            GSTR1_TOTAL: 3,
            SEGMENT_TOTAL: 2,
            ENTITY_TOTAL: 1,
            GRAND_TOTAL: 1,
        }.get(self.kind, 0)


@dataclass
class _Bucket:
    doc_count: int = 0
    gst: Decimal = field(default_factory=lambda: ZERO)
    einv: Decimal = field(default_factory=lambda: ZERO)
    ewb: Decimal = field(default_factory=lambda: ZERO)

    def add(self, doc_count, gst, einv, ewb):
        self.doc_count += doc_count
        self.gst += gst
        self.einv += einv
        self.ewb += ewb

    def reset(self):
        self.doc_count = 0
        self.gst = self.einv = self.ewb = ZERO


GROUP_FIELDS = ("EntityGstin", "Segment", "Gstr1Section", "ReasonRemark", "DocumentType")


def _s(value):
    return "" if value is None else str(value)


def _d(value):
    return ZERO if value is None else value


def pivot_groups(queryset):
    """The detail rows: one per (vendor, segment, section, remark, type)."""
    rows = (
        queryset
        .values(*GROUP_FIELDS)
        .annotate(
            doc_count=Count("id"),
            gst=Sum("GSTDocumentValue"),
            einv=Sum("EINVDocumentValue"),
            ewb=Sum("EWBDocumentValue"),
        )
    )
    # Sorted in Python so NULL and '' sort together rather than however the
    # database happens to order them — a vendor with a blank Segment should
    # not open a second block halfway down its own section.
    return sorted(
        rows,
        key=lambda row: tuple(_s(row[name]) for name in GROUP_FIELDS),
    )


def build_pivot(queryset):
    """``[PivotRow, ...]`` — detail rows with subtotal bands folded in."""
    groups = pivot_groups(queryset)
    if not groups:
        return []

    rows = []
    gstr1_bucket, segment_bucket = _Bucket(), _Bucket()
    entity_bucket, grand_bucket = _Bucket(), _Bucket()
    current = None  # (entity, segment, gstr1) of the block being accumulated

    def close(upto):
        """Emit the subtotal bands for levels 0..``upto``.

        0 is the GSTR1 band, 1 the Segment band, 2 the Entity band. They are
        always emitted innermost first, so a vendor's block ends
        "SECTION Total / SEGMENT Total / VENDOR Total" in that order.
        """
        entity, segment, gstr1 = current
        if upto >= 0:
            rows.append(PivotRow(
                kind=GSTR1_TOTAL, entity=entity, segment=segment, gstr1=gstr1,
                doc_count=gstr1_bucket.doc_count, gst=gstr1_bucket.gst,
                einv=gstr1_bucket.einv, ewb=gstr1_bucket.ewb))
            gstr1_bucket.reset()
        if upto >= 1:
            rows.append(PivotRow(
                kind=SEGMENT_TOTAL, entity=entity, segment=segment,
                doc_count=segment_bucket.doc_count, gst=segment_bucket.gst,
                einv=segment_bucket.einv, ewb=segment_bucket.ewb))
            segment_bucket.reset()
        if upto >= 2:
            rows.append(PivotRow(
                kind=ENTITY_TOTAL, entity=entity,
                doc_count=entity_bucket.doc_count, gst=entity_bucket.gst,
                einv=entity_bucket.einv, ewb=entity_bucket.ewb))
            entity_bucket.reset()

    for group in groups:
        entity = _s(group["EntityGstin"])
        segment = _s(group["Segment"])
        gstr1 = _s(group["Gstr1Section"])
        key = (entity, segment, gstr1)

        if current is not None and key != current:
            # Close outward as far as the level that actually changed: a new
            # vendor ends its segment and section blocks too, a new segment
            # ends only its sections, a new section ends only itself.
            if current[0] != entity:
                close(2)
            elif current[1] != segment:
                close(1)
            else:
                close(0)
        current = key

        doc_count = group["doc_count"]
        gst, einv, ewb = _d(group["gst"]), _d(group["einv"]), _d(group["ewb"])

        rows.append(PivotRow(
            kind=DETAIL, entity=entity, segment=segment, gstr1=gstr1,
            reason=_s(group["ReasonRemark"]), doc_type=_s(group["DocumentType"]),
            doc_count=doc_count, gst=gst, einv=einv, ewb=ewb))

        for bucket in (gstr1_bucket, segment_bucket, entity_bucket, grand_bucket):
            bucket.add(doc_count, gst, einv, ewb)

    close(2)  # the last vendor's block

    rows.append(PivotRow(
        kind=GRAND_TOTAL,
        doc_count=grand_bucket.doc_count, gst=grand_bucket.gst,
        einv=grand_bucket.einv, ewb=grand_bucket.ewb))

    compute_row_spans(rows)
    return rows


def compute_row_spans(rows):
    """Work out how far each merged label cell runs.

    Every merge starts on a detail row — a group cannot begin with its own
    subtotal — and runs until the row that closes it. The three levels stop at
    different places, which is what lets one "GST" label sit beside B2B, B2CL,
    B2CL_CDNUR and every other section under it.
    """
    levels = (
        ("gstr1_span", _GSTR1_SPANS, ("entity", "segment", "gstr1")),
        ("segment_span", _SEGMENT_SPANS, ("entity", "segment")),
        ("entity_span", _ENTITY_SPANS, ("entity",)),
    )

    for index, row in enumerate(rows):
        if row.kind != DETAIL:
            continue
        previous = rows[index - 1] if index else None

        for attribute, kinds, keys in levels:
            continues = (
                previous is not None
                and previous.kind in kinds
                and all(getattr(previous, key) == getattr(row, key) for key in keys)
            )
            if continues:
                continue  # an earlier row's merge already covers this one

            span = 0
            for candidate in rows[index:]:
                if candidate.kind not in kinds:
                    break
                if any(getattr(candidate, key) != getattr(row, key) for key in keys):
                    break
                span += 1
            setattr(row, attribute, span)


def letterheads(queryset):
    """``{gstin: {"name": ..., "period": ...}}`` for the banner above each block.

    The vendor's name is taken from whichever spelling appears on the most of
    its rows, because ``EntityName`` is free text in the export and one vendor
    can arrive spelled two ways in the same file.
    """
    names = {}
    for row in (queryset.values("EntityGstin", "EntityName")
                .annotate(n=Count("id")).order_by("EntityGstin", "-n")):
        gstin = _s(row["EntityGstin"])
        if gstin and gstin not in names and row["EntityName"]:
            names[gstin] = row["EntityName"]

    result = {}
    for row in (queryset.values("EntityGstin")
                .annotate(d_from=Min("GstDocumentDate"), d_to=Max("GstDocumentDate"))):
        gstin = _s(row["EntityGstin"])
        if not gstin:
            continue
        result[gstin] = {
            "name": names.get(gstin) or gstin,
            "period": format_period(row["d_from"], row["d_to"]),
        }
    return result


def format_period(d_from, d_to):
    """``May'26`` for one month, ``May'26 - Aug'26`` for a range."""
    if not d_from:
        return ""
    d_to = d_to or d_from
    start = d_from.strftime("%b'%y")
    end = d_to.strftime("%b'%y")
    return start if start == end else f"{start} - {end}"
