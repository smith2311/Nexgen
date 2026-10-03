"""KPI Dashboard + Vendor Scorecard — VendorLink Phase 4.

Two SOW sheets that share one access model (Global SCM enters both, never the
vendor) and one launcher-adjacent purpose ("Performance monitoring &
analytics" / "Weighted evaluation & vendor ranking"), so they live in one
module file rather than forking commercial.py's pattern into two more files.

KPI Dashboard: 8 metrics per vendor per month (the SOW's "KPI Dashboard"
sheet). Storage grain is monthly; a quarterly view is a rollup over 3 months
rather than a second row shape.

Vendor Scorecard: Cost 20% / Serviceability 40% / WMS-Reports 20% / TAT 20%,
each scored out of 10 with a mandatory comment, rolling up to a Final Score.
Kept entirely separate from the pre-existing VendorScore model that
ai_evaluator.py reads — this is a new, additional model, not a replacement.
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .constants import KPI_METRIC_FIELDS, SCORECARD_ATTRIBUTES
from .forms import VendorKPIRecordForm, VendorScorecardForm
from .models import Vendor, VendorKPIRecord, VendorScorecard
from .rbac import can_access_vendorlink, can_edit_vendorlink, is_vendorlink_admin

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
edit_required = user_passes_test(can_edit_vendorlink, login_url="vendorlink:kpi_dashboard")
admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:kpi_dashboard")

#: How many trailing months the dashboard trend chart plots by default.
TREND_MONTHS = 12


def _ctx(request, **extra):
    base = {
        "is_vm_admin": is_vendorlink_admin(request.user),
        "can_edit": can_edit_vendorlink(request.user),
    }
    base.update(extra)
    return base


def _month_labels(n):
    """The last n months as (date-of-1st, 'Mon YYYY') pairs, oldest first."""
    today = date.today().replace(day=1)
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()
    return months


# ── KPI Dashboard ─────────────────────────────────────────────────────────────
@login_required
@access_required
def kpi_dashboard(request):
    """Fleet-wide KPI overview, or one vendor's trend when ?vendor= is set."""
    vendor_id = request.GET.get("vendor", "").strip()
    focus_vendor = None
    if vendor_id.isdigit():
        focus_vendor = Vendor.objects.filter(pk=vendor_id).first()

    months = _month_labels(TREND_MONTHS)
    month_labels = [d.strftime("%b %Y") for d in months]

    records_qs = VendorKPIRecord.objects.filter(period__gte=months[0]).select_related("vendor")
    if focus_vendor:
        records_qs = records_qs.filter(vendor=focus_vendor)
    records = list(records_qs)

    # One series per metric: month -> [values recorded that month], averaged.
    series = {}
    by_month = defaultdict(lambda: defaultdict(list))
    for rec in records:
        for name, _label, _unit in KPI_METRIC_FIELDS:
            value = getattr(rec, name)
            if value is not None:
                by_month[name][rec.period].append(float(value))
    for name, _label, _unit in KPI_METRIC_FIELDS:
        series[name] = [
            round(sum(by_month[name][m]) / len(by_month[name][m]), 2) if by_month[name].get(m) else None
            for m in months
        ]

    # "Latest per vendor" table — the state a Global SCM analyst actually
    # wants to eyeball, not a flat log of every record ever entered.
    vendors = Vendor.objects.filter(is_active=True).order_by("vendor_name")
    if focus_vendor:
        vendors = vendors.filter(pk=focus_vendor.pk)
    latest_rows = [
        {"vendor": v, "record": v.latest_kpi_record}
        for v in vendors
    ]
    if not focus_vendor:
        latest_rows = [row for row in latest_rows if row["record"]]

    metric_meta = {name: {"label": label, "unit": unit} for name, label, unit in KPI_METRIC_FIELDS}

    return render(request, "vendorlink/kpi/dashboard.html", _ctx(
        request,
        focus_vendor=focus_vendor,
        all_vendors=Vendor.objects.filter(is_active=True).order_by("vendor_name"),
        metric_fields=KPI_METRIC_FIELDS,
        month_labels_json=json.dumps(month_labels),
        series_json=json.dumps(series),
        metric_meta_json=json.dumps(metric_meta),
        latest_rows=latest_rows,
        total_records=VendorKPIRecord.objects.count(),
        vendors_tracked=VendorKPIRecord.objects.values("vendor").distinct().count(),
    ))


