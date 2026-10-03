"""
reconciliation/views.py — GST / E-Invoice / E-Way Bill reconciliation.
=============================================================================
Three screens, and the JSON endpoints behind them:

* **Records** — upload an export, preview what it says, import it, then filter,
  sort, correct and export the documents.
* **Dashboard** — the filing summary pivot, per vendor.
* **Billing Type Master** — the 53 rules the classifier judges rows against.

The pages are server-rendered shells; the grid, the upload progress and the
edit drawer talk to the endpoints below. That split is what makes a
10,000-row import bearable: the page never re-renders the table to show a
different page of it, and the progress bar shows counts from the work actually
happening rather than a spinner.

Every write is POST and CSRF-protected, and every endpoint re-checks the role —
hiding a button is a courtesy to the user, not a permission.
"""
import json
import os
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import analytics, exports, filters, importer, parsing, pivot, previews, roles, schema
from .classify import (AMOUNT_TOLERANCE, SOURCE_LABELS, SOURCES, classify_record,
                       explain, explain_record, load_masters)
from .models import (ReconBillingType, ReconImportJob, ReconRecord,
                     ReconSalesType, quantize_decimal)

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
PREVIEW_PAGE_SIZE = 25

# There is one way to change a record: the edit drawer, which walks
# schema.ALL_COLUMNS, parses every field the way an import parses it, and
# re-classifies afterwards. A second, lighter "just the six columns the grid
# shows" endpoint is tempting and was tried; it is also a second place for the
# parse-and-reclassify rules to drift, for the sake of saving a request.


# ── guards ───────────────────────────────────────────────────────────────────

def _require(user, check):
    if not check(user):
        raise PermissionDenied("Your role does not allow this action.")


def _own_job(user, pk):
    """A job, if it is this user's to look at.

    Scoped to the person who started it rather than to the module role: a
    staged file is one operator's work in progress, and two people watching the
    same stage would each be able to confirm the other's upload.
    """
    job = get_object_or_404(ReconImportJob, pk=pk)
    if job.created_by_id and job.created_by_id != user.pk and not roles.is_admin(user):
        raise PermissionDenied("That import belongs to someone else.")
    return job


def _ok(**payload):
    payload.setdefault("ok", True)
    return JsonResponse(payload)


def _error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


# ── serialisation ────────────────────────────────────────────────────────────

def _money(value):
    """A number the browser can format, or None. Sent as a float rather than a
    Decimal string because the grid right-aligns and groups it in the page;
    the authoritative value stays NUMERIC in the database."""
    if value is None:
        return None
    return float(value)


def _row(record):
    return {
        "id": record.pk,
        "DocumentNumber": record.DocumentNumber,
        "DocumentType": record.DocumentType,
        "EntityGstin": record.EntityGstin,
        "party": record.display_party,
        "GstDocumentDate": record.GstDocumentDate.isoformat() if record.GstDocumentDate else None,
        # Taxable value: what reconciliation compares, so what the grid shows.
        # The document values ride along for the detail modal, which shows both.
        "gst": _money(record.GSTDocumentTaxableValue),
        "einv": _money(record.EINVDocumentTaxableValue),
        "ewb": _money(record.EWBDocumentTaxableValue),
        "GSTDocumentValue": _money(record.GSTDocumentValue),
        "EINVDocumentValue": _money(record.EINVDocumentValue),
        "EWBDocumentValue": _money(record.EWBDocumentValue),
        "Presence": record.Presence or "---",
        "ReasonRemark": record.ReasonRemark or "",
        "Segment": record.Segment or "",
        "Gstr1Section": record.Gstr1Section or "",
        "Health": record.Health or "idle",
        "MaxGap": _money(record.MaxGap),
        "ManualReason": record.ManualReason,
        "IsDeleted": record.IsDeleted,
    }


