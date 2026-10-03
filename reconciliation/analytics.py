"""
reconciliation/analytics.py — what the filing summary's charts are made of.
=============================================================================
One function, ``build``, turning the same filtered queryset the pivot uses into
the numbers behind the dashboard's tiles, charts, insights and vendor table.

It lives apart from views.py because it is arithmetic, not HTTP: every figure
here is reproducible from a queryset alone, which is what makes it testable and
what stops the charts and the pivot below them from disagreeing.

Two rules run through all of it:

* **Always ``.order_by()`` before ``.values().annotate()``.** Django folds a
  queryset's ordering columns into the GROUP BY, so aggregating over an ordered
  queryset silently groups by ``id`` — one group per record, every count 1.
  That shipped once already; it will not ship twice.

* **Money is taxable value.** The headline tile sums
  ``GSTDocumentTaxableValue`` because that is the figure reconciliation
  compares — the business team reads the summary in taxable terms, so the tile
  reads in the same terms. ``MaxGap`` is also taxable by construction. (The
  pivot below still prints document value: it is the published filing-summary
  layout and the Grand Total has to tie out to that sheet, not to the tile.)
"""
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth

ZERO = Decimal("0")

# How many bars each ranked chart shows before the tail is folded away. Past
# this a horizontal bar chart is a list with extra steps.
TOP_REASONS = 8
TOP_SECTIONS = 8
TOP_VENDORS = 15

HEALTH_ORDER = ("ok", "warn", "bad")
HEALTH_LABEL = {"ok": "Clear", "warn": "Watch", "bad": "Exception"}


def _money(value):
    return float(value or ZERO)


def _percent(part, whole):
    return round(part / whole * 100, 1) if whole else 0.0


def kpis(queryset):
    """The six headline figures."""
    totals = queryset.order_by().aggregate(
        documents=Count("id"),
        gst_value=Sum("GSTDocumentTaxableValue"),
        gap_exposure=Sum("MaxGap"),
        vendors=Count("EntityGstin", distinct=True),
        ok=Count("id", filter=Q(Health="ok")),
        warn=Count("id", filter=Q(Health="warn")),
        bad=Count("id", filter=Q(Health="bad")),
    )
    documents = totals["documents"] or 0
    return {
        "documents": documents,
        "vendors": totals["vendors"] or 0,
        "ok": totals["ok"] or 0,
        "warn": totals["warn"] or 0,
        "bad": totals["bad"] or 0,
        "ok_pct": _percent(totals["ok"] or 0, documents),
        "bad_pct": _percent(totals["bad"] or 0, documents),
        "gst_value": _money(totals["gst_value"]),
        "gap_exposure": _money(totals["gap_exposure"]),
    }


def trend(queryset):
    """Documents per month, split Clear / Watch / Exception.

    A stacked bar rather than three lines. Document volume and exception volume
    differ by two orders of magnitude, so plotting them as separate lines on one
    axis flattens the exceptions into the baseline — and the alternative, a
    second y-axis, is the one chart form never worth using. Stacking keeps a
    single axis, puts the total back as the bar's height, and makes the
    exception band directly comparable month to month.

    The month comes from the GST date, falling back to the e-invoice and then
    the e-way bill date, so a document that never reached GSTR-1 still lands in
    the month it happened rather than vanishing from the trend.
    """
    rows = (
        queryset.order_by()
        .annotate(month=TruncMonth(Coalesce(
            "GstDocumentDate", "EINVDocumentDate", "EWBDocumentDate")))
        .values("month", "Health")
        .annotate(n=Count("id"))
    )

    months = {}
    for row in rows:
        if row["month"] is None:
            continue
        bucket = months.setdefault(row["month"], {"ok": 0, "warn": 0, "bad": 0})
        if row["Health"] in bucket:
            bucket[row["Health"]] += row["n"]

    return [
        {
            "month": month.strftime("%Y-%m"),
            "label": month.strftime("%b '%y"),
            "ok": counts["ok"],
            "warn": counts["warn"],
            "bad": counts["bad"],
            "total": counts["ok"] + counts["warn"] + counts["bad"],
        }
        for month, counts in sorted(months.items())
    ]


def _ranked(queryset, column, limit, label_for=None):
    """Top ``limit`` values of ``column`` by document count, tail folded in.

    The tail becomes one "Other" bar rather than being dropped: a chart that
    silently omits rows invites the reader to add the bars up and get a
    different total from the tile above them.
    """
    rows = list(
        queryset.order_by()
        .exclude(**{f"{column}__isnull": True}).exclude(**{column: ""})
        .values(column)
        .annotate(n=Count("id"))
        .order_by("-n", column)
    )
    head = rows[:limit]
    tail = rows[limit:]

    out = [{"label": label_for(r[column]) if label_for else r[column], "count": r["n"]}
           for r in head]
    if tail:
        out.append({"label": f"Other ({len(tail)})",
                    "count": sum(r["n"] for r in tail), "other": True})
    return out