@login_required
@edit_required
def kpi_record_create(request):
    initial = {}
    vendor_id = request.GET.get("vendor")
    if vendor_id:
        initial["vendor"] = vendor_id

    form = VendorKPIRecordForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.recorded_by = request.user
        try:
            # Wrapped in its own savepoint: Postgres poisons the whole request
            # transaction after an uncaught IntegrityError, so a bare
            # try/except around .save() would leave every later query in this
            # request failing with "current transaction is aborted". The two
            # collision paths this guards are day-of-month normalisation
            # (June 15 vs June 20 both -> June 1, which the form's own
            # validate_unique() cannot see since it compares raw posted
            # values) and a genuine concurrent double-submit.
            with transaction.atomic():
                record.save()
        except IntegrityError:
            form.add_error(None, "A KPI record for this vendor and month already exists — edit it instead.")
        else:
            messages.success(request, f"KPI record saved for {record.vendor.vendor_name} — {record.period:%b %Y}.")
            return redirect(f"{reverse('vendorlink:kpi_dashboard')}?vendor={record.vendor_id}")

    return render(request, "vendorlink/kpi/form.html", _ctx(request, form=form, mode="create"))


@login_required
@edit_required
def kpi_record_edit(request, pk):
    record = get_object_or_404(VendorKPIRecord, pk=pk)
    form = VendorKPIRecordForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"KPI record updated for {record.vendor.vendor_name} — {record.period:%b %Y}.")
        return redirect(f"{reverse('vendorlink:kpi_dashboard')}?vendor={record.vendor_id}")

    return render(request, "vendorlink/kpi/form.html", _ctx(request, form=form, mode="edit", record=record))


@login_required
@admin_required
@require_POST
def kpi_record_delete(request, pk):
    record = get_object_or_404(VendorKPIRecord, pk=pk)
    vendor_id, name, period = record.vendor_id, record.vendor.vendor_name, record.period
    record.delete()
    messages.success(request, f"KPI record deleted for {name} — {period:%b %Y}.")
    return redirect(f"{reverse('vendorlink:kpi_dashboard')}?vendor={vendor_id}")


@login_required
@access_required
def kpi_export(request):
    records = VendorKPIRecord.objects.select_related("vendor").order_by("vendor__vendor_name", "-period")
    vendor_id = request.GET.get("vendor", "").strip()
    if vendor_id.isdigit():
        records = records.filter(vendor_id=vendor_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "KPI Dashboard"
    headers = ["Vendor Name", "Period"] + [label for _, label, _ in KPI_METRIC_FIELDS] + ["Currency", "Notes"]
    ws.append(headers)
    fill = PatternFill("solid", fgColor="0076C8")
    font = Font(bold=True, color="FFFFFF", size=11)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font, cell.fill = font, fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    for rec in records:
        ws.append(
            [rec.vendor.vendor_name, rec.period]
            + [getattr(rec, name) for name, _label, _unit in KPI_METRIC_FIELDS]
            + [rec.currency, rec.notes]
        )
    for index, width in enumerate([28, 12] + [16] * len(KPI_METRIC_FIELDS) + [10, 30], start=1):
        ws.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="kpi_dashboard.xlsx"'
    return response


# ── Vendor Scorecard ─────────────────────────────────────────────────────────
@login_required
@access_required
def scorecard_list(request):
    query = request.GET.get("q", "").strip()
    vendors = Vendor.objects.filter(is_active=True)
    if query:
        vendors = vendors.filter(Q(vendor_name__icontains=query) | Q(country__icontains=query))

    rows = []
    for v in vendors:
        card = v.current_scorecard
        if card:
            rows.append({"vendor": v, "card": card})
    rows.sort(key=lambda r: r["card"].final_score, reverse=True)

    return render(request, "vendorlink/scorecard/list.html", _ctx(
        request,
        rows=rows,
        top10=rows[:10],
        query=query,
        scored_count=len(rows),
        total_vendors=Vendor.objects.filter(is_active=True).count(),
        attributes=SCORECARD_ATTRIBUTES,
    ))


@login_required
@edit_required
def scorecard_create(request):
    initial = {}
    vendor_id = request.GET.get("vendor")
    if vendor_id:
        initial["vendor"] = vendor_id

    form = VendorScorecardForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        card = form.save(commit=False)
        card.scored_by = request.user
        card.save()
        messages.success(request, f"{card.vendor.vendor_name} scored — Final Score {card.final_score}/10.")
        return redirect("vendorlink:scorecard_vendor_history", vendor_pk=card.vendor_id)

    return render(request, "vendorlink/scorecard/form.html", _ctx(request, form=form))


@login_required
@access_required
def scorecard_vendor_history(request, vendor_pk):
    vendor = get_object_or_404(Vendor, pk=vendor_pk)
    cards = vendor.scorecards.order_by("-scored_at")
    return render(request, "vendorlink/scorecard/vendor_history.html", _ctx(
        request, vendor=vendor, cards=cards, current=cards.first(),
    ))


@login_required
@admin_required
@require_POST
def scorecard_delete(request, pk):
    card = get_object_or_404(VendorScorecard, pk=pk)
    vendor_id, name = card.vendor_id, card.vendor.vendor_name
    card.delete()
    messages.success(request, f"Scorecard entry removed for {name}.")
    return redirect("vendorlink:scorecard_vendor_history", vendor_pk=vendor_id)