def _staged_row(values):
    """One staged row, shaped like a stored one so the preview table and the
    records table are the same component."""
    def party():
        for column in ("EINVBillToTradeName", "EWBBillToTradeName",
                       "GSTBillToTradeName", "GSTBillToLegalName"):
            value = values.get(column)
            if value:
                return value
        return ""

    date = values.get("GstDocumentDate")
    return {
        "id": None,
        "DocumentNumber": values.get("DocumentNumber"),
        "DocumentType": values.get("DocumentType"),
        "EntityGstin": values.get("EntityGstin"),
        "party": party(),
        "GstDocumentDate": date.isoformat() if date else None,
        "gst": _money(values.get("GSTDocumentTaxableValue")),
        "einv": _money(values.get("EINVDocumentTaxableValue")),
        "ewb": _money(values.get("EWBDocumentTaxableValue")),
        "GSTDocumentValue": _money(values.get("GSTDocumentValue")),
        "EINVDocumentValue": _money(values.get("EINVDocumentValue")),
        "EWBDocumentValue": _money(values.get("EWBDocumentValue")),
        "Presence": values.get("Presence") or "---",
        "ReasonRemark": values.get("ReasonRemark") or "",
        "Segment": values.get("Segment") or "",
        "Gstr1Section": values.get("Gstr1Section") or "",
        "Health": values.get("Health") or "idle",
        "MaxGap": _money(values.get("MaxGap")),
        "ManualReason": False,
        "IsDeleted": False,
    }


def _job_payload(job):
    return {
        "id": job.pk,
        "kind": job.kind,
        "state": job.state,
        "mode": job.mode,
        "phase": job.phase,
        "done": job.done,
        "total": job.total,
        "percent": job.percent,
        "message": job.message,
        "filename": job.filename,
        "row_count": job.row_count,
        "matched_headers": job.matched_headers,
        "known_columns": len(schema.ALL_COLUMNS),
        "unclassified": job.unclassified,
        "inserted": job.inserted,
        "updated": job.updated,
        "skipped_manual": job.skipped_manual,
        "has_stage": bool(job.stage_path),
    }


def _format_for_edit(value):
    """The same formatting the grid uses, so a value round-trips through the
    edit drawer unchanged when nobody touches it."""
    if value is None:
        return ""
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


# ── pages ────────────────────────────────────────────────────────────────────

@ensure_csrf_cookie
@login_required
def records(request):
    """The records screen: upload, filter, correct, export."""
    _require(request.user, roles.can_access)

    # A job left over from a previous visit — the user read a file, navigated
    # away, and came back. Its stage is still on disk, so offer the preview
    # rather than making them upload it again.
    pending = (
        ReconImportJob.objects
        .filter(created_by=request.user, kind=ReconImportJob.KIND_READ,
                state=ReconImportJob.STATE_DONE)
        .exclude(stage_path="")
        .first()
    )

    context = {
        "recon_perms": roles.permissions_for(request.user),
        # Passed as objects, not pre-encoded strings: |json_script does the
        # encoding, and handing it a string would deliver the page a quoted
        # blob the browser parses back into a string rather than an object.
        "filter_options": _filter_options(),
        "pending_job": _job_payload(pending) if pending else None,
        "page_size": PAGE_SIZE,
        "column_count": len(schema.ALL_COLUMNS),
    }
    return render(request, "reconciliation/records.html", context)


@ensure_csrf_cookie
@login_required
def dashboard(request):
    """The filing summary pivot."""
    _require(request.user, roles.can_access)

    records_qs, spec = filters.filtered_records(request.GET)
    # Removed records never appear on the dashboard: a filing summary that
    # counts documents someone deleted is not a filing summary.
    records_qs = records_qs.filter(IsDeleted=False)

    rows = pivot.build_pivot(records_qs)
    heads = pivot.letterheads(records_qs) if rows else {}

    # Tiles, charts, insights and the vendor table all come from one call, so
    # the analytics half of the screen and the pivot below it can never be
    # describing different populations.
    analysis = analytics.build(records_qs)
    summary = {"documents": analysis["kpis"]["documents"],
               "vendors": analysis["kpis"]["vendors"]}
    period = records_qs.aggregate(
        d_from=Min("GstDocumentDate"), d_to=Max("GstDocumentDate"))

    # Where the letterhead banners go: before the first detail row of each
    # vendor. Worked out here so the template stays a loop with no lookahead.
    banner_at = {}
    last = None
    for index, row in enumerate(rows):
        if row.kind == pivot.DETAIL and row.entity != last:
            banner_at[index] = heads.get(row.entity, {"name": row.entity, "period": ""})
            last = row.entity
        elif row.kind == pivot.ENTITY_TOTAL:
            last = None

    context = {
        "recon_perms": roles.permissions_for(request.user),
        "rows": [(index, row, banner_at.get(index)) for index, row in enumerate(rows)],
        "has_rows": bool(rows),
        "any_records": ReconRecord.objects.filter(IsDeleted=False).exists(),
        "spec": spec,
        "vendors": _vendor_options(),
        "summary": summary,
        "period": pivot.format_period(period["d_from"], period["d_to"]),
        "health_counts": _health_counts(records_qs),
        "analysis": analysis,
        # The chart series, handed to Chart.js through |json_script rather than
        # a second request — the page already has the numbers.
        "chart_data": {
            "trend": analysis["trend"],
            "reasons": analysis["reasons"],
            "presence": analysis["presence"],
            "sections": analysis["sections"],
        },
        "query_string": request.GET.urlencode(),
    }
    return render(request, "reconciliation/dashboard.html", context)


