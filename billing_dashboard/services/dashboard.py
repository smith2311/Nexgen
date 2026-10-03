"""
billing_dashboard/services/dashboard.py

Dashboard engine — turns ExportBillingDetails into the aggregates the UI needs:
KPI cards, monthly-by-region pivot, month trend, country (world-map) totals,
subsidiary / region / LOB / document-type splits, reversals and pending.

Revenue measure = ``invoice_value_lc`` (Invoice Value in Local Currency, INR).
Rows with ``is_excluded`` (Billing_Type = F5) are always dropped, per spec.
Values are reported in Lakhs (÷100,000) for readability.
"""
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum

from ..models import ExportBillingDetails

LAKH = Decimal("100000")
CRORE = Decimal("10000000")
FY_MONTH_LABELS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
                   "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]


def _f(value):
    """Decimal → float in Lakhs, rounded to 2 dp (for charts / JSON)."""
    return float((value or Decimal("0")) / LAKH).__round__(2)


def base_queryset(filters=None):
    """Active export rows (non-F5, non-domestic), narrowed by optional filters.

    Each upload is an independent snapshot, so the dashboard is always scoped to
    a single upload via ``filters['upload']``. Without it the queryset spans every
    upload (used only where no snapshot has been chosen)."""
    qs = ExportBillingDetails.objects.filter(is_excluded=False, is_domestic=False)
    filters = filters or {}
    if filters.get("upload"):
        qs = qs.filter(upload_id=filters["upload"])
    if filters.get("fin_year"):
        qs = qs.filter(fin_year=filters["fin_year"])
    if filters.get("region"):
        qs = qs.filter(region=filters["region"])
    if filters.get("lob"):
        qs = qs.filter(lob=filters["lob"])
    if filters.get("billing_type"):
        qs = qs.filter(billing_type=filters["billing_type"])
    # Reversal handling: reversals (S1 / negative-value credit lines) are part of
    # billing and are INCLUDED by default (as the .NET logic and the tracker do).
    # This optional toggle produces a gross, net-of-reversals view on demand.
    if filters.get("exclude_reversals"):
        qs = qs.filter(is_reversal=False)
    scope = filters.get("scope")
    if scope == "subsidiary":
        qs = qs.filter(is_subsidiary=True)
    elif scope == "non_subsidiary":
        qs = qs.filter(is_subsidiary=False)
    return qs


def _distinct_clean(qs, field, reverse=False):
    """Distinct dropdown values, trimmed and de-duplicated case-insensitively so
    variants like 'West Europe' / 'west europe ' collapse into one option."""
    seen = {}
    for v in qs.values_list(field, flat=True).distinct():
        if v is None:
            continue
        label = str(v).strip()
        key = label.lower()
        if label and key not in seen:
            seen[key] = label
    return sorted(seen.values(), key=lambda s: s.lower(), reverse=reverse)


def filter_options(upload=None):
    """Dropdown values for the filter bar, scoped to the selected snapshot so the
    options always reflect the data actually shown."""
    qs = ExportBillingDetails.objects.filter(is_excluded=False, is_domestic=False)
    if upload:
        qs = qs.filter(upload_id=upload)
    return {
        "fin_years": _distinct_clean(qs, "fin_year", reverse=True),
        "regions": _distinct_clean(qs, "region"),
        "lobs": _distinct_clean(qs, "lob"),
        "doc_types": _distinct_clean(qs, "billing_type"),
    }


def _kpis(qs):
    agg = qs.aggregate(
        revenue=Sum("invoice_value_lc"),
        lines=Count("id"),
        invoices=Count("billing_doc", distinct=True),
        qty=Sum("billed_qty"),
    )
    revenue = agg["revenue"] or Decimal("0")
    sub = qs.filter(is_subsidiary=True).aggregate(v=Sum("invoice_value_lc"))["v"] or Decimal("0")
    nonsub = qs.filter(is_subsidiary=False).aggregate(v=Sum("invoice_value_lc"))["v"] or Decimal("0")
    rev = qs.filter(is_reversal=True).aggregate(v=Sum("invoice_value_lc"), c=Count("id"))
    pend = qs.filter(is_pending=True).aggregate(v=Sum("invoice_value_lc"), c=Count("id"))
    return {
        "revenue_lakhs": _f(revenue),
        "revenue_crores": float((revenue / CRORE)).__round__(2),
        "subsidiary_lakhs": _f(sub),
        "non_subsidiary_lakhs": _f(nonsub),
        "lines": agg["lines"] or 0,
        "invoices": agg["invoices"] or 0,
        "reversal_lakhs": _f(rev["v"]),
        "reversal_count": rev["c"] or 0,
        "pending_lakhs": _f(pend["v"]),
        "pending_count": pend["c"] or 0,
    }


def _split(qs, field, label_key="label"):
    rows = (qs.values(field)
              .annotate(value=Sum("invoice_value_lc"), lines=Count("id"))
              .order_by("-value"))
    return [
        {label_key: r[field] or "—", "value": _f(r["value"]), "lines": r["lines"]}
        for r in rows
    ]


