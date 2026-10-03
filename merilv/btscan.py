"""BT Scanning — the floor console for a branch transfer.

Branch Transfer (``views.branch_transfer*``) is the plan side: upload the plan
and the MB52 stock, pre-assign recommended batches, review progress. This is
what the picker stands in front of, and it adds the three things the legacy
``bt-scanner.js`` had that the plan screen has nowhere to put:

*Cartons.*  Scans go into "Box 1", "Box 2", … rather than into an undifferentiated
pile, because the receiving dock counts cartons, not units.

*Colour zones.*  A carton carries a zone so the destination can sort a pallet by
eye. The plan's own ``intransit_color_zone`` says which zone a *line* belongs
to; this says which zone a *carton* was packed for, and they are not the same
thing — one carton can serve several lines of one zone.

*Location priority.*  Per session, because one site draws FG02-first and another
FG01-first, and that is an instruction to the picker rather than a property of
the entity.

Picking goes through :mod:`merilv.services.transfers` and moves the same
``BranchTransferLine.picked_qty`` the plan screen shows. This is a second
console onto one operation, deliberately not a parallel set of numbers — the
alternative (its own quantity column) would let the two screens disagree about
what physically left the building.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import (
    ActivityLog,
    BranchTransferLine,
    BranchTransferPlan,
    BtScan,
    BtScanBox,
    BtScanSession,
    ScanLog,
)
from .params import int_or_none
from .rbac import (
    can_access_merilv,
    can_operate_merilv,
    entities_for,
    get_scoped_or_404,
    is_merilv_admin,
)
from . import focus as operator_focus
from .scanfeed import build_scan_feed
from .services import entities as entity_service
from .services import export, ingest, transfers

access_required = user_passes_test(can_access_merilv, login_url="landing_page")
operate_required = user_passes_test(can_operate_merilv, login_url="merilv:index")

#: First carton of a fresh session. The legacy tool opened on "Box 1" too, and
#: a picker who never touches the carton controls still gets a valid carton
#: rather than scans with nowhere to go.
FIRST_BOX_NAME = "Box 1"


def _ctx(request, **extra):
    context = {
        "is_mv_admin": is_merilv_admin(request.user),
        "can_operate": can_operate_merilv(request.user),
        "entities": entities_for(request.user),
    }
    context.update(extra)
    return context


def _log(request, action, target_type="", target_id="", **metadata):
    ActivityLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def _pool(session):
    """The plan's MB52 stock as a :class:`StockPool`, or ``None``.

    The session's own ``location_priority`` wins over the entity default when
    set — that is the whole point of having it per session.
    """
    plan = session.plan
    if not plan.stock_file:
        return None
    priority = session.priority_locations or entity_service.get_bt_location_priority(plan.entity)
    try:
        plan.stock_file.open("rb")
        stock = ingest.parse_system_dump(plan.stock_file, plan.stock_file.name)
        return transfers.StockPool.from_records(stock.records, location_priority=priority)
    except (ingest.IngestError, OSError, ValueError):
        return None
    finally:
        plan.stock_file.close()


def _visible_lines(session, user=None, lines=None):
    """Plan lines this operator is working on, honouring their Focus Mode.

    Per operator, not per session: the session is shared, and two receivers
    filtered to one person's chosen plant is how the other one's scans go
    astray. See merilv/focus.py.
    """
    rows = list(lines if lines is not None else session.plan.lines.all())
    key = operator_focus.get(session, user).value
    if key:
        rows = [line for line in rows if (line.receiving_node_code or "") == key]
    return rows


def payload_for(session, user=None):
    """Everything the console repaints from: cartons, lines, totals.

    Takes the operator, because Focus Mode is theirs alone (merilv/focus.py).
    """
    all_lines = list(session.plan.lines.all())
    lines = _visible_lines(session, user, all_lines)
    boxes = list(session.boxes.all())
    counts: dict[int, int] = {}
    for scan in session.scans.all():
        if scan.box_id:
            counts[scan.box_id] = counts.get(scan.box_id, 0) + 1

    required = sum(float(line.quantity) for line in lines)
    picked = sum(float(line.picked_qty) for line in lines)
    return {
        "boxes": [
            {
                "id": box.pk,
                "name": box.name,
                "zone": box.zone,
                "is_closed": box.is_closed,
                "count": counts.get(box.pk, 0),
            }
            for box in boxes
        ],
        "active_box": _active_box_id(session, boxes),
        "focus_key": operator_focus.get(session, user).value,
        "location_priority": session.location_priority,
        "plants": sorted(
            {line.receiving_node_code for line in all_lines if line.receiving_node_code}
        ),
        "lines": [
            {
                "id": line.pk,
                "sku_code": line.sku_code,
                "receiving_node_code": line.receiving_node_code,
                "zone": line.intransit_color_zone,
                "assigned_batch": line.assigned_batch,
                "required": float(line.quantity),
                "picked": float(line.picked_qty),
                "pending": float(line.pending_qty),
            }
            for line in lines
        ],
        "totals": {
            "required": required,
            "picked": picked,
            "pending": max(required - picked, 0),
            "percent": round(min(picked / required, 1) * 100, 1) if required else 0,
            "boxes": len(boxes),
            "scans": sum(counts.values()),
        },
    }


def _active_box_id(session, boxes=None):
    """The open carton scans land in: the last one not yet closed."""
    rows = list(boxes if boxes is not None else session.boxes.all())
    open_boxes = [box for box in rows if not box.is_closed]
    return open_boxes[-1].pk if open_boxes else None


def _active_box(session):
    box_id = _active_box_id(session)
    return session.boxes.filter(pk=box_id).first() if box_id else None


# ─── Landing ─────────────────────────────────────────────────────────────────
#: Where the BT Scanning landing used to be. It is not a module of its own any
#: more — BT Scanning is a *mode* of the Outbound Scanner, alongside ZVA05, for
#: the same reason ZVA05 became one: neither is a different tool, only a
#: different console onto the same picking job. Kept as a redirect rather than
#: deleted so existing bookmarks and links still land somewhere useful.
BTSCAN_HOME = "/meril-v/outbound/?mode=btscan"


@login_required
@access_required
def index(request):
    """Legacy entry point — now the Outbound Scanner's BT Scanning mode."""
    return redirect(BTSCAN_HOME)