@ensure_csrf_cookie
@login_required
def billing_types(request):
    """The 53-code master the classifier reads."""
    _require(request.user, roles.can_access)

    rows = list(ReconBillingType.objects.all())
    usage = {
        row["GSTCustom1"]: row["n"]
        for row in (ReconRecord.objects.filter(IsDeleted=False)
                    .exclude(GSTCustom1__isnull=True).exclude(GSTCustom1="")
                    .values("GSTCustom1").annotate(n=Count("id")))
    }
    context = {
        "recon_perms": roles.permissions_for(request.user),
        "rows": rows,
        "usage": usage,
        "doc_types": ["Invoice", "Service", "Scrap Invoice", "Credit note",
                      "Debit note", "Challan", "Jobwork", "Exempt"],
    }
    return render(request, "reconciliation/billing_types.html", context)


@ensure_csrf_cookie
@login_required
def sales_types(request):
    """The sales-type master: which GSTR-1 section each transaction type files
    under. Add a row here when the export starts carrying a new code."""
    _require(request.user, roles.can_access)

    rows = list(ReconSalesType.objects.all())
    usage = {
        row["GstTransactionType"]: row["n"]
        for row in (ReconRecord.objects.filter(IsDeleted=False)
                    .exclude(GstTransactionType__isnull=True)
                    .exclude(GstTransactionType="")
                    .values("GstTransactionType").annotate(n=Count("id")))
    }
    # Codes the file carries that the master has no row for. These are the ones
    # worth adding, and the screen says so rather than leaving someone to
    # compare two lists by eye.
    known = {row.code.upper() for row in rows}
    unmapped = sorted(
        ({"code": code, "count": count} for code, count in usage.items()
         if code and code.upper() not in known),
        key=lambda item: -item["count"])

    context = {
        "recon_perms": roles.permissions_for(request.user),
        "rows": rows,
        "usage": usage,
        "unmapped": unmapped,
        "sections": ["B2B", "B2CS", "B2CL", "B2CL_CDNUR", "CDNR", "DDNR",
                     "WPAY", "WOPAY", "EXEMPT", "JOBWORK", "CONSG", "LBT",
                     "ITC_Rev", "EXCEPTION", "UNMAPPED"],
    }
    return render(request, "reconciliation/sales_types.html", context)


# ── filter option lists ──────────────────────────────────────────────────────

def _counted(queryset, column, deleted_scope=False):
    base = queryset if deleted_scope else queryset.filter(IsDeleted=False)
    rows = (base.exclude(**{f"{column}__isnull": True}).exclude(**{column: ""})
            .values(column).annotate(n=Count("id")).order_by("-n"))
    return [{"value": row[column], "count": row["n"]} for row in rows]


def _vendor_options():
    # Vendors are listed regardless of deleted state: picking a vendor and
    # picking active-or-deleted are independent choices.
    return _counted(ReconRecord.objects.all(), "EntityGstin", deleted_scope=True)


def _filter_options():
    records_qs = ReconRecord.objects.all()
    return {
        "vendors": _vendor_options(),
        "reasons": _counted(records_qs, "ReasonRemark"),
        "presence": sorted(_counted(records_qs, "Presence"),
                           key=lambda row: row["value"]),
        "segments": _counted(records_qs, "Segment"),
        "sections": _counted(records_qs, "Gstr1Section"),
    }


