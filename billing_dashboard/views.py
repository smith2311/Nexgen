"""
billing_dashboard/views.py

Screens:
  * home          — upload control + upload history + quick stats
  * upload        — POST handler (validate → ingest → upsert)         [admin]
  * dashboard     — KPI cards, charts, world map, monthly-by-region pivot
  * dashboard_data_api — JSON for client-side filter changes
  * data_grid     — paginated, filterable line-item grid (+ exceptions)
  * export_xlsx   — "Download All" (filtered) Excel export
  * delete_upload — soft-remove an upload and its rows                 [admin]

Access model (mirrors the platform's superuser-sees-all convention):
  view  → superuser OR any Billing group
  admin → superuser OR "Billing Dashboard" / "Billing-Admin"
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BillingUpload, ExportBillingDetails
from .services import dashboard as dash
from .services.export import build_workbook
from .services.ingest import process_upload

VIEW_GROUPS = ["Billing Dashboard", "Billing-Admin", "Billing-Members"]
ADMIN_GROUPS = ["Billing Dashboard", "Billing-Admin"]

ALLOWED_EXT = (".xlsx", ".xlsm")


def _has_access(user, groups):
    return user.is_superuser or user.groups.filter(name__in=groups).exists()


def billing_view_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if _has_access(request.user, VIEW_GROUPS):
            return view(request, *args, **kwargs)
        raise PermissionDenied("You don't have access to the Billing Dashboard.")
    return wrapper


def billing_admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if _has_access(request.user, ADMIN_GROUPS):
            return view(request, *args, **kwargs)
        raise PermissionDenied("Uploading requires the Billing-Admin role.")
    return wrapper


def _is_admin(request):
    return _has_access(request.user, ADMIN_GROUPS)


def _completed_uploads():
    """Uploads that produced a viewable snapshot, newest first (for pickers)."""
    return BillingUpload.objects.filter(status=BillingUpload.Status.COMPLETED)


def _resolve_upload(request):
    """The snapshot the user is looking at: an explicit ``?upload=<pk>`` if it is a
    real completed upload, otherwise the most recent completed one (or None)."""
    requested = request.GET.get("upload")
    completed = _completed_uploads()
    if requested and str(requested).isdigit():
        return completed.filter(pk=requested).first() or completed.first()
    return completed.first()


# ── HOME / UPLOAD ────────────────────────────────────────────────────────────
@billing_view_required
def home(request):
    # Annotate each upload with the live rows it currently owns so the UI only
    # offers a dashboard for snapshots that actually hold data. (Uploads ingested
    # before per-upload snapshots existed had their rows overwritten and now own
    # none — those correctly show as empty.)
    # order_by() is explicit on purpose: Django drops Meta.ordering ("-created_at")
    # from any query that groups rows for an aggregate, so the annotate() below
    # silently emitted SQL with no ORDER BY and the history listed uploads in
    # whatever order Postgres returned them. -pk breaks ties between uploads made
    # in the same second so "newest first" stays stable.
    from django.db.models import Count, Q
    uploads = BillingUpload.objects.all().annotate(
        row_count=Count("rows", filter=Q(rows__is_deleted=False)),
    ).order_by("-created_at", "-pk")[:15]
    # Each upload is its own snapshot; the quick stats describe the latest one.
    latest = _completed_uploads().first()
    rows = ExportBillingDetails.objects.filter(is_excluded=False, is_domestic=False)
    if latest:
        rows = rows.filter(upload_id=latest.pk)
    else:
        rows = rows.none()
    total_rows = rows.count()
    unmapped = rows.exclude(mapping_status=ExportBillingDetails.MappingStatus.OK).count()
    ctx = {
        "uploads": uploads,
        "latest_upload": latest,
        "total_rows": total_rows,
        "unmapped": unmapped,
        "can_upload": _is_admin(request),
    }
    return render(request, "billing_dashboard/home.html", ctx)


@billing_admin_required
@require_POST
def upload(request):
    f = request.FILES.get("dump")
    if not f:
        messages.error(request, "Please choose a SAP Billing Dump file to upload.")
        return redirect("billing:home")
    if not f.name.lower().endswith(ALLOWED_EXT):
        messages.error(
            request,
            f"'{f.name}' is not a supported Excel file. Please upload a .xlsx or .xlsm export.",
        )
        return redirect("billing:home")

    upload_obj = BillingUpload.objects.create(
        file=f, original_name=f.name, status=BillingUpload.Status.PENDING,
    )

    # Background processing: enqueue and return immediately so a large dump does
    # not block the request. Falls back to synchronous if the broker is down.
    if getattr(settings, "BILLING_ASYNC_UPLOAD", False):
        from .tasks import process_upload_task
        try:
            process_upload_task.delay(upload_obj.pk, request.user.username)
            messages.info(
                request,
                f"'{f.name}' is being processed in the background — this page refreshes when it's done.",
            )
            return redirect("billing:home")
        except Exception as exc:  # broker unreachable → process now
            messages.warning(request, f"Background queue unavailable ({exc}); processing now…")

    try:
        process_upload(upload_obj, username=request.user.username)
    except Exception as exc:  # never let a bad dump 500 the page
        upload_obj.status = BillingUpload.Status.FAILED
        upload_obj.message = f"Processing error: {exc}"
        upload_obj.save(update_fields=["status", "message", "updated_at"])

    if upload_obj.status == BillingUpload.Status.COMPLETED:
        messages.success(
            request,
            f"Snapshot ready — {upload_obj.inserted} new, {upload_obj.updated} recurring, "
            f"{upload_obj.skipped} skipped, {upload_obj.unmapped} need master maintenance. "
            "Open its dashboard from the Upload History below.",
        )
    else:
        messages.error(request, upload_obj.message or "Upload failed.")
    return redirect("billing:home")


@billing_view_required
def upload_detail(request, pk):
    """Layout-transparency report: how each Excel header mapped (by name, not
    position), extra columns preserved in JSON, and a sample of the extracted
    per-row JSON."""
    import json

    up = get_object_or_404(BillingUpload, pk=pk)
    sample = list(ExportBillingDetails.objects.filter(upload=up)[:5])
    samples = [
        {"key": f"{r.billing_doc}/{r.item}", "json": json.dumps(r.raw, indent=2, ensure_ascii=False)}
        for r in sample
    ]
    ctx = {
        "up": up,
        "column_map": sorted((up.column_map or {}).items()),
        "unmapped_headers": up.unmapped_headers or [],
        "samples": samples,
    }
    return render(request, "billing_dashboard/upload_detail.html", ctx)


@billing_view_required
def download_upload(request, pk):
    """Stream the original uploaded SAP dump back to the user as a download."""
    up = get_object_or_404(BillingUpload, pk=pk)
    if not up.file:
        raise Http404("The original file is no longer available.")
    try:
        fh = up.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("The original file is no longer available.")
    return FileResponse(fh, as_attachment=True, filename=up.original_name)


@billing_view_required
def upload_status(request, pk):
    """Lightweight JSON for polling an in-progress upload (background task)."""
    up = get_object_or_404(BillingUpload, pk=pk)
    return JsonResponse({
        "id": up.pk,
        "status": up.status,
        "processed_rows": up.processed_rows,
        "total_rows": up.total_rows,
        "progress_pct": up.progress_pct,
        "inserted": up.inserted,
        "updated": up.updated,
        "skipped": up.skipped,
        "unmapped": up.unmapped,
        "message": up.message or "",
    })


@billing_admin_required
@require_POST
def delete_upload(request, pk):
    """Permanently remove an upload: its billing rows, the stored Excel file and
    the batch record itself. This is a hard delete — the snapshot is gone and
    cannot be restored, so the UI confirms before posting here.

    Rows go through ``all_objects`` (not the active-only default manager) so any
    previously soft-deleted rows in this batch are removed too, and via a
    queryset delete so the fact table takes the fast path instead of pulling
    every row into memory.
    """
    up = get_object_or_404(BillingUpload.all_objects, pk=pk)
    name = up.original_name

    deleted, _ = ExportBillingDetails.all_objects.filter(upload=up).delete()

    # Drop the uploaded workbook from storage too; a missing file must not
    # block the record from being removed.
    if up.file:
        try:
            up.file.delete(save=False)
        except Exception as exc:  # noqa: BLE001 — storage backend / file already gone
            messages.warning(request, f"Upload removed, but its stored file could not be deleted ({exc}).")

    up.delete()
    messages.success(
        request,
        f"Permanently deleted '{name}' and its {deleted} billing row(s).",
    )
    return redirect("billing:home")


# ── DASHBOARD ────────────────────────────────────────────────────────────────
def _filters_from_request(request, upload_id=None):
    return {
        "upload": upload_id if upload_id is not None else (request.GET.get("upload") or ""),
        "fin_year": request.GET.get("fin_year") or "",
        "region": request.GET.get("region") or "",
        "lob": request.GET.get("lob") or "",
        "billing_type": request.GET.get("billing_type") or "",
        "scope": request.GET.get("scope") or "",
        "exclude_reversals": request.GET.get("exclude_reversals") == "1",
    }


@billing_view_required
def dashboard(request):
    # Always view exactly one snapshot; default to the newest completed upload.
    selected = _resolve_upload(request)
    upload_id = selected.pk if selected else ""
    filters = _filters_from_request(request, upload_id=upload_id)
    # Count of active filters (excluding the snapshot) for the Filters button badge.
    active_filter_count = sum(
        1 for k in ("scope", "fin_year", "region", "lob", "billing_type") if filters.get(k)
    ) + (1 if filters.get("exclude_reversals") else 0)
    ctx = {
        "options": dash.filter_options(upload_id),
        "data": dash.dashboard_data(filters),
        "filters": filters,
        "active_filter_count": active_filter_count,
        "uploads": _completed_uploads(),
        "selected_upload": selected,
    }
    return render(request, "billing_dashboard/dashboard.html", ctx)


@billing_view_required
def dashboard_data_api(request):
    selected = _resolve_upload(request)
    upload_id = selected.pk if selected else ""
    return JsonResponse(dash.dashboard_data(_filters_from_request(request, upload_id=upload_id)))


# ── DATA GRID / EXCEPTIONS ───────────────────────────────────────────────────
def _grid_queryset(request):
    qs = ExportBillingDetails.objects.all()
    g = request.GET
    # Snapshot scoping: the grid (and export) show one upload, defaulting to the
    # newest completed one, so rows from different snapshots never mix.
    selected = _resolve_upload(request)
    if selected:
        qs = qs.filter(upload_id=selected.pk)
    else:
        qs = qs.none()
    if g.get("include_excluded") != "1":
        qs = qs.filter(is_excluded=False)
    if g.get("include_domestic") != "1":
        qs = qs.filter(is_domestic=False)
    if g.get("fin_year"):
        qs = qs.filter(fin_year=g["fin_year"])
    if g.get("region"):
        qs = qs.filter(region=g["region"])
    if g.get("lob"):
        qs = qs.filter(lob=g["lob"])
    if g.get("billing_type"):
        qs = qs.filter(billing_type=g["billing_type"])
    if g.get("scope") == "subsidiary":
        qs = qs.filter(is_subsidiary=True)
    elif g.get("scope") == "non_subsidiary":
        qs = qs.filter(is_subsidiary=False)
    if g.get("exclude_reversals") == "1":
        qs = qs.filter(is_reversal=False)
    if g.get("view") == "exceptions":
        qs = qs.exclude(mapping_status=ExportBillingDetails.MappingStatus.OK)
    elif g.get("view") == "reversals":
        qs = qs.filter(is_reversal=True)
    elif g.get("view") == "pending":
        qs = qs.filter(is_pending=True)
    search = (g.get("q") or "").strip()
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(billing_doc__icontains=search)
            | Q(material__icontains=search)
            | Q(sold_to_name__icontains=search)
            | Q(payer_name__icontains=search)
            | Q(description__icontains=search)
        )
    return qs.order_by("-billing_date", "billing_doc", "item")


@billing_view_required
def data_grid(request):
    qs = _grid_queryset(request)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page") or 1)

    # Preserved query strings for pagination (drop page) and tabs (drop page+view)
    page_q = request.GET.copy()
    page_q.pop("page", None)
    tab_q = page_q.copy()
    tab_q.pop("view", None)

    selected = _resolve_upload(request)
    active_filter_count = sum(
        1 for k in ("scope", "fin_year", "region", "lob", "billing_type") if request.GET.get(k)
    )
    ctx = {
        "page": page,
        "options": dash.filter_options(selected.pk if selected else None),
        "params": request.GET,
        "active_filter_count": active_filter_count,
        "view": request.GET.get("view") or "all",
        "q": request.GET.get("q") or "",
        "total": paginator.count,
        "page_query": page_q.urlencode(),
        "tab_query": tab_q.urlencode(),
        "uploads": _completed_uploads(),
        "selected_upload": selected,
    }
    return render(request, "billing_dashboard/data.html", ctx)


@billing_view_required
def export_xlsx(request):
    qs = _grid_queryset(request)
    buf = build_workbook(qs)
    stamp = timezone.now().strftime("%Y%m%d_%H%M")
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="export_billing_{stamp}.xlsx"'
    return resp