@login_required
@operate_required
@require_POST
def open_session(request):
    """Open a carton-tracked scanning session against a transfer plan."""
    plan = get_scoped_or_404(
        BranchTransferPlan, request.user, field="created_by",
        pk=int_or_none(request.POST.get("plan")),
    )
    if plan.is_finished:
        messages.error(request, "That transfer plan is already closed.")
        return redirect(BTSCAN_HOME)

    with transaction.atomic():
        session = BtScanSession.objects.create(
            plan=plan,
            entity=plan.entity,
            name=(request.POST.get("name") or "").strip(),
            location_priority=(request.POST.get("location_priority") or "").strip(),
            created_by=request.user,
        )
        # An opening focus goes on the OPERATOR, not the session. The column
        # this used to be written to is gone — it was shared, so the person
        # opening a session set the scan target for everyone who later looked
        # at it. See merilv/focus.py, and the focus() view below, which is the
        # same call. Unvalidated here only because there is nothing yet to
        # validate against differently: the plan is the one just scoped above.
        opening_focus = (request.POST.get("focus_key") or "").strip()
        if opening_focus:
            operator_focus.set_to(session, request.user, opening_focus)
        # A session with no carton would reject its first scan, so the
        # first one is opened here rather than waiting for the picker.
        BtScanBox.objects.create(session=session, name=FIRST_BOX_NAME, sequence=0)

    _log(request, "btscan_open", "BtScanSession", session.pk, plan=plan.pk)
    messages.success(request, f"BT scanning session opened for {plan}.")
    return redirect("merilv:btscan_detail", pk=session.pk)


@login_required
@access_required
def detail(request, pk):
    session = get_scoped_or_404(
        BtScanSession.objects.select_related("plan", "entity"),
        request.user, field="created_by", pk=pk,
    )
    # Built from ScanLog rather than session.scans so rejected attempts survive
    # a reload — see merilv/scanfeed.py for why an accepted-then-undone code
    # must not stay in the accepted tab.
    feed, counts = build_scan_feed(
        ScanLog.objects.filter(bt_scan_session=session).order_by("-created_at")[:200],
        {scan.raw_scan: (scan.box.name if scan.box_id else "") for scan in session.scans.select_related("box") if scan.raw_scan},
    )
    return render(
        request,
        "merilv/btscan_detail.html",
        _ctx(
            request,
            session=session,
            plan=session.plan,
            entity=session.entity,
            payload=payload_for(session, request.user),
            feed=feed,
            counts=counts,
            zones=BtScanSession.ZONE_CHOICES,
        ),
    )