def _health_counts(queryset):
    """How many Clear / Watch / Exception rows the current view holds.

    ``.order_by()`` first, and it is not cosmetic. Django folds a queryset's
    ordering columns into the GROUP BY of a ``.values().annotate()``, so
    counting over the *sorted* grid queryset grouped by ``id`` — one group per
    record, every count 1. The tiles read "1 / 1 / 1" against 3,732 documents
    and nobody could tell whether that was the data or the page.
    """
    counts = {"ok": 0, "warn": 0, "bad": 0}
    tally = queryset.order_by().values("Health").annotate(n=Count("id"))
    for row in tally:
        if row["Health"] in counts:
            counts[row["Health"]] = row["n"]
    return counts


def _cancelled_count(queryset):
    """How many cancelled invoices the current view holds — either side.

    Counted off the raw queryset rather than the active-only grid so the
    Cancelled tile reads the same number whether the user has the cancelled
    filter on or off: it is a running total of what is cancellable in scope,
    not an echo of the filter.
    """
    return queryset.order_by().filter(
        Q(GSTDocumentStatus__iexact="Cancelled")
        | Q(EINVDocumentStatus__iexact="Cancelled")
    ).count()


@login_required
@require_GET
def filter_options(request):
    """Refreshed after anything that changes the counts."""
    _require(request.user, roles.can_access)
    return _ok(options=_filter_options())


# ── the grid ─────────────────────────────────────────────────────────────────

@login_required
@require_GET
def records_data(request):
    _require(request.user, roles.can_access)

    queryset, spec = filters.filtered_records(request.GET)
    queryset, sort, direction = filters.order_records(
        queryset, request.GET.get("sort"), request.GET.get("dir"))

    try:
        page_size = min(MAX_PAGE_SIZE, max(10, int(request.GET.get("page_size", PAGE_SIZE))))
    except (TypeError, ValueError):
        page_size = PAGE_SIZE

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page") or 1)

    # The denominator matches the same active/deleted/all scope as the view, so
    # "12 of 3,733" means something. Comparing a filtered active count against
    # a total that included removed rows would be quietly misleading.
    scope_total = filters.apply_status(ReconRecord.objects.all(), spec.status).count()

    return _ok(
        rows=[_row(record) for record in page.object_list],
        page=page.number,
        pages=paginator.num_pages,
        filtered=paginator.count,
        total=scope_total,
        page_size=page_size,
        sort=sort,
        dir=direction,
        health=_health_counts(queryset),
        cancelled=_cancelled_count(queryset),
        narrowed=spec.is_narrowed,
        describe=spec.describe(),
    )


# ── single record ────────────────────────────────────────────────────────────

@login_required
@require_GET
def record_detail(request, pk):
    """Every one of the 204 source columns, grouped for the edit drawer."""
    _require(request.user, roles.can_access)
    record = get_object_or_404(ReconRecord, pk=pk)

    groups = []
    for name, label, columns in schema.grouped_columns():
        groups.append({
            "name": name,
            "label": label,
            "fields": [
                {"column": column, "value": _format_for_edit(getattr(record, column))}
                for column in columns
            ],
        })

    return _ok(
        record=_row(record),
        groups=groups,
        classification={
            "ReasonRemark": record.ReasonRemark or "",
            "Segment": record.Segment or "",
            "Gstr1Section": record.Gstr1Section or "",
            "Health": record.Health or "",
            "ManualReason": record.ManualReason,
        },
        audit={
            "CreatedBy": record.CreatedBy or "",
            "SourceFile": record.SourceFile or "",
            "UpdatedAt": record.UpdatedAt.isoformat() if record.UpdatedAt else "",
            "DeletedBy": record.DeletedBy or "",
        },
    )


# ── the document detail modal ────────────────────────────────────────────────
#
# Read-only, and deliberately not the edit drawer. The drawer exists to change
# a record; this exists to answer "why does this row say that", which is a
# different question and a different risk — nobody should have to open an
# editable form to find out why a remark was generated.

def _source_columns():
    """The 204 columns, split by which source they describe.

    Grouped so the modal can show GSTR-1, the e-invoice and the e-way bill side
    by side: comparing them is the whole job, and a single alphabetical list of
    204 fields makes that comparison impossible.
    """
    groups = {key: [] for key in ("Document", "GST", "EINV", "EWB", "Comparison", "Other")}
    for column in schema.ALL_COLUMNS:
        groups[schema.group_for(column)].append(column)
    return groups


