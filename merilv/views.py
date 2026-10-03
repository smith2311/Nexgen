"""Meril-V views.

House style for this codebase: function-based views, `user_passes_test`
decorators built from ``rbac.py``, and raw ``request.POST``/``request.FILES``
handling with a hand-built ``errors`` list rather than Django forms.

The scanning consoles post JSON and re-render themselves client-side — a picker
holding a scanner gun cannot wait for a full page round-trip between items.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    ActivityLog,
    BoxProfile,
    BranchTransferLine,
    BranchTransferPlan,
    BtScanSession,
    ConsolidationRow,
    ConsolidationRun,
    CourierMaster,
    CustomerCourierMapping,
    DeliveryDump,
    DeliveryLine,
    DispatchedSerial,
    FinishedDelivery,
    MaterialMaster,
    OutboundScan,
    OutboundSession,
    ScanLog,
    ScanRecord,
    ScanSession,
    ValidationRow,
    ValidationRun,
    Zva05Dump,
)
from . import useractivity
from .pagination import paginate
from .params import int_or_none
from . import rbac
from .permissions import can as can_do
from .permissions import capability_required
from .rbac import (
    can_access_merilv,
    can_operate_merilv,
    get_scoped_or_404,
    is_merilv_admin,
    scope_queryset_by_team,
)
from .scanfeed import build_scan_feed
from . import focus
from . import transit
from .services import entities as entity_service
from .services import extractor as extractor_service
from .services.batching import bulk_create_in_chunks
from .services import outbound as outbound_engine
from .services import export, ingest, transfers, validation
from .services.barcode import (
    format_serial_component,
    is_non_serial,
    normalise_scan_input,
    parse_barcode,
)
from .services.consolidator import ConsolidationError, consolidate

access_required = user_passes_test(can_access_merilv, login_url="landing_page")
operate_required = user_passes_test(can_operate_merilv, login_url="merilv:index")
admin_required = user_passes_test(is_merilv_admin, login_url="merilv:index")

#: Row cap for a single validation run. Beyond this the request would block a
#: worker for minutes; the operator is told to split the file instead.
MAX_VALIDATION_ROWS = 50_000

#: ── Where each model keeps its plant ────────────────────────────────────────
#: Passed to rbac.scope_visible, which uses it to bound the SHARED grants — a
#: supervisor's upload, and a supervisor's reach — to the site the work is
#: physically at. Three of the module's models carry a plant; the rest have no
#: such column, and for those the user graph is the whole of the boundary.
#:
#: A module's list screen and its detail view must pass the SAME path, or the
#: two disagree and a row is listed that answers 404 when it is clicked.

#: A finished delivery carries one of its own, recorded at finish time: the
#: delivery outlives the session it came from, and a plant-scoped user still
#: has to be able to find it afterwards.
DELIVERY_PLANT_PATH = "plant"

#: A validation run reaches one through the stock rows it reconciled.
#: ``system_plant`` is the plant SAP says the stock is at, which is where the
#: count physically happened.
RUN_PLANT_PATH = "rows__system_plant"

#: An outbound session reaches one through its dump's delivery lines — the
#: plant the picking physically happens at.
SESSION_PLANT_PATH = "dump__lines__plant"


logger = logging.getLogger(__name__)


def _ctx(request, **extra):
    """Shared template context: role flags and the entity picker."""
    context = {
        "is_mv_admin": is_merilv_admin(request.user),
        "can_operate": can_operate_merilv(request.user),
        # Per-action answers, so a control the server would refuse is never
        # offered. One source for the rule and the UI — merilv/permissions.py.
        # Only the entities this user may work in. A picker mapped to one
        # business unit must not be offered another's console in the switcher.
        "entities": rbac.entities_for(request.user),
        # #22: plant/team scope now fails CLOSED, so a non-admin with no plant
        # mapping and no manager link sees empty screens. Correct, and
        # indistinguishable from a broken module unless we say so -- the shell
        # renders a banner off this telling them what to ask an admin for.
        "mv_unmapped": rbac.is_unmapped(request.user),
        # The granular capabilities the templates gate controls on, so a
        # screen never offers a button whose endpoint will answer 403. One
        # flag per capability the UI actually draws — see merilv/permissions.py
        # for the resolution order and for why a manager holds these by
        # default while a picker does not.
        "mv_can_document_edit": can_do(request.user, "mv_document_edit_outbound"),
        "mv_can_courier_edit": can_do(request.user, "mv_courier_edit"),
        "mv_can_clear_scans": can_do(request.user, "mv_clear_scans"),
        "mv_can_delete_scan": can_do(request.user, "mv_delete_scan"),
        "mv_can_batch_correction": can_do(request.user, "mv_batch_correction_outbound"),
        "mv_can_master_upload": can_do(request.user, "mv_master_upload"),
        # The Finish Delivery radios, labelled with the SAP code each one
        # exports as. Handed down from transit.py so the options cannot drift
        # from the mapping that turns them into M1/M2.
        "transit_modes": transit.choices(),
    }
    context.update(extra)
    return context


def _selected_entity(request, param="entity"):
    """Resolve the entity from the request, remembering the last choice.

    Pickers work in one business unit all day, so the selection is sticky per
    session rather than something they re-pick on every screen.
    """
    code = request.POST.get(param) or request.GET.get(param) or request.session.get("merilv_entity")
    # Scoped, so a hand-typed ?entity= cannot select a business unit this user
    # is not mapped to — the switcher is a convenience, this is the boundary.
    queryset = rbac.entities_for(request.user)
    entity = queryset.filter(code=code).first() if code else None
    if entity is None:
        entity = queryset.first()
    if entity is not None:
        request.session["merilv_entity"] = entity.code
    return entity


def _log(request, action, target_type="", target_id="", **metadata):
    ActivityLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def _xlsx_response(buffer, filename):
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Launcher
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@access_required
def index(request):
    """Tool launcher with a snapshot of today's activity."""
    today = timezone.localdate()
    week_ago = timezone.now() - timedelta(days=7)

    # Every tile is team-scoped. A launcher that counts work the rest of the
    # module then refuses to show reads as data loss, not as access control.
    stats = {
        "deliveries_today": scope_queryset_by_team(
            FinishedDelivery.objects.filter(created_at__date=today),
            request.user, field="picker", plant_path=DELIVERY_PLANT_PATH,
        ).distinct().count(),
        "scans_today": scope_queryset_by_team(
            OutboundScan.objects.filter(created_at__date=today),
            request.user, field="session__operator",
        ).count(),
        "open_sessions": scope_queryset_by_team(
            OutboundSession.objects.filter(status=OutboundSession.STATUS_ACTIVE),
            request.user, field="operator",
        ).count(),
        "validations_week": scope_queryset_by_team(
            ValidationRun.objects.filter(created_at__gte=week_ago),
            request.user, field="created_by", plant_path=RUN_PLANT_PATH,
        ).distinct().count(),
    }
    recent_deliveries = scope_queryset_by_team(
        FinishedDelivery.objects.select_related("entity"),
        request.user, field="picker", plant_path=DELIVERY_PLANT_PATH,
    ).order_by("-created_at")[:8]

    # Branch Transfer has no tile of its own: it is a mode of the scanning
    # suite, reached from the tab bar every scanning console carries
    # (_shell/scan_mode_tabs.html), the same way BT Scanning and ZVA05 are
    # reached from inside the Outbound Scanner. A tile here as well made it
    # look like a separate place to go.
    #
    # Inbound Scanner and Barcode Extractor are on that same tab bar and DO
    # still have tiles. That is not an oversight — it is the line as drawn, and
    # if the tiles should follow the tab bar exactly then those two go too.
    tools = [
        {
            "name": "Stock Validator",
            "description": "Reconcile a physical scan file against a SAP stock dump.",
            "url": reverse("merilv:validator"),
            "icon": "fas fa-clipboard-check",
        },
        {
            "name": "Outbound Scanner",
            "description": "Scan and assign serials against delivery requirements.",
            "url": reverse("merilv:outbound"),
            "icon": "fas fa-truck-fast",
        },
        {
            "name": "Barcode Extractor",
            "description": "Parse barcodes, run live scans and live validation.",
            "url": reverse("merilv:extractor"),
            "icon": "fas fa-barcode",
        },
        {
            "name": "Inbound Scanner",
            "description": "Book in stock arriving from another plant and catch transit losses.",
            "url": reverse("merilv:inbound"),
            "icon": "fas fa-dolly",
        },
        {
            "name": "Invoice Consolidator",
            "description": "Flatten invoice PDFs or spreadsheets into one export.",
            "url": reverse("merilv:consolidator"),
            "icon": "fas fa-file-invoice",
        },
        {
            "name": "Monitoring",
            "description": "See who is picking what, right now.",
            "url": reverse("merilv:monitoring"),
            "icon": "fas fa-tower-broadcast",
        },
        {
            "name": "Delivery History",
            "description": "Search completed deliveries and dispatch records.",
            "url": reverse("merilv:delivery_history"),
            "icon": "fas fa-clock-rotate-left",
        },
        {
            "name": "Reports",
            "description": "Throughput, top materials and picker activity.",
            "url": reverse("merilv:reports"),
            "icon": "fas fa-chart-column",
        },
    ]
    if is_merilv_admin(request.user):
        tools.append(
            {
                "name": "Master Data",
                "description": "Materials, customers, couriers, cartons and entity rules.",
                "url": reverse("merilv:masters"),
                "icon": "fas fa-database",
            }
        )

    return render(
        request,
        "merilv/index.html",
        _ctx(request, tools=tools, stats=stats, recent_deliveries=recent_deliveries),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stock validator
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@access_required
def validator(request):
    entity = _selected_entity(request)
    runs_qs = scope_queryset_by_team(
        ValidationRun.objects.select_related("entity"),
        request.user, field="created_by", plant_path=RUN_PLANT_PATH,
    )
    recent = runs_qs.order_by("-created_at")[:10]
    # Aggregated off the same scoped queryset: a KPI counting runs the
    # table below refuses to show is worse than no KPI at all.
    dashboard = runs_qs.aggregate(
        runs=Count("id"), scanned=Sum("total_scanned"), matched=Sum("matched_qty"), variance=Sum("variance_qty")
    )
    # An entity configured for positional parsing with no rules stored parses
    # nothing and says nothing — see entities.positional_rules_misconfigured.
    # Surfaced here so the screen names the cause instead of the operator
    # discovering it as barcodes that mysteriously will not match.
    rules_missing = entity is not None and entity_service.positional_rules_misconfigured(entity)
    return render(request, "merilv/validator.html", _ctx(
        request, entity=entity, recent=recent, dashboard=dashboard,
        rules_missing=rules_missing,
    ))


@login_required
@operate_required
@require_POST
def validator_run(request):
    """Parse both uploads, reconcile them and persist the result."""
    entity = _selected_entity(request)
    errors: list[str] = []

    system_file = request.FILES.get("system_file")
    scan_file = request.FILES.get("scan_file")
    if entity is None:
        errors.append("No active entity is configured. Ask an administrator to set one up.")
    if not system_file:
        errors.append("Upload the SAP stock dump.")
    if not scan_file:
        errors.append("Upload the physical scan file.")

    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect("merilv:validator")

    profile = entity_service.get_profile(entity)
    settings_obj = entity_service.get_validation_settings(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    try:
        system_result = ingest.parse_system_dump(system_file, system_file.name, require_serial=entity.supports_serial)
        scan_result = ingest.parse_scan_file(scan_file, scan_file.name, use_scan_qty=entity.use_scan_qty)
    except ingest.IngestError as exc:
        messages.error(request, str(exc))
        return redirect("merilv:validator")

    if len(scan_result.records) > MAX_VALIDATION_ROWS:
        messages.error(
            request,
            f"The scan file has {len(scan_result.records):,} rows, above the {MAX_VALIDATION_ROWS:,} limit. "
            "Split it into smaller files.",
        )
        return redirect("merilv:validator")

    inventory = validation.InventoryIndex(
        (validation.InventoryItem(**record) for record in system_result.records),
        smart_zero=entity.smart_zero,
    )
    scans = [validation.ScanInput(**record) for record in scan_result.records]
    parsed_scans = [parse_barcode(scan.raw, profile, material_master, rules) for scan in scans]
    outcome = validation.reconcile(
        scans, parsed_scans, inventory, settings_obj, profile,
        material_master=material_master,
    )

    with transaction.atomic():
        run = ValidationRun.objects.create(
            entity=entity,
            name=request.POST.get("name", "").strip(),
            system_file=system_file,
            scan_file=scan_file,
            system_filename=system_file.name,
            scan_filename=scan_file.name,
            total_scanned=outcome.total_scanned,
            total_system=outcome.total_system,
            matched_qty=outcome.matched_qty,
            batch_matched_qty=outcome.batch_matched_qty,
            material_matched_qty=outcome.material_matched_qty,
            variance_qty=outcome.variance_qty,
            reconciliation_pct=outcome.reconciliation_pct,
            settings_snapshot={
                "entity": entity.code,
                "plant_priority": settings_obj.plant_priority,
                "multi_row_consume": settings_obj.multi_row_consume,
                "smart_zero": settings_obj.smart_zero,
            },
            warnings=outcome.warnings + system_result.warnings + scan_result.warnings,
            created_by=request.user,
        )
        ValidationRow.objects.bulk_create(
            [
                ValidationRow(
                    run=run,
                    sequence=row.sequence,
                    raw_scan=row.raw_scan,
                    material=row.material,
                    physical_batch=row.physical_batch,
                    physical_serial=row.physical_serial,
                    scan_qty=row.scan_qty,
                    consumed_qty=row.consumed_qty,
                    condition=row.condition,
                    expiry_date=row.expiry_date,
                    system_batch=row.system_batch,
                    system_serial=row.system_serial,
                    system_plant=row.system_plant,
                    system_location=row.system_location,
                    system_expiry=row.system_expiry,
                    available_batches=row.available_batches,
                    available_serials=row.available_serials,
                    material_description=row.material_description,
                    material_group=row.material_group,
                    ph_level=row.ph_level,
                    customer_code=row.customer_code,
                    customer_name=row.customer_name,
                    billing_document=row.billing_document,
                    billing_date=row.billing_date,
                    status=row.status,
                    status_priority=row.status_priority,
                    match_type=row.match_type,
                    stock_status=row.stock_status,
                    target_location=row.target_location,
                    detail=row.detail,
                    flags=row.flags,
                )
                for row in outcome.rows
            ],
            batch_size=1000,
        )

    _log(request, "validation_run", "ValidationRun", run.pk, entity=entity.code, rows=len(outcome.rows))
    messages.success(request, f"Validated {len(outcome.rows):,} scans — {outcome.reconciliation_pct}% reconciled.")
    return redirect("merilv:validation_detail", pk=run.pk)


@login_required
@access_required
def validation_detail(request, pk):
    run = get_scoped_or_404(
        ValidationRun.objects.select_related("entity"),
        request.user, field="created_by", plant_path=RUN_PLANT_PATH, pk=pk,
    )
    rows = run.rows.all()

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        rows = rows.filter(status=status_filter)

    # Stock-condition filter, so the Good/Damage/Expiry KPI tiles are clickable
    # rather than decorative. "expiry" spans three stock statuses because that is
    # how the tile counts them — a filter that disagreed with the number above it
    # would be worse than no filter.
    stock_filter = request.GET.get("stock", "").strip()
    if stock_filter == "expiry":
        rows = rows.filter(
            stock_status__in=[
                ValidationRow.STOCK_EXPIRED,
                ValidationRow.STOCK_NEAR_EXPIRY,
                ValidationRow.STOCK_SHORT_EXPIRY,
            ]
        )
    elif stock_filter:
        rows = rows.filter(stock_status=stock_filter)

    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(material__icontains=query)
            | Q(physical_batch__icontains=query)
            | Q(physical_serial__icontains=query)
            | Q(system_batch__icontains=query)
            | Q(system_serial__icontains=query)
            | Q(detail__icontains=query)
            | Q(raw_scan__icontains=query)
        )

    sort_fields = {
        "material": "material", "physical": "physical_batch", "system": "system_batch",
        "plant": "system_plant", "qty": "scan_qty", "status": "status_priority", "stock": "stock_status",
        # Serials are sortable too — grouping a run by serial is how a duplicate
        # or an off-by-one range gets spotted by eye.
        "phy_serial": "physical_serial", "sys_serial": "system_serial",
        "expiry": "expiry_date",
    }
    sort = request.GET.get("sort", "").strip()
    direction = request.GET.get("dir", "asc").lower()
    if sort in sort_fields:
        rows = rows.order_by(("-" if direction == "desc" else "") + sort_fields[sort], "sequence", "id")

    counts = {
        entry["status"]: entry["n"]
        for entry in run.rows.values("status").annotate(n=Count("id"))
    }

    # Stock-condition KPI tiles — grouped by qty (not row count) so they add
    # up to total_scanned the same way the screenshot does.
    stock_qty = {
        entry["stock_status"]: entry["qty"] or 0
        for entry in run.rows.values("stock_status").annotate(qty=Sum("scan_qty"))
    }
    batch_mismatch_qty = run.rows.filter(status=ValidationRow.STATUS_BATCH_MISMATCH).aggregate(
        qty=Sum("scan_qty")
    )["qty"] or 0
    # The three counts the feedback asked for and the strip did not carry.
    #
    # Serial mismatch is not a status of its own — a scan whose batch matched
    # while the serial disagreed is stored as PARTIAL, the same status as a
    # clean batch match with no serial scanned. Counting every PARTIAL would
    # overstate it, so this counts only the rows whose two serials genuinely
    # fail to correspond, which is the same condition the row detail uses to
    # say "scanned serial differs".
    #
    # Decided in Python rather than SQL because that condition is
    # validation.serial_matches()' to answer, and answering it needs the
    # entity's concatenation rules — which no database expression can carry.
    # Comparing the two columns with F() is what let this tile contradict the
    # engine: a scan of batch L2512001 serial 029 against SAP's L2512001029 is a
    # Concat match the engine accepts, and was still counted here as a mismatch.
    profile = entity_service.get_profile(run.entity)
    concat_delimiter = entity_service.get_validation_settings(run.entity).concat_delimiter
    serial_mismatch_qty = sum(
        (
            candidate.scan_qty
            for candidate in run.rows.filter(status=ValidationRow.STATUS_PARTIAL)
            .exclude(physical_serial="")
            .exclude(system_serial="")
            .only("physical_batch", "physical_serial", "system_serial", "scan_qty")
            if not validation.serial_matches(
                candidate.physical_batch,
                candidate.physical_serial,
                candidate.system_serial,
                profile,
                concat_delimiter,
            )
        ),
        Decimal(0),
    )

    # "Not found" is the plain variance — the material is not in SAP at all.
    # STOCK_EXHAUSTED is deliberately excluded: that material *is* in SAP, just
    # fully claimed, and folding the two would send an operator hunting for
    # stock that is sitting there under another batch.
    not_found_qty = run.rows.filter(
        status=ValidationRow.STATUS_VARIANCE
    ).aggregate(qty=Sum("scan_qty"))["qty"] or 0

    duplicate_qty = run.rows.filter(
        status=ValidationRow.STATUS_DUPE_SCAN
    ).aggregate(qty=Sum("scan_qty"))["qty"] or 0

    kpis = {
        "batch_mismatch": batch_mismatch_qty,
        "serial_mismatch": serial_mismatch_qty,
        "not_found": not_found_qty,
        "duplicate": duplicate_qty,
        # Every match tier consumes SAP stock, not just an exact serial
        # match — a row matched on batch alone, or on material alone, still
        # claimed a unit of `total_system`. Subtracting only `matched_qty`
        # left batch- and material-matched quantity double-counted as
        # "remaining", which on this run overstated it by exactly
        # batch_matched_qty + material_matched_qty (13 + 9 = 22 units on the
        # JPS run this was caught against: 74 - 50 = 24 here before the fix,
        # vs. the correct 74 - 50 - 13 - 9 = 2 the reference tool shows for
        # the same file as "Remaining SAP").
        "remaining_stock": (
            run.total_system - run.matched_qty
            - run.batch_matched_qty - run.material_matched_qty
        ),
        "good": stock_qty.get(ValidationRow.STOCK_GOOD, 0),
        "damage": stock_qty.get(ValidationRow.STOCK_DAMAGE, 0),
        # Split into the three tiles the Stock Validator reference tool shows
        # separately (Expired / Near Expiry / Short Expiry) instead of one
        # combined "Expiry/Near" figure — a single lumped number was exactly
        # the kind of tile nobody could tell apart from "how many are
        # damaged" or "which of these is just short-dated". Each already has
        # a matching ?stock= filter value below (STOCK_EXPIRED == "expired",
        # etc.), so no new filtering logic is needed for any of the three.
        "expired": stock_qty.get(ValidationRow.STOCK_EXPIRED, 0),
        "near_expiry": stock_qty.get(ValidationRow.STOCK_NEAR_EXPIRY, 0),
        "short_expiry": stock_qty.get(ValidationRow.STOCK_SHORT_EXPIRY, 0),
    }
    return render(
        request,
        "merilv/validation_detail.html",
        _ctx(
            request,
            run=run,
            page_obj=paginate(request, rows),
            counts=counts,
            kpis=kpis,
            status_filter=status_filter,
            stock_filter=stock_filter,
            q=query,
            status_choices=ValidationRow.STATUS_CHOICES,
            sort=sort,
            direction=direction,
        ),
    )


@login_required
@access_required
def validation_export(request, pk):
    run = get_scoped_or_404(
        ValidationRun.objects.select_related("entity"),
        request.user, field="created_by", plant_path=RUN_PLANT_PATH, pk=pk,
    )
    rows = [
        {
            "raw_scan": row.raw_scan,
            "material": row.material,
            "material_description": row.material_description,
            "material_group": row.material_group,
            "ph_level": row.ph_level,
            "physical_batch": row.physical_batch,
            "physical_serial": row.physical_serial,
            "scan_qty": row.scan_qty,
            "consumed_qty": row.consumed_qty,
            "system_batch": row.system_batch,
            "system_serial": row.system_serial,
            "system_plant": row.system_plant,
            "system_location": row.system_location,
            "system_expiry": row.system_expiry,
            "available_batches": ", ".join(row.available_batches),
            "available_serials": ", ".join(row.available_serials),
            "stock_status": row.get_stock_status_display(),
            "target_location": row.target_location,
            "status": row.get_status_display(),
            "detail": row.detail,
        }
        for row in run.rows.all()
    ]
    summary = {
        "Entity": run.entity.code,
        "Total scanned": run.total_scanned,
        "Total in SAP": run.total_system,
        "Matched": run.matched_qty,
        "Batch matched": run.batch_matched_qty,
        "Material matched": run.material_matched_qty,
        "Variance": run.variance_qty,
        "Reconciliation %": run.reconciliation_pct,
    }
    buffer = export.export_validation(str(run), rows, summary)
    return _xlsx_response(buffer, export.filename_for("validation", run.pk))


@login_required
@access_required
def validation_history(request):
    runs = ValidationRun.objects.select_related("entity", "created_by").order_by("-created_at")
    runs = scope_queryset_by_team(
        runs, request.user, field="created_by", plant_path=RUN_PLANT_PATH,
    )
    entity_code = request.GET.get("entity", "").strip()
    if entity_code:
        runs = runs.filter(entity__code=entity_code)
    return render(
        request,
        "merilv/validation_history.html",
        _ctx(request, page_obj=paginate(request, runs), entity_code=entity_code),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Outbound scanner
# ─────────────────────────────────────────────────────────────────────────────
#: The three kinds of outbound scanning, as the client describes them: against
#: a YVL06 delivery dump ("normal"), straight onto open sales-order lines when
#: SAP deliveries do not exist yet ("zva05"), or picking a branch-transfer plan
#: on the floor with cartons and colour zones ("btscan"). These used to be
#: separate modules in the nav; none of them is a different *tool* from plain
#: outbound scanning, only a different source document or a different console
#: onto the same picking job, which is why they are modes here now.
OUTBOUND_MODE_NORMAL = "normal"
OUTBOUND_MODE_ZVA05 = "zva05"
OUTBOUND_MODE_BTSCAN = "btscan"
OUTBOUND_MODES = (
    (OUTBOUND_MODE_NORMAL, "Normal outbound", "Scan against a YVL06 delivery dump."),
    (OUTBOUND_MODE_ZVA05, "ZVA05 auto-assign", "Assign scans to open sales orders before deliveries exist."),
    (OUTBOUND_MODE_BTSCAN, "BT Scanning", "Pick a branch transfer on the floor: boxes, zones and location priority."),
)


def scope_outbound_sessions(queryset, user):
    """Narrow ``queryset`` to the outbound sessions this user may work on.

    A thin call onto :func:`rbac.scope_visible`, named here because the
    landing list and the console both have to ask it with the SAME plant path
    — they used to ask two different questions in two different places and
    disagree, so a supervisor's session was listed to a picker and then
    answered 404 when they clicked it.

    The ENTITY bound arrives through the same call: :func:`rbac.scope_visible`
    applies it to any model that has an entity, so it reaches the console and
    every mutation endpoint via :func:`get_session_or_404` without this
    function or its callers having to remember. Filtering the landing query
    alone would not have been a boundary — it would only have hidden the link.
    """
    return rbac.scope_visible(queryset, user, field="operator", plant_path=SESSION_PLANT_PATH)


def get_session_or_404(user, pk, *related):
    """One outbound session this user may work on, by pk, or 404.

    The counterpart to :func:`scope_outbound_sessions` for the console and its
    mutation endpoints — scoping only the landing list is not a boundary, it
    just hides the link.

    404 rather than 403, for the reason rbac.get_scoped_or_404 gives: a 403 on
    a row that exists and a 404 on one that does not lets an outsider map which
    ids are real.
    """
    queryset = OutboundSession.objects.all()
    if related:
        queryset = queryset.select_related(*related)
    try:
        return get_object_or_404(scope_outbound_sessions(queryset, user), pk=pk)
    except (TypeError, ValueError) as exc:
        # A hand-edited form post with pk="abc" makes the IntegerField raise
        # rather than simply not match, which would surface as a 500.
        raise Http404(f"Malformed lookup: {exc}") from exc


@login_required
@access_required
def outbound(request):
    """Outbound scanner landing, for whichever scanning mode is selected."""
    entity = _selected_entity(request)
    mode = request.GET.get("mode")
    if mode not in {key for key, _label, _hint in OUTBOUND_MODES}:
        mode = OUTBOUND_MODE_NORMAL

    sessions = []
    session_page = None
    session_total = 0
    session_q = ""
    dumps = []
    bt_plans = []
    bt_sessions = []
    if mode == OUTBOUND_MODE_ZVA05:
        dumps = scope_queryset_by_team(
            rbac.scope_entities(
                Zva05Dump.objects.select_related("entity", "created_by").filter(is_finished=False),
                request.user, field="entity__code",
            ),
            request.user, field="created_by",
        ).order_by("-created_at")[:20]
    elif mode == OUTBOUND_MODE_BTSCAN:
        bt_plans = scope_queryset_by_team(
            rbac.scope_entities(
                BranchTransferPlan.objects.select_related("entity").filter(is_finished=False),
                request.user, field="entity__code",
            ),
            request.user, field="created_by",
        ).order_by("-created_at")[:40]
        bt_sessions = scope_queryset_by_team(
            rbac.scope_entities(
                BtScanSession.objects.select_related("plan", "entity", "created_by").filter(is_finished=False),
                request.user, field="entity__code",
            ),
            request.user, field="created_by",
        ).order_by("-created_at")[:20]
    else:
        sessions = (
            # operator joined too: the list names who opened each session now
            # that it is not only your own work, and twenty rows would
            # otherwise be twenty extra queries.
            rbac.scope_entities(
                OutboundSession.objects.select_related("entity", "dump", "operator"),
                request.user, field="entity__code",
            )
            .filter(status=OutboundSession.STATUS_ACTIVE)
            # Bounded by what this user MAY see, which is the actual
            # boundary — a picker in CARDIO must not be shown MHPL's open
            # sessions, and must not be able to open one by guessing its id.
            # Both counts drive the Delete control's confirmation text, so they
            # are annotated rather than counted per row in the template — 20
            # rows would otherwise be 40 extra queries.
            #
            # Correlated subqueries rather than Count() over the relations:
            # dump__lines and scans are both to-many, so joining both put every
            # line next to every scan of the same session. COUNT(DISTINCT ...)
            # repaired the totals, which is why the numbers on screen were
            # right, but it does nothing about the intermediate rows the
            # database has to sort to get there. Each subquery counts on its
            # own and returns one value per session, so the join disappears
            # instead of being corrected after the fact.
            .annotate(
                line_count=Coalesce(
                    Subquery(
                        DeliveryLine.objects
                        .filter(dump_id=OuterRef("dump_id"))
                        .order_by()
                        .values("dump_id")
                        .annotate(n=Count("pk"))
                        .values("n"),
                        output_field=IntegerField(),
                    ),
                    0,
                ),
                scan_count=Coalesce(
                    Subquery(
                        OutboundScan.objects
                        .filter(session_id=OuterRef("pk"))
                        .order_by()
                        .values("session_id")
                        .annotate(n=Count("pk"))
                        .values("n"),
                        output_field=IntegerField(),
                    ),
                    0,
                ),
            )
            .order_by("-created_at")
        )

        # The switcher narrows, it does not bound. Applying it to everyone
        # pinned an ADMIN — who is never entity-scoped — to whichever unit
        # happened to be selected, and they lost the all-entities view of
        # active sessions they had before. Reported from the floor: "unable to
        # see other entity active session in admin ID". So the selection is
        # applied only to somebody the selection is a real choice FOR: a user
        # already bound to one or more entities. For an unbound user the list
        # is everything they may see, which is all of it.
        if rbac.entity_scope(request.user) is not None and entity is not None:
            sessions = sessions.filter(entity=entity)
        # The same rule the console applies, rather than a second one written
        # here. This list was scoped by plant alone while the console was
        # scoped by team alone, so it listed rows that answered 404 when they
        # were clicked — including, for a picker, every session a supervisor
        # had uploaded for them to pick.
        sessions = scope_outbound_sessions(sessions, request.user)

        # Reachable, not just listed.
        #
        # This was a hard `[:20]` over `-created_at`, so the twenty NEWEST open
        # sessions were the only ones that existed as far as the screen was
        # concerned. An older session somebody left open could not be opened,
        # resumed or cleaned up at all — and the cross-session serial guard
        # happily blocks a scan against exactly such a session, telling the
        # operator to "undo it there" with no there to go to. That pairing is
        # what made the block unrecoverable.
        #
        # Searched on the session's own name, its id, and the delivery numbers
        # inside it, because those are the three things somebody arrives
        # holding: the number from the error message, the number on the box, or
        # what the supervisor called the upload.
        session_q = (request.GET.get("sq") or "").strip()
        if session_q:
            match = Q(name__icontains=session_q) | Q(dump__lines__delivery_no__icontains=session_q)
            if session_q.isdigit():
                match |= Q(pk=int(session_q))
            # distinct(): the delivery join multiplies a session by its lines.
            sessions = sessions.filter(match).distinct()
        session_page = paginate(request, sessions, param="spage", page_size=20)
        session_total = session_page.paginator.count
        sessions = list(session_page.object_list)

    # Each list shows the VIEWER's own place in the record, not "the record's
    # focus" — there is no such thing any more (merilv/focus.py). Attached per
    # row rather than looked up in the template, which cannot index a dict by a
    # variable key.
    for row in sessions:
        row.my_focus = focus.get(row, request.user).value or focus.get(row, request.user).customer_code
    for row in dumps:
        row.my_focus = focus.get(row, request.user).value
    for row in bt_sessions:
        row.my_focus = focus.get(row, request.user).value

    return render(
        request,
        "merilv/outbound.html",
        _ctx(
            request,
            entity=entity,
            sessions=sessions,
            session_page=session_page,
            session_total=session_total,
            session_q=session_q,
            zva05_dumps=dumps,
            bt_plans=bt_plans,
            bt_sessions=bt_sessions,
            mode=mode,
            modes=OUTBOUND_MODES,
        ),
    )


@login_required
@operate_required
@require_POST
def outbound_upload(request):
    """Ingest a YVL06 dump and open a scanning session against it."""
    entity = _selected_entity(request)
    dump_file = request.FILES.get("dump_file")
    if entity is None or not dump_file:
        messages.error(request, "Select an entity and upload a YVL06 delivery dump.")
        return redirect("merilv:outbound")

    try:
        result = ingest.parse_delivery_dump(
            dump_file, dump_file.name, aggregate=entity.aggregate_delivery_lines
        )
    except ingest.IngestError as exc:
        messages.error(request, str(exc))
        return redirect("merilv:outbound")

    customer_master = entity_service.build_customer_master(entity)

    with transaction.atomic():
        dump = DeliveryDump.objects.create(
            entity=entity,
            source_file=dump_file,
            original_filename=dump_file.name,
            row_count=len(result.records),
            skipped_count=result.skipped,
            warnings=result.warnings,
            uploaded_by=request.user,
        )
        bulk_create_in_chunks(
            DeliveryLine.objects,
            (
                DeliveryLine(
                    dump=dump,
                    entity=entity,
                    sequence=record["sequence"],
                    delivery_no=record["delivery_no"],
                    item_no=record["item_no"],
                    material=record["material"],
                    batch=record["batch"],
                    original_batch=record["batch"],
                    plant=record["plant"],
                    location=record["location"],
                    company_code=record["company_code"],
                    ean=record["ean"],
                    required_qty=record["required_qty"],
                    delivery_date=record["delivery_date"],
                    bill_to_code=record["bill_to_code"],
                    bill_to_name=record["bill_to_name"],
                    ship_to_code=record["ship_to_code"],
                    ship_to_name=record["ship_to_name"],
                    customer_name=entity_service.resolve_customer_name(
                        customer_master, record["bill_to_code"], record["customer_name"]
                    ),
                    ref_document=record["ref_document"],
                    customer_po_no=record["customer_po_no"],
                    port_of_loading=record["port_of_loading"],
                    discharging_place=record["discharging_place"],
                    final_destination=record["final_destination"],
                    country_of_origin=record["country_of_origin"],
                    source_items=record.get("source_items", []),
                )
                for record in result.records
            ),
            batch_size=1000,
        )
        session = OutboundSession.objects.create(
            entity=entity,
            dump=dump,
            name=request.POST.get("name", "").strip() or dump_file.name,
            operator=request.user,
        )

    _log(request, "outbound_dump_upload", "DeliveryDump", dump.pk, lines=len(result.records))
    for warning in result.warnings:
        messages.warning(request, warning)
    messages.success(request, f"Loaded {len(result.records):,} delivery lines.")
    return redirect("merilv:outbound_session", pk=session.pk)


def _wants_json(request) -> bool:
    """True when JavaScript is asking, rather than a browser navigating.

    Every in-place action on these screens posts with this header (see post()
    in static/merilv/js/scanner.js and the async-form handler in datatable.js),
    so it is a reliable signal — and the two callers need genuinely different
    answers, not the same one in two shapes: one repaints where it stands, the
    other follows a redirect and reads the page-top alert block.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required
@operate_required
@require_POST
def outbound_session_delete(request, pk):
    """Delete an active session and the dump behind it — the wrong-file escape.

    Uploading the wrong YVL06 is the one mistake this screen had no answer for:
    the session could not be removed, so it sat in the active list forever,
    and its lines stayed matchable by the scanner. Nothing short of a hard
    delete fixes that — a hidden or "abandoned" session still holds the lines
    a scan can land on.

    The deletion is real and complete: the session, its scans, its scan log,
    the dump, the dump's delivery lines and the uploaded file itself. What
    survives is the :class:`ActivityLog` entry written below, which is an
    independent row rather than a relation — so the audit trail records what
    was removed, by whom, and what was in it, after the data itself is gone.

    Two guards, both server-side:

    * A session with a finished delivery is refused outright. Those have
      shipped, their serials are in the dispatched ledger, and
      ``FinishedDelivery.session`` is ``SET_NULL`` — so deleting the session
      would silently detach a dispatch record from its pick rather than
      failing. "I uploaded the wrong file" is discovered before anything
      ships; after that it is a different problem with a different answer.
    * Otherwise an admin may delete any active session, and the picker who
      opened it may delete it while it still has no scans. A wrong file is
      noticed immediately, so the common case needs no supervisor; once
      scanning has started, removing the work is a supervisor's call.
    """
    session = get_session_or_404(request.user, pk, "entity", "dump")

    # A refusal used to redirect to the session console and print the reason
    # in the page-top alert block, so clicking Delete on the list took the
    # operator to a different screen, scrolled to the top of it, to be told no.
    # The list asks; the list is answered.
    console = _wants_json(request)

    def refuse(message):
        if console:
            return JsonResponse({"ok": False, "message": message}, status=409)
        messages.error(request, message)
        return redirect("merilv:outbound_session", pk=session.pk)

    finished = list(
        FinishedDelivery.objects.filter(session=session).values_list("delivery_no", flat=True)
    )
    if finished:
        return refuse(
            f"Session {session} cannot be deleted — deliver{'y' if len(finished) == 1 else 'ies'} "
            f"{', '.join(finished)} already finished and shipped from it."
        )

    scan_count = session.scans.count()
    is_admin = is_merilv_admin(request.user)
    # Whose session it is, checked here rather than left to the scope.
    #
    # It used to be implied: only the operator's own team could reach a
    # session at all, so "reachable and not an admin" meant "mine" or "my
    # team's". Sessions are now shared across a plant (scope_outbound_sessions)
    # and that implication no longer holds — without this, any picker could
    # delete the dump a manager had just uploaded, right up until the first
    # scan landed on it.
    #
    # A supervisor keeps it: the scope already confines them to their own
    # plants, and removing a wrong upload at their site is the job. Everyone
    # else may remove only what they opened.
    if not rbac.is_merilv_manager(request.user) and session.operator_id != request.user.pk:
        owner = (
            (session.operator.get_full_name() or session.operator.username)
            if session.operator else "someone else"
        )
        return refuse(
            f"Session {session} was opened by {owner} — only they or a supervisor can delete it."
        )
    if not is_admin and scan_count:
        return refuse(
            f"Session {session} already has {scan_count:,} scan(s) — only a Meril-V admin "
            "can delete it once scanning has started."
        )

    dump = session.dump
    # Everything the audit entry needs, read before any of it is deleted.
    lines = dump.lines.count() if dump else 0
    deliveries = (
        sorted({no for no in dump.lines.values_list("delivery_no", flat=True) if no})
        if dump else []
    )
    audit = {
        "session": str(session),
        "entity": session.entity.code if session.entity_id else "",
        "dump": (dump.original_filename or "") if dump else "",
        "lines": lines,
        "scans": scan_count,
        "attempts": session.logs.count(),
        # Capped: a dump can span dozens of deliveries and the log renders this
        # inline. The count is what matters once the list stops being readable.
        "deliveries": ", ".join(deliveries[:20]) + ("…" if len(deliveries) > 20 else ""),
        "delivery_count": len(deliveries),
        "opened_by": session.operator.get_username() if session.operator_id else "",
    }
    label = str(session)

    with transaction.atomic():
        # The session first, so its scans and scan log go with it while their
        # line FKs are still intact; then the dump, which takes the delivery
        # lines. The reverse order would run DeliveryLine's SET_NULL over every
        # scan row on the way out for nothing.
        session.delete()
        if dump is not None and not dump.sessions.exists():
            stored = dump.source_file
            dump.delete()
            # After the row, and outside the ORM's cascade: a file left behind
            # is litter, but a file removed while the row survives would make
            # the dump un-downloadable. Missing or already-purged storage must
            # not turn a completed delete into a 500.
            if stored:
                try:
                    stored.delete(save=False)
                except (OSError, ValueError):
                    logger.warning("Could not remove dump file %s", stored.name, exc_info=True)

    _log(request, "outbound_session_delete", "OutboundSession", pk, **audit)
    note = (
        f"Session {label} deleted — {lines:,} delivery line(s) and {scan_count:,} scan(s) removed. "
        "The deletion is recorded in the activity log."
    )
    if console:
        # The row is gone from the table the moment this returns; the list does
        # not reload to find that out.
        return JsonResponse({"ok": True, "message": note, "session_id": pk})
    messages.success(request, note)
    return redirect("merilv:outbound")


def _material_descriptions(entity, lines):
    """``{material code: description}`` for the materials in one dump.

    Display only — the SAP upload sheet's column set is fixed and this never
    reaches it (see services/export.py:OUTBOUND_BASE_HEADERS). A picker reading
    "HDAA-32/40+" has no way to tell a left from a right component without it,
    which is what the request asks for.

    One query over just the codes the dump uses, not the whole master: MHPL's
    is 29,000 rows and this runs on every two-second console poll. Both the SAP
    code and the label's scan code are matched, because a delivery line carries
    whichever of the two its source file used.
    """
    codes = {(line.material or "").strip() for line in lines}
    codes.discard("")
    if not codes or entity is None:
        return {}

    lookup = {}
    rows = MaterialMaster.objects.filter(entity=entity).filter(
        Q(material_code__in=codes) | Q(scan_code__in=codes)
    ).values_list("material_code", "scan_code", "material_description")
    for material_code, scan_code, description in rows:
        if not description:
            continue
        for key in (material_code, scan_code):
            key = (key or "").strip()
            if key:
                lookup.setdefault(key, description)
    return lookup


def _has_focus(session, user):
    """True when scanning is locked to a delivery, or to an allowed customer.

    The single reading of the focus rule: :func:`_focus_gate` refuses the
    request when this is false, and :func:`_session_payload` hands the same
    boolean to the console as ``can_scan``, so the control the console disables
    and the request the server refuses can never disagree.
    """
    current = focus.get(session, user)
    if current.value:
        return True
    return bool(
        current.customer_code
        and session.entity
        and session.entity.allow_customer_focus
    )


def _session_payload(session, user):
    """Everything the scanner console needs to redraw itself.

    Takes the operator, because focus is theirs alone — see merilv/focus.py.
    Handing this one session's payload to two people used to hand them one
    focus, and the console polls it every two seconds.
    """
    current = focus.get(session, user)
    lines = list(session.dump.lines.all()) if session.dump else []
    progress = outbound_engine.summarise(lines)
    descriptions = _material_descriptions(session.entity, lines)

    # The courier each delivery would be finished with, so the Finish Delivery
    # modal can show it before the operator types one. The field's hint has
    # always claimed it was pre-filled; until now nothing did the filling, and
    # the mapping was applied silently at submit time instead.
    couriers = (
        _resolve_couriers_for(session.entity, lines)
        if (lines and session.entity) else {}
    )
    scanned = float(sum(float(line.scanned_qty) for line in lines))
    required = float(sum(float(line.required_qty) for line in lines))

    # Which deliveries are closed. The console stays on this screen through a
    # whole dump now, so "I finished that one" has to be visible on the card
    # rather than inferred from the operator's memory — and a finished delivery
    # refuses further scans (see _reject_if_delivery_already_finished), which a
    # picker should be able to see before they scan into it rather than after.
    #
    # One query for all three answers — which are finished, which are
    # invoiceable, and what was recorded on each — because the Finish modal
    # now reads back a closed delivery's paperwork rather than offering an
    # empty form for it.
    finished_rows = {
        row.delivery_no: row
        for row in FinishedDelivery.objects.filter(session=session).select_related("picker")
    }
    finished_numbers = set(finished_rows)
    # Which of those have their courier paperwork in. The invoice sheet is
    # fourteen columns of dispatch detail and every one is read off
    # FinishedDelivery, so a delivery finished but still pending its courier
    # has nothing to put in them — the console greys the control and says which
    # of the two steps is outstanding rather than handing over an empty sheet.
    invoiceable = {
        no for no, row in finished_rows.items()
        if row.status != FinishedDelivery.STATUS_PENDING_COURIER
    }

    # Assigned serials per line — the client's "material details + assigned
    # serials not shown per line" complaint (Row 2.1). Built as one grouped
    # query rather than N+1 per-line lookups.
    serials_by_line: dict[int, list[str]] = {}
    if lines:
        for line_id, serial_only in session.scans.filter(
            line_id__in=[line.pk for line in lines]
        ).exclude(serial_only="").order_by("created_at").values_list("line_id", "serial_only"):
            serials_by_line.setdefault(line_id, []).append(serial_only)

    return {
        # What this operator may do, so the console can grey a control rather
        # than offer one the server will refuse. Sent per poll because it is
        # cheap and because role changes should not need a reload.
        # What was actually recorded when each delivery was closed, keyed by
        # delivery number. The Finish modal fills its fields from this and
        # greys them: a delivery is finished once, and an operator reopening
        # the form should see what went on the docket rather than a blank form
        # that looks like an invitation to do it again.
        "finished_detail": {no: _finished_paperwork(row) for no, row in finished_rows.items()},
        "deliveries": [
            {
                "delivery_no": item.delivery_no,
                "customer_name": item.customer_name,
                "customer_code": item.customer_code,
                # Plant reaches the console per delivery as well as per line:
                # a picker needs to know which plant a delivery ships from
                # without expanding it, and plant-scoped users are filtered on
                # exactly this value (see plant_scope in outbound()).
                "plant": item.plant,
                "required": item.required_qty,
                "scanned": item.scanned_qty,
                "pending": item.pending_qty,
                "percent": item.percent,
                "complete": item.is_complete,
                # Empty string, not null: the console writes these straight
                # into data-* attributes, where null stringifies to "null".
                "courier": _courier_of(couriers, item.delivery_no)["name"],
                "courier_number": _courier_of(couriers, item.delivery_no)["number"],
                "courier_vehicle": _courier_of(couriers, item.delivery_no)["vehicle"],
                "courier_vehicle_type": _courier_of(couriers, item.delivery_no)["vehicle_type"],
                "courier_source": _courier_of(couriers, item.delivery_no)["source"],
                # Mode of transit for this delivery's carrier, so Finish
                # Delivery can pre-select it instead of defaulting every
                # dispatch to Road.
                "courier_mode": _courier_of(couriers, item.delivery_no)["mode"],
                # Line counts as well as unit quantities: a delivery card that
                # opens into its own line list has to be able to say how many
                # lines it is about to show.
                "line_count": item.line_count,
                "completed_lines": item.completed_lines,
                # Closed, paperwork recorded, no longer scannable. Distinct
                # from `complete`, which only says every line reached its
                # required quantity — a delivery can be complete and not yet
                # finished, and (when it was shipped short on purpose) finished
                # without being complete.
                "finished": item.delivery_no in finished_numbers,
                "invoiceable": item.delivery_no in invoiceable,
            }
            for item in progress
        ],
        "lines": [
            {
                "id": line.pk,
                "delivery_no": line.delivery_no,
                "item_no": line.item_no,
                "material": line.material,
                # Display only — deliberately absent from the export, whose
                # column set SAP matches by name.
                "material_description": descriptions.get((line.material or "").strip(), ""),
                "batch": line.batch,
                # What the dump said before anyone overrode it, and whether
                # they did. The console shows the original beside the
                # replacement: a batch that silently differs from the paperwork
                # is the kind of thing nobody notices until dispatch.
                "original_batch": line.original_batch or "",
                "batch_overridden": bool(line.batch_overridden),
                # Whether the batch may still be changed. Locked the moment
                # anything is scanned onto the line, because repointing an
                # already-scanned unit at a different batch corrupts the
                # dispatch record — outbound_line_batch re-checks this under a
                # row lock, and this is only so the console can say why the
                # control is not offered instead of leaving a dead cell.
                "batch_locked": float(line.scanned_qty) > 0,
                "required": float(line.required_qty),
                "scanned": float(line.scanned_qty),
                "pending": float(line.pending_qty),
                "plant": line.plant,
                # Carried per line so the console can show a delivery's own
                # lines without a second request, and so that a card opened
                # for one delivery can filter on a value it already has.
                "customer_name": line.customer_name or line.bill_to_name,
                "customer_code": line.bill_to_code or line.ship_to_code,
                "serials": serials_by_line.get(line.pk, []),
            }
            for line in lines
        ],
        "totals": {
            "required": required,
            "scanned": scanned,
            "pending": max(required - scanned, 0),
            "percent": round(min(scanned / required, 1) * 100, 1) if required else 0,
        },
        "focus_delivery": current.value,
        "focus_customer": current.customer_code,
        "allow_customer_focus": bool(session.entity and session.entity.allow_customer_focus),
        # Whether picking may run at all right now. Nothing is scanned or
        # assigned until a delivery is focused (see _focus_gate), and the
        # console needs to say so on the screen rather than letting an operator
        # scan into a refusal.
        "can_scan": _has_focus(session, user),
        # Whether this entity's barcodes carry a serial at all.
        #
        # MLSIPL is configured supports_serial=False, so the parser always
        # returns a blank serial and the "Assigned serials" column is
        # permanently empty for it — correctly, because there is nothing to
        # assign. Rendered as a bare dash that read as a fault, and prompted
        # "it gave me no serial codes assigned in the column". The console now
        # says which it is instead of leaving the reader to guess.
        "supports_serial": bool(session.entity and session.entity.supports_serial),
    }


#: Rows of scan history rendered into the console. 200 rather than 25: the feed
#: is filterable by status, and a picker searching for "did that code go
#: through?" needs enough of the session in the DOM for the filter to find it.
#: The status badges count the whole session regardless — see
#: :func:`_session_scan_counts`.
SCAN_FEED_LIMIT = 200


def _session_scan_counts(session):
    """All / scanned / rejected over the *whole* session, not the rendered slice.

    "Scanned" means the code is assigned to a line right now, which is not the
    same as "a success row exists": ``ScanLog`` is append-only, so an accepted
    scan that was later undone still has its success row. The surviving
    ``OutboundScan`` rows are the authority, and they are joined here as a
    subquery rather than pulled into Python so the count stays one query
    whatever the session's size.
    """
    total = session.logs.count()
    scanned = session.logs.filter(
        status=ScanLog.STATUS_SUCCESS,
        raw_scan__in=session.scans.exclude(raw_scan="").values("raw_scan"),
    ).count()
    return {"all": total, "accepted": scanned, "rejected": max(total - scanned, 0)}


@login_required
@access_required
def outbound_session(request, pk):
    session = get_session_or_404(request.user, pk, "entity", "dump")
    payload = _session_payload(session, request.user)
    # One pass over the session's surviving scans feeds both maps: where each
    # accepted code landed, and the serial it was given. The serial cannot be
    # read back off the log — an item whose label carries none is assigned a
    # running BATCH || 001, and that number exists only here.
    surviving = [
        scan for scan in session.scans.select_related("line") if scan.raw_scan
    ]
    feed, rendered = build_scan_feed(
        session.logs.all()[:SCAN_FEED_LIMIT],
        {
            scan.raw_scan: (scan.line.delivery_no if scan.line_id else "")
            for scan in surviving
        },
        {scan.raw_scan: scan.serial_only for scan in surviving if scan.serial_only},
    )
    counts = _session_scan_counts(session)
    # What the cap left out, per status. The filter bar tallies the rows it can
    # see in the DOM, so without this the badges would report the slice rather
    # than the session — which is exactly what they did: a session with 729
    # accepted and 40 rejected scans rendered "All 200 / Scanned 200 /
    # Rejected 0", and the Scanned view was missing 529 of its rows.
    overflow = {key: max(counts[key] - rendered[key], 0) for key in counts}
    boxes = BoxProfile.objects.filter(is_active=True)
    return render(
        request,
        "merilv/outbound_session.html",
        _ctx(
            request,
            session=session,
            entity=session.entity,
            payload=payload,
            feed=feed,
            counts=counts,
            overflow=overflow,
            feed_limit=SCAN_FEED_LIMIT,
            boxes=boxes,
        ),
    )


def _attempt_delivery_no(session, outcome, user):
    """The delivery a scan attempt belongs to, for the audit row.

    The line it landed on when it was accepted; otherwise the delivery the
    session is focused on, which — since nothing may be scanned unfocused — is
    the delivery the operator was picking when the rejection happened. Customer
    focus leaves this blank: the target was a customer, not one delivery, so
    naming a delivery here would be an invention.
    """
    if outcome.line is not None and getattr(outcome.line, "delivery_no", ""):
        return outcome.line.delivery_no
    return focus.get(session, user).value


#: Message shown when picking is attempted with nothing focused. One string,
#: because the single-scan path, the bulk path and the console's own pre-flight
#: check all have to say the same thing.
NO_FOCUS_MESSAGE = (
    "Select a delivery before scanning. Click a delivery in the list on the "
    "right to focus it, then scan."
)


def _focus_gate(session, user):
    """Refuse the request when nothing is focused, else ``None``.

    A scan with no focus is resolved against *every* open line in the dump, so
    it lands on whichever delivery happens to want that material first — which
    is a guess, made silently, about which order the operator is picking. The
    agreed rule is that no assignment runs until the operator has said which
    delivery they are on, so this is checked before a single code is processed.

    Customer focus counts, but only where the entity allows it
    (``allow_customer_focus``): it is the same promise — the operator has named
    the target — narrowed to a customer instead of one delivery.

    Deliberately a pre-flight check rather than a per-code rejection: nothing is
    written to ``ScanLog`` for it, the same way a closed session is refused
    before it reaches the engine. A 2,000-code paste with no focus is one
    operator mistake, not 2,000 audit rows.
    """
    if _has_focus(session, user):
        return None
    return JsonResponse(
        {
            "accepted": False,
            "reason_code": outbound_engine.ReasonCode.NO_FOCUS,
            "message": NO_FOCUS_MESSAGE,
            "error_title": outbound_engine.ERROR_TITLE[outbound_engine.ReasonCode.NO_FOCUS],
            # Its own cue and its own colour family — see AUDIO_CUE in
            # services/outbound.py and REASON_FAMILY in templatetags/merilv_ui.
            # This is a step not taken, not a verdict on the item.
            "audio_cue": outbound_engine.AUDIO_CUE[outbound_engine.ReasonCode.NO_FOCUS],
            # Bulk's own response shape, so the console can read either without
            # having to know which endpoint refused it.
            "accepted_count": 0,
            "rejected_count": 0,
            "results": [],
        },
        status=409,
    )


@login_required
@operate_required
@capability_required("mv_scan")
@require_POST
def outbound_scan(request, pk):
    """Resolve one scan and, if accepted, commit it atomically."""
    session = get_session_or_404(request.user, pk, "entity", "dump")
    # Normalised, not merely stripped: a gun's terminator, a code pasted with
    # the line wrapped, and a cell that brought its spreadsheet quotes along
    # all used to reach the parser verbatim and come back UNPARSEABLE. The
    # stored raw_scan is the normalised form so the audit log, the offline
    # queue's replay key and the scan feed all agree on what the code was.
    raw_scan = normalise_scan_input(request.POST.get("raw_scan"))
    selected_box = (request.POST.get("box") or session.selected_box or "").strip()
    client_ref = (request.POST.get("client_ref") or "").strip()[:64]

    if not raw_scan:
        return JsonResponse({"accepted": False, "reason_code": "EMPTY", "message": "Nothing scanned."}, status=400)
    if session.status != OutboundSession.STATUS_ACTIVE:
        return JsonResponse(
            {"accepted": False, "reason_code": "SESSION_CLOSED", "message": "This session is closed."},
            status=409,
        )
    refused = _focus_gate(session, request.user)
    if refused is not None:
        return refused

    # Idempotency. The offline queue replays a scan whose response was lost,
    # reusing the ref from the first attempt — so if that attempt actually
    # landed, this is the same scan arriving twice, not two units picked. Report
    # it as already-recorded rather than booking a second one. Serialised items
    # were already protected by the duplicate-serial check; non-serialised ones
    # had no guard at all, which is the hole this closes.
    if client_ref:
        already = session.scans.filter(client_ref=client_ref).first()
        if already is not None:
            return JsonResponse(
                {
                    "accepted": True,
                    "reason_code": "ALREADY_RECORDED",
                    "message": f"Already recorded as {already.serial_identity} — not counted twice.",
                    "serial_identity": already.serial_identity,
                    "audio_cue": "duplicate",
                    "state": _session_payload(session, request.user),
                }
            )

    entity = session.entity
    profile = entity_service.get_profile(entity)
    settings_obj = entity_service.get_outbound_settings(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    box_capacity = 0
    box_usage = 0
    if settings_obj.enforce_box_capacity and selected_box:
        box = BoxProfile.objects.filter(code=selected_box, is_active=True).first()
        box_capacity = box.max_qty if box else 0
        if box_capacity:
            box_usage = session.scans.filter(box=selected_box).count()

    # The whole read-decide-write cycle is locked: two guns on one session must
    # not both see the same line as open and each consume the last unit.
    with transaction.atomic():
        lines = list(session.dump.lines.select_for_update()) if session.dump else []
        operator_focus = focus.get(session, request.user)
        existing = set(session.scans.values_list("serial_identity", flat=True))
        dispatched, reserved, reserved_sessions = _serial_guards(session)

        outcome = outbound_engine.process_scan(
            raw_scan,
            profile,
            lines,
            settings_obj,
            existing_serials=existing,
            dispatched_serials=dispatched,
            reserved_serials=reserved,
            reserved_sessions=reserved_sessions,
            focus_delivery=operator_focus.value,
            focus_customer=operator_focus.customer_code,
            selected_box=selected_box,
            box_capacity=box_capacity,
            box_usage=box_usage,
            material_master=material_master,
            positional_rules=rules,
        )
        outcome = _reject_if_delivery_already_finished(outcome, _finished_delivery_numbers(session))

        recorded = None
        if outcome.accepted and outcome.line is not None:
            parsed = outcome.parsed
            recorded = OutboundScan.objects.create(
                session=session,
                line=outcome.line,
                serial_identity=outcome.serial_identity,
                serial_only=outcome.serial_only,
                material=parsed.material,
                batch=parsed.batch,
                mfg_code=parsed.mfg,
                expiry_code=parsed.expiry,
                box=selected_box,
                raw_scan=raw_scan,
                client_ref=client_ref,
                scanned_by=request.user,
            )
            DeliveryLine.objects.filter(pk=outcome.line.pk).update(scanned_qty=F("scanned_qty") + 1)
            if selected_box and session.selected_box != selected_box:
                session.selected_box = selected_box
                session.save(update_fields=["selected_box", "updated_at"])

        ScanLog.objects.create(
            session=session,
            raw_scan=raw_scan,
            status=outcome.status,
            reason_code=outcome.reason_code,
            message=outcome.message,
            delivery_no=_attempt_delivery_no(session, outcome, request.user),
            parsed=outcome.parsed.as_dict() if outcome.parsed else {},
        )

    session.refresh_from_db()
    response = outcome.as_dict()
    response["state"] = _session_payload(session, request.user)
    # What the scan became, in the operator's own terms: which line it landed
    # on, the serial it was given, and when. The console echoed only a reason
    # code, so "did that get a serial?" could not be answered without leaving
    # the screen — and the recent-scan rows were timestamped from the browser
    # clock, which is a different clock from the one the record carries.
    response.update(_assigned_scan_summary(recorded))
    return JsonResponse(response)


def _assigned_scan_summary(scan):
    """The console's view of one recorded scan: serial, line and timestamp.

    Blank fields rather than nulls — these are written straight into the DOM,
    where ``null`` stringifies to the text "null".
    """
    if scan is None:
        return {
            "serial_only": "", "delivery_no": "", "item_no": "",
            "material": "", "batch": "", "scanned_at": "", "scan_id": None,
        }
    local = timezone.localtime(scan.created_at)
    return {
        "serial_only": scan.serial_only,
        "delivery_no": scan.line.delivery_no if scan.line_id else "",
        "item_no": scan.line.item_no if scan.line_id else "",
        "material": scan.material,
        "batch": scan.batch,
        # One format everywhere — the feed rows, the live rows and the serials
        # modal all print this string rather than each deriving their own.
        "scanned_at": local.strftime(SCAN_TIMESTAMP_FORMAT),
        "scan_id": scan.pk,
    }


#: How a scan's moment is written wherever the console shows one. Local time,
#: because a picker reconciles against the clock on the wall; the value stored
#: is timezone-aware either way.
SCAN_TIMESTAMP_FORMAT = "%d %b %Y %H:%M:%S"


def _serial_guards(session) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Serials this session may not assign, and a label naming where each went.

    Returns ``(dispatched, reserved, reserved_sessions)`` for
    :func:`outbound_engine.process_scan`. The third is serial -> the id of the
    open session holding it, kept apart from the label so the console can link
    straight to that session: the message told the operator to "undo it there"
    and the landing page lists only the twenty newest, so the session they were
    sent to was usually not on it.
    ``dispatched`` has shipped and is permanent, ``reserved`` is held by another
    open session and is released by undoing it there.

    Both are bounded rather than "every serial ever":

    * An assigned serial is ``BATCH || NUMBER``, so only a serial sharing one of
      *this dump's* batches can possibly collide. ``DispatchedSerial.batch`` is
      indexed for exactly this lookup, which keeps it cheap as that table grows
      with every dispatch.
    * ``reserved`` is read from active sessions only — a closed session's
      serials either shipped (and are in ``dispatched``) or were abandoned, and
      filtering on the session keeps this off ``OutboundScan``'s ``batch``
      column, which has no index of its own.
    """
    if not session.dump:
        return {}, {}, {}
    batches = {
        (batch or "").strip().upper()
        for batch in session.dump.lines.values_list("batch", flat=True)
    }
    batches.discard("")
    if not batches:
        return {}, {}, {}

    dispatched = {
        serial: f"delivery {delivery_no}"
        for serial, delivery_no in DispatchedSerial.objects.filter(
            batch__in=batches
        ).values_list("serial", "delivery_no")
        if serial
    }

    reserved: dict[str, str] = {}
    reserved_sessions: dict[str, int] = {}
    other_scans = (
        OutboundScan.objects.filter(session__status=OutboundSession.STATUS_ACTIVE)
        .exclude(session_id=session.pk)
        .values_list("serial_identity", "batch", "line__delivery_no", "session_id")
    )
    # Matched in Python rather than with a SQL ``batch__in``: both sides are
    # written normalised today, but a row from before that was true would be
    # dropped by an exact match — and a guard that silently misses a serial is
    # the failure this whole ledger exists to prevent. The session filter above
    # is what keeps the row count down.
    for serial, batch, delivery_no, other_session in other_scans:
        if not serial or serial in dispatched:
            continue
        if (batch or "").strip().upper() not in batches:
            continue
        reserved[serial] = f"delivery {delivery_no or '—'} (session #{other_session})"
        reserved_sessions[serial] = other_session
    return dispatched, reserved, reserved_sessions


def _record_dispatched_serials(session, finished, user) -> int:
    """Burn a finished delivery's serials so no future upload can reuse them.

    Called at finish rather than at scan time: while a delivery is open a scan
    can still be undone or deleted by an admin, and burning the serial before
    the stock physically leaves would strand it.

    ``ignore_conflicts`` because a pick that has already shipped must never be
    refused by this bookkeeping — a re-finish replays the same serials, and the
    scan-time gate is what stops a genuine reuse getting this far.
    """
    rows = [
        DispatchedSerial(
            serial=scan.serial_identity,
            entity=session.entity,
            finished_delivery=finished,
            delivery_no=finished.delivery_no,
            material=scan.material,
            batch=scan.batch,
            dispatched_by=user,
        )
        for scan in session.scans.filter(line__delivery_no=finished.delivery_no)
        if scan.serial_identity
    ]
    if not rows:
        return 0
    DispatchedSerial.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)
    return len(rows)


def _finished_paperwork(finished) -> dict:
    """What a closed delivery recorded, in the shape the Finish modal reads.

    Field names match the form's own ``name`` attributes so the modal can fill
    itself by looking each input up rather than carrying a second mapping that
    would have to be kept in step with the markup.

    Strings rather than nulls throughout — these go straight into input values,
    where ``null`` becomes the text "null".
    """
    return {
        "courier_partner": finished.courier_partner or "",
        "courier_number": finished.courier_number or "",
        "docket_no": finished.docket_no or "",
        "vehicle_number": finished.vehicle_number or "",
        "vehicle_type": finished.vehicle_type or "",
        "mode_of_transit": finished.mode_of_transit or "",
        "weight_kg": str(finished.weight_kg) if finished.weight_kg is not None else "",
        # Per-type box counts, already decoded from "A:2,B:1" by the model.
        "boxes": {label: count for label, count in finished.box_breakdown.items()},
        # Who closed it and when, for the banner. A picker who finds a
        # delivery already closed asks those two questions first.
        "finished_at": timezone.localtime(finished.created_at).strftime(SCAN_TIMESTAMP_FORMAT),
        "finished_by": (
            finished.picker.get_full_name() or finished.picker.username
            if finished.picker_id else ""
        ),
        "pending_courier": finished.status == FinishedDelivery.STATUS_PENDING_COURIER,
    }


def _finished_delivery_numbers(session) -> set[str]:
    return set(FinishedDelivery.objects.filter(session=session).values_list("delivery_no", flat=True))


def _close_session_if_fully_shipped(session) -> bool:
    """Move a session out of the active list once its dump is fully finished.

    ``OutboundSession.STATUS_COMPLETED`` has existed since the model was
    written and nothing ever set it, so every session opened stayed "active"
    for good. The outbound list filters on exactly that status, so a dump whose
    every delivery had shipped weeks ago still sat at the top of the working
    list — and the only thing offered to clear it, Delete, is refused outright
    for a session with a finished delivery, because those have shipped and
    their serials are in the dispatched ledger. So the list showed work that
    was done and the one control for it always said no.

    A session is done when every delivery its dump carries has a
    ``FinishedDelivery``. Deliberately narrow:

    * A dump with no lines is not "fully shipped", it is empty — closing it
      would hide a bad upload instead of leaving it to be deleted, which IS
      allowed for a session that has never finished anything.
    * An ad-hoc session with no dump has no set of deliveries to complete.
    * Only an active session is touched, so this is idempotent and a re-finish
      cannot resurrect or re-close anything.

    Closing is not hiding: a completed session keeps its scans, its log and its
    console URL, and its deliveries are in Delivery History. What changes is
    that it stops being offered as somewhere to scan — outbound_scan already
    refuses a session that is not active, which is correct once there is
    nothing left in it to pick.
    """
    if session.dump_id is None or session.status != OutboundSession.STATUS_ACTIVE:
        return False
    wanted = {
        no for no in session.dump.lines.values_list("delivery_no", flat=True) if no
    }
    if not wanted or (wanted - _finished_delivery_numbers(session)):
        return False
    session.status = OutboundSession.STATUS_COMPLETED
    session.save(update_fields=["status", "updated_at"])
    return True


def _reject_if_delivery_already_finished(outcome, finished_deliveries: set[str]):
    """Row 2.5 fix — nothing previously stopped a scan from landing against a
    delivery that had already been finished and exported.

    A session stays "active" until every delivery in its dump is finished, so
    scanning a second delivery after the first is closed is completely
    ordinary — but a stray scan matching the *closed* one's lines was still
    silently accepted, incrementing ``DeliveryLine.scanned_qty`` behind the
    back of the ``FinishedDelivery`` snapshot already handed to the courier.
    Deliberately checked here rather than in the engine: the engine has no
    concept of ``FinishedDelivery`` and stays testable without a database.
    ``finished_deliveries`` is fetched once by the caller rather than queried
    per scan, since the bulk-paste path calls this up to
    :data:`BULK_SCAN_MAX_CODES` times in one request.
    """
    if not (outcome.accepted and outcome.line is not None):
        return outcome
    if outcome.line.delivery_no not in finished_deliveries:
        return outcome
    return outbound_engine.ScanOutcome(
        accepted=False,
        status=outbound_engine.STATUS_ERROR,
        reason_code=outbound_engine.ReasonCode.DELIVERY_ALREADY_FINISHED,
        message=(
            f"Delivery {outcome.line.delivery_no} was already finished and exported — this scan was not "
            "recorded. Ask your supervisor to reopen it if more stock genuinely needs to be added."
        ),
        parsed=outcome.parsed,
    )


#: Legacy floor for "looks like a real code" (old tool's own rule, see
#: ``outbound-scanner.js:1584``) — short tokens pasted in are almost always
#: stray whitespace or a typo, not a scannable barcode.
BULK_SCAN_MIN_LENGTH = 5
BULK_SCAN_MAX_CODES = 2000

_BULK_COMMENT_RE = re.compile(r"#.*$")


def _strip_bulk_comments(raw_text):
    """Drop a trailing ``# ...`` annotation from every pasted line.

    Test/demo barcode lists (this module's own
    ``dummy_tempelate/.../barcodes_to_scan.txt`` included) document and use
    exactly this shape — ``<barcode>    # <which delivery/item>`` — for a
    human reading the file. Without this, pasting one verbatim submitted the
    annotation as part of the "barcode" (and a bare ``# section header``
    comment line as a barcode outright), producing junk rows that render as
    nonsense in the scan log rather than being silently ignored like a
    comment should be.
    """
    # split("\n") rather than splitlines(): the latter also breaks on \x1c-\x1e,
    # and \x1d is GS — GS1's field separator, which a scanner puts INSIDE one
    # unbracketed barcode after every variable-length AI. Splitting there turned
    # a single code into one "line" per AI before split_pasted ever saw it, so a
    # GS-separated paste arrived as fragments and every one of them was
    # rejected.
    normalised = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_BULK_COMMENT_RE.sub("", line) for line in normalised.split("\n"))


