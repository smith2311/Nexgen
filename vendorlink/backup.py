"""Admin-only VendorLink historical read and export.

Was "year-end backup and retention tools". Both of those are gone: the
retention purge (which deleted a completed year) and then the year-end
backup screen (which exported only the current year, a narrower duplicate
of this). What remains reads any date range and exports it — nothing here
deletes anything.
"""

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa

from .models import (
    BidAward,
    Vendor,
    VendorBid,
    VendorCommercial,
    VendorEvaluation,
    VendorKPIRecord,
    VendorRegistration,
    VendorRequest,
    VendorScore,
    VendorScorecard,
)
from .rbac import is_vendorlink_admin

admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:index")


def _window(start, end):
    """Everything VendorLink recorded between two dates, inclusive.

    One builder for both screens -- the current-year backup and the historical
    range -- so they can never disagree about the same dates.

    Each model names its own date column: registrations use ``submitted_at``,
    scorecards ``scored_at``, and KPI records ``period``, which is a DateField
    rather than a DateTimeField. Filtering them all on a single guessed column
    would silently return nothing for most of them.
    """
    requests = list(
        VendorRequest.objects
        .filter(created_at__date__gte=start, created_at__date__lte=end)
        # `award` is a reverse OneToOne, so it select_relates; the bid count is
        # annotated. Without both, the Historical Data table ran two queries
        # per row -- 41 for eleven requests.
        .select_related("raised_by", "award")
        .annotate(bid_total=Count("bids"))
        .order_by("created_at")
    )
    request_ids = [request.pk for request in requests]
    return {
        "start": start,
        "end": end,
        "requests": requests,
        "bids": list(
            VendorBid.objects.filter(vendor_request_id__in=request_ids)
            .select_related("vendor_request", "submitted_by")
        ),
        # Vendors are master data, not dated activity -- the whole list is the
        # right answer for any window.
        "vendors": list(Vendor.objects.order_by("vendor_name")),
        "registrations": list(
            VendorRegistration.objects
            .filter(submitted_at__date__gte=start, submitted_at__date__lte=end)
            .order_by("submitted_at")
        ),
        "awards": list(
            BidAward.objects.filter(vendor_request_id__in=request_ids)
            .select_related("vendor_request", "bid")
        ),
        "evaluations": list(VendorEvaluation.objects.filter(vendor_request_id__in=request_ids)),
        "scores": list(
            VendorScore.objects
            .filter(created_at__date__gte=start, created_at__date__lte=end)
            .select_related("vendor")
        ),
        "scorecards": list(
            VendorScorecard.objects
            .filter(scored_at__date__gte=start, scored_at__date__lte=end)
            .select_related("vendor")
        ),
        # `period` is already a date, so no __date lookup.
        "kpis": list(
            VendorKPIRecord.objects
            .filter(period__gte=start, period__lte=end)
            .select_related("vendor")
        ),
        "commercials": list(
            VendorCommercial.objects
            .filter(created_at__date__gte=start, created_at__date__lte=end)
            .select_related("vendor")
        ),
    }


def _requested_range(request):
    """The from/to dates for Historical Data, defaulting to the year so far.

    Bad or reversed input is corrected rather than rejected: a range filter
    that 500s or empties the page on a mistyped date is worse than one that
    shows a sane window and lets the user adjust it.
    """
    today = timezone.localdate()

    def parse(name, fallback):
        raw = (request.GET.get(name) or "").strip()
        if not raw:
            return fallback
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return fallback

    start = parse("from", date(today.year, 1, 1))
    end = parse("to", today)
    if start > end:
        start, end = end, start
    return start, end


def _text(value):
    return str(value or "")


def _excel_response(context):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    sheets = {
        "Requests": (["ID", "Subject", "Country", "Status", "Raised By", "Created At", "Recipients"], [
            [r.pk, r.subject, r.country, r.status, r.raised_by.username if r.raised_by else "", r.created_at.replace(tzinfo=None), r.recipient_count]
            for r in context["requests"]
        ]),
        "Bids": (["Request ID", "Request", "Vendor", "Bid #", "Currency", "Total", "Status", "Submitted At"], [
            [b.vendor_request_id, b.vendor_request.subject, b.submitted_by.get_full_name() or b.submitted_by.username, b.bid_number, b.currency, _bid_total(b), b.status, b.submitted_at.replace(tzinfo=None)]
            for b in context["bids"]
        ]),
        "Vendors": (["ID", "Vendor", "Country", "Contact Person", "Email", "Mobile", "Active"], [
            [v.pk, v.vendor_name, v.country, v.contact_person, v.email, v.mobile, v.is_active]
            for v in context["vendors"]
        ]),
        "Registrations": (["ID", "Company", "Country", "Contact", "Email", "Status", "Submitted At"], [
            [r.pk, r.vendor_name, r.country, r.contact_person, r.email, r.status, r.submitted_at.replace(tzinfo=None)]
            for r in context["registrations"]
        ]),
        "Awards": (["Request ID", "Vendor", "Amount", "Currency", "Status", "Awarded At"], [
            [a.vendor_request_id, a.vendor_name, a.awarded_amount, a.awarded_currency, a.status, a.awarded_at.replace(tzinfo=None)]
            for a in context["awards"]
        ]),
    }
    for title, (headers, rows) in sheets.items():
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
            cell.fill = openpyxl.styles.PatternFill("solid", fgColor="0076C8")
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(max(max(len(_text(c.value)) for c in column) + 2, 12), 42)
    output = io.BytesIO()
    workbook.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    stem = context.get("label") or "export"
    response["Content-Disposition"] = f'attachment; filename="vendorlink-{stem}.xlsx"'
    return response


def _bid_total(bid):
    return sum((getattr(bid, field) or Decimal("0")) for field in (
        "storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
        "packing_cost", "last_mile_charges", "manpower_charges",
    ))


def _pdf_response(context, template="vendorlink/year_end_backup_pdf.html"):
    html = render_to_string(template, context)
    output = io.BytesIO()
    result = pisa.CreatePDF(io.BytesIO(html.encode("utf-8")), dest=output)
    if result.err:
        return HttpResponse("Could not create the PDF.", status=500)
    stem = context.get("label") or "export"
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="vendorlink-{stem}.pdf"'
    return response


@admin_required
def historical_data(request):
    """Read VendorLink's past over any date range.

    The read-only replacement for deleting a completed year. Nothing on this
    screen mutates: it exists so old requests, bids, awards and evaluations
    stay queryable instead of being purged once they are a year old.
    """
    start, end = _requested_range(request)
    context = _window(start, end)
    context["label"] = f"{start.isoformat()}_to_{end.isoformat()}"
    return render(request, "vendorlink/historical_data.html", context)


@admin_required
def historical_data_excel(request):
    start, end = _requested_range(request)
    context = _window(start, end)
    context["label"] = f"{start.isoformat()}_to_{end.isoformat()}"
    return _excel_response(context)


@admin_required
def historical_data_pdf(request):
    start, end = _requested_range(request)
    context = _window(start, end)
    context["label"] = f"{start.isoformat()}_to_{end.isoformat()}"
    return _pdf_response(context, template="vendorlink/year_end_backup_pdf.html")