def _field(record, column):
    value = getattr(record, column, None)
    empty = value is None or (isinstance(value, str) and not value.strip())
    return {
        "column": column,
        # "NA" rather than a blank: the source genuinely does not carry this,
        # and an empty cell reads as "we lost it".
        "value": "NA" if empty else _format_for_edit(value),
        "empty": empty,
    }


@login_required
@require_GET
def record_document(request, pk):
    """Everything behind one document, and why it was classified as it was."""
    _require(request.user, roles.can_access)
    record = get_object_or_404(ReconRecord, pk=pk)

    derived, ctx = explain_record(record, **load_masters())

    # The MAP, per source: is it here, is it expected to agree, what does it say.
    sources = []
    for source in SOURCES:
        sources.append({
            "key": source,
            "label": SOURCE_LABELS[source],
            "present": ctx.present(source),
            "applicable": ctx.applicable(source),
            "taxable": _money(ctx.taxable(source)),
        })

    rule = ctx.rule
    groups = _source_columns()

    return _ok(
        record=_row(record),
        verdict={
            "presence": ctx.presence,
            "remark": derived["ReasonRemark"],
            "health": derived["Health"],
            "segment": derived["Segment"],
            "section": derived["Gstr1Section"],
            "max_gap": _money(derived["MaxGap"]),
            "tolerance": float(AMOUNT_TOLERANCE),
            "matched": ctx.matches(),
            "gap": _money(ctx.gap()),
            "rule_id": ctx.rule_id,
            "why": explain(ctx.rule_id),
            "manual": record.ManualReason,
            "compared": [source for source, _ in ctx.compared],
            "sources": sources,
            "billing_type": None if rule is None else {
                "code": rule.code,
                "description": rule.description,
                "doc_type": rule.doc_type,
                "gst": rule.gst_applicable,
                "einv": rule.einv_applicable,
                "ewb": rule.ewb_applicable,
                "final_remark": rule.final_remark,
            },
            "billing_code": ctx.bt_code,
        },
        panels=[
            {
                "key": key,
                "label": label,
                "fields": [_field(record, column) for column in groups[key]],
            }
            for key, label in (
                ("Document", "Document"),
                ("GST", "GSTR-1"),
                ("EINV", "E-Invoice"),
                ("EWB", "E-Way Bill"),
                ("Comparison", "Portal verdicts"),
                ("Other", "Other"),
            )
            if groups[key]
        ],
        audit={
            "CreatedBy": record.CreatedBy or "NA",
            "SourceFile": record.SourceFile or "NA",
            "UpdatedAt": record.UpdatedAt.isoformat() if record.UpdatedAt else "",
        },
    )


def _assign(record, payload, columns):
    """Parse and assign the columns present in ``payload``.

    Parsed exactly the way an import parses them — same ``parse_for`` — so a
    date typed into the drawer means what a date in the file means, and a
    number with a comma in it is still a number.
    """
    for column in columns:
        if column not in payload:
            continue
        value = parsing.parse_for(column, payload.get(column))
        if column in schema.DECIMAL_COLUMNS:
            value = quantize_decimal(value)
        setattr(record, column, value)


@login_required
@require_POST
def record_save(request, pk):
    """Save the edit drawer: every source column, plus the classification pins."""
    _require(request.user, roles.can_edit)
    record = get_object_or_404(ReconRecord, pk=pk)
    payload = json.loads(request.body or "{}")
    fields = payload.get("fields") or {}

    _assign(record, fields, schema.ALL_COLUMNS)

    if not (record.DocumentNumber or "").strip():
        return _error("A document number is required.")

    classification = payload.get("classification") or {}
    record.ManualReason = bool(classification.get("ManualReason"))
    if record.ManualReason:
        # Only meaningful when pinned — otherwise the classifier is about to
        # overwrite all four anyway, and accepting them would make the form
        # look like it had saved something it had not.
        record.ReasonRemark = (classification.get("ReasonRemark") or "").strip() or None
        record.Segment = (classification.get("Segment") or "").strip() or None
        record.Gstr1Section = (classification.get("Gstr1Section") or "").strip() or None
        record.Health = (classification.get("Health") or "").strip() or None

    classify_record(record, **load_masters())
    record.save()

    return _ok(record=_row(record),
               message=f"{record.DocumentNumber} saved. Reason remark: {record.ReasonRemark}")