def _monthly_trend(qs):
    rows = {r["fin_month_seq"]: r["v"]
            for r in qs.values("fin_month_seq").annotate(v=Sum("invoice_value_lc"))}
    return [{"month": FY_MONTH_LABELS[s - 1], "value": _f(rows.get(s))} for s in range(1, 13)]


def _fy_start_year(qs):
    """Calendar start-year of the (latest) financial year present, e.g. 2026 for
    '2026-27'. Used only to render the tracker's month labels ("1 Apr'26")."""
    years = [y for y in qs.exclude(fin_year__isnull=True)
             .values_list("fin_year", flat=True).distinct() if y]
    if not years:
        return None
    try:
        return int(max(years).split("-")[0])
    except (ValueError, IndexError):
        return None


def _month_labels(start_year):
    """12 FY-ordered month labels. With a year → tracker style "1 Apr'26" …
    "12 Mar'27"; without → plain "Apr" … "Mar"."""
    labels = []
    for i, mon in enumerate(FY_MONTH_LABELS):  # Apr … Mar
        seq = i + 1
        if start_year is None:
            labels.append(mon)
        else:
            yy = (start_year if seq <= 9 else start_year + 1) % 100
            labels.append(f"{seq} {mon}'{yy:02d}")
    return labels


def _monthly_by_region(qs):
    """Pivot matching the tracker's ``Dashborad`` sheet: region rows × 12 FY
    months, grouped into a Non-Subsidiary block and a Subsidiary block (each with
    a subtotal), plus column totals and grand total (Lakhs). Reversals are summed
    in — no ``is_reversal`` filter — exactly as the tracker does."""
    labels = _month_labels(_fy_start_year(qs))
    grid = {}  # (is_subsidiary, region) → [12 Decimals]
    for r in qs.values("is_subsidiary", "region", "fin_month_seq").annotate(v=Sum("invoice_value_lc")):
        seq = r["fin_month_seq"] or 0
        if not (1 <= seq <= 12):
            continue
        key = (bool(r["is_subsidiary"]), r["region"] or "—")
        grid.setdefault(key, [Decimal("0")] * 12)[seq - 1] += (r["v"] or Decimal("0"))

    col_totals = [Decimal("0")] * 12
    blocks = []
    for is_sub, name in [(False, "Non Subsidiary"), (True, "Subsidiary")]:
        rows, sub_totals = [], [Decimal("0")] * 12
        for region in sorted(k[1] for k in grid if k[0] == is_sub):
            cells = grid[(is_sub, region)]
            for i in range(12):
                sub_totals[i] += cells[i]
                col_totals[i] += cells[i]
            rows.append({"region": region, "months": [_f(c) for c in cells], "total": _f(sum(cells))})
        blocks.append({
            "name": name,
            "rows": rows,
            "subtotal": {"months": [_f(c) for c in sub_totals], "total": _f(sum(sub_totals))},
        })
    return {
        "labels": labels,
        "blocks": blocks,
        "col_totals": [_f(c) for c in col_totals],
        "grand_total": _f(sum(col_totals)),
    }


def _by_country(qs):
    """One row per real country. Grouped by ``country_code`` — the actual
    unique geography key — not by name, so the same country can never be
    split across rows by a stray name variant. Rows with no code at all are
    folded into a single 'Unspecified' bucket instead of being dropped."""
    coded = (qs.exclude(Q(country_code__isnull=True) | Q(country_code=""))
               .values("country_code")
               .annotate(country=Max("country"), value=Sum("invoice_value_lc"), lines=Count("id"))
               .order_by("-value"))
    result = [
        {"country": r["country"] or r["country_code"], "code": r["country_code"],
         "value": _f(r["value"]), "lines": r["lines"]}
        for r in coded
    ]
    uncoded = qs.filter(Q(country_code__isnull=True) | Q(country_code="")) \
                .exclude(Q(country__isnull=True) | Q(country=""))
    agg = uncoded.aggregate(value=Sum("invoice_value_lc"), lines=Count("id"))
    if agg["lines"]:
        result.append({"country": "Unspecified", "code": "", "value": _f(agg["value"]), "lines": agg["lines"]})
    result.sort(key=lambda r: r["value"], reverse=True)
    return result


def _by_doc_type(qs):
    rows = (qs.values("billing_type", "bill_type_descr")
              .annotate(value=Sum("invoice_value_lc"), lines=Count("id"))
              .order_by("-lines"))
    return [
        {
            "billing_type": r["billing_type"] or "—",
            "descr": r["bill_type_descr"] or "",
            "value": _f(r["value"]),
            "lines": r["lines"],
        }
        for r in rows
    ]


def dashboard_data(filters=None):
    qs = base_queryset(filters)
    return {
        "kpis": _kpis(qs),
        "monthly_trend": _monthly_trend(qs),
        "monthly_by_region": _monthly_by_region(qs),
        "by_country": _by_country(qs),
        "by_region": _split(qs, "region"),
        "by_subsidiary": _split(qs.filter(is_subsidiary=True), "subsidiary_name"),
        "by_lob": _split(qs, "lob"),
        "by_doc_type": _by_doc_type(qs),
        "has_data": qs.exists(),
    }