@login_required
@operate_required
@require_POST
def outbound_bulk_scan(request, pk):
    """Process many pasted codes in one request — the legacy "Bulk Paste" tab.

    Each code runs through exactly the same gates as ``outbound_scan``, in the
    order pasted, so duplicates/over-scans/wrong-batch are rejected exactly as
    they would be scanned one at a time. The only difference is the console
    repaints once at the end instead of after every line, which is what makes
    a 2,000-line paste usable instead of freezing the browser.
    """
    session = get_session_or_404(request.user, pk, "entity", "dump")
    raw_text = request.POST.get("raw_text") or ""
    codes = [
        code
        for code in (
            normalise_scan_input(c)
            for c in extractor_service.split_pasted(_strip_bulk_comments(raw_text))
        )
        if len(code) > BULK_SCAN_MIN_LENGTH
    ]

    if not codes:
        return JsonResponse(
            {"accepted_count": 0, "rejected_count": 0, "results": [], "message": "No valid codes found."},
            status=400,
        )
    if len(codes) > BULK_SCAN_MAX_CODES:
        return JsonResponse(
            {"message": f"Paste at most {BULK_SCAN_MAX_CODES:,} codes at a time."}, status=400
        )
    if session.status != OutboundSession.STATUS_ACTIVE:
        return JsonResponse({"message": "This session is closed."}, status=409)
    # Bulk paste is single-delivery by nature — a pasted sheet is one
    # delivery's codes — so the focus requirement costs it nothing and buys the
    # same guarantee as a hand scan: every code lands on the delivery the
    # operator named.
    refused = _focus_gate(session, request.user)
    if refused is not None:
        return refused

    entity = session.entity
    profile = entity_service.get_profile(entity)
    settings_obj = entity_service.get_outbound_settings(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    results = []
    accepted_count = 0
    with transaction.atomic():
        lines = list(session.dump.lines.select_for_update()) if session.dump else []
        operator_focus = focus.get(session, request.user)
        existing = set(session.scans.values_list("serial_identity", flat=True))
        finished_deliveries = _finished_delivery_numbers(session)
        # Read once for the whole paste rather than per code: neither set can
        # change while this transaction holds the lines, and a 2,000-code paste
        # would otherwise run 4,000 extra queries.
        dispatched, reserved, reserved_sessions = _serial_guards(session)
        new_scans = []
        line_increments: dict[int, int] = {}

        for raw_scan in codes:
            outcome = outbound_engine.process_scan(
                raw_scan,
                profile,
                lines,
                settings_obj,
                existing_serials=existing,
                dispatched_serials=dispatched,
                reserved_serials=reserved,
                reserved_sessions=reserved_sessions,
                focus_delivery=operator_focus.value,
                focus_customer=operator_focus.customer_code,
                selected_box=session.selected_box,
                material_master=material_master,
                positional_rules=rules,
            )
            outcome = _reject_if_delivery_already_finished(outcome, finished_deliveries)
            if outcome.accepted and outcome.line is not None:
                parsed = outcome.parsed
                new_scans.append(
                    OutboundScan(
                        session=session,
                        line=outcome.line,
                        serial_identity=outcome.serial_identity,
                        serial_only=outcome.serial_only,
                        material=parsed.material,
                        batch=parsed.batch,
                        mfg_code=parsed.mfg,
                        expiry_code=parsed.expiry,
                        box=session.selected_box,
                        raw_scan=raw_scan,
                        scanned_by=request.user,
                    )
                )
                # Mutate the in-memory line so the NEXT code in this same paste
                # sees the incremented quantity — otherwise every duplicate SKU
                # in a big paste would look open forever and over-scan.
                outcome.line.scanned_qty = float(outcome.line.scanned_qty or 0) + 1
                line_increments[outcome.line.pk] = line_increments.get(outcome.line.pk, 0) + 1
                if outcome.serial_identity:
                    existing.add(outcome.serial_identity)
                accepted_count += 1
            ScanLog.objects.create(
                session=session,
                raw_scan=raw_scan,
                status=outcome.status,
                reason_code=outcome.reason_code,
                message=outcome.message,
                delivery_no=_attempt_delivery_no(session, outcome, request.user),
                parsed=outcome.parsed.as_dict() if outcome.parsed else {},
            )
            results.append(
                {
                    "raw_scan": raw_scan,
                    "accepted": outcome.accepted,
                    "reason_code": outcome.reason_code,
                    "message": outcome.message,
                    "audio_cue": outcome.audio_cue,
                    # The popup groups a paste's rejections by this, so it has
                    # to travel with each result rather than being looked up per
                    # row in the console.
                    "error_title": outcome.error_title,
                    # Same as the single-scan path: a bulk row has to be able
                    # to say what serial it was given and where it landed.
                    "serial_identity": outcome.serial_identity if outcome.accepted else "",
                    "serial_only": outcome.serial_only if outcome.accepted else "",
                    # Unlike the two above, this is sent for rejections too: a
                    # paste's error popup lists the codes it refused, and a
                    # barcode is not what a picker finds a carton by.
                    "display_serial": outcome.display_serial,
                    "blocking_session_id": outcome.blocking_session_id,
                    "delivery_no": outcome.line.delivery_no if (outcome.accepted and outcome.line) else "",
                }
            )

        if new_scans:
            OutboundScan.objects.bulk_create(new_scans)
        for line_pk, increment in line_increments.items():
            DeliveryLine.objects.filter(pk=line_pk).update(scanned_qty=F("scanned_qty") + increment)

    session.refresh_from_db()
    _log(request, "outbound_bulk_scan", "OutboundSession", session.pk, codes=len(codes), accepted=accepted_count)
    return JsonResponse(
        {
            "accepted_count": accepted_count,
            "rejected_count": len(codes) - accepted_count,
            # A paste is not one outcome. The request asks for the breakdown —
            # how many landed, how many were already packed, how many could not
            # be read and how many were readable but not wanted here — because
            # "17 rejected" gives an operator nothing to act on.
            "summary": _bulk_summary(results),
            "results": results,
            "state": _session_payload(session, request.user),
        }
    )


#: Reason codes that mean "this string is not a barcode we can read", as
#: opposed to "we read it and it does not belong on this delivery".
_BULK_PARSE_FAILURES = {
    outbound_engine.ReasonCode.UNPARSEABLE,
    outbound_engine.ReasonCode.MISSING_MATERIAL,
    outbound_engine.ReasonCode.MISSING_BATCH,
}


def _bulk_summary(results):
    """Group one paste's outcomes into the four buckets an operator acts on."""
    summary = {"accepted": 0, "duplicate": 0, "unmatched": 0, "unparsed": 0}
    for result in results:
        if result["accepted"]:
            summary["accepted"] += 1
        elif result["reason_code"] == outbound_engine.ReasonCode.DUPLICATE_SERIAL:
            summary["duplicate"] += 1
        elif result["reason_code"] in _BULK_PARSE_FAILURES:
            summary["unparsed"] += 1
        else:
            summary["unmatched"] += 1
    return summary


def _remove_scan(session, scan, *, reason_code, message):
    """Delete one assigned scan and give its unit back to the line.

    Shared by Undo and by the admin delete, because the two differ only in who
    may do it and what the audit row says. Everything else — the
    finished-delivery guard, the floored decrement and the append-only log
    entry — has to be identical, and was duplicated before this.

    Returns ``(removed_identity, raw_scan)``. The caller has already opened the
    transaction and re-read ``scan`` under a row lock.
    """
    delivery_no = scan.line.delivery_no if scan.line_id else ""
    if scan.line_id:
        # Floor at zero: a removal must never drive the counter negative, even
        # if the line was edited underneath the session.
        DeliveryLine.objects.filter(pk=scan.line_id, scanned_qty__gt=0).update(
            scanned_qty=F("scanned_qty") - 1
        )
    removed, raw = scan.serial_identity, scan.raw_scan
    scan.delete()

    # Append the reversal instead of amending the original accept: ScanLog is
    # an audit trail. The console's status filter reads the surviving
    # OutboundScan rows to decide what is still assigned, so deleting the
    # scan above is already enough to move this code out of the Scanned
    # tab — this row is what tells the operator *why* it moved.
    ScanLog.objects.create(
        session=session,
        raw_scan=raw,
        status=ScanLog.STATUS_WARNING,
        reason_code=reason_code,
        message=message.format(serial=removed),
        # The delivery the unit went back to, read before the scan row was
        # deleted — a reversal belongs to the same delivery the accept did, and
        # must disappear from that delivery's log with it.
        delivery_no=delivery_no,
    )
    return removed, raw


def _blocked_by_finished_delivery(session, scan):
    """The message to refuse a removal with, or ``None`` when it may proceed.

    Same reasoning as the scan-side guard, mirrored: decrementing a finished
    delivery's counters after export corrupts the snapshot already handed to
    the courier just as surely as incrementing them would have.
    """
    if not scan.line_id:
        return None
    if scan.line.delivery_no not in _finished_delivery_numbers(session):
        return None
    return (
        f"Delivery {scan.line.delivery_no} was already finished and exported — "
        "ask your supervisor to reopen it before removing this scan."
    )


@login_required
@operate_required
@capability_required("mv_undo_scan")
@require_POST
def outbound_undo(request, pk):
    """Reverse the most recent scan, or a specific one by serial identity."""
    session = get_session_or_404(request.user, pk)
    identity = (request.POST.get("serial_identity") or "").strip()

    with transaction.atomic():
        queryset = session.scans.select_related("line")
        # Tie-broken on pk. created_at is auto_now_add and this console is
        # scanned into faster than its resolution — two scans in the same
        # instant made "the last one" arbitrary, so Undo could reverse a scan
        # the operator did not just make. pk is the real insertion order.
        scan = (
            queryset.filter(serial_identity=identity).first() if identity
            else queryset.order_by("-created_at", "-pk").first()
        )
        if scan is None:
            return JsonResponse({"ok": False, "message": "Nothing to undo."}, status=404)
        blocked = _blocked_by_finished_delivery(session, scan)
        if blocked:
            return JsonResponse({"ok": False, "message": blocked}, status=409)
        removed, undone_raw = _remove_scan(
            session, scan,
            reason_code="UNDONE",
            message="Scan reversed — {serial} returned to pending.",
        )

    session.refresh_from_db()
    _log(request, "outbound_undo", "OutboundSession", session.pk, serial=removed)
    return JsonResponse(
        {
            "ok": True,
            "removed": removed,
            "raw_scan": undone_raw,
            "state": _session_payload(session, request.user),
        }
    )


@login_required
@operate_required
@capability_required("mv_clear_scans")
@require_POST
def outbound_clear_scans(request, pk):
    """Clear every scan on ONE delivery in a single action.

    Undo already removes scans one at a time and any operator may use it, so
    this is that same right exercised in bulk rather than a new one. What it
    replaces is deleting the whole upload and re-importing the YVL06 to correct
    a single delivery, which is what the floor was actually doing.

    Scoped to a named delivery deliberately — there is no "clear the session"
    here. That is ``outbound_session_delete``, which asks a much louder
    question first and refuses once anything has shipped.
    """
    session = get_session_or_404(request.user, pk)
    delivery_no = (request.POST.get("delivery_no") or "").strip()
    if not delivery_no:
        return JsonResponse(
            {"ok": False, "message": "Choose which delivery to clear."}, status=400
        )
    if delivery_no in _finished_delivery_numbers(session):
        return JsonResponse(
            {
                "ok": False,
                "message": (
                    f"Delivery {delivery_no} is finished — its scans cannot be "
                    "cleared. Reopen it from Delivery History first."
                ),
            },
            status=409,
        )

    cleared = []
    with transaction.atomic():
        # Resolve the lines FIRST and filter the scans on line_id. Filtering on
        # `line__delivery_no` would join DeliveryLine, and OutboundScan.line is
        # nullable — PostgreSQL refuses SELECT … FOR UPDATE over the nullable
        # side of an outer join (see _locked_scans). line_id__in reads a local
        # column, so the lock holds.
        line_ids = list(
            session.dump.lines.filter(delivery_no=delivery_no).values_list("pk", flat=True)
        ) if session.dump else []
        if not line_ids:
            return JsonResponse(
                {"ok": False, "message": f"Delivery {delivery_no} is not part of this session."},
                status=404,
            )
        for scan in list(_locked_scans(session).filter(line_id__in=line_ids)):
            removed, _raw = _remove_scan(
                session, scan,
                reason_code="CLEARED",
                message="Scan cleared — {serial} returned to pending.",
            )
            cleared.append(removed)

    session.refresh_from_db()
    _log(
        request, "outbound_clear_scans", "OutboundSession", session.pk,
        delivery_no=delivery_no, cleared=len(cleared),
    )
    return JsonResponse(
        {
            "ok": True,
            "delivery_no": delivery_no,
            "cleared_count": len(cleared),
            "cleared": cleared,
            "state": _session_payload(session, request.user),
        }
    )


@login_required
@operate_required
@capability_required("mv_clear_scans")
@require_POST
def outbound_delivery_delete(request, pk):
    """Remove one open delivery from a session, releasing any scans on it.

    This is the per-delivery cleanup path for a session that should keep going:
    five deliveries can be left in the current session while five incomplete
    ones are removed and picked in the next session. It deliberately refuses a
    finished delivery, because that has a dispatch record and a serial ledger;
    those are handled from Delivery History.
    """
    session = get_session_or_404(request.user, pk, "dump")
    delivery_no = (request.POST.get("delivery_no") or "").strip()
    if not delivery_no:
        return JsonResponse({"ok": False, "message": "Choose which delivery to delete."}, status=400)
    if not session.dump_id:
        return JsonResponse({"ok": False, "message": "This session has no delivery dump."}, status=409)
    if delivery_no in _finished_delivery_numbers(session):
        return JsonResponse(
            {
                "ok": False,
                "message": (
                    f"Delivery {delivery_no} is finished — remove its dispatch record "
                    "from Delivery History before deleting it from a session."
                ),
            },
            status=409,
        )

    removed_scans = []
    with transaction.atomic():
        lines = list(
            DeliveryLine.objects.select_for_update().filter(
                dump=session.dump, delivery_no=delivery_no,
            )
        )
        if not lines:
            return JsonResponse(
                {"ok": False, "message": f"Delivery {delivery_no} is not part of this session."},
                status=404,
            )
        line_ids = [line.pk for line in lines]
        for scan in list(_locked_scans(session).filter(line_id__in=line_ids)):
            removed, _raw = _remove_scan(
                session, scan,
                reason_code="DELIVERY_DELETED",
                message="Delivery removed — scan {serial} returned to pending.",
            )
            removed_scans.append(removed)
        DeliveryLine.objects.filter(pk__in=line_ids).delete()

    if focus.get(session, request.user).value == delivery_no:
        focus.set_to(session, request.user)
    session.refresh_from_db()
    _log(
        request, "outbound_delivery_delete", "OutboundSession", session.pk,
        delivery_no=delivery_no, lines=len(line_ids), scans=len(removed_scans),
    )
    return JsonResponse(
        {
            "ok": True,
            "delivery_no": delivery_no,
            "line_count": len(line_ids),
            "scan_count": len(removed_scans),
            "state": _session_payload(session, request.user),
        }
    )


def _locked_scans(session):
    """This session's scans, locked for update.

    Deliberately NOT ``select_related("line")``. ``OutboundScan.line`` is
    nullable, so selecting it makes the query a LEFT OUTER JOIN, and PostgreSQL
    refuses ``SELECT … FOR UPDATE`` over the nullable side of one:

        FOR UPDATE cannot be applied to the nullable side of an outer join

    SQLite accepts it silently, so this only ever surfaces on a real database.
    The line is read through the returned row's own FK instead — one extra
    query on a path that runs once per correction, against a 500 on every one.
    """
    return session.scans.select_for_update()


@login_required
@operate_required
@capability_required("mv_delete_scan")
@require_POST
def outbound_scan_delete(request, pk):
    """Delete one assigned scan outright. Admins only.

    Undo is a picker's tool and only reaches the last scan (or one they can
    name); correcting a mis-scan found an hour later needs a supervisor to
    remove a specific row. The screen offered no such control at all, which is
    what "delete is not working even for admin users" means here.

    The tier is checked *in the view* rather than only by a decorator so a
    picker's console gets a 403 with a reason it can show, instead of the
    login redirect ``admin_required`` would issue to a fetch() call. Either
    way the check is on the server; the button's absence in the UI is a
    convenience, not the boundary.
    """
    session = get_session_or_404(request.user, pk)
    if not is_merilv_admin(request.user):
        return JsonResponse(
            {"ok": False, "message": "Only a Meril-V admin can delete a recorded scan."},
            status=403,
        )

    scan_id = int_or_none(request.POST.get("scan_id"))
    identity = (request.POST.get("serial_identity") or "").strip()
    if scan_id is None and not identity:
        return JsonResponse({"ok": False, "message": "Nothing selected to delete."}, status=400)

    with transaction.atomic():
        queryset = _locked_scans(session)
        scan = (
            queryset.filter(pk=scan_id).first() if scan_id is not None
            else queryset.filter(serial_identity=identity).first()
        )
        if scan is None:
            return JsonResponse(
                {"ok": False, "message": "That scan is no longer in this session."}, status=404,
            )
        blocked = _blocked_by_finished_delivery(session, scan)
        if blocked:
            return JsonResponse({"ok": False, "message": blocked}, status=409)
        delivery_no = scan.line.delivery_no if scan.line_id else ""
        removed, removed_raw = _remove_scan(
            session, scan,
            reason_code="DELETED",
            message="Scan {serial} deleted by an admin — returned to pending.",
        )

    session.refresh_from_db()
    _log(
        request, "outbound_scan_delete", "OutboundSession", session.pk,
        serial=removed, delivery_no=delivery_no,
    )
    return JsonResponse(
        {
            "ok": True,
            "removed": removed,
            "raw_scan": removed_raw,
            "delivery_no": delivery_no,
            "state": _session_payload(session, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def outbound_focus(request, pk):
    """Lock scanning to one delivery or one customer, or clear the lock."""
    session = get_session_or_404(request.user, pk, "entity")
    delivery = (request.POST.get("delivery_no") or "").strip()
    customer = (request.POST.get("customer_code") or "").strip()

    # Recorded against the OPERATOR, not the session. The session is shared on
    # purpose — a supervisor uploads a dump and the floor picks it — but two
    # pickers standing in the same place is how one of them ends up scanning
    # into the other's delivery. See merilv/focus.py.
    #
    # The two modes stay mutually exclusive, and clicking the focused thing
    # again still clears it; focus.toggle holds both rules for all four
    # consoles.
    if not customer or not session.entity.allow_customer_focus:
        customer = ""
    focus.toggle(session, request.user, delivery, customer)

    return JsonResponse({"ok": True, "state": _session_payload(session, request.user)})


@login_required
@access_required
def outbound_state(request, pk):
    session = get_session_or_404(request.user, pk, "dump")
    return JsonResponse(_session_payload(session, request.user))


#: Dispatch paperwork fields, in the order the courier form presents them.
_COURIER_TEXT_FIELDS = (
    ("courier_partner", "courier_partner"),
    ("courier_number", "courier_number"),
    ("vehicle_number", "vehicle_number"),
    ("docket_no", "docket_no"),
    ("mode_of_transit", "mode_of_transit"),
    ("vehicle_type", "vehicle_type"),
)


def _apply_courier_paperwork(finished, post_data, overwrite=False):
    """Fold dispatch details into the row that already exists.

    Two callers with two different intents, which is what ``overwrite`` names:

    ``False`` — only *fills gaps*. A value already recorded is never replaced,
    so a half-empty submission cannot wipe the docket somebody else entered.

    ``True`` — an admin editing a closed delivery on purpose, where replacing
    what is there is the whole point. Still only for the fields that arrived
    with a value: an admin who leaves a box empty has said nothing about it,
    not "make it empty", and treating a blank form as an instruction to clear
    six fields is the accident this refuses to have.

    Saves nothing when nothing changed either way, which keeps a stray
    double-click from touching ``updated_at``.
    """
    changed = []
    for field, key in _COURIER_TEXT_FIELDS:
        value = (post_data.get(key) or "").strip()
        if value and (overwrite or not getattr(finished, field)):
            if getattr(finished, field) == value:
                continue
            setattr(finished, field, value)
            changed.append(field)

    boxes = 0
    labels = []
    for label in "ABCDEF":
        try:
            quantity = int(post_data.get(f"box_{label}") or 0)
        except ValueError:
            quantity = 0
        if quantity > 0:
            boxes += quantity
            labels.append(f"{label}:{quantity}")
    if boxes and (overwrite or not finished.box_count):
        finished.box_count = boxes
        finished.boxes_used = ",".join(labels)
        changed += ["box_count", "boxes_used"]

    weight_raw = (post_data.get("weight_kg") or "").strip()
    if weight_raw and (overwrite or finished.weight_kg is None):
        try:
            finished.weight_kg = Decimal(weight_raw)
            changed.append("weight_kg")
        except (InvalidOperation, ValueError):
            pass

    # Courier Master owns the partner code, the same rule the finish path
    # applies. Without this, changing the courier on a closed delivery left
    # the previous courier's code on the row and in its invoice, because the
    # two fields are posted independently and only the one the admin actually
    # edited would change.
    master_number = _courier_number_for(finished.courier_partner, finished.plant)
    if master_number and finished.courier_number != master_number:
        finished.courier_number = master_number
        if "courier_number" not in changed:
            changed.append("courier_number")

    # Paperwork arriving is exactly what promotes a pending pick to complete.
    if changed and finished.status == FinishedDelivery.STATUS_PENDING_COURIER:
        if finished.docket_no or finished.courier_number or finished.box_count:
            finished.status = FinishedDelivery.STATUS_COMPLETE
            changed.append("status")

    if changed:
        finished.save(update_fields=[*changed, "updated_at"])
    return bool(changed)


#: Second half of :func:`_finish_one_delivery`'s answer when the delivery is
#: already closed. A third value beside ``None`` ("not part of this session")
#: and a list of short lines, because the three refusals are three different
#: things to tell the operator.
ALREADY_FINISHED = "already_finished"


class DocketConflict(str):
    """A refused docket, carrying its own explanation.

    A ``str`` subclass so it reads as the message it is, while ``isinstance``
    still tells it apart from the shortfall list and ``ALREADY_FINISHED`` in
    the same return slot.
    """


def _docket_conflict(entity, docket_no, customer_code, session=None, exclude_pk=None):
    """Whether ``docket_no`` may be assigned to ``customer_code`` on ``entity``.

    A session is one YVL06 dump — one shipment run — so several deliveries in
    it for the SAME customer may share one docket freely (the bulk-order case:
    many products, one consignment, one docket), however far along each is. It
    may never be assigned to a DIFFERENT customer, in this session or any
    other, open or already shipped.

    Reusing it for the same customer from a DIFFERENT session — a separate,
    later shipment — is blocked once the earlier session's delivery actually
    reached ``STATUS_COMPLETE``: the docket has shipped, and a consignment
    number is not reusable. A still-open ``PENDING_COURIER`` row elsewhere has
    not shipped, so it does not retire the docket.

    Returns the conflict message, or ``""`` when there is none.
    """
    docket_no = (docket_no or "").strip()
    if not docket_no:
        return ""
    others = FinishedDelivery.objects.filter(entity=entity, docket_no__iexact=docket_no)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    for other in others:
        if other.customer_code != customer_code:
            return (
                f"Docket {docket_no} is already assigned to customer "
                f"{other.customer_code or 'unknown'} (delivery {other.delivery_no}) — "
                "it cannot be reused for a different customer."
            )
        if session is not None and other.session_id == session.pk:
            continue
        if other.status == FinishedDelivery.STATUS_COMPLETE:
            return (
                f"Docket {docket_no} was already used on completed delivery "
                f"{other.delivery_no} and cannot be reused, even for the same customer."
            )
    return ""


def _already_finished_message(session, delivery_no) -> str:
    """Why the finish was refused, and where the work actually goes now."""
    row = FinishedDelivery.objects.filter(
        session=session, delivery_no=delivery_no
    ).select_related("picker").order_by("pk").first()
    if row is None:  # raced away between the refusal and this lookup
        return f"Delivery {delivery_no} has already been finished."

    when = timezone.localtime(row.created_at).strftime(SCAN_TIMESTAMP_FORMAT)
    who = (
        f" by {row.picker.get_full_name() or row.picker.username}" if row.picker_id else ""
    )
    if row.status == FinishedDelivery.STATUS_PENDING_COURIER:
        return (
            f"Delivery {delivery_no} was finished at {when}{who} and is waiting on its "
            "courier paperwork. Complete it from Delivery History — finishing it again "
            "here would record the pick twice."
        )
    return (
        f"Delivery {delivery_no} was already finished at {when}{who}. "
        "Only a Meril-V admin can change what was recorded."
    )


def _finish_one_delivery(session, delivery_no, post_data, user, proceed_anyway=False):
    """Everything ``outbound_finish`` does for one delivery, factored out so
    the bulk path (Row 2.4's "Finish & Export Selected") can finish several
    deliveries with the same shared paperwork without duplicating this logic.

    Returns ``(finished_delivery, gaps)``. ``finished_delivery`` is ``None``
    when nothing was created — either the delivery isn't part of this
    session (``gaps`` is ``None`` in that case), or it has short lines and
    neither ``proceed_anyway`` nor the entity's partial-export setting allows
    shipping short (``gaps`` is the non-empty shortfall list).
    """
    lines = list(session.dump.lines.filter(delivery_no=delivery_no)) if session.dump else []
    if not lines:
        return None, None

    # Finishing a delivery that is already finished must never create a second
    # row. The bulk path has always skipped those; this one did not, and the
    # live data shows what that costs: delivery 81090058 was finished twice in
    # one session — once by the picker at 05:42 with no paperwork, then again
    # at 07:34 carrying the courier details. The first row was orphaned as
    # "pending courier" forever, the delivery was counted twice in every KPI,
    # and its 50 boxes were the entire box total for the period.
    #
    # A delivery is finished ONCE. A second submission used to fold itself
    # silently into the first, which read to the operator as having finished
    # the same delivery twice — same button, same success, and nothing on
    # screen saying the first one had already happened.
    #
    # The two-actor flow it was protecting is not lost: a pick left pending
    # its courier is completed from Delivery History (delivery_courier_update),
    # which is a different person doing a different job at a different time and
    # has its own screen for it. What the console refuses is re-finishing.
    #
    # Someone holding Document Edit may still submit, because the Finish form
    # doubles as the edit form for a closed delivery — the modal greys the
    # fields and offers Edit to exactly those users, and this is the server
    # half of that. Managers hold it by default; the capability is configurable
    # per tier under Roles & Access (see merilv/permissions.py).
    existing = FinishedDelivery.objects.filter(
        session=session, delivery_no=delivery_no
    ).order_by("pk").first()
    if existing is not None:
        if not can_do(user, "mv_document_edit_outbound"):
            return None, ALREADY_FINISHED
        # The admin edit path writes the paperwork straight onto the row, so
        # the docket rule has to hold here too — without this, editing a closed
        # delivery is a way around it and the same docket lands on two
        # customers. Only a CHANGED docket is checked: re-submitting the row's
        # own number must not conflict with itself.
        new_docket = (post_data.get("docket_no") or "").strip()
        if new_docket and new_docket.lower() != (existing.docket_no or "").strip().lower():
            conflict = _docket_conflict(
                session.entity, new_docket, existing.customer_code,
                session=session, exclude_pk=existing.pk,
            )
            if conflict:
                return None, DocketConflict(conflict)
        _apply_courier_paperwork(existing, post_data, overwrite=True)
        return existing, outbound_engine.shortfall(lines, delivery_no)

    gaps = outbound_engine.shortfall(lines, delivery_no)
    if gaps and not (proceed_anyway or session.entity.allow_partial_export):
        return None, gaps

    first = lines[0]
    courier_name = (post_data.get("courier_partner") or "").strip()
    courier_number = (post_data.get("courier_number") or "").strip()
    if not courier_name:
        courier_name, courier_number = _resolve_courier(session.entity, first)
    # Courier Master owns the partner code, so it is applied here rather than
    # trusting what the form posted. The modal prefills that field, which
    # means a posted number can be the prefill left behind by the courier that
    # was selected a moment ago — and the invoice would then carry another
    # courier's code. A number typed for a courier the master does not know is
    # still kept.
    courier_number = _courier_number_for(courier_name, first.plant) or courier_number

    box_counts = []
    total_boxes = 0
    for label in "ABCDEF":
        try:
            quantity = int(post_data.get(f"box_{label}") or 0)
        except ValueError:
            quantity = 0
        if quantity > 0:
            total_boxes += quantity
            box_counts.append(f"{label}:{quantity}")

    docket_no = (post_data.get("docket_no") or "").strip()
    customer_code = _line_customer_code(first)
    if docket_no:
        conflict = _docket_conflict(session.entity, docket_no, customer_code, session=session)
        if conflict:
            return None, DocketConflict(conflict)

    # The two-actor flow the legacy tool used: a picker who supplies no courier
    # paperwork finishes the pick and the row lands as "pending courier" for a
    # manager to complete. A picker who *did* fill it in is done in one step, so
    # the one-step path still works exactly as before. Inferred from the data
    # rather than a checkbox, because the picker should not have to declare
    # which workflow they are in.
    has_courier_detail = bool(docket_no or courier_number or total_boxes)
    status = (
        FinishedDelivery.STATUS_COMPLETE if has_courier_detail
        else FinishedDelivery.STATUS_PENDING_COURIER
    )

    scanned = session.scans.filter(line__delivery_no=delivery_no).count()
    weight_raw = (post_data.get("weight_kg") or "").strip()
    try:
        weight = Decimal(weight_raw) if weight_raw else None
    except (InvalidOperation, ValueError):
        # A malformed weight must not abort a finished pick — the manager can
        # correct it from the courier form later.
        weight = None

    with transaction.atomic():
        finished = FinishedDelivery.objects.create(
            entity=session.entity,
            session=session,
            delivery_no=delivery_no,
            # Same fallback: a dump with no bill-to column was recording an
            # empty customer code on every finished delivery.
            customer_code=customer_code,
            customer_name=(
                first.customer_name or first.bill_to_name or first.ship_to_name or ""
            ),
            plant=first.plant or "",
            courier_partner=courier_name,
            courier_number=courier_number,
            vehicle_number=(post_data.get("vehicle_number") or "").strip(),
            docket_no=docket_no,
            mode_of_transit=(post_data.get("mode_of_transit") or "").strip(),
            vehicle_type=(post_data.get("vehicle_type") or "").strip(),
            weight_kg=weight,
            box_count=total_boxes,
            boxes_used=",".join(box_counts),
            status=status,
            is_partial=bool(gaps),
            line_count=len(lines),
            scanned_count=scanned,
            picker=user,
        )
        # Inside the same transaction as the finish: a delivery that is closed
        # but whose serials were never burned would let the next upload reuse
        # them, which is the whole point of the ledger.
        _record_dispatched_serials(session, finished, user)
    return finished, gaps


@login_required
@operate_required
@require_POST
def outbound_finish(request, pk):
    """Close out a delivery, recording courier and packaging details.

    Answers the console in place and a plain form with a redirect.

    A session holds a whole dump — often twenty-odd deliveries — and a picker
    works through them one after another without leaving the screen. Finishing
    used to redirect to the finished delivery's detail page, so closing each
    one threw the operator out of the session and they navigated back in to
    start the next; a shortfall did the same thing via a full discrepancy page.
    Neither is a failure of the finish — the data was saved either way — but
    between twenty deliveries it is twenty round trips away from the scan
    input.

    So the console gets JSON: what happened, the repainted session state, and
    (when the delivery is short and nothing said to proceed) the shortfall
    itself, which the Finish modal shows in place with the option to proceed.
    The form path is untouched — it still redirects and still renders
    outbound_discrepancy.html — because it is what a browser with no
    JavaScript, and every existing link into this endpoint, relies on.
    """
    session = get_session_or_404(request.user, pk, "entity", "dump")
    console = _wants_json(request)
    delivery_no = (request.POST.get("delivery_no") or focus.get(session, request.user).value or "").strip()
    if not delivery_no:
        if console:
            return JsonResponse(
                {"ok": False, "message": "Choose which delivery to finish."}, status=400
            )
        messages.error(request, "Choose which delivery to finish.")
        return redirect("merilv:outbound_session", pk=session.pk)

    proceed = request.POST.get("proceed_anyway") == "1"
    finished, gaps = _finish_one_delivery(session, delivery_no, request.POST, request.user, proceed_anyway=proceed)

    if finished is None:
        if gaps is None:
            if console:
                return JsonResponse(
                    {
                        "ok": False,
                        "message": f"Delivery {delivery_no} is not part of this session.",
                    },
                    status=404,
                )
            messages.error(request, f"Delivery {delivery_no} is not part of this session.")
            return redirect("merilv:outbound_session", pk=session.pk)
        if gaps is ALREADY_FINISHED:
            # Named refusal rather than a silent merge. The message says which
            # of the two situations it is, because the ways out differ: a pick
            # still pending its courier is completed from Delivery History,
            # and a fully closed one is an admin's to change.
            message = _already_finished_message(session, delivery_no)
            if console:
                return JsonResponse(
                    {"ok": False, "already_finished": True, "delivery_no": delivery_no,
                     "message": message},
                    status=409,
                )
            messages.error(request, message)
            return redirect("merilv:outbound_session", pk=session.pk)
        if isinstance(gaps, DocketConflict):
            # Checked before the shortfall branch below, which measures `gaps`
            # with len() and would read a message as a list of short lines.
            if console:
                return JsonResponse(
                    {"ok": False, "docket_conflict": True, "delivery_no": delivery_no,
                     "message": str(gaps)},
                    status=409,
                )
            messages.error(request, str(gaps))
            return redirect("merilv:outbound_session", pk=session.pk)
        # Short lines, and nothing said to proceed anyway — show the
        # discrepancy rather than silently shipping short.
        if console:
            # The same refusal, as data. The modal lists the short lines and
            # offers "finish anyway", which re-posts with proceed_anyway — one
            # decision taken where it arose, rather than a page that can only
            # be answered by navigating back.
            return JsonResponse(
                {
                    "ok": False,
                    "short": True,
                    "delivery_no": delivery_no,
                    "message": (
                        f"Delivery {delivery_no} is short on {len(gaps)} line"
                        f"{'' if len(gaps) == 1 else 's'}."
                    ),
                    "gaps": [
                        {
                            "item_no": line.item_no,
                            "material": line.material,
                            "batch": line.batch,
                            "required": float(line.required_qty),
                            "scanned": float(line.scanned_qty),
                            "pending": float(line.pending_qty),
                        }
                        for line in gaps
                    ],
                },
                status=409,
            )
        return render(
            request,
            "merilv/outbound_discrepancy.html",
            _ctx(request, session=session, delivery_no=delivery_no, gaps=gaps),
        )

    _log(request, "outbound_finish", "FinishedDelivery", finished.pk,
         delivery=delivery_no, partial=bool(gaps), status=finished.status)
    # Was that the last one? If so the session leaves the active list rather
    # than sitting there with nothing left to pick and a Delete that refuses.
    session_closed = _close_session_if_fully_shipped(session)
    if gaps:
        note = f"Delivery {delivery_no} closed with {len(gaps)} short line(s)."
        level = "warn"
    elif finished.status == FinishedDelivery.STATUS_PENDING_COURIER:
        note = (
            f"Delivery {delivery_no} marked complete. "
            "A manager will fill in the courier details."
        )
        level = "ok"
    else:
        note = f"Delivery {delivery_no} completed."
        level = "ok"

    if console:
        session.refresh_from_db()
        if session_closed:
            note += " That was the last delivery — this session is complete."
        return JsonResponse(
            {
                "ok": True,
                "delivery_no": delivery_no,
                "message": note,
                "level": level,
                # Every delivery in the dump is finished. The console says so
                # and stops inviting the next scan; there is nothing left.
                "session_closed": session_closed,
                "partial": bool(gaps),
                "status": finished.status,
                # Offered as a link rather than followed: the operator decides
                # whether to look at the paperwork now or carry on picking.
                "detail_url": reverse("merilv:delivery_detail", args=[finished.pk]),
                "state": _session_payload(session, request.user),
            }
        )

    getattr(messages, "warning" if gaps else "success")(request, note)
    return redirect("merilv:delivery_detail", pk=finished.pk)


@login_required
@operate_required
@require_POST
def outbound_bulk_finish(request, pk):
    """Finish several complete deliveries in one action — Row 2.4's parity
    with the legacy screen's "Finish & Export Selected" bulk bar.

    Every delivery in the batch gets the same courier/vehicle/box paperwork,
    since a batch finished together normally ships together. Deliveries that
    are already finished, aren't part of this session, or are short without
    ``proceed_anyway`` are skipped and reported rather than silently dropped
    or allowed to abort the whole batch.
    """
    session = get_session_or_404(request.user, pk, "entity", "dump")
    delivery_numbers = [d.strip() for d in request.POST.getlist("delivery_no") if d.strip()]
    if not delivery_numbers:
        return JsonResponse({"ok": False, "message": "Select at least one delivery to finish."}, status=400)

    proceed = request.POST.get("proceed_anyway") == "1"
    finished_ids = []
    skipped = []
    with transaction.atomic():
        already_finished = _finished_delivery_numbers(session)
        for delivery_no in delivery_numbers:
            if delivery_no in already_finished:
                skipped.append({"delivery_no": delivery_no, "reason": "Already finished."})
                continue
            finished, gaps = _finish_one_delivery(
                session, delivery_no, request.POST, request.user, proceed_anyway=proceed
            )
            if finished is None:
                if isinstance(gaps, DocketConflict):
                    reason = str(gaps)
                elif gaps is None:
                    reason = "Not part of this session."
                else:
                    reason = f"{len(gaps)} short line(s) — resolve or force before including it."
                skipped.append({"delivery_no": delivery_no, "reason": reason})
                continue
            finished_ids.append(finished.pk)

    _log(
        request, "outbound_bulk_finish", "OutboundSession", session.pk,
        finished=len(finished_ids), skipped=len(skipped),
    )
    # The path most likely to finish the LAST delivery, since it closes several
    # at once — so it has to complete the session for the same reason the
    # single finish does.
    session_closed = _close_session_if_fully_shipped(session)
    return JsonResponse(
        {
            "ok": True,
            "finished_count": len(finished_ids),
            "finished_ids": finished_ids,
            "skipped": skipped,
            "session_closed": session_closed,
            "state": _session_payload(session, request.user),
        }
    )


@login_required
@operate_required
@capability_required("mv_courier_edit")
@require_POST
def delivery_courier_update(request, pk):
    """Fill in courier paperwork on a delivery the picker left pending.

    Separate from ``outbound_finish`` because it is a different person doing a
    different job at a different time — the picker has gone home. Kept behind
    ``operate_required`` and the caller's own data scope so one team cannot
    complete another team's paperwork.
    """
    delivery = get_object_or_404(
        scope_queryset_by_team(
            FinishedDelivery.objects.all(), request.user,
            field="picker", plant_path=DELIVERY_PLANT_PATH,
        ),
        pk=pk,
    )

    docket_no = (request.POST.get("docket_no") or "").strip()
    courier_number = (request.POST.get("courier_number") or "").strip()
    # Validated server-side. The legacy form checked these with two browser
    # alert() calls, which any direct POST bypassed entirely.
    if not courier_number:
        messages.error(request, "Courier number is required.")
        return redirect("merilv:delivery_detail", pk=delivery.pk)
    if not docket_no:
        messages.error(request, "Docket number is required.")
        return redirect("merilv:delivery_detail", pk=delivery.pk)

    if docket_no != delivery.docket_no:
        conflict = _docket_conflict(
            delivery.entity, docket_no, delivery.customer_code,
            session=delivery.session, exclude_pk=delivery.pk,
        )
        if conflict:
            messages.error(request, conflict)
            return redirect("merilv:delivery_detail", pk=delivery.pk)

    box_counts = []
    total_boxes = 0
    for label in "ABCDEF":
        try:
            quantity = int(request.POST.get(f"box_{label}") or 0)
        except ValueError:
            quantity = 0
        if quantity < 0:
            messages.error(request, f"Box {label} count cannot be negative.")
            return redirect("merilv:delivery_detail", pk=delivery.pk)
        if quantity:
            total_boxes += quantity
            box_counts.append(f"{label}:{quantity}")

    weight_raw = (request.POST.get("weight_kg") or "").strip()
    try:
        weight = Decimal(weight_raw) if weight_raw else None
    except (InvalidOperation, ValueError):
        messages.error(request, "Enter the weight as a number, for example 12.5.")
        return redirect("merilv:delivery_detail", pk=delivery.pk)
    if weight is not None and weight < 0:
        messages.error(request, "Weight cannot be negative.")
        return redirect("merilv:delivery_detail", pk=delivery.pk)

    with transaction.atomic():
        delivery.courier_number = courier_number
        delivery.courier_partner = (request.POST.get("courier_partner") or delivery.courier_partner).strip()
        delivery.vehicle_number = (request.POST.get("vehicle_number") or "").strip()
        delivery.vehicle_type = (request.POST.get("vehicle_type") or "").strip()
        delivery.mode_of_transit = (request.POST.get("mode_of_transit") or "").strip()
        delivery.docket_no = docket_no
        if weight is not None:
            delivery.weight_kg = weight
        if box_counts:
            delivery.box_count = total_boxes
            delivery.boxes_used = ",".join(box_counts)
        delivery.status = FinishedDelivery.STATUS_COMPLETE
        delivery.save()

    _log(request, "delivery_courier_update", "FinishedDelivery", delivery.pk, docket=docket_no)
    messages.success(request, f"Courier details saved for {delivery.delivery_no}.")
    return redirect("merilv:delivery_detail", pk=delivery.pk)


#: The legacy invoice-upload sheet, column for column. These names are what the
#: downstream SAP upload expects, so they are not ours to tidy.
DELIVERY_INVOICE_HEADERS = (
    "DATE", "DEL", "CURR", "DOC", "DISP_DT", "BOX", "WEIGHT", "WT_UNIT",
    "DEL_DT", "COST", "TRANS", "DISTAN", "VEH", "VEH_TYPE",
)


def _selected_deliveries(request, deliveries):
    """Narrow to the ticked rows, or the whole filtered set if none are ticked.

    ``pks`` is intersected with the already-scoped queryset rather than used to
    look rows up directly — otherwise anyone could export another team's
    delivery by posting its id.
    """
    picked = request.GET.getlist("pks") or request.POST.getlist("pks")
    if not picked:
        return deliveries
    numeric = [value for value in picked if str(value).isdigit()]
    return deliveries.filter(pk__in=numeric)


#: Ceiling on an unfiltered "Export All". Without one, hitting the export with
#: no filters on a year of dispatch history builds every row in memory and then
#: an openpyxl workbook on top of it. delivery_packing_list already caps at 50;
#: the two Excel paths were the ones that never got the same treatment.
EXPORT_ROW_CAP = 10_000


def _capped(rows):
    """The export slice plus whether it truncated.

    Returned as a pair so the caller can tell the operator. Silently exporting
    the first N rows of a larger result is the failure mode worth avoiding —
    a short spreadsheet looks complete.
    """
    limited = list(rows[:EXPORT_ROW_CAP + 1])
    if len(limited) > EXPORT_ROW_CAP:
        return limited[:EXPORT_ROW_CAP], True
    return limited, False


@login_required
@access_required
def delivery_export(request):
    """Excel export of the current selection — the legacy Export Selected / All."""
    deliveries, _filters = _filtered_deliveries(request)
    rows, truncated = _capped(_selected_deliveries(request, deliveries))

    headers = [
        "Date", "Delivery No", "Plant", "Customer Code", "Customer Name", "Courier Partner",
        # The code, like every other sheet — but this one is read by people
        # rather than by SAP, and "M2" alone in a column called Mode tells them
        # nothing. The invoice sheet cannot do this: its headers are matched
        # verbatim by SAP and are not ours to annotate.
        "Courier No", "Docket", "Vehicle", "Vehicle Type",
        "Mode (M1 air/rail, M2 surface)", "Boxes",
        "Box A", "Box B", "Box C", "Box D", "Box E", "Box F",
        "Weight (kg)", "Lines", "Scanned", "Status", "Picker",
    ]
    data = []
    for delivery in rows:
        boxes = delivery.box_breakdown
        data.append([
            delivery.created_at, delivery.delivery_no, delivery.plant,
            delivery.customer_code, delivery.customer_name, delivery.courier_partner,
            delivery.courier_number, delivery.docket_no, delivery.vehicle_number,
            delivery.vehicle_type, transit.code_for(delivery.mode_of_transit), delivery.box_count,
            boxes["A"], boxes["B"], boxes["C"], boxes["D"], boxes["E"], boxes["F"],
            delivery.weight_kg, delivery.line_count, delivery.scanned_count,
            delivery.get_status_display(),
            delivery.picker.get_full_name() if delivery.picker else "",
        ])

    if truncated:
        data.append([f"Truncated at {EXPORT_ROW_CAP:,} rows — narrow the filters to export the rest."])

    buffer = export.export_generic("Deliveries", headers, data)
    return _xlsx_response(buffer, "merilv_deliveries.xlsx")


@login_required
@access_required
def delivery_invoice_export(request):
    """The 14-column invoice-upload sheet the legacy screen produced."""
    deliveries, _filters = _filtered_deliveries(request)
    rows, truncated = _capped(_selected_deliveries(request, deliveries))

    data = []
    for delivery in rows:
        stamp = timezone.localtime(delivery.created_at).strftime("%d.%m.%Y")
        # COST, DISTAN and DEL_DT blank, and the CURR column carrying the
        # courier partner code, for the reasons spelled out in
        # outbound_invoice_export: the two exports produce the same sheet and
        # must not disagree.
        data.append([
            stamp, delivery.delivery_no, delivery.courier_number,
            delivery.docket_no, stamp,
            delivery.box_count, delivery.weight_kg, "KG", None, None,
            transit.code_for(delivery.mode_of_transit), None, delivery.vehicle_number,
            delivery.vehicle_type or "R",
        ])

    if truncated:
        data.append([f"Truncated at {EXPORT_ROW_CAP:,} rows — narrow the filters to export the rest."])

    buffer = export.export_generic("Invoice", list(DELIVERY_INVOICE_HEADERS), data)
    return _xlsx_response(buffer, "Invoice_Format.xlsx")


@login_required
@access_required
def delivery_packing_list(request):
    """A printable packing list per delivery, for the selected rows.

    Rendered from a template through xhtml2pdf rather than drawn primitive by
    primitive: the legacy version was ~350 lines of jsPDF coordinate maths that
    nobody could safely change, and this is a document whose wording and layout
    the business will want to edit.
    """
    from xhtml2pdf import pisa

    deliveries, _filters = _filtered_deliveries(request)
    rows = list(_selected_deliveries(request, deliveries)[:50])
    if not rows:
        messages.error(request, "Nothing selected to print.")
        return redirect("merilv:delivery_history")

    packs = []
    for delivery in rows:
        scans = []
        if delivery.session_id:
            scans = list(
                OutboundScan.objects.filter(
                    session_id=delivery.session_id, line__delivery_no=delivery.delivery_no,
                ).select_related("line")[:500]
            )
        packs.append({"delivery": delivery, "scans": scans, "boxes": delivery.box_breakdown})

    html = render(request, "merilv/delivery_packing_list.html", {
        "packs": packs,
        "generated_at": timezone.localtime(),
        "generated_by": request.user.get_full_name() or request.user.username,
    }).content.decode("utf-8")

    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        messages.error(request, "The packing list could not be generated.")
        return redirect("merilv:delivery_history")

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="packing_list.pdf"'
    return response


def _courier_of(resolved, delivery_no):
    """One delivery's resolved courier, or a blank entry."""
    return resolved.get(delivery_no) or _courier_entry("", "", source="")


def _courier_entry(name, number, vehicle="", vehicle_type="", source="", mode=""):
    """One resolved courier, in the shape the console and the modal consume.

    ``source`` names the rule that fired — "customer" or "plant" — so the
    modal's hint can state where the value came from instead of asserting one
    origin for all of them. The hint claiming a source it did not have is what
    made this feature read as broken.

    Vehicle and type ride along because courier names are not unique across
    plants: "Blue Dart" exists for more than one, with different numbers and
    vehicles, so the client cannot safely look the row up by name.
    """
    return {
        "name": (name or "").strip(),
        "number": (number or "").strip(),
        "vehicle": (vehicle or "").strip(),
        "vehicle_type": (vehicle_type or "").strip(),
        "source": source if (name or "").strip() else "",
        # Mode of transit off the Courier Master row. Blank when the courier
        # is unknown to the master or the master has not been told — the form
        # then leaves its own default standing rather than asserting one.
        #
        # Normalised to the radio group's own vocabulary so a master row
        # reading "SURFACE" selects Road instead of growing a sixth radio.
        # A word transit.py does not know is passed through untouched; that
        # is what the modal's add-a-radio path is for.
        "mode": transit.canonical(mode) or (mode or "").strip(),
    }


def _line_customer_code(line):
    """The customer code to key a courier lookup on.

    Bill-to first, ship-to as the fallback. A YVL06 export has no bill-to
    column — it carries "Ship-to party" — so keying only on bill-to matched an
    empty string and never resolved a courier for dumps of that shape, which
    is every MLSIPL one. Bill-to still wins where a dump does carry it: the
    payer is the right key for a courier account.
    """
    return (line.bill_to_code or "").strip() or (line.ship_to_code or "").strip()


def _resolve_couriers_for(entity, lines):
    """``{delivery_no: {name, number, vehicle, vehicle_type, source}}`` per dump.

    Same precedence as :func:`_resolve_courier` — plant-specific mapping, then
    any mapping for the customer, then the customer master, then the plant
    default — but resolved in a handful of queries for the whole dump instead
    of two per delivery. This runs inside the session payload, which the
    console rebuilds every two seconds, so a per-delivery lookup on a
    24-delivery dump would be ~50 queries a tick.

    The first line of each delivery decides, which is what the single-row
    version does too: bill-to and plant are properties of the delivery, not of
    the line.
    """
    from .models import CustomerMaster

    firsts = {}
    for line in lines:
        firsts.setdefault(line.delivery_no, line)
    if not firsts:
        return {}

    keys = {delivery: _line_customer_code(line) for delivery, line in firsts.items()}
    codes = {code for code in keys.values() if code}

    # Plant-specific overrides for this entity, then the customer-wide ones.
    by_code_plant, by_code = {}, {}
    mappings = (
        CustomerCourierMapping.objects.filter(customer_code__in=codes) if codes else []
    )
    for mapping in mappings:
        if mapping.entity_id == entity.pk and mapping.plant:
            by_code_plant.setdefault((mapping.customer_code, mapping.plant), mapping)
        by_code.setdefault(mapping.customer_code, mapping)

    customers = (
        {row.customer_code: row
         for row in CustomerMaster.objects.filter(customer_code__in=codes)}
        if codes else {}
    )

    # The dispatching plant's default carrier. This is the last resort for
    # rows that do not carry a usable customer code, which is exactly the
    # shape the MLSIPL/YVL exports use for some deliveries.
    plants = {(line.plant or "").strip() for line in firsts.values()}
    plants.discard("")
    by_plant = {}
    if plants:
        for courier in CourierMaster.objects.filter(plant__in=plants, is_active=True):
            by_plant.setdefault(courier.plant, courier)

    resolved = {}
    for delivery_no, line in firsts.items():
        code = keys[delivery_no]
        mapping = by_code_plant.get((code, line.plant)) if code else None
        if mapping is None and code:
            mapping = by_code.get(code)
        if mapping:
            resolved[delivery_no] = _courier_entry(
                mapping.courier_name, mapping.courier_number, source="customer",
            )
            continue
        customer = customers.get(code) if code else None
        if customer and (customer.courier_partner or "").strip():
            resolved[delivery_no] = _courier_entry(
                customer.courier_partner, customer.courier_number, source="customer",
            )
            continue

        # Nothing customer-specific: use the dispatching plant's default.
        plant_courier = by_plant.get((line.plant or "").strip())
        resolved[delivery_no] = (
            _courier_entry(
                plant_courier.courier_name, plant_courier.courier_number,
                vehicle=plant_courier.vehicle_number,
                vehicle_type=plant_courier.vehicle_type,
                source="plant",
                mode=plant_courier.mode_of_transit,
            )
            if plant_courier else _courier_entry("", "", source="")
        )

    # Fill in what only Courier Master knows — mode of transit, and the
    # vehicle fields a customer mapping does not carry — for every courier
    # resolved by name above. Keyed on (name, plant) first because a courier
    # name is not unique across plants and the two rows genuinely differ.
    _enrich_from_courier_master(resolved, firsts)
    return resolved


def _enrich_from_courier_master(resolved, firsts):
    """Add the Courier Master columns a customer-level mapping cannot supply.

    Mutates ``resolved`` in place. One query for every courier named across
    the dump, rather than one per delivery: this runs inside the session
    payload, which the console rebuilds every two seconds.
    """
    names = {entry["name"] for entry in resolved.values() if entry["name"]}
    if not names:
        return

    by_name_plant, by_name = {}, {}
    for courier in CourierMaster.objects.filter(courier_name__in=names, is_active=True):
        by_name_plant.setdefault((courier.courier_name, (courier.plant or "").strip()), courier)
        by_name.setdefault(courier.courier_name, courier)

    for delivery_no, entry in resolved.items():
        if not entry["name"]:
            continue
        line = firsts.get(delivery_no)
        plant = (getattr(line, "plant", "") or "").strip()
        courier = by_name_plant.get((entry["name"], plant)) or by_name.get(entry["name"])
        if courier is None:
            continue
        # Never overwrite what the customer mapping already decided — this
        # only fills blanks, the same rule the modal's autofill follows.
        master_mode = (courier.mode_of_transit or "").strip()
        entry["mode"] = entry["mode"] or transit.canonical(master_mode) or master_mode
        entry["vehicle"] = entry["vehicle"] or (courier.vehicle_number or "").strip()
        entry["vehicle_type"] = entry["vehicle_type"] or (courier.vehicle_type or "").strip()
        # The courier NUMBER is the partner code (24BD, 07BY, 24ART …) and it
        # is the invoice sheet's third column. It only fills a BLANK here: a
        # customer-level code, where one exists, is the one that reaches SAP.
        #
        # This used to be the other way round — Courier Master overrode the
        # customer's code — on the reading that one master owns the value. The
        # data says otherwise. The same courier legitimately carries two
        # different partner codes depending on the customer:
        #
        #     Arts Express Solutions    24AES (102 customers) / 24ART (189)
        #     Blue Dart Express Limited 24BD   (51)           / 41BD  (85)
        #     By Hand                   BH     (37)           / HAND  (26)
        #     Maruti Air Couriers       24MCA (152)           / 41MC  (150)
        #     Pannest Integrated        24PIS  (48)           / 24PTI (80)
        #
        # Courier Master holds one row per courier, so it can only carry one
        # of each pair — and overriding flattened roughly half of every
        # customer base onto the wrong code. That is what was reported as
        # "41 MC, 24 AES and 24 BD disappearing". Courier Master remains the
        # answer for a delivery resolved by PLANT default, which has no
        # customer code to prefer, and for a customer row that names a courier
        # without repeating its number.
        entry["number"] = entry["number"] or (courier.courier_number or "").strip()


def _courier_number_for(name, plant):
    """The partner code Courier Master holds for this courier.

    Keyed on (name, plant) first because a courier name is not unique across
    plants — "Blue Dart" exists for several, with different numbers — and only
    then on the name alone.
    """
    name = (name or "").strip()
    if not name:
        return ""
    plant = (plant or "").strip()
    rows = list(CourierMaster.objects.filter(courier_name=name, is_active=True))
    for row in rows:
        if (row.plant or "").strip() == plant:
            return (row.courier_number or "").strip()
    return (rows[0].courier_number or "").strip() if rows else ""


def _with_master_number(name, number, line):
    """The caller's partner code wins; Courier Master only fills a blank.

    The caller here is a CustomerCourierMapping row or a CustomerMaster row,
    and the code they carry is customer-specific on purpose — the same courier
    is 24AES for one customer and 24ART for another, BH for one and HAND for
    another. Courier Master has one row per courier and cannot express that,
    so it is the fallback rather than the authority. See the note in
    :func:`_enrich_from_courier_master`, which applies the same rule on the
    console's display path; the two must agree or the modal shows one code and
    the saved record carries another.
    """
    name = (name or "").strip()
    number = (number or "").strip()
    if not name:
        return "", number
    return name, number or _courier_number_for(name, getattr(line, "plant", ""))


def _resolve_courier(entity, line):
    """Plant-specific courier mapping first, then the customer's default.

    Returns ``(name, number)``, where the number is the partner code the
    invoice sheet carries. A mapping or customer row that names a courier
    without repeating its number is completed from Courier Master rather than
    returned blank — see ``_enrich_from_courier_master`` for why that is the
    normal case rather than the exception. If nothing customer-specific
    resolves, the caller gets blanks and can enter the courier manually.
    """
    code = _line_customer_code(line)
    if code:
        mapping = CustomerCourierMapping.objects.filter(
            customer_code=code, entity=entity, plant=line.plant
        ).first()
        if mapping is None:
            mapping = CustomerCourierMapping.objects.filter(customer_code=code).first()
        if mapping:
            return _with_master_number(mapping.courier_name, mapping.courier_number, line)

        from .models import CustomerMaster

        customer = CustomerMaster.objects.filter(customer_code=code).first()
        if customer and (customer.courier_partner or "").strip():
            return _with_master_number(customer.courier_partner, customer.courier_number, line)

    # Nothing customer-specific resolved — fall through to the dispatching
    # plant's default. A line that CARRIES a customer code the Customer Master
    # has never heard of used to stop here and return blanks, while the
    # batched resolver behind the Finish modal went on to the plant tier. The
    # screen then showed a courier the submit path would not save, and the
    # invoice's courier code came out empty. The two must agree.
    plant = (line.plant or "").strip()
    if plant:
        plant_courier = CourierMaster.objects.filter(plant=plant, is_active=True).first()
        if plant_courier:
            return plant_courier.courier_name, plant_courier.courier_number
    return "", ""


def _resolve_courier_detail(entity, line):
    """The full resolved courier for one line, matching the batched resolver.

    Used where the vehicle and type are wanted as well as the name — the
    submit path keeps the two-value :func:`_resolve_courier` it has always
    had, so its callers are untouched.
    """
    return _resolve_couriers_for(entity, [line]).get(line.delivery_no)


@login_required
@operate_required
@capability_required("mv_batch_correction_outbound")
@require_POST
def outbound_line_batch(request, pk):
    """Override the batch on a delivery line.

    SAP dumps sometimes name a batch the warehouse cannot supply, and the
    picker substitutes another. The override is locked once anything has been
    scanned onto the line — silently repointing scanned units at a different
    batch would corrupt the dispatch record. Undo the scans first.
    """
    session = get_session_or_404(request.user, pk)
    line = get_object_or_404(
        DeliveryLine, pk=int_or_none(request.POST.get("line_id")), dump=session.dump,
    )
    new_batch = (request.POST.get("batch") or "").strip().upper()

    if not new_batch:
        return JsonResponse({"ok": False, "message": "Enter a batch number."}, status=400)

    # Re-read under a row lock and re-check: the scanned_qty > 0 guard is the
    # whole point of this endpoint, and checking it outside a transaction let a
    # scan land between the check and the save — repointing an already-scanned
    # unit at a different batch, exactly what the docstring says it prevents.
    with transaction.atomic():
        line = DeliveryLine.objects.select_for_update().get(pk=line.pk)
        if float(line.scanned_qty) > 0:
            return JsonResponse(
                {"ok": False, "message": "Batch change is locked — undo this line's scans first."},
                status=409,
            )

        if not line.original_batch:
            line.original_batch = line.batch
        line.batch = new_batch
        line.batch_overridden = new_batch != line.original_batch
        line.save(update_fields=["batch", "original_batch", "batch_overridden"])

    _log(
        request, "outbound_batch_override", "DeliveryLine", line.pk,
        original=line.original_batch, replacement=new_batch,
    )
    return JsonResponse({"ok": True, "state": _session_payload(session, request.user)})


@login_required
@operate_required
@capability_required("mv_serial_edit")
@require_POST
def outbound_edit_scan(request, pk):
    """Correct a mistyped serial without disturbing the quantity counters."""
    session = get_session_or_404(request.user, pk)
    old_identity = (request.POST.get("serial_identity") or "").strip()
    scan_id = int_or_none(request.POST.get("scan_id"))
    # Through the same formatter as a scanned serial, so a supervisor typing
    # "7" produces the same assigned serial as a label printed "(21)007".
    new_serial = format_serial_component(request.POST.get("serial"))

    if not new_serial:
        return JsonResponse({"ok": False, "message": "Enter the corrected serial."}, status=400)

    # Locked because the uniqueness the database now enforces is on the whole
    # identity: two concurrent corrections would both pass the exists() check
    # and the second would fail at commit with an IntegrityError rather than a
    # message anyone can act on.
    with transaction.atomic():
        queryset = _locked_scans(session)
        scan = (
            queryset.filter(pk=scan_id).first() if scan_id is not None
            else queryset.filter(serial_identity=old_identity).first()
        )
        if scan is None:
            return JsonResponse(
                {"ok": False, "message": "That scan is no longer in this session."}, status=404,
            )

        new_identity = f"{(scan.batch or '').upper()}{new_serial}"
        if session.scans.filter(serial_identity=new_identity).exclude(pk=scan.pk).exists():
            return JsonResponse(
                {"ok": False, "message": f"Serial {new_serial} is already packed."}, status=409,
            )
        # A correction is another way to assign a serial, so it answers to the
        # same permanence rule as a scan — otherwise the one gate the ledger
        # exists to enforce could be walked straight past by editing a serial
        # into one that has already shipped.
        dispatched = (
            DispatchedSerial.objects.filter(serial=new_identity)
            .exclude(finished_delivery__session_id=session.pk)
            .values_list("delivery_no", flat=True)
            .first()
        )
        if dispatched:
            return JsonResponse(
                {
                    "ok": False,
                    "message": (
                        f"Serial {new_identity} was already dispatched on delivery "
                        f"{dispatched} — it cannot be assigned again."
                    ),
                },
                status=409,
            )

        scan.serial_identity = new_identity
        # The stored serial is the whole assigned value, batch included — the
        # same thing the scan path writes and the same thing the SAP sheet's
        # Serial column carries. Writing the bare serial here was why a
        # corrected scan exported a serial with its batch prefix missing.
        scan.serial_only = new_identity
        scan.save(update_fields=["serial_identity", "serial_only"])

    _log(request, "outbound_edit_scan", "OutboundScan", scan.pk, old=old_identity, new=new_identity)
    return JsonResponse(
        {
            "ok": True,
            "serial_identity": new_identity,
            "serial_only": new_identity,
            "state": _session_payload(session, request.user),
        }
    )


#: Reasons a delivery has no invoice to give. Each is a state the operator can
#: act on, so each says which one it is rather than "not available".
_INVOICE_NOT_READY = {
    "unknown": "Delivery {no} is not part of this session.",
    "unfinished": (
        "Delivery {no} is not finished yet. The invoice sheet is built from the "
        "dispatch record — the docket, the box count, the weight and the vehicle "
        "— and none of that exists until the delivery is finished."
    ),
    "pending_courier": (
        "Delivery {no} is finished but its courier details are still outstanding. "
        "An invoice with no docket, vehicle or weight is a sheet SAP will reject; "
        "fill in the dispatch paperwork first."
    ),
}


def _invoice_state(session, delivery_no):
    """``(finished_delivery, reason)`` — exactly one of the two is set.

    The invoice sheet is fourteen columns of dispatch paperwork
    (DELIVERY_INVOICE_HEADERS) and every one of them is read off
    ``FinishedDelivery``. There is no partial answer to give before a delivery
    is finished: not a thinner sheet, not an empty one — the row does not
    exist. So this is a real prerequisite rather than a policy, and it is worth
    saying which of the two prerequisites is missing.

    A shortfall is not one of them. A delivery shipped short on purpose has
    still shipped, and what shipped is what gets invoiced.
    """
    if not (session.dump and session.dump.lines.filter(delivery_no=delivery_no).exists()):
        return None, "unknown"
    finished = (
        FinishedDelivery.objects.filter(session=session, delivery_no=delivery_no)
        .order_by("pk")
        .first()
    )
    if finished is None:
        return None, "unfinished"
    if finished.status == FinishedDelivery.STATUS_PENDING_COURIER:
        return None, "pending_courier"
    return finished, ""


@login_required
@access_required
def outbound_invoice_export(request, pk):
    """The invoice-format sheet for one finished delivery, from the console.

    The same fourteen columns Delivery History exports (delivery_invoice_export)
    — one format, not two — but reachable where the delivery was picked. An
    operator who has just closed a delivery should not have to leave the session
    and find it again in history to get its invoice sheet.

    Refused unless the delivery is finished AND its courier paperwork is in,
    because those are the columns. The refusal names which of the two is
    missing; the console shows it rather than downloading an empty sheet.
    """
    session = get_session_or_404(request.user, pk, "entity", "dump")
    delivery_no = (request.GET.get("delivery_no") or focus.get(session, request.user).value or "").strip()
    if not delivery_no:
        return JsonResponse(
            {"ok": False, "message": "Choose which delivery to invoice."}, status=400
        )

    finished, reason = _invoice_state(session, delivery_no)
    if finished is None:
        message = _INVOICE_NOT_READY[reason].format(no=delivery_no)
        if _wants_json(request):
            return JsonResponse(
                {"ok": False, "reason": reason, "message": message}, status=409
            )
        messages.error(request, message)
        return redirect("merilv:outbound_session", pk=session.pk)

    stamp = timezone.localtime(finished.created_at).strftime("%d.%m.%Y")
    # COST, DISTAN and DEL_DT are left EMPTY — not zero, and not an empty
    # string.
    #
    # None of the three is ours to state. Freight cost is priced by the finance
    # team against the courier's own bill, which does not exist yet at the
    # moment a picker closes a delivery; distance is the carrier's; and DEL_DT
    # is the date the goods reach the customer, which has not happened when the
    # van has not left. It was being filled with the date the PICK finished,
    # which is DISP_DT — the same value in two columns, one of them answering a
    # question nobody could answer yet.
    #
    # Writing 0 into a cost column reads as "this shipment was free" to
    # whatever ingests the sheet; writing "" puts a zero-length string into a
    # column, which is a different input again from a column nobody filled in.
    # None is the only one of the three that says what is true — see
    # _cell_value in services/export.py, which keeps it genuinely blank all the
    # way to the file.
    #
    # Third column is the COURIER PARTNER CODE, not a currency, despite the
    # header reading CURR — it carried a hardcoded "INR" until the business
    # said what the column is actually for.
    #
    # That code is `courier_number`: the Courier Master holds it there (24BD,
    # 07BY, 24ART …) and _resolve_courier already stamps it onto the delivery
    # at finish time, so it is read straight off the row rather than looked up
    # again. See delivery_invoice_export, which produces the same sheet and
    # must not disagree.
    rows = [[
        stamp, finished.delivery_no,
        finished.courier_number, finished.docket_no, stamp,
        finished.box_count, finished.weight_kg, "KG", None, None,
        transit.code_for(finished.mode_of_transit), None, finished.vehicle_number,
        finished.vehicle_type or "R",
    ]]
    _log(request, "outbound_invoice_export", "FinishedDelivery", finished.pk,
         delivery=delivery_no)
    buffer = export.export_generic("Invoice", list(DELIVERY_INVOICE_HEADERS), rows)
    return _xlsx_response(buffer, export.filename_for("invoice", delivery_no))


@login_required
@access_required
def outbound_export(request, pk):
    """Export the SAP serial-upload sheet for one delivery."""
    session = get_session_or_404(request.user, pk, "entity")
    # The parameter alone, not the session's focus.
    #
    # It used to fall back to ``session.focus_delivery_no``, which was harmless
    # while focus was optional and most sessions had none. Now that nothing can
    # be scanned unfocused, every session has one, and that fallback would make
    # the whole-session export unreachable — an unparameterised request would
    # always come back as one delivery. The console asks for the focused
    # delivery explicitly (see the Export link in outbound_session.html and the
    # per-card export), so "no delivery_no" can go back to meaning "everything".
    delivery_no = (request.GET.get("delivery_no") or "").strip()
    scans = session.scans.select_related("line").order_by("created_at")
    if delivery_no:
        scans = scans.filter(line__delivery_no=delivery_no)

    box_lookup = {box.code: box for box in BoxProfile.objects.all()}
    rows = []
    for scan in scans:
        line = scan.line
        box = box_lookup.get(scan.box)
        rows.append(
            {
                "delivery_no": line.delivery_no if line else "",
                "item_no": line.item_no if line else "",
                "material": scan.material,
                "plant": line.plant if line else "",
                "location": line.location if line else "",
                "batch": scan.batch,
                "serial": scan.serial_only,
                # Both, and export_outbound prefers the code — see the note
                # there. The name stays available for the fallback and for the
                # screens that read this same dict.
                "customer_code": _line_customer_code(line) if line else "",
                "customer_name": line.customer_name if line else "",
                "box": scan.box,
                "box_l": float(box.length_cm) if box else "",
                "box_w": float(box.width_cm) if box else "",
                "box_h": float(box.height_cm) if box else "",
                "vol_divisor": box.divisor if box else "",
                "vol_weight": box.volumetric_weight if box else "",
                "saved_date": scan.created_at.strftime("%d-%m-%Y"),
                "saved_time": scan.created_at.strftime("%H:%M:%S"),
                "scan_string": scan.raw_scan,
            }
        )

    buffer = export.export_outbound(
        delivery_no or str(session),
        rows,
        include_customer=request.GET.get("customer") == "1",
        include_box=request.GET.get("box") == "1",
        include_box_dims=request.GET.get("dims") == "1",
    )
    # Every scan, delete and export logged with a user and a timestamp. Scan
    # (ScanLog), undo, admin-delete and finish already wrote to the activity
    # log; this was the one silent action — downloading a delivery's SAP serial
    # sheet left no record of who took it or when.
    _log(request, "outbound_export", "OutboundSession", session.pk,
         delivery_no=delivery_no, rows=len(rows))
    return _xlsx_response(buffer, export.filename_for("outbound", delivery_no or session.pk))


# ─────────────────────────────────────────────────────────────────────────────
# Delivery history & reporting
# ─────────────────────────────────────────────────────────────────────────────
#: Sort options, mirroring the legacy screen's dropdown. Mapped to real ORM
#: fields here rather than sorted client-side as the old code did, so sorting is
#: correct across the whole result set and not just the visible page.
DELIVERY_SORTS = {
    "date_desc": ("-created_at", "Newest first"),
    "date_asc": ("created_at", "Oldest first"),
    "del_asc": ("delivery_no", "Delivery No. A-Z"),
    "del_desc": ("-delivery_no", "Delivery No. Z-A"),
    "scans_desc": ("-scanned_count", "Most scans"),
}

#: Page sizes the legacy screen offered. Whitelisted, not taken from the query
#: string as-is — an unbounded `per_page` is a trivial way to exhaust memory.
DELIVERY_PAGE_SIZES = (25, 50, 75, 100)


def _parse_date(value):
    """Parse a yyyy-mm-dd filter value, ignoring anything malformed."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _delivery_analytics(deliveries):
    """The legacy 'Summary Analytics' tab, computed over the filtered set.

    Aggregated in the database rather than by walking rows in Python: the old
    code loaded every record into the browser and counted there, which is fine
    for a demo and not for a year of deliveries.
    """
    totals = deliveries.aggregate(
        deliveries=Count("id"),
        scans=Sum("scanned_count"),
        boxes=Sum("box_count"),
    )
    # Scoped through the delivery number, not through ``session__finished``.
    # ``finished`` is the reverse side of one session's many finished
    # deliveries, so that filter joined every scan in a session against every
    # finished delivery in it and counted each scan once per delivery. On the
    # live data that turned 319 real scans into 13,079 rows and reported a
    # material scanned 6 times as "246" — inflated ~41x, and mis-ranked, since
    # the multiplier differs per session. The session clause stays so a
    # delivery number reused by another entity cannot bleed in.
    scoped_scans = OutboundScan.objects.filter(
        session__in=deliveries.values("session_id"),
        line__delivery_no__in=deliveries.values("delivery_no"),
    )
    top_materials = list(
        scoped_scans.values("material")
        .annotate(qty=Count("id"))
        .order_by("-qty", "material")[:20]
    )
    top_pickers = list(
        deliveries.exclude(picker__isnull=True)
        .values("picker__username", "picker__first_name", "picker__last_name")
        .annotate(scans=Sum("scanned_count"), deliveries=Count("id"))
        .order_by("-scans")[:10]
    )
    by_day = list(
        deliveries.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(deliveries=Count("id"), scans=Sum("scanned_count"))
        .order_by("-day")[:30]
    )
    return {
        "deliveries": totals["deliveries"] or 0,
        "scans": totals["scans"] or 0,
        "boxes": totals["boxes"] or 0,
        # "Unique products" is distinct materials across the filtered set, which
        # is why it cannot come from the same aggregate() as the counts above.
        "unique_products": scoped_scans.values("material").distinct().count(),
        "avg_scans": round((totals["scans"] or 0) / totals["deliveries"], 1) if totals["deliveries"] else 0,
        "top_materials": top_materials,
        "top_pickers": top_pickers,
        "by_day": by_day,
    }


def _filtered_deliveries(request):
    """The delivery queryset every Delivery History view shares.

    One function so the table, the analytics and all three exports can never
    disagree about what "the current selection" means — the legacy screen had
    the filter logic duplicated per export and they drifted.
    """
    deliveries = FinishedDelivery.objects.select_related("entity", "picker")
    # A picker sees their team's deliveries, a manager their team's, an admin
    # everyone's. Applied before any user filter so it cannot be filtered around.
    deliveries = scope_queryset_by_team(
        deliveries, request.user, field="picker", plant_path=DELIVERY_PLANT_PATH,
    )

    query = request.GET.get("q", "").strip()
    if query:
        deliveries = deliveries.filter(
            Q(delivery_no__icontains=query)
            | Q(customer_code__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(docket_no__icontains=query)
            | Q(courier_number__icontains=query)
            | Q(plant__icontains=query)
        )

    entity_code = request.GET.get("entity", "").strip()
    if entity_code:
        deliveries = deliveries.filter(entity__code=entity_code)

    status = request.GET.get("status", "").strip()
    if status in dict(FinishedDelivery.STATUS_CHOICES):
        deliveries = deliveries.filter(status=status)

    plant = request.GET.get("plant", "").strip()
    if plant:
        deliveries = deliveries.filter(plant=plant)

    date_from = _parse_date(request.GET.get("from"))
    if date_from:
        deliveries = deliveries.filter(created_at__date__gte=date_from)
    date_to = _parse_date(request.GET.get("to"))
    if date_to:
        deliveries = deliveries.filter(created_at__date__lte=date_to)

    sort = request.GET.get("sort", "date_desc")
    if sort not in DELIVERY_SORTS:
        sort = "date_desc"

    context = {
        "q": query, "entity_code": entity_code, "status": status, "plant": plant,
        "date_from": request.GET.get("from", "").strip(),
        "date_to": request.GET.get("to", "").strip(),
        "sort": sort,
    }
    return deliveries.order_by(DELIVERY_SORTS[sort][0], "-id"), context


@login_required
@access_required
def delivery_history(request):
    """The legacy screen's three tabs: log, analytics and finished deliveries.

    One page rather than three, with the tab chosen client-side — every tab is a
    view of the same filtered set, so re-querying per tab would be wasted work
    and would let the tabs disagree with each other.
    """
    deliveries, filters = _filtered_deliveries(request)
    # How many serials each record burned. It drives the delete confirmation's
    # wording — "its 729 dispatched serials go back into circulation" is the
    # whole consequence of the button, and a count fetched per row would be one
    # query per row on a 50-row page.
    deliveries = deliveries.annotate(serial_count=Count("dispatched_serials", distinct=True))

    try:
        per_page = int(request.GET.get("per_page") or DELIVERY_PAGE_SIZES[0])
    except ValueError:
        per_page = DELIVERY_PAGE_SIZES[0]
    if per_page not in DELIVERY_PAGE_SIZES:
        per_page = DELIVERY_PAGE_SIZES[0]

    tab = request.GET.get("tab", "log")
    if tab not in ("log", "analytics", "finished"):
        tab = "log"

    # Computed only when the analytics tab is actually being viewed — it is
    # several aggregates and the other two tabs never read it.
    analytics = _delivery_analytics(deliveries) if tab == "analytics" else None

    pending_count = deliveries.filter(status=FinishedDelivery.STATUS_PENDING_COURIER).count()
    plants = sorted(
        # order_by() clears Meta.ordering ("-created_at") before DISTINCT.
        # Without it the ordering column joins the SELECT and every delivery
        # yields its own "distinct" row — 41 options for three real plants.
        value for value in deliveries.values_list("plant", flat=True).order_by().distinct() if value
    )

    return render(
        request,
        "merilv/delivery_history.html",
        _ctx(
            request,
            page_obj=paginate(request, deliveries, page_size=per_page),
            tab=tab,
            analytics=analytics,
            pending_count=pending_count,
            result_count=deliveries.count(),
            plants=plants,
            per_page=per_page,
            page_sizes=DELIVERY_PAGE_SIZES,
            sort_choices=[(key, label) for key, (_field, label) in DELIVERY_SORTS.items()],
            status_choices=FinishedDelivery.STATUS_CHOICES,
            **filters,
        ),
    )


@login_required
@operate_required
@require_POST
def delivery_delete(request, pk):
    """Remove a dispatch record, and release the serials it burned. Admins only.

    Two things had no answer before this. A finished delivery could not be
    removed at all — and deleting the *session* it came from is refused
    precisely because it would orphan this row rather than resolve it. So a
    delivery finished by mistake, or against a test upload, stayed in Delivery
    History for good.

    Worse, its serials stayed spent. ``DispatchedSerial.serial`` is globally
    unique and its ``finished_delivery`` is ``SET_NULL``, so the ledger row
    outlives the delivery record and keeps refusing that unit — which is
    correct while the stock really has shipped, and a dead end when it has not.
    Re-uploading the same dump and rescanning it answered DUPLICATE forever,
    with nothing anywhere able to clear it.

    So deleting a dispatch record means what it says: this dispatch did not
    happen. The serials go back into circulation with it. That is the only
    coherent reading — a ledger entry for a dispatch that has been declared
    never to have occurred is not protecting anything, it is just blocking the
    stock.

    Admin-only, and the reason is the same one that makes it necessary: it
    un-burns serials. The ActivityLog entry records the delivery, the paperwork
    and every serial released, because after this runs that is the only place
    they exist.
    """
    delivery = get_scoped_or_404(
        FinishedDelivery.objects.select_related("entity", "session"),
        request.user, field="picker", plant_path=DELIVERY_PLANT_PATH, pk=pk,
    )
    if not is_merilv_admin(request.user):
        message = "Only a Meril-V admin can remove a dispatch record."
        if _wants_json(request):
            return JsonResponse({"ok": False, "message": message}, status=403)
        messages.error(request, message)
        return redirect("merilv:delivery_detail", pk=delivery.pk)

    serials = list(
        DispatchedSerial.objects.filter(finished_delivery=delivery)
        .values_list("serial", flat=True)
    )
    audit = {
        "delivery_no": delivery.delivery_no,
        "entity": delivery.entity.code if delivery.entity_id else "",
        "customer": delivery.customer_name,
        "status": delivery.status,
        "docket_no": delivery.docket_no,
        "courier_partner": delivery.courier_partner,
        "box_count": delivery.box_count,
        "session": delivery.session_id,
        "released_count": len(serials),
        # Capped in the metadata blob — the count above is the fact that
        # matters, and 700 serials inline makes the activity row unreadable.
        "released": ", ".join(serials[:50]) + ("…" if len(serials) > 50 else ""),
    }
    label = delivery.delivery_no

    with transaction.atomic():
        DispatchedSerial.objects.filter(finished_delivery=delivery).delete()
        delivery.delete()

    _log(request, "delivery_delete", "FinishedDelivery", pk, **audit)
    note = (
        f"Dispatch record for {label} removed"
        + (f" — {len(serials):,} serial(s) released for re-picking." if serials
           else ". It had no serials assigned.")
    )
    if _wants_json(request):
        return JsonResponse({"ok": True, "message": note, "released": len(serials)})
    messages.success(request, note)
    return redirect("merilv:delivery_history")


@login_required
@access_required
def delivery_detail(request, pk):
    delivery = get_scoped_or_404(
        FinishedDelivery.objects.select_related("entity", "picker", "session"),
        request.user, field="picker", plant_path=DELIVERY_PLANT_PATH, pk=pk,
    )
    scans = []
    if delivery.session:
        scans = delivery.session.scans.select_related("line").filter(
            line__delivery_no=delivery.delivery_no
        )
    return render(
        request, "merilv/delivery_detail.html", _ctx(request, delivery=delivery, scans=scans)
    )


#: Windows the Reports screen offers. Anything else is coerced to the default
#: rather than trusted: the <select> only ever submits one of these, but a
#: hand-typed ?days= reached the view unchecked.
REPORT_WINDOWS = (7, 30, 90)
REPORT_WINDOW_DEFAULT = 30
REPORT_WINDOW_MAX = 3650


def _report_window_days(raw):
    """Days for the reports window, from an untrusted query parameter.

    ``int(request.GET.get("days") or 30)`` was a 500 waiting to happen —
    ``?days=abc`` raised ``ValueError`` and ``?days=999999999`` overflowed
    ``timedelta``. A negative value was worse than a crash: it put the window
    in the future, so every KPI silently rendered zero and the screen looked
    like an empty warehouse rather than a bad request.
    """
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return REPORT_WINDOW_DEFAULT
    if days < 1:
        return REPORT_WINDOW_DEFAULT
    return min(days, REPORT_WINDOW_MAX)


@login_required
@access_required
def reports(request):
    """Operational KPIs: throughput, top materials, picker activity."""
    days = _report_window_days(request.GET.get("days"))
    since = timezone.now() - timedelta(days=days)

    # Scoped, which also makes "top pickers" mean the caller's own team rather
    # than a company-wide leaderboard a site supervisor has no business seeing.
    finished = scope_queryset_by_team(
        FinishedDelivery.objects.filter(created_at__gte=since),
        request.user, field="picker", plant_path=DELIVERY_PLANT_PATH,
    )
    scans = scope_queryset_by_team(
        OutboundScan.objects.filter(created_at__gte=since),
        request.user, field="session__operator",
    )

    by_day = (
        finished.values("created_at__date")
        .annotate(n=Count("id"))
        .order_by("created_at__date")
    )
    top_materials = (
        scans.values("material").annotate(n=Count("id")).order_by("-n")[:10]
    )
    top_pickers = (
        finished.exclude(picker=None)
        .values("picker__username", "picker__first_name", "picker__last_name")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    by_entity = finished.values("entity__code").annotate(n=Count("id")).order_by("-n")

    summary = {
        "deliveries": finished.count(),
        "scans": scans.count(),
        "partial": finished.filter(is_partial=True).count(),
        "boxes": finished.aggregate(total=Sum("box_count"))["total"] or 0,
    }

    by_day_rows = list(by_day)
    top_materials_rows = list(top_materials)
    top_pickers_rows = list(top_pickers)
    by_entity_rows = list(by_entity)

    def _picker_label(row):
        name = f"{row['picker__first_name']} {row['picker__last_name']}".strip()
        return name or row["picker__username"]

    # ── Chart payload ────────────────────────────────────────────────────
    # by_day only contains dates that actually have deliveries, so a window
    # with one busy day used to render as a single full-width bar — a trend
    # line that cannot show a trend. Zero-filling the whole window is what
    # makes the quiet days visible, which is the point of a throughput chart.
    counts_by_date = {row["created_at__date"]: row["n"] for row in by_day_rows}
    today = timezone.localdate()
    span = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    charts = {
        "has_data": bool(summary["deliveries"] or summary["scans"]),
        "trend": {
            "labels": [day.strftime("%d %b") for day in span],
            "values": [counts_by_date.get(day, 0) for day in span],
        },
        "materials": {
            "labels": [row["material"] or "—" for row in top_materials_rows],
            "values": [row["n"] for row in top_materials_rows],
        },
        "pickers": {
            "labels": [_picker_label(row) for row in top_pickers_rows],
            "values": [row["n"] for row in top_pickers_rows],
        },
        "entities": [
            {"name": row["entity__code"] or "—", "y": row["n"]} for row in by_entity_rows
        ],
    }

    return render(
        request,
        "merilv/reports.html",
        _ctx(
            request,
            days=days,
            summary=summary,
            by_day=by_day_rows,
            top_materials=top_materials_rows,
            top_pickers=top_pickers_rows,
            by_entity=by_entity_rows,
            charts=charts,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reports → User Activity
#
# Admin-only, and unscoped by design: this is the one screen whose whole point
# is cross-team visibility, so `scope_queryset_by_team` is deliberately absent.
# `admin_required` (is_merilv_admin, which is True for superusers) is what keeps
# it out of a manager's or picker's reach — a picker could otherwise read their
# colleagues' throughput off it, which the row-level scoping everywhere else in
# this module exists to prevent.
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@admin_required
def user_activity(request):
    """All-users dashboard, or a redirect into one user's own drill-down.

    The ``user`` parameter is the picker at the top of the screen. It redirects
    rather than branching in place so the individual view has its own URL —
    shareable, bookmarkable, and back-button-correct, which a query-string
    branch on the same page would not be.
    """
    days = useractivity.resolve_window(request.GET.get("days"))
    picked = int_or_none(request.GET.get("user"))
    if picked:
        return redirect(
            f"{reverse('merilv:user_activity_detail', args=[picked])}?days={days}"
        )

    data = useractivity.leaderboard(days)
    return render(
        request,
        "merilv/reports_users.html",
        _ctx(
            request,
            days=days,
            windows=useractivity.WINDOW_CHOICES,
            kind_labels=useractivity.KIND_LABELS,
            # `charts` rides through in **data and the template hands it to
            # `json_script`, which escapes </script> and friends. Serialising it
            # here with json.dumps and printing it raw would not.
            **data,
        ),
    )


@login_required
@admin_required
def user_activity_detail(request, pk):
    """One user's end-to-end flow: what, how much, when, and over what span."""
    from django.core.paginator import Paginator

    days = useractivity.resolve_window(request.GET.get("days"))
    subject = get_object_or_404(User, pk=pk)
    data = useractivity.user_detail(subject, days)

    # The feed is paged rather than rendered whole: 250 rows under the charts
    # made the page several screens long with no way to reach the end of it.
    # Paged in Python because the list is already merged from fifteen sources
    # and capped, so there is no query to slice — and no extra query to pay.
    feed = Paginator(data.pop("events"), useractivity.FEED_PAGE_SIZE)
    # get_page, not page(): it clamps a junk or out-of-range ?events_page to a
    # real page instead of raising, so a stale bookmark or a hand-edited URL
    # renders the feed rather than a 404 on the whole drill-down.
    events_page = feed.get_page(request.GET.get("events_page"))

    return render(
        request,
        "merilv/reports_user_detail.html",
        _ctx(
            request,
            subject=subject,
            days=days,
            windows=useractivity.WINDOW_CHOICES,
            events=events_page.object_list,
            events_page=events_page,
            events_total=feed.count,
            # `data["picker"]` carries every user so the dropdown can jump
            # sideways without going back to the roster first. Built inside
            # user_detail so it reuses the totals the peer ranking already
            # fetched, rather than re-running them here.
            **data,
        ),
    )


@login_required
@admin_required
def user_activity_export(request):
    """The leaderboard as a workbook — one row per user, one column per source.

    Columns mirror the screen plus the full per-source breakdown, which the
    table only shows as a total: an admin auditing "who did what" needs the
    split, and a spreadsheet has room for fifteen columns where a table does
    not.
    """
    days = useractivity.resolve_window(request.GET.get("days"))
    data = useractivity.leaderboard(days)

    headers = [
        "User", "Username", "Email", "Role", "Plants", "Manager", "Account Active",
        "Total Actions", "Active Days", "Actions / Active Day", "Actions / Hour",
        "First Action", "Last Action", "Active Window", "Session Time",
        "Busiest Day", "Busiest Day Actions", "Last Login",
    ] + [source.label for source in useractivity.ACTIVITY_SOURCES]

    rows = []
    for row in data["rows"]:
        rows.append([
            row["name"], row["username"], row["email"], row["role"] or "—",
            ", ".join(row["plants"]) or "All", row["manager"] or "—",
            "Yes" if row["is_active"] else "No",
            row["total"], row["active_days"], row["per_active_day"], row["actions_per_hour"],
            # openpyxl cannot write a tz-aware datetime, and this is read in one
            # timezone — same call the activity-log export makes.
            timezone.localtime(row["first_seen"]).replace(tzinfo=None) if row["first_seen"] else "",
            timezone.localtime(row["last_seen"]).replace(tzinfo=None) if row["last_seen"] else "",
            useractivity.humanise_seconds(row["active_seconds"]),
            useractivity.humanise_seconds(row["session_seconds"]),
            row["busiest_day"] or "", row["busiest_day_count"],
            timezone.localtime(row["last_login"]).replace(tzinfo=None) if row["last_login"] else "",
        ] + [row["by_source"].get(source.key, 0) for source in useractivity.ACTIVITY_SOURCES])

    buffer = export.export_generic("User Activity", headers, rows)
    return _xlsx_response(buffer, f"merilv_user_activity_{days}d.xlsx")


# ─────────────────────────────────────────────────────────────────────────────
# Barcode extractor
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@access_required
def extractor(request):
    entity = _selected_entity(request)
    sessions = scope_queryset_by_team(
        ScanSession.objects.select_related("entity"), request.user, field="created_by",
    ).order_by("-created_at")[:15]
    return render(request, "merilv/extractor.html", _ctx(request, entity=entity, sessions=sessions))


def _extractor_rows(request):
    """Parse the extract form's pasted text and/or uploaded file into rows.

    Shared by :func:`extractor_parse`, which returns them as JSON for the
    on-screen table, and :func:`extractor_export`, which writes the same rows
    to a workbook. One parse path means a download can never disagree with the
    table the user is looking at when they click Export.

    Returns ``(rows, error)``; ``error`` is a message fit to show a user, and
    ``rows`` is None whenever it is set.
    """
    entity = _selected_entity(request)
    if entity is None:
        return None, "No active entity configured."

    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    raw_text = request.POST.get("barcodes") or ""
    codes = extractor_service.split_pasted(raw_text)
    column_rows = []

    upload = request.FILES.get("file")
    if upload:
        try:
            result = ingest.parse_scan_file(upload, upload.name, use_scan_qty=False)
            for record in result.records:
                if record["raw"]:
                    codes.append(record["raw"])
                elif record["material"] or record["batch"] or record["serial"]:
                    column_rows.append(record)
        except ingest.IngestError as exc:
            return None, str(exc)

    if not codes and not column_rows:
        return None, "Paste some barcodes or upload a file."

    # The entity's own thresholds decide the expiry band — MHPL and MLSIPL
    # invert "near" and "short", so a shared default would mislabel one of them.
    val_settings = entity_service.get_validation_settings(entity)
    rows = extractor_service.extract_many(codes, profile, material_master, rules, val_settings)
    for record in column_rows:
        rows.append(
            extractor_service.ExtractedRow(
                sequence=len(rows),
                raw=record["raw"],
                material=record["material"],
                scan_code=record["material"],
                batch=record["batch"],
                serial=record["serial"],
                description=record["description"],
                is_valid=bool(record["material"] or record["serial"]),
                strategy="columns",
            )
        )
    return rows, None


@login_required
@operate_required
@require_POST
def extractor_parse(request):
    """Parse a pasted block or uploaded column of barcodes."""
    rows, error = _extractor_rows(request)
    if error:
        return JsonResponse({"ok": False, "message": error}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "count": len(rows),
            "valid": sum(1 for row in rows if row.is_valid),
            "rows": [row.__dict__ for row in rows],
        }
    )


@login_required
@operate_required
@require_POST
def extractor_export(request):
    """Download the extract-mode results as a formatted workbook.

    Extract mode deliberately creates no session — it is the "paste a pile of
    barcodes and see what they say" tool — which also meant the parsed rows
    existed only in the browser and there was no way to get them out. This
    re-parses the same submitted input rather than accepting rows back from the
    page, so the download is produced by the same extraction path that drew the
    table and cannot be edited in transit.

    A native form POST rather than fetch, so the browser handles the download
    and the filename in Content-Disposition; failures therefore redirect back
    to the screen with a message instead of returning JSON nobody would see.
    """
    rows, error = _extractor_rows(request)
    if error:
        messages.error(request, error)
        return redirect("merilv:extractor")

    # "Expiry Status" carries the on-screen highlight into the workbook. A
    # colour cannot survive a copy-paste into another sheet; a word can.
    headers = [
        "#", "Raw Scan", "Material", "Scan Code", "Batch", "Serial",
        "MFG", "Expiry", "Expiry Status", "Description",
        # Row 17 — always exported, unlike on screen where they are behind the
        # column toggle. A workbook has no width limit, and the reason the
        # client wants these fields is to pivot on them.
        "Material Group", "PH Level",
        "GTIN", "Strategy", "Valid",
    ]
    body = [
        [
            index,
            row.raw, row.material, row.scan_code, row.batch, row.serial,
            row.mfg, row.expiry, (row.expiry_state or "").replace("_", " ").title(),
            row.description,
            row.material_group, row.ph_level,
            row.gtin, row.strategy,
            "Yes" if row.is_valid else "No",
        ]
        for index, row in enumerate(rows, start=1)
    ]
    buffer = export.export_generic("Barcode Extract", headers, body)
    return _xlsx_response(buffer, export.filename_for("extractor"))


@login_required
@operate_required
@require_POST
def scan_session_create(request):
    """Open a live-scan or live-validate session."""
    entity = _selected_entity(request)
    mode = request.POST.get("mode") or ScanSession.MODE_LIVE_SCAN
    if entity is None:
        messages.error(request, "No active entity configured.")
        return redirect("merilv:extractor")

    session = ScanSession.objects.create(
        entity=entity,
        mode=mode,
        name=(request.POST.get("name") or "").strip(),
        condition=(request.POST.get("condition") or "").strip(),
        box_no=(request.POST.get("box_no") or "").strip(),
        dump_file=request.FILES.get("dump_file"),
        created_by=request.user,
    )
    # Parsed once, here, rather than on every scan for the life of the session.
    if session.dump_file:
        _store_dump_snapshot(session)
    return redirect("merilv:scan_session", pk=session.pk)


@login_required
@access_required
def scan_session(request, pk):
    session = get_scoped_or_404(
        ScanSession.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    records = list(session.records.all()[:500])
    # Row 3.9 — band each stored expiry so the feed can highlight it. Computed
    # here rather than in the template because the thresholds belong to the
    # entity and classify_stock needs a parsed date, neither of which a
    # template filter should be reaching for.
    val_settings = entity_service.get_validation_settings(session.entity)
    for record in records:
        record.expiry_state = validation.classify_stock(
            record.condition,
            extractor_service.expiry_to_date(record.expiry_code),
            val_settings,
        )
    box_count = session.records.filter(box_no=session.box_no).count() if session.box_no else 0
    return render(
        request,
        "merilv/scan_session.html",
        _ctx(request, session=session, entity=session.entity, records=records, box_count=box_count),
    )


#: The only fields LiveValidateState.from_records reads. Storing just these
#: keeps the snapshot small — a dump's other twenty columns are audit detail
#: that live-validate never consults.
_DUMP_SNAPSHOT_FIELDS = ("material", "batch", "serial", "description", "qty")


def _parse_dump_rows(session):
    """Parse the session's dump file down to the snapshot fields, or ``[]``."""
    if not session.dump_file:
        return []
    try:
        session.dump_file.open("rb")
        result = ingest.parse_system_dump(session.dump_file, session.dump_file.name)
    except (ingest.IngestError, OSError, ValueError):
        return []
    else:
        return [
            {field: record.get(field) for field in _DUMP_SNAPSHOT_FIELDS}
            for record in result.records
        ]
    finally:
        session.dump_file.close()


def _store_dump_snapshot(session):
    """Parse the dump once and persist the reduced rows on the session."""
    session.dump_snapshot = _parse_dump_rows(session)
    session.save(update_fields=["dump_snapshot", "updated_at"])
    return session.dump_snapshot


def _live_validate_state(session):
    """Rebuild live-validate expectations from the uploaded dump plus history.

    The *history* half is still derived per call, deliberately, so a reloaded
    page, a second device or a resumed session all agree on what is left to
    count. The *dump* half is not: it cannot change once uploaded, so it is
    parsed once into ``dump_snapshot`` and read back from there. Re-parsing the
    spreadsheet on every scan was the single most expensive thing in the gun
    path.
    """
    state = extractor_service.LiveValidateState()
    rows = session.dump_snapshot
    if not rows and session.dump_file:
        # A session created before dump_snapshot existed — backfill it on first
        # use so this costs one parse in total, not one per scan.
        rows = _store_dump_snapshot(session)
    if rows:
        state = extractor_service.LiveValidateState.from_records(rows)

    for record in session.records.all():
        key = f"{record.material}|{record.batch}"
        entry = state.stock.get(key)
        if entry is not None and record.result == extractor_service.MATCH:
            entry.scanned += 1
            serial = validation.clean(record.serial)
            if entry.serials and not is_non_serial(serial):
                entry.scanned_serials[serial] += 1
        # Only a real serial is ever a duplicate — a bare truthiness check
        # lets the "Unknown" non-serial placeholder through and wrongly seeds
        # every repeat scan of the same batch as already-seen (Row 3.2).
        if not is_non_serial(record.serial):
            state.seen.add(
                f"{validation.clean(record.material)}|{validation.clean(record.batch)}|{validation.clean(record.serial)}"
            )
    state.total = session.total_count
    state.matched = session.found_count
    state.duplicates = session.duplicate_count
    state.not_in_dump = session.not_found_count
    return state


@login_required
@operate_required
@require_POST
def scan_session_scan(request, pk):
    """Record one scan into a live-scan or live-validate session."""
    session = get_scoped_or_404(
        ScanSession.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    raw_scan = (request.POST.get("raw_scan") or "").strip()
    if not raw_scan:
        return JsonResponse({"ok": False, "message": "Nothing scanned."}, status=400)
    if session.is_finished:
        return JsonResponse({"ok": False, "message": "This session is closed."}, status=409)

    # Offline-queue replay guard. The client generates client_ref before its
    # first attempt and reuses it when a scan has to be queued, so a scan the
    # server accepted but whose response never made it back arrives again with
    # the same ref. Answer it as already-recorded rather than counting a second
    # unit — checked before anything increments. Mirrors outbound_session_scan.
    client_ref = (request.POST.get("client_ref") or "").strip()[:64]
    if client_ref:
        already = session.records.filter(client_ref=client_ref).first()
        if already is not None:
            return JsonResponse({
                "ok": True,
                "replayed": True,
                "result": already.result,
                "message": "Already recorded — not counted twice.",
                "record_id": already.pk,
                "condition": already.condition,
                "box_no": already.box_no,
                "row": already.parsed or {},
                "counters": {
                    "total": session.total_count,
                    "found": session.found_count,
                    "not_found": session.not_found_count,
                    "duplicates": session.duplicate_count,
                },
                "box_count": session.records.filter(box_no=already.box_no).count() if already.box_no else None,
            })

    entity = session.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)
    val_settings = entity_service.get_validation_settings(entity)

    # The whole read-decide-write cycle is inside one locked transaction, the
    # same shape as outbound_scan. The counters below are written back as
    # *absolute* values, so reading them outside the lock let two guns on one
    # session (a supported case — see _live_validate_state's note on a second
    # device) both read total=10 and both write 11: one scan silently vanishes
    # from the counters while both ScanRecord rows exist.
    with transaction.atomic():
        session = (
            ScanSession.objects.select_for_update().select_related("entity").get(pk=session.pk)
        )
        if session.is_finished:
            return JsonResponse({"ok": False, "message": "This session is closed."}, status=409)

        if session.mode == ScanSession.MODE_LIVE_VALIDATE:
            state = _live_validate_state(session)
            result = extractor_service.live_validate(
                raw_scan, profile, state, material_master, rules, val_settings
            )
            counters = state.counters()
        else:
            state = extractor_service.LiveScanState(
                seen={
                    f"{validation.clean(r.material)}|{validation.clean(r.batch)}|{validation.clean(r.serial)}"
                    for r in session.records.all()
                    if not is_non_serial(r.serial)
                },
                found=session.found_count,
                not_found=session.not_found_count,
                duplicates=session.duplicate_count,
                total=session.total_count,
            )
            result = extractor_service.live_scan(
                raw_scan, profile, state, material_master, rules, val_settings
            )
            counters = state.counters()

        # The operator can switch boxes or flip Good/Damage mid-session without
        # opening a new one (Row 3.4 / 3.5) — whatever is posted becomes the
        # session's new default for every scan after this one, mirroring how the
        # legacy tool's box/condition selectors worked.
        box_no = (request.POST.get("box_no") or session.box_no or "").strip()
        condition = (request.POST.get("condition") or session.condition or "").strip()
        session_fields = []
        if box_no != session.box_no:
            session.box_no = box_no
            session_fields.append("box_no")
        if condition != session.condition:
            session.condition = condition
            session_fields.append("condition")

        record = ScanRecord.objects.create(
            session=session,
            raw_scan=raw_scan,
            material=result.parsed.material,
            scan_code=result.parsed.scan_code or result.parsed.material,
            batch=result.parsed.batch,
            serial=result.parsed.serial,
            mfg_code=result.parsed.mfg,
            expiry_code=result.parsed.expiry,
            description=result.row.description,
            result=result.result,
            comment=result.message,
            condition=condition,
            box_no=box_no,
            parsed=result.parsed.as_dict(),
            client_ref=client_ref,
        )
        session.total_count = int(counters.get("total", session.total_count))
        session.found_count = int(counters.get("found", counters.get("matched", session.found_count)))
        session.not_found_count = int(
            counters.get("not_found", counters.get("not_in_dump", session.not_found_count))
        )
        session.duplicate_count = int(counters.get("duplicates", session.duplicate_count))
        session.save(
            update_fields=[
                "total_count", "found_count", "not_found_count", "duplicate_count", "updated_at",
                *session_fields,
            ]
        )

    box_count = session.records.filter(box_no=box_no).count() if box_no else None

    return JsonResponse(
        {
            "ok": True,
            "result": result.result,
            # Batch-wrong and serial-wrong are both MISMATCH, and the console
            # gave them one identical red row. They need different actions, so
            # the kind travels with the verdict for the UI to colour on.
            "mismatch_kind": getattr(result, "mismatch_kind", ""),
            "message": result.message,
            "audio_cue": result.audio_cue,
            "row": result.row.__dict__,
            "counters": counters,
            "record_id": record.pk,
            "condition": condition,
            "box_no": box_no,
            "box_count": box_count,
        }
    )


@login_required
@operate_required
@require_POST
def scan_session_record_condition(request, pk, record_pk):
    """Correct one scan's Good/Damage condition after the fact (Row 3.5).

    Independent of the session-wide toggle in ``scan_session_scan`` — a picker
    who scanned a carton as Good and only later noticed damage on it should
    not have to rescan, and should not have to flip every future scan's
    default to fix this one past mistake.
    """
    get_scoped_or_404(ScanSession, request.user, field="created_by", pk=pk)
    record = get_object_or_404(
        ScanRecord.objects.select_related("session"), pk=record_pk, session_id=pk,
    )
    if record.session.is_finished:
        return JsonResponse({"ok": False, "message": "This session is finished and read-only."}, status=409)
    condition = (request.POST.get("condition") or "").strip()
    if not condition:
        return JsonResponse({"ok": False, "message": "Choose a condition."}, status=400)
    record.condition = condition
    record.save(update_fields=["condition"])
    _log(request, "scan_record_condition", "ScanRecord", record.pk, condition=condition)
    return JsonResponse({"ok": True, "record_id": record.pk, "condition": condition})


@login_required
@operate_required
@require_POST
def scan_session_finish(request, pk):
    session = get_scoped_or_404(ScanSession, request.user, field="created_by", pk=pk)
    session.is_finished = True
    name = (request.POST.get("name") or "").strip()
    if name:
        session.name = name
    session.save(update_fields=["is_finished", "name", "updated_at"])
    _log(request, "scan_session_finish", "ScanSession", session.pk)
    messages.success(request, "Session saved.")
    return redirect("merilv:scan_history")


@login_required
@operate_required
def scan_session_columns(request, pk):
    """Review and override how a Live Validate dump's columns were detected.

    Automatic detection covers the usual SAP exports, but a one-off report with
    unusual headers would otherwise validate against nothing and report every
    scan as "not in dump". Letting the operator correct the mapping is quicker
    than sending them back to SAP for a re-export.
    """
    session = get_scoped_or_404(
        ScanSession.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    if not session.dump_file:
        messages.error(request, "This session has no dump file to map.")
        return redirect("merilv:scan_session", pk=session.pk)

    headers: list[str] = []
    try:
        session.dump_file.open("rb")
        grid = ingest.read_grid(session.dump_file, session.dump_file.name)
        index = ingest.find_header_row(grid, [("material", "item")]) if grid else -1
        if index >= 0:
            headers = [str(cell or "").strip() for cell in grid[index] if str(cell or "").strip()]
    except (ingest.IngestError, OSError, ValueError) as exc:
        messages.error(request, f"Could not read the dump file: {exc}")
        return redirect("merilv:scan_session", pk=session.pk)
    finally:
        session.dump_file.close()

    fields = [
        {"key": "material", "label": "Material", "required": True},
        {"key": "batch", "label": "Batch", "required": False},
        {"key": "qty", "label": "Quantity", "required": True},
        {"key": "description", "label": "Description", "required": False},
        {"key": "plant", "label": "Plant", "required": False},
        {"key": "location", "label": "Storage location", "required": False},
    ]

    if request.method == "POST":
        mapping = {}
        missing = []
        for field in fields:
            value = (request.POST.get(field["key"]) or "").strip()
            if value:
                mapping[field["key"]] = value
            elif field["required"]:
                missing.append(field["label"])
        if missing:
            messages.error(request, "These columns must be mapped: " + ", ".join(missing) + ".")
        else:
            session.column_map = mapping
            session.save(update_fields=["column_map", "updated_at"])
            _log(request, "scan_session_column_map", "ScanSession", session.pk, mapping=mapping)
            messages.success(request, "Column mapping saved.")
            return redirect("merilv:scan_session", pk=session.pk)

    return render(
        request,
        "merilv/scan_session_columns.html",
        _ctx(
            request,
            session=session,
            entity=session.entity,
            headers=headers,
            fields=fields,
            mapping=session.column_map or {},
        ),
    )


@login_required
@access_required
def scan_session_export(request, pk):
    session = get_scoped_or_404(
        ScanSession.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    headers = [
        "Raw Scan", "Material", "Scan Code", "Batch", "Serial", "MFG", "Expiry", "Description",
        "Condition", "Box", "Result", "Comment", "Scanned At",
    ]
    rows = [
        [
            record.raw_scan, record.material, record.scan_code, record.batch, record.serial,
            record.mfg_code, record.expiry_code, record.description,
            record.condition, record.box_no,
            record.get_result_display(), record.comment, record.created_at,
        ]
        for record in session.records.all()
    ]
    buffer = export.export_generic(str(session), headers, rows)
    return _xlsx_response(buffer, export.filename_for("scan_session", session.pk))


#: Whitelisted — an arbitrary ``sort`` value must never reach order_by() directly.
SCAN_HISTORY_SORTS = {
    "newest": "-created_at",
    "oldest": "created_at",
    "name": "name",
    "found": "-found_count",
}


def _filtered_scan_sessions(request):
    """One shared queryset builder for the history table and its export.

    Kept in one place so the two can never disagree about what "the current
    filtered list" is — the same lesson Row 9's Delivery History was built on.
    """
    sessions = scope_queryset_by_team(
        ScanSession.objects.select_related("entity", "created_by"),
        request.user, field="created_by",
    )
    mode = (request.GET.get("mode") or "").strip()
    if mode:
        sessions = sessions.filter(mode=mode)

    query = (request.GET.get("q") or "").strip()
    if query:
        sessions = sessions.filter(
            Q(name__icontains=query) | Q(entity__code__icontains=query) | Q(entity__name__icontains=query)
        )

    status = (request.GET.get("status") or "").strip()
    if status == "finished":
        sessions = sessions.filter(is_finished=True)
    elif status == "open":
        sessions = sessions.filter(is_finished=False)

    sort = request.GET.get("sort", "newest")
    if sort not in SCAN_HISTORY_SORTS:
        sort = "newest"
    sessions = sessions.order_by(SCAN_HISTORY_SORTS[sort])

    return sessions, {"mode": mode, "q": query, "status": status, "sort": sort}


@login_required
@access_required
def scan_history(request):
    """Row 3.13 — dropdown-driven filtering + sorting over every extractor session."""
    sessions, filters = _filtered_scan_sessions(request)
    return render(
        request,
        "merilv/scan_history.html",
        _ctx(
            request,
            page_obj=paginate(request, sessions),
            mode_choices=ScanSession.MODE_CHOICES,
            **filters,
        ),
    )


@login_required
@access_required
def scan_history_export(request):
    """Row 3.11 — export the (filtered) session list itself, not one session's scans."""
    sessions, _filters = _filtered_scan_sessions(request)
    headers = ["Session", "Mode", "Entity", "Operator", "Found", "Total", "Duplicates", "Not Found", "Status", "Created"]
    rows = [
        [
            str(s), s.get_mode_display(), s.entity.code,
            s.created_by.get_full_name() if s.created_by else "",
            s.found_count, s.total_count, s.duplicate_count, s.not_found_count,
            "Finished" if s.is_finished else "Open", s.created_at,
        ]
        for s in sessions
    ]
    buffer = export.export_generic("Scan History", headers, rows)
    return _xlsx_response(buffer, export.filename_for("history", "scan_sessions"))


# ─────────────────────────────────────────────────────────────────────────────
# Branch transfer
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@access_required
def branch_transfer(request):
    entity = _selected_entity(request)
    plans = scope_queryset_by_team(
        BranchTransferPlan.objects.select_related("entity"), request.user, field="created_by",
    ).order_by("-created_at")[:20]

    if request.method == "POST" and can_operate_merilv(request.user):
        plan_file = request.FILES.get("plan_file")
        if not plan_file or entity is None:
            messages.error(request, "Select an entity and upload a BT plan.")
            return redirect("merilv:branch_transfer")
        try:
            result = ingest.parse_bt_plan(plan_file, plan_file.name)
        except ingest.IngestError as exc:
            messages.error(request, str(exc))
            return redirect("merilv:branch_transfer")

        with transaction.atomic():
            plan = BranchTransferPlan.objects.create(
                entity=entity,
                name=(request.POST.get("name") or plan_file.name).strip(),
                plan_file=plan_file,
                stock_file=request.FILES.get("stock_file"),
                created_by=request.user,
            )
            BranchTransferLine.objects.bulk_create(
                [BranchTransferLine(plan=plan, **record) for record in result.records],
                batch_size=1000,
            )
        _log(request, "bt_plan_upload", "BranchTransferPlan", plan.pk, lines=len(result.records))
        for warning in result.warnings:
            messages.warning(request, warning)
        messages.success(request, f"Loaded {len(result.records):,} transfer lines.")
        return redirect("merilv:branch_transfer_plan", pk=plan.pk)

    return render(
        request, "merilv/branch_transfer.html", _ctx(request, entity=entity, plans=plans)
    )


def _bt_pool(plan):
    """Load the plan's MB52 stock dump into a :class:`StockPool`, or ``None``.

    Shared by the scan endpoint and the auto-assign endpoint so they never
    disagree about what stock is available.
    """
    if not plan.stock_file:
        return None
    try:
        plan.stock_file.open("rb")
        stock = ingest.parse_system_dump(plan.stock_file, plan.stock_file.name)
        return transfers.StockPool.from_records(
            stock.records, location_priority=entity_service.get_bt_location_priority(plan.entity)
        )
    except (ingest.IngestError, OSError, ValueError):
        return None
    finally:
        plan.stock_file.close()


def _bt_plant_summary(lines):
    """Plant → colour-zone dispatch summary — the legacy "Plan Summary" table.

    Ported from ``bt-scanner.js:653-790`` (``renderBTSummaryTable``): group by
    receiving plant, then by ``intransit_color_zone``, showing plan/dispatched/
    pending/percent at both levels plus a grand total.
    """
    plants: dict[str, dict[str, dict[str, float]]] = {}
    for line in lines:
        plant = line.receiving_node_code or "—"
        zone = (line.intransit_color_zone or "UNSPECIFIED").upper()
        bucket = plants.setdefault(plant, {}).setdefault(zone, {"required": 0.0, "picked": 0.0})
        bucket["required"] += float(line.quantity)
        bucket["picked"] += float(line.picked_qty)

    summary = []
    grand_required = grand_picked = 0.0
    for plant in sorted(plants):
        zones = []
        plant_required = plant_picked = 0.0
        for zone in sorted(plants[plant]):
            bucket = plants[plant][zone]
            required, picked = bucket["required"], bucket["picked"]
            plant_required += required
            plant_picked += picked
            zones.append({
                "zone": zone,
                "required": required,
                "picked": picked,
                "pending": max(required - picked, 0),
                "percent": round(min(picked / required, 1) * 100, 1) if required else 0,
            })
        grand_required += plant_required
        grand_picked += plant_picked
        summary.append({
            "plant": plant,
            "required": plant_required,
            "picked": plant_picked,
            "pending": max(plant_required - plant_picked, 0),
            "percent": round(min(plant_picked / plant_required, 1) * 100, 1) if plant_required else 0,
            "zones": zones,
        })

    return {
        "plants": summary,
        "grand_total": {
            "required": grand_required,
            "picked": grand_picked,
            "pending": max(grand_required - grand_picked, 0),
            "percent": round(min(grand_picked / grand_required, 1) * 100, 1) if grand_required else 0,
        },
    }


def _bt_state_payload(plan):
    """Everything the Branch Transfer console needs to redraw itself."""
    lines = list(plan.lines.all())
    required = sum(float(line.quantity) for line in lines)
    picked = sum(float(line.picked_qty) for line in lines)
    return {
        "lines": [
            {
                "id": line.pk,
                "sku_code": line.sku_code,
                "supply_node_code": line.supply_node_code,
                "receiving_node_code": line.receiving_node_code,
                "storage_location": line.storage_location,
                "required": float(line.quantity),
                "picked": float(line.picked_qty),
                "pending": float(line.pending_qty),
                "assigned_batch": line.assigned_batch,
                "batch_overridden": line.batch_overridden,
                "locked": float(line.picked_qty) > 0,
            }
            for line in lines
        ],
        "totals": {
            "required": required,
            "picked": picked,
            "pending": max(required - picked, 0),
            "percent": round(min(picked / required, 1) * 100, 1) if required else 0,
        },
        "summary": _bt_plant_summary(lines),
    }


@login_required
@access_required
def branch_transfer_plan(request, pk):
    plan = get_scoped_or_404(
        BranchTransferPlan.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    lines = plan.lines.all()
    state = _bt_state_payload(plan)
    return render(
        request,
        "merilv/branch_transfer_plan.html",
        _ctx(request, plan=plan, entity=plan.entity, lines=lines, totals=state["totals"], summary=state["summary"]),
    )


@login_required
@access_required
def branch_transfer_state(request, pk):
    plan = get_scoped_or_404(BranchTransferPlan, request.user, field="created_by", pk=pk)
    return JsonResponse(_bt_state_payload(plan))


@login_required
@operate_required
@require_POST
def branch_transfer_scan(request, pk):
    """Allocate one scan against the transfer plan."""
    plan = get_scoped_or_404(
        BranchTransferPlan.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    raw_scan = (request.POST.get("raw_scan") or "").strip()
    if not raw_scan:
        return JsonResponse({"accepted": False, "message": "Nothing scanned."}, status=400)
    if plan.is_finished:
        return JsonResponse({"accepted": False, "message": "This transfer plan is closed."}, status=409)

    entity = plan.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)
    pool = _bt_pool(plan)

    with transaction.atomic():
        lines = list(plan.lines.select_for_update())
        outcome = transfers.process_transfer_scan(
            raw_scan,
            profile,
            lines,
            pool,
            material_master=material_master,
            positional_rules=rules,
        )
        if outcome.accepted:
            material = outcome.parsed.material
            target = next(
                (line for line in lines if line.sku_code.upper() == material.upper()
                 and float(line.picked_qty) < float(line.quantity)),
                None,
            )
            if target is not None:
                BranchTransferLine.objects.filter(pk=target.pk).update(picked_qty=F("picked_qty") + 1)

    plan.refresh_from_db()
    payload = {
        "accepted": outcome.accepted,
        "reason_code": outcome.reason_code,
        "message": outcome.message,
        "audio_cue": outcome.audio_cue,
        "batch": outcome.batch,
        "location": outcome.location,
        "state": _bt_state_payload(plan),
    }
    return JsonResponse(payload)


def _bt_codes_from_request(request):
    """Collect scan strings from paste text and/or a scan workbook."""
    codes = [
        c for c in extractor_service.split_pasted(_strip_bulk_comments(request.POST.get("raw_text") or ""))
        if len(c) > BULK_SCAN_MIN_LENGTH
    ]
    scan_file = request.FILES.get("scan_file")
    if scan_file:
        result = ingest.parse_scan_file(scan_file, scan_file.name, use_scan_qty=False)
        for record in result.records:
            raw = str(record.get("raw") or "").strip()
            if not raw:
                material = str(record.get("material") or "").strip()
                batch = str(record.get("batch") or "").strip()
                serial = str(record.get("serial") or "").strip()
                if material:
                    raw = f"(240){material}"
                    if batch:
                        raw += f"(10){batch}"
                    if serial:
                        raw += f"(21){serial}"
            if len(raw) > BULK_SCAN_MIN_LENGTH:
                codes.append(raw)
    return codes


@login_required
@operate_required
@require_POST
def branch_transfer_bulk_scan(request, pk):
    """Process a pasted list or Excel scan sheet against a Branch Transfer plan."""
    plan = get_scoped_or_404(
        BranchTransferPlan.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    if plan.is_finished:
        return JsonResponse({"message": "This transfer plan is closed."}, status=409)

    try:
        codes = _bt_codes_from_request(request)
    except ingest.IngestError as exc:
        return JsonResponse({"message": str(exc)}, status=400)

    if not codes:
        return JsonResponse(
            {"accepted_count": 0, "rejected_count": 0, "results": [], "message": "No valid codes found."},
            status=400,
        )
    if len(codes) > BULK_SCAN_MAX_CODES:
        return JsonResponse({"message": f"Upload or paste at most {BULK_SCAN_MAX_CODES:,} codes at a time."}, status=400)

    entity = plan.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)
    pool = _bt_pool(plan)

    results = []
    accepted_count = 0
    existing_serials: set[str] = set()
    with transaction.atomic():
        lines = list(plan.lines.select_for_update())
        line_increments: dict[int, int] = {}

        for raw_scan in codes:
            outcome = transfers.process_transfer_scan(
                raw_scan,
                profile,
                lines,
                pool,
                existing_serials=existing_serials,
                material_master=material_master,
                positional_rules=rules,
            )
            if outcome.accepted:
                material = outcome.parsed.material
                target = next(
                    (
                        line for line in lines
                        if line.sku_code.upper() == material.upper()
                        and float(line.picked_qty) < float(line.quantity)
                    ),
                    None,
                )
                if target is not None:
                    target.picked_qty = float(target.picked_qty or 0) + 1
                    line_increments[target.pk] = line_increments.get(target.pk, 0) + 1
                    if outcome.parsed and not is_non_serial(outcome.parsed.serial):
                        existing_serials.add(
                            f"{validation.clean(outcome.parsed.batch)}{validation.clean(outcome.parsed.serial)}"
                        )
                    accepted_count += 1

            results.append(
                {
                    "raw_scan": raw_scan,
                    "accepted": outcome.accepted,
                    "reason_code": outcome.reason_code,
                    "message": outcome.message,
                    "audio_cue": outcome.audio_cue,
                }
            )

        for line_pk, increment in line_increments.items():
            BranchTransferLine.objects.filter(pk=line_pk).update(picked_qty=F("picked_qty") + increment)

    plan.refresh_from_db()
    _log(request, "bt_bulk_scan", "BranchTransferPlan", plan.pk, codes=len(codes), accepted=accepted_count)
    return JsonResponse(
        {
            "accepted_count": accepted_count,
            "rejected_count": len(codes) - accepted_count,
            "results": results,
            "message": f"Processed {len(codes):,} code(s): {accepted_count:,} accepted, {len(codes) - accepted_count:,} rejected.",
            "state": _bt_state_payload(plan),
        }
    )


@login_required
@operate_required
@require_POST
def branch_transfer_auto_assign(request, pk):
    """Pre-assign a system-recommended batch to every open, unlocked plan line.

    "Ask material" in the client's own words — the legacy tool's ``assignBTBatches``
    (bt-scanner.js:835-902). Requires a stock dump; lines a picker has already
    started (picked_qty > 0) or already manually assigned are left untouched.
    """
    plan = get_scoped_or_404(
        BranchTransferPlan.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    pool = _bt_pool(plan)
    if pool is None:
        return JsonResponse(
            {"ok": False, "message": "Upload an MB52 stock dump for this plan before assigning batches."},
            status=400,
        )

    with transaction.atomic():
        lines = list(plan.lines.select_for_update())
        assigned = transfers.assign_transfer_batches(pool, lines)

        updated = 0
        for line in lines:
            if line.batch_overridden or float(line.picked_qty) > 0 or line.assigned_batch:
                continue  # already fixed — assign_transfer_batches only reserved its stock
            batch = assigned.get(_bt_line_key(line))
            if batch:
                line.assigned_batch = batch
                line.save(update_fields=["assigned_batch"])
                updated += 1

    plan.refresh_from_db()
    _log(request, "bt_auto_assign", "BranchTransferPlan", plan.pk, assigned=updated)
    return JsonResponse({"ok": True, "assigned": updated, "state": _bt_state_payload(plan)})


def _bt_line_key(line):
    return str(line.pk or line.unique_number or line.sku_code)


@login_required
@operate_required
@capability_required("mv_batch_correction_transfer")
@require_POST
def branch_transfer_line_batch(request, pk):
    """Manually assign/override the batch a picker should draw for one line.

    Locked once anything has been picked against the line, matching the same
    convention as ``outbound_line_batch`` — silently repointing already-picked
    units at a different batch would corrupt what was physically moved.
    """
    plan = get_scoped_or_404(BranchTransferPlan, request.user, field="created_by", pk=pk)
    line = get_object_or_404(
        BranchTransferLine, pk=int_or_none(request.POST.get("line_id")), plan=plan,
    )
    new_batch = (request.POST.get("batch") or "").strip().upper()

    if not new_batch:
        return JsonResponse({"ok": False, "message": "Enter a batch number."}, status=400)
    # Locked re-read, same reasoning as outbound_line_batch: without it a pick
    # landing between the check and the save repoints already-picked units.
    with transaction.atomic():
        line = BranchTransferLine.objects.select_for_update().get(pk=line.pk)
        if float(line.picked_qty) > 0:
            return JsonResponse(
                {
                    "ok": False,
                    "message": "Batch change is locked — this line already has picks against it.",
                },
                status=409,
            )

        line.assigned_batch = new_batch
        line.batch_overridden = True
        line.save(update_fields=["assigned_batch", "batch_overridden"])

    _log(request, "bt_line_batch_override", "BranchTransferLine", line.pk, batch=new_batch)
    return JsonResponse({"ok": True, "state": _bt_state_payload(plan)})


@login_required
@access_required
def branch_transfer_export(request, pk):
    plan = get_scoped_or_404(BranchTransferPlan, request.user, field="created_by", pk=pk)
    headers = [
        "Unique Number", "Supply Node Code", "Node Code", "Storage Location",
        "Receiving Parent Node Code", "Receiving Node Code", "SKU Code",
        "Quantity", "Picked Qty", "Pending", "Intransit Color Zone", "Mode",
    ]
    rows = [
        [
            line.unique_number, line.supply_node_code, line.node_code, line.storage_location,
            line.receiving_parent_node_code, line.receiving_node_code, line.sku_code,
            float(line.quantity), float(line.picked_qty), float(line.pending_qty),
            line.intransit_color_zone, line.mode,
        ]
        for line in plan.lines.all()
    ]
    buffer = export.export_generic(str(plan), headers, rows)
    return _xlsx_response(buffer, export.filename_for("branch_transfer", plan.pk))


# ─────────────────────────────────────────────────────────────────────────────
# Invoice consolidator
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@access_required
def consolidator(request):
    runs = scope_queryset_by_team(
        ConsolidationRun.objects.select_related("created_by"), request.user, field="created_by",
    ).order_by("-created_at")[:20]

    if request.method == "POST" and can_operate_merilv(request.user):
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "Upload at least one invoice file.")
            return redirect("merilv:consolidator")

        try:
            result = consolidate(files)
        except ConsolidationError as exc:
            messages.error(request, str(exc))
            return redirect("merilv:consolidator")

        source_type = (
            ConsolidationRun.SOURCE_PDF
            if any(f.name.lower().endswith(".pdf") for f in files)
            else ConsolidationRun.SOURCE_SHEET
        )
        with transaction.atomic():
            run = ConsolidationRun.objects.create(
                name=(request.POST.get("name") or "").strip(),
                source_type=source_type,
                file_count=result.file_count,
                row_count=result.row_count,
                warnings=result.warnings,
                created_by=request.user,
            )
            ConsolidationRow.objects.bulk_create(
                [
                    ConsolidationRow(
                        run=run,
                        sequence=index,
                        source_file=str(row.get("source_file") or ""),
                        invoice_no=str(row.get("invoice_no") or ""),
                        invoice_date=str(row.get("invoice_date") or ""),
                        customer_name=str(row.get("customer_name") or ""),
                        material=str(row.get("material") or ""),
                        description=str(row.get("description") or ""),
                        batch=str(row.get("batch") or ""),
                        quantity=row.get("quantity"),
                        rate=row.get("rate"),
                        amount=row.get("amount"),
                        # The parser's OWN `data` blob, not the whole row.
                        #
                        # Storing `row` put the extras one level too deep —
                        # `{"invoice_no": …, "data": {"line_no": …}}` — so both
                        # readers, which look for `data["line_no"]`, found
                        # nothing. That is why Line #, Mfg Date, Expiry Date and
                        # 510(k) came out blank on the screen and in the
                        # workbook on runs whose parse had actually found all
                        # four (#6, "not working").
                        #
                        # `confidence` is folded in because it is a real
                        # per-row fact with no column of its own — PDF parsing
                        # is a heuristic and every row carries a score — and
                        # storing the whole dict was the only reason it
                        # survived. Every other top-level key is already a
                        # column on this model.
                        data={
                            **(row.get("data") or {}),
                            "confidence": row.get("confidence"),
                        },
                    )
                    for index, row in enumerate(result.rows)
                ],
                batch_size=1000,
            )

        for warning in result.warnings:
            messages.warning(request, warning)
        messages.success(request, f"Consolidated {result.row_count:,} rows from {result.file_count} file(s).")
        return redirect("merilv:consolidation_detail", pk=run.pk)

    return render(request, "merilv/consolidator.html", _ctx(request, runs=runs))


def _known_material_codes(materials):
    """Which of ``materials`` exist in the material master, upper-cased.

    One query for the whole page rather than one per row. Matched on either
    ``material_code`` or ``scan_code`` because an invoice may quote either —
    the export invoices in ``module_info/`` use the sales code, which is the
    scan code for some entities and the material code for others.
    """
    wanted = {m.strip().upper() for m in materials if m and m.strip()}
    if not wanted:
        return set()
    found = MaterialMaster.objects.filter(
        Q(material_code__in=wanted) | Q(scan_code__in=wanted)
    ).values_list("material_code", "scan_code")
    known = set()
    for material_code, scan_code in found:
        for code in (material_code, scan_code):
            if code and code.strip().upper() in wanted:
                known.add(code.strip().upper())
    return known


@login_required
@access_required
def consolidation_detail(request, pk):
    run = get_scoped_or_404(ConsolidationRun, request.user, field="created_by", pk=pk)
    rows = run.rows.all()
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(invoice_no__icontains=query)
            | Q(material__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(description__icontains=query)
            | Q(batch__icontains=query)
        )

    # Totals over the whole run, not the current page: the client's screen shows
    # Rows / Quantity / Invoices for the batch that was processed.
    totals = run.rows.aggregate(quantity=Sum("quantity"))
    invoice_count = run.rows.exclude(invoice_no="").values("invoice_no").distinct().count()

    page_obj = paginate(request, rows)
    known = _known_material_codes(row.material for row in page_obj)
    for row in page_obj:
        # The client's "DB Match" column — is this invoice line's item code
        # something the material master actually knows about? A miss is the
        # signal that either the master needs a row or the invoice has a typo.
        row.in_master = bool(row.material) and row.material.strip().upper() in known

    return render(
        request,
        "merilv/consolidation_detail.html",
        _ctx(
            request,
            run=run,
            page_obj=page_obj,
            q=query,
            total_quantity=totals["quantity"] or 0,
            invoice_count=invoice_count,
            is_export_layout=run.source_type == ConsolidationRun.SOURCE_PDF,
        ),
    )


@login_required
@access_required
def consolidation_export(request, pk):
    """Excel export of a run, in the same shape as the screen.

    An export-layout (PDF) run gets the client's own 13-column sheet — Line #,
    Mfg/Expiry Date, 510(k) and DB Match included. A sheet-sourced run keeps
    the domestic columns, because those five fields do not exist for it and a
    block of empty columns in a spreadsheet reads as missing data rather than
    as not-applicable.
    """
    run = get_scoped_or_404(ConsolidationRun, request.user, field="created_by", pk=pk)
    all_rows = list(run.rows.all())
    known = _known_material_codes(row.material for row in all_rows)

    def match_label(material):
        if not material:
            return ""
        return "Match" if material.strip().upper() in known else "Not in DB"

    def number(value):
        return float(value) if value is not None else ""

    if run.source_type == ConsolidationRun.SOURCE_PDF:
        headers = [
            "Invoice #", "Invoice Date", "Line #", "Qty", "Item Code",
            "Mfg Date", "Expiry Date", "Lot Number", "Description", "510(k)",
            "Unit Price", "Line Total", "DB Match", "Source File",
        ]
        rows = [
            [
                row.invoice_no, row.invoice_date, (row.data or {}).get("line_no", ""),
                number(row.quantity), row.material,
                (row.data or {}).get("mfg_date", ""), (row.data or {}).get("expiry_date", ""),
                row.batch, row.description, (row.data or {}).get("k_number", ""),
                number(row.rate), number(row.amount),
                match_label(row.material), row.source_file,
            ]
            for row in all_rows
        ]
    else:
        headers = [
            "Source File", "Invoice No", "Invoice Date", "Customer",
            "Material", "Description", "Batch", "Quantity", "Rate", "Amount", "DB Match",
        ]
        rows = [
            [
                row.source_file, row.invoice_no, row.invoice_date, row.customer_name,
                row.material, row.description, row.batch,
                number(row.quantity), number(row.rate), number(row.amount),
                match_label(row.material),
            ]
            for row in all_rows
        ]

    buffer = export.export_generic(str(run), headers, rows)
    return _xlsx_response(buffer, export.filename_for("consolidation", run.pk))