@login_required
@require_POST
def record_create(request):
    """Add one document by hand.

    Only the handful of fields the add form offers; everything else stays
    empty and can be filled in from the edit drawer. The reason remark is
    worked out from what was entered, the same as for an imported row.
    """
    _require(request.user, roles.can_edit)
    payload = json.loads(request.body or "{}")

    record = ReconRecord(CreatedBy=request.user.get_username(), SourceFile="(entered by hand)")
    _assign(record, payload, schema.ALL_COLUMNS)

    if not (record.DocumentNumber or "").strip():
        return _error("A document number is required.")

    classify_record(record, **load_masters())
    record.save()
    return _ok(record=_row(record),
               message=f"Record created. Reason remark: {record.ReasonRemark}")


@login_required
@require_POST
def record_delete(request, pk):
    """Soft delete — recoverable from the Record status filter."""
    _require(request.user, roles.can_edit)
    record = get_object_or_404(ReconRecord, pk=pk)
    record.IsDeleted = True
    record.DeletedAt = timezone.now()
    record.DeletedBy = request.user.get_username()
    record.save(update_fields=["IsDeleted", "DeletedAt", "DeletedBy", "UpdatedAt"])
    return _ok(record=_row(record),
               message="Record removed. Switch Record status to \"Deleted only\" to restore it.")


@login_required
@require_POST
def record_restore(request, pk):
    _require(request.user, roles.can_edit)
    record = get_object_or_404(ReconRecord, pk=pk)
    record.IsDeleted = False
    record.DeletedAt = None
    record.DeletedBy = None
    record.save(update_fields=["IsDeleted", "DeletedAt", "DeletedBy", "UpdatedAt"])
    return _ok(record=_row(record), message="Record restored.")


@login_required
@require_POST
def remove_filtered(request):
    """Soft-delete whatever the current filters show — never the whole table.

    With several vendors' data in one table, an unscoped "clear everything"
    button is exactly the control that should not exist. Scoping it to the
    active filters means clearing one vendor's bad import can never touch
    anyone else's data, and the soft delete means it is recoverable either way.
    """
    _require(request.user, roles.can_import)
    payload = json.loads(request.body or "{}")

    queryset, spec = filters.filtered_records(payload)
    if not spec.is_narrowed:
        return _error(
            "Choose at least one filter — vendor, date, remark — before removing "
            "records. This will not run unfiltered."
        )

    affected = queryset.filter(IsDeleted=False).update(
        IsDeleted=True, DeletedAt=timezone.now(), DeletedBy=request.user.get_username())

    return _ok(affected=affected,
               message=f"{affected:,} record(s) removed. Soft delete — recoverable "
                       "from the Record status filter.")


# ── upload and import ────────────────────────────────────────────────────────

@login_required
@require_POST
def upload(request):
    """Step 1: save the file and start reading it. Nothing is stored yet."""
    _require(request.user, roles.can_import)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return _error("Choose a file first.")

    name = uploaded.name.casefold()
    if name.endswith(".xls"):
        return _error("The legacy .xls format isn't supported — please save as .xlsx "
                      "and try again.")
    if not name.endswith((".xlsx", ".xlsm", ".csv")):
        return _error("Please choose a .xlsx or .csv file.")

    mode = request.POST.get("mode") or ReconImportJob.MODE_UPSERT
    if mode not in dict(ReconImportJob.MODE_CHOICES):
        mode = ReconImportJob.MODE_UPSERT

    # Any earlier stage belonging to this user is abandoned — one upload at a
    # time, so "Confirm" can never import a file the user has forgotten about.
    _discard_stages(request.user)

    job = ReconImportJob.objects.create(
        kind=ReconImportJob.KIND_READ,
        mode=mode,
        created_by=request.user,
        filename=uploaded.name,
        source_path=importer.save_upload(uploaded),
        phase="Queued",
    )
    importer.start_job(job)
    return _ok(job=_job_payload(job))


@login_required
@require_GET
def job_status(request, pk):
    _require(request.user, roles.can_access)
    job = _own_job(request.user, pk)
    return _ok(job=_job_payload(job))