def reasons(queryset):
    """The reason remarks carrying the most documents, worst health first.

    Each bar carries the health it mostly represents, so the chart can be read
    against the tiles without a second query.
    """
    ranked = _ranked(queryset, "ReasonRemark", TOP_REASONS)
    health_by_reason = {
        row["ReasonRemark"]: row["Health"]
        for row in queryset.order_by().values("ReasonRemark", "Health")
        .annotate(n=Count("id")).order_by("ReasonRemark", "-n")
    }
    for item in ranked:
        item["health"] = health_by_reason.get(item["label"], "ok") if not item.get("other") else "ok"
    return ranked


def presence(queryset):
    """The G / E / W footprint — which combinations of sources documents sit in."""
    return _ranked(queryset, "Presence", 8)


def sections(queryset):
    """GSTR-1 section mix: B2B, B2C, exports, notes and the exception buckets."""
    return _ranked(queryset, "Gstr1Section", TOP_SECTIONS)


def vendors(queryset):
    """Per-vendor health, worst exception count first."""
    rows = (
        queryset.order_by()
        .exclude(EntityGstin__isnull=True).exclude(EntityGstin="")
        .values("EntityGstin")
        .annotate(
            docs=Count("id"),
            ok=Count("id", filter=Q(Health="ok")),
            bad=Count("id", filter=Q(Health="bad")),
            gst_value=Sum("GSTDocumentTaxableValue"),
        )
        .order_by("-bad", "-docs")[:TOP_VENDORS]
    )

    names = {}
    for row in (queryset.order_by().values("EntityGstin", "EntityName")
                .annotate(n=Count("id")).order_by("EntityGstin", "-n")):
        gstin = row["EntityGstin"]
        if gstin and gstin not in names and row["EntityName"]:
            names[gstin] = row["EntityName"]

    return [
        {
            "gstin": row["EntityGstin"],
            "name": names.get(row["EntityGstin"]) or row["EntityGstin"],
            "docs": row["docs"],
            "ok_pct": _percent(row["ok"], row["docs"]),
            "bad": row["bad"],
            "gst_value": _money(row["gst_value"]),
        }
        for row in rows
    ]


def insights(totals, reason_rows, vendor_rows, trend_rows):
    """Short, specific sentences about what the current filters actually show.

    Written from the numbers already computed rather than from fresh queries, so
    an insight can never contradict the chart beside it.
    """
    out = []
    if not totals["documents"]:
        return [{
            "tag": "Nothing to show",
            "headline": "No documents match the current scope.",
            "detail": "Widen the vendor or date filters, or clear them entirely.",
            "tone": "idle",
        }]

    out.append({
        "tag": "Overall health",
        "headline": (f"{totals['ok_pct']}% clear · {totals['bad']:,} exception"
                     f"{'' if totals['bad'] == 1 else 's'} · {totals['warn']:,} to watch"),
        "detail": f"Across {totals['documents']:,} documents from "
                  f"{totals['vendors']:,} vendor{'' if totals['vendors'] == 1 else 's'}.",
        "tone": "ok" if not totals["bad"] else ("warn" if totals["bad_pct"] < 5 else "bad"),
    })

    worst = next((r for r in reason_rows if r.get("health") == "bad"), None)
    if worst:
        out.append({
            "tag": "Largest exception reason",
            "headline": f"{worst['label']} — {worst['count']:,} document"
                        f"{'' if worst['count'] == 1 else 's'}",
            "detail": "Filter the records grid by this remark to work the queue.",
            "tone": "bad",
        })

    top_vendor = next((v for v in vendor_rows if v["bad"]), None)
    if top_vendor:
        out.append({
            "tag": "Vendor concentration",
            "headline": f"{top_vendor['name']} carries {top_vendor['bad']:,} of them",
            "detail": f"{top_vendor['ok_pct']}% of that vendor's "
                      f"{top_vendor['docs']:,} documents are clear.",
            "tone": "warn",
        })

    if totals["gap_exposure"]:
        out.append({
            "tag": "Value at risk",
            "headline": f"Rs {totals['gap_exposure']:,.0f} of taxable value disagrees",
            "detail": "Summed across every document whose sources report "
                      "different taxable figures.",
            "tone": "warn",
        })
    elif len(trend_rows) > 1:
        out.append({
            "tag": "Value at risk",
            "headline": "Every document's sources agree on taxable value",
            "detail": "No taxable disagreement anywhere in this scope.",
            "tone": "ok",
        })

    return out


def build(queryset):
    """Everything the dashboard's analytics half needs, in one payload."""
    totals = kpis(queryset)
    reason_rows = reasons(queryset)
    vendor_rows = vendors(queryset)
    trend_rows = trend(queryset)

    return {
        "kpis": totals,
        "trend": trend_rows,
        "reasons": reason_rows,
        "presence": presence(queryset),
        "sections": sections(queryset),
        "vendors": vendor_rows,
        "insights": insights(totals, reason_rows, vendor_rows, trend_rows),
        "health_order": list(HEALTH_ORDER),
        "health_labels": HEALTH_LABEL,
    }