@login_required
@access_required
def state(request, pk):
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    return JsonResponse(payload_for(session, request.user))


# ─── Scanning ────────────────────────────────────────────────────────────────
@login_required
@operate_required
@require_POST
def scan(request, pk):
    """Allocate one scan against the plan and drop it into the open carton."""
    session = get_scoped_or_404(
        BtScanSession.objects.select_related("plan", "entity"),
        request.user, field="created_by", pk=pk,
    )
    raw_scan = (request.POST.get("raw_scan") or "").strip()
    if not raw_scan:
        return JsonResponse({"accepted": False, "message": "Nothing scanned."}, status=400)
    if session.is_finished:
        return JsonResponse({"accepted": False, "message": "This session is closed."}, status=409)
    if session.plan.is_finished:
        return JsonResponse({"accepted": False, "message": "This transfer plan is closed."}, status=409)

    entity = session.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)
    pool = _pool(session)

    with transaction.atomic():
        all_lines = list(session.plan.lines.select_for_update())
        # Focus Mode narrows what the *engine* may allocate against, not just
        # what the screen shows: a picker locked to one destination plant must
        # not be able to consume another plant's requirement by accident.
        lines = _visible_lines(session, request.user, all_lines)
        outcome = transfers.process_transfer_scan(
            raw_scan,
            profile,
            lines,
            pool,
            existing_serials=session.scans.exclude(serial="").values_list("serial", flat=True),
            material_master=material_master,
            positional_rules=rules,
        )

        box = _active_box(session)
        if outcome.accepted and box is None:
            outcome = transfers.TransferOutcome(
                False,
                transfers.ReasonCode.NO_PLAN_DATA,
                "Every carton is closed — open a new one before scanning.",
                outcome.parsed,
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
                BranchTransferLine.objects.filter(pk=target.pk).update(picked_qty=F("picked_qty") + 1)
            BtScan.objects.create(
                session=session,
                line=target,
                box=box,
                material=material,
                batch=outcome.batch or outcome.parsed.batch,
                serial=outcome.parsed.serial or "",
                location=outcome.location or "",
                raw_scan=raw_scan,
                scanned_by=request.user,
            )

        ScanLog.objects.create(
            bt_scan_session=session,
            raw_scan=raw_scan,
            status=outcome.status,
            reason_code=outcome.reason_code,
            message=outcome.message,
            parsed=outcome.parsed.as_dict() if outcome.parsed else {},
        )

    return JsonResponse(
        {
            "accepted": outcome.accepted,
            "reason_code": outcome.reason_code,
            "message": outcome.message,
            "audio_cue": outcome.audio_cue,
            "batch": outcome.batch,
            "location": outcome.location,
            "box": box.name if box else "",
            "state": payload_for(session, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def undo(request, pk):
    """Reverse the most recent pick in this session."""
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    if session.is_finished:
        return JsonResponse({"ok": False, "message": "This session is closed."}, status=409)

    with transaction.atomic():
        scan_row = session.scans.select_for_update().order_by("-created_at", "-id").first()
        if scan_row is None:
            return JsonResponse({"ok": False, "message": "Nothing to undo."}, status=404)
        if scan_row.line_id:
            BranchTransferLine.objects.filter(pk=scan_row.line_id, picked_qty__gt=0).update(
                picked_qty=F("picked_qty") - 1
            )
        raw = scan_row.raw_scan
        scan_row.delete()
        # The feed is rebuilt from ScanLog, so the log rows for this code have
        # to go too or the undone scan stays visible as accepted.
        ScanLog.objects.filter(bt_scan_session=session, raw_scan=raw).delete()

    return JsonResponse({"ok": True, "state": payload_for(session, request.user)})


# ─── Cartons ─────────────────────────────────────────────────────────────────
@login_required
@operate_required
@require_POST
def box_new(request, pk):
    """Close the open carton and start the next one."""
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    if session.is_finished:
        return JsonResponse({"ok": False, "message": "This session is closed."}, status=409)

    zone = (request.POST.get("zone") or "").strip()
    valid_zones = {key for key, _label in BtScanSession.ZONE_CHOICES}
    if zone not in valid_zones:
        zone = ""

    with transaction.atomic():
        existing = list(session.boxes.select_for_update())
        session.boxes.filter(is_closed=False).update(is_closed=True)
        # Numbered from the count, not from a stored counter: a deleted carton
        # would otherwise leave a permanent gap or, worse, collide on the
        # unique (session, name) constraint.
        sequence = len(existing)
        name = (request.POST.get("name") or "").strip() or f"Box {sequence + 1}"
        while session.boxes.filter(name=name).exists():
            sequence += 1
            name = f"Box {sequence + 1}"
        box = BtScanBox.objects.create(session=session, name=name, zone=zone, sequence=sequence)

    _log(request, "btscan_box_new", "BtScanBox", box.pk, session=session.pk)
    return JsonResponse({"ok": True, "box": box.name, "state": payload_for(session, request.user)})


@login_required
@operate_required
@require_POST
def box_zone(request, pk, box_pk):
    """Set or clear a carton's colour zone."""
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    box = session.boxes.filter(pk=box_pk).first()
    if box is None:
        return JsonResponse({"ok": False, "message": "That carton is not in this session."}, status=404)

    zone = (request.POST.get("zone") or "").strip()
    if zone not in {key for key, _label in BtScanSession.ZONE_CHOICES}:
        return JsonResponse({"ok": False, "message": "Unknown colour zone."}, status=400)

    box.zone = zone
    box.save(update_fields=["zone"])
    return JsonResponse({"ok": True, "state": payload_for(session, request.user)})


# ─── Session settings ────────────────────────────────────────────────────────
@login_required
@operate_required
@require_POST
def focus(request, pk):
    """Lock scanning to one receiving plant, or clear the lock."""
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    wanted = (request.POST.get("focus_key") or "").strip()
    if wanted:
        # Validated against the plan rather than trusted: a focus on a plant
        # this plan never ships to would silently hide every line.
        known = set(session.plan.lines.values_list("receiving_node_code", flat=True))
        if wanted not in known:
            return JsonResponse(
                {"ok": False, "message": f"{wanted} is not a receiving plant on this plan."},
                status=400,
            )
    # Against the OPERATOR, not the session — see merilv/focus.py.
    operator_focus.set_to(session, request.user, wanted)
    return JsonResponse({"ok": True, "state": payload_for(session, request.user)})


@login_required
@operate_required
@require_POST
def priority(request, pk):
    """Set the storage-location draw order for this session."""
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    session.location_priority = (request.POST.get("location_priority") or "").strip()[:200]
    session.save(update_fields=["location_priority", "updated_at"])
    return JsonResponse(
        {"ok": True, "location_priority": session.location_priority, "state": payload_for(session, request.user)}
    )


@login_required
@operate_required
@require_POST
def finish(request, pk):
    session = get_scoped_or_404(BtScanSession, request.user, field="created_by", pk=pk)
    session.is_finished = True
    session.boxes.filter(is_closed=False).update(is_closed=True)
    session.save(update_fields=["is_finished", "updated_at"])
    _log(request, "btscan_finish", "BtScanSession", session.pk)
    messages.success(request, "BT scanning session closed.")
    return redirect("merilv:btscan")


# ─── Export ──────────────────────────────────────────────────────────────────
@login_required
@access_required
def export_xlsx(request, pk):
    """Carton-level pick list — what physically goes on the pallet."""
    session = get_scoped_or_404(
        BtScanSession.objects.select_related("plan", "entity"),
        request.user, field="created_by", pk=pk,
    )
    headers = [
        "Box", "Zone", "SKU Code", "Batch", "Serial", "Storage Location",
        "Receiving Plant", "Line Zone", "Scanned At", "Scanned By",
    ]
    rows = [
        [
            scan.box.name if scan.box else "",
            scan.box.zone if scan.box else "",
            scan.material,
            scan.batch,
            scan.serial,
            scan.location,
            scan.line.receiving_node_code if scan.line else "",
            scan.line.intransit_color_zone if scan.line else "",
            scan.created_at.replace(tzinfo=None),
            scan.scanned_by.get_full_name() if scan.scanned_by else "",
        ]
        for scan in session.scans.select_related("box", "line", "scanned_by")
    ]
    buffer = export.export_generic(str(session), headers, rows)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{export.filename_for("bt_scan", session.pk)}"'
    )
    return response