@login_required
@require_GET
def preview(request, pk):
    """A page of the staged rows, before anything is written."""
    _require(request.user, roles.can_access)
    job = get_object_or_404(ReconImportJob, pk=pk)
    if not job.stage_path:
        return _error("There is nothing staged for this upload.")

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    offset = (page - 1) * PREVIEW_PAGE_SIZE
    try:
        rows = importer.read_stage(job.stage_path, limit=PREVIEW_PAGE_SIZE, offset=offset)
    except FileNotFoundError:
        return _error("The staged file is no longer available — please upload it again.")

    pages = max(1, -(-job.row_count // PREVIEW_PAGE_SIZE))
    return _ok(rows=[_staged_row(values) for values in rows],
               page=page, pages=pages, job=_job_payload(job))


@login_required
@require_GET
def preview_full(request, pk):
    """The staged file across all 204 columns, in the export's own layout.

    The upload step's own table shows eight columns, because eight is what you
    judge a classification on. This answers the other question — did every
    column land where I think it did — before anything is written.
    """
    _require(request.user, roles.can_access)
    job = _own_job(request.user, pk)
    if not job.stage_path:
        return _error("There is nothing staged for this upload.")

    try:
        rows = importer.read_stage(job.stage_path, limit=exports.PREVIEW_ROWS)
    except FileNotFoundError:
        return _error("The staged file is no longer available — please upload it again.")

    preview = previews.staged_preview(rows, job.row_count)
    preview["filename"] = job.filename
    return _ok(preview=preview)


@login_required
@require_POST
def confirm_import(request, pk):
    """Step 2: write the staged rows. One transaction — all or nothing."""
    _require(request.user, roles.can_import)
    job = get_object_or_404(ReconImportJob, pk=pk)
    if not job.stage_path:
        return _error("There is nothing staged to import — upload a file first.")

    payload = json.loads(request.body or "{}")
    mode = payload.get("mode") or job.mode
    if mode not in dict(ReconImportJob.MODE_CHOICES):
        mode = ReconImportJob.MODE_UPSERT

    import_job = ReconImportJob.objects.create(
        kind=ReconImportJob.KIND_IMPORT,
        mode=mode,
        created_by=request.user,
        filename=job.filename,
        stage_path=job.stage_path,
        source_path=job.source_path,
        row_count=job.row_count,
        phase="Queued",
    )
    # The read job hands its stage over: only one job owns it, so a retry after
    # a failed import re-uses the import job's own stage rather than the read's.
    ReconImportJob.objects.filter(pk=job.pk).update(stage_path="")

    importer.start_job(import_job)
    return _ok(job=_job_payload(import_job))


@login_required
@require_POST
def cancel_upload(request, pk):
    """Throw the staged rows away. Nothing was written, so nothing is undone."""
    _require(request.user, roles.can_import)
    job = get_object_or_404(ReconImportJob, pk=pk)
    _discard_stage(job)
    return _ok(message="Upload discarded.")


def _discard_stage(job):
    if job.stage_path:
        try:
            os.remove(job.stage_path)
        except OSError:
            pass
        ReconImportJob.objects.filter(pk=job.pk).update(stage_path="")


def _discard_stages(user):
    for job in ReconImportJob.objects.filter(created_by=user).exclude(stage_path=""):
        _discard_stage(job)


@login_required
@require_POST
def rerun_rules(request):
    """Re-classify every stored record against the current master."""
    _require(request.user, roles.can_import)
    job = ReconImportJob.objects.create(
        kind=ReconImportJob.KIND_RERUN,
        created_by=request.user,
        phase="Queued",
    )
    importer.start_job(job)
    return _ok(job=_job_payload(job))


# ── exports ──────────────────────────────────────────────────────────────────

def _stamp():
    return timezone.localtime().strftime("%Y%m%d_%H%M%S")


def _xlsx_response(workbook, filename):
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@login_required
@require_GET
def export_records_preview(request):
    """What the records export will contain, before it is downloaded.

    Rendered by previews.py from the exporter's own palette, so this is the
    file rather than an impression of it. Scoped by exactly the same filters
    the download uses — the point is to answer "did my filters do what I
    meant" without waiting for a 1.5 MB file first.
    """
    _require(request.user, roles.can_access)
    queryset, spec = filters.filtered_records(request.GET)
    queryset, _, _ = filters.order_records(
        queryset, request.GET.get("sort"), request.GET.get("dir"))

    total = queryset.count()
    if not total:
        return _error("Nothing matches the current filters.")

    preview = previews.records_preview(queryset, total)
    preview["describe"] = spec.describe()
    preview["filename"] = f"GST_Reconciliation_{_stamp()}.xlsx"
    return _ok(preview=preview)


@login_required
@require_GET
def export_summary_preview(request):
    """What the filing summary will look like, before it is downloaded."""
    _require(request.user, roles.can_access)
    queryset, spec = filters.filtered_records(request.GET)
    queryset = queryset.filter(IsDeleted=False)

    rows = pivot.build_pivot(queryset)
    if not rows:
        return _error("Nothing to preview yet.")

    preview = previews.summary_preview(rows, pivot.letterheads(queryset))
    preview["describe"] = spec.describe()
    preview["filename"] = f"GST_Filing_Summary_{_stamp()}.xlsx"
    return _ok(preview=preview)


@login_required
@require_GET
def export_records(request):
    """Whatever the current filters show, as a real .xlsx."""
    _require(request.user, roles.can_access)
    queryset, _ = filters.filtered_records(request.GET)
    queryset, _, _ = filters.order_records(
        queryset, request.GET.get("sort"), request.GET.get("dir"))

    if not queryset.exists():
        return _error("Nothing matches the current filters.")

    return _xlsx_response(exports.records_workbook(queryset),
                          f"GST_Reconciliation_{_stamp()}.xlsx")


@login_required
@require_GET
def export_summary(request):
    """The dashboard pivot, as a real .xlsx."""
    _require(request.user, roles.can_access)
    queryset, _ = filters.filtered_records(request.GET)
    queryset = queryset.filter(IsDeleted=False)

    rows = pivot.build_pivot(queryset)
    if not rows:
        return _error("Nothing to export yet.")

    workbook = exports.pivot_workbook(rows, pivot.letterheads(queryset))
    return _xlsx_response(workbook, f"GST_Filing_Summary_{_stamp()}.xlsx")


# ── billing-type master ──────────────────────────────────────────────────────

@login_required
@require_POST
def sales_type_save(request, pk):
    """Edit one sales type, or create the row for a code the file carries.

    Like the billing-type master, saving does not re-label anything by itself:
    the numbers move when the rules are re-run, which is a separate and
    deliberate action.
    """
    _require(request.user, roles.can_manage_masters)
    payload = json.loads(request.body or "{}")

    if pk:
        rule = get_object_or_404(ReconSalesType, pk=pk)
    else:
        code = (payload.get("code") or "").strip().upper()
        if not code:
            return _error("A transaction-type code is required.")
        if ReconSalesType.objects.filter(code__iexact=code).exists():
            return _error(f"{code} is already in the master.")
        rule = ReconSalesType(code=code)

    section = (payload.get("gstr1_section") or "").strip()
    if not section:
        return _error("Choose the GSTR-1 section this type files under.")

    rule.description = (payload.get("description") or "").strip()
    rule.gstr1_section = section
    rule.is_export = bool(payload.get("is_export"))
    rule.is_b2c = bool(payload.get("is_b2c"))
    rule.is_active = bool(payload.get("is_active", True))
    rule.notes = (payload.get("notes") or "").strip()
    rule.save()

    return _ok(message=f"{rule.code} saved. Re-run the reason rules to apply it "
                       "to records already imported.")


@login_required
@require_POST
def billing_type_save(request, pk):
    """Edit one rule.

    Changing a rule does not re-label anything by itself — the numbers only
    move when the rules are re-run, which is a separate, deliberate action. The
    response says so, because an edit that looks like it did nothing is worse
    than one that did too much.
    """
    _require(request.user, roles.can_manage_masters)
    rule = get_object_or_404(ReconBillingType, pk=pk)
    payload = json.loads(request.body or "{}")

    rule.description = (payload.get("description") or "").strip()
    rule.doc_type = (payload.get("doc_type") or "").strip()
    rule.gst_applicable = bool(payload.get("gst_applicable"))
    rule.einv_applicable = bool(payload.get("einv_applicable"))
    rule.ewb_applicable = bool(payload.get("ewb_applicable"))
    rule.final_remark = (payload.get("final_remark") or "").strip()
    rule.pending_remark = (payload.get("pending_remark") or "").strip()
    rule.health = (payload.get("health") or "ok").strip()
    rule.other_conditions = (payload.get("other_conditions") or "").strip()
    rule.is_active = bool(payload.get("is_active", True))
    rule.save()

    return _ok(message=f"{rule.code} saved. Re-run the reason rules to apply it to "
                       "records already imported.")
