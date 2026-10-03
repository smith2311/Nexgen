"""Inbound Scanner — the destination-plant half of a branch transfer.

The picking side (``views.branch_transfer``) records what left the sending plant
as ``BranchTransferLine.picked_qty``. This console books the same goods in at the
other end and answers the question that was previously unanswerable: **did
everything that was dispatched actually arrive?**

Two deliberate choices worth knowing before changing anything here:

* Expected quantity is ``picked_qty``, never ``quantity``. See
  ``BranchTransferLine.pending_receipt_qty``.
* A plan may be received in several loads, so ``received_qty`` lives on the line
  and accumulates across every receipt raised against the plan. Closing one
  receipt does not close the plan.

Shaped after ``merilv/zva05.py`` (full route set with an incremental ``state``
payload) rather than after the thin 4-view picking side, so the console can
update in place instead of reloading after every scan.
"""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import (
    ActivityLog,
    BranchTransferLine,
    BranchTransferPlan,
    BranchTransferReceipt,
    BranchTransferReceiptScan,
    ScanLog,
)
from .rbac import (
    can_access_merilv,
    can_operate_merilv,
    get_scoped_or_404,
    is_merilv_admin,
    scope_queryset_by_team,
)
from . import focus as operator_focus
from .permissions import capability_required
from .scanfeed import build_scan_feed
from .services import entities as entity_service
from .services import export, transfers

access_required = user_passes_test(can_access_merilv, login_url="landing_page")
operate_required = user_passes_test(can_operate_merilv, login_url="merilv:index")


def _ctx(request, **extra):
    context = {
        "is_mv_admin": is_merilv_admin(request.user),
        "can_operate": can_operate_merilv(request.user),
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


def _lines_for(receipt):
    """Plan lines that are relevant to receiving, newest plan order preserved.

    Lines the sender never picked are still returned — the receiver needs to see
    a dispatched-zero line to understand why a physically present carton is being
    refused.
    """
    return receipt.plan.lines.all()


def payload_for(receipt, user=None):
    """Console state for ONE operator — focus is theirs alone (merilv/focus.py)."""
    """State the console re-renders itself from after every scan."""
    lines = list(_lines_for(receipt))

    # Row 5 parity with the Outbound console's Lines view: which serials have
    # actually been booked in against each line, not just a running count.
    # Scoped to the plan, matching the duplicate-serial check in
    # ``_state_for_scan`` — the same unit must not appear against two receipts
    # of the same transfer.
    serials_by_line: dict[int, list[str]] = {}
    if lines:
        for line_id, serial in (
            BranchTransferReceiptScan.objects.filter(receipt__plan=receipt.plan, line_id__in=[line.pk for line in lines])
            .exclude(serial="")
            .order_by("created_at")
            .values_list("line_id", "serial")
        ):
            serials_by_line.setdefault(line_id, []).append(serial)

    # Per line, so the results table can show which lines arrived damaged
    # rather than only a session total.
    damaged_by_line: dict[int, int] = {}
    for line_id in (
        receipt.scans.exclude(condition="")
        .exclude(condition__iexact="Good")
        .values_list("line_id", flat=True)
    ):
        if line_id is not None:
            damaged_by_line[line_id] = damaged_by_line.get(line_id, 0) + 1

    groups: dict[str, dict] = {}
    for line in lines:
        key = line.receiving_node_code or "—"
        group = groups.setdefault(
            key,
            {"focus_key": key, "node": key, "dispatched": 0.0, "received": 0.0},
        )
        group["dispatched"] += float(line.picked_qty)
        group["received"] += float(line.received_qty)

    for group in groups.values():
        group["pending"] = max(group["dispatched"] - group["received"], 0)
        group["percent"] = (
            round(min(group["received"] / group["dispatched"], 1) * 100, 1)
            if group["dispatched"] else 0
        )
        group["complete"] = group["dispatched"] > 0 and group["pending"] <= 0

    dispatched = sum(float(line.picked_qty) for line in lines)
    received = sum(float(line.received_qty) for line in lines)
    # #5's fourth KPI. Counted from the scans rather than kept as a denormalised
    # column, because a condition can be corrected after the fact and a stored
    # counter would need every correction path to remember to move it.
    #
    # A damaged unit is still received — it is NOT subtracted from `received`.
    # Short means "dispatched but never arrived"; damaged means "arrived
    # broken". The warehouse acts on those differently.
    damaged = (
        receipt.scans.exclude(condition="")
        .exclude(condition__iexact="Good")
        .count()
    )

    return {
        "lines": [
            {
                "id": line.pk,
                "material": line.sku_code,
                "node": line.receiving_node_code,
                "planned": float(line.quantity),
                "dispatched": float(line.picked_qty),
                "received": float(line.received_qty),
                "pending": float(line.pending_receipt_qty),
                "serials": serials_by_line.get(line.pk, []),
                "damaged": damaged_by_line.get(line.pk, 0),
            }
            for line in lines
        ],
        "groups": sorted(groups.values(), key=lambda g: g["node"]),
        "totals": {
            # "expected" and "dispatched" are the same number under two names:
            # the brief's KPI is called Expected, and this module's own word for
            # it is dispatched (expected quantity IS picked_qty — see the module
            # docstring). Both are emitted so neither the template nor the brief
            # has to translate.
            "expected": dispatched,
            "dispatched": dispatched,
            "received": received,
            "damaged": damaged,
            "pending": max(dispatched - received, 0),
            "percent": round(min(received / dispatched, 1) * 100, 1) if dispatched else 0,
        },
        "condition": receipt.condition or "Good",
        "focus_key": operator_focus.get(receipt, user).value,
    }


def _state_for_scan(receipt, user=None):
    """Engine state built from the plan lines, ready for one scan decision."""
    rows = list(receipt.plan.lines.select_for_update())
    state = transfers.InboundState(
        lines=[
            transfers.InboundLine(
                material=row.sku_code,
                dispatched_qty=float(row.picked_qty),
                received_qty=float(row.received_qty),
                receiving_node=row.receiving_node_code,
                unique_number=row.unique_number,
                line_id=row.pk,
            )
            for row in rows
        ],
        focus_key=operator_focus.get(receipt, user).value,
        # Scoped to the plan, not the receipt: the same serial must not be booked
        # in twice across two vehicle loads of the same transfer.
        seen={
            f"{s.material}|{s.batch}|{s.serial}"
            for s in BranchTransferReceiptScan.objects.filter(receipt__plan=receipt.plan)
            if s.serial
        },
    )
    return rows, state


# ─── Screens ─────────────────────────────────────────────────────────────────
@login_required
@access_required
def index(request):
    """Pick a dispatched plan to receive against, or open an existing receipt."""
    if request.method == "POST" and can_operate_merilv(request.user):
        plan = get_scoped_or_404(
            BranchTransferPlan, request.user, field="created_by", pk=request.POST.get("plan"),
        )
        receipt = BranchTransferReceipt.objects.create(
            plan=plan,
            entity=plan.entity,
            name=(request.POST.get("name") or "").strip(),
            vehicle_no=(request.POST.get("vehicle_no") or "").strip(),
            docket_no=(request.POST.get("docket_no") or "").strip(),
            received_by=request.user,
        )
        _log(request, "bt_receipt_open", "BranchTransferReceipt", receipt.pk, plan=plan.pk)
        messages.success(request, f"Inbound session opened for {plan}.")
        return redirect("merilv:inbound_detail", pk=receipt.pk)

    # Only plans with something actually dispatched can be received against —
    # offering a plan nobody has picked yet would just produce a console where
    # every scan is refused.
    plans = [
        plan for plan in scope_queryset_by_team(
            BranchTransferPlan.objects.select_related("entity").prefetch_related("lines"),
            request.user, field="created_by",
        ).order_by("-created_at")[:40]
        if any(line.picked_qty > 0 for line in plan.lines.all())
    ]
    receipts = scope_queryset_by_team(
        BranchTransferReceipt.objects.select_related("plan", "entity", "received_by"),
        request.user, field="received_by",
    ).order_by("-created_at")[:20]
    return render(
        request,
        "merilv/inbound.html",
        _ctx(request, plans=plans, receipts=receipts),
    )


@login_required
@access_required
def detail(request, pk):
    receipt = get_scoped_or_404(
        BranchTransferReceipt.objects.select_related("plan", "entity"),
        request.user, field="received_by", pk=pk,
    )
    feed, counts = build_scan_feed(
        receipt.logs.order_by("-created_at")[:200],
        {
            scan.raw_scan: (scan.line.receiving_node_code if scan.line_id else "")
            for scan in receipt.scans.select_related("line")
            if scan.raw_scan
        },
    )
    return render(
        request,
        "merilv/inbound_detail.html",
        _ctx(
            request,
            receipt=receipt,
            plan=receipt.plan,
            entity=receipt.entity,
            payload=payload_for(receipt, request.user),
            feed=feed,
            counts=counts,
        ),
    )


# ─── Scan endpoints ──────────────────────────────────────────────────────────
@login_required
@operate_required
@require_POST
def scan(request, pk):
    receipt = get_scoped_or_404(
        BranchTransferReceipt.objects.select_related("plan", "entity"),
        request.user, field="received_by", pk=pk,
    )
    raw_scan = (request.POST.get("raw_scan") or "").strip()
    if not raw_scan:
        return JsonResponse({"accepted": False, "message": "Nothing scanned."}, status=400)
    if receipt.is_finished:
        return JsonResponse(
            {"accepted": False, "reason_code": "CLOSED", "message": "This receipt is closed."},
            status=409,
        )

    entity = receipt.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    # The receiver can flip Good/Damage mid-session without opening a new
    # receipt: whatever is posted becomes the default for every scan after this
    # one, the same way the extractor's console works (Row 3.5).
    condition = (request.POST.get("condition") or receipt.condition or "Good").strip()

    # Locked for the whole read-decide-write cycle so two guns at the same dock
    # cannot both book in the last outstanding unit.
    with transaction.atomic():
        if condition != receipt.condition:
            receipt.condition = condition
            receipt.save(update_fields=["condition", "updated_at"])
        rows, state = _state_for_scan(receipt, request.user)
        outcome = transfers.process_inbound_scan(
            raw_scan, profile, state, material_master, rules
        )

        if outcome.accepted:
            # The engine incremented its own copy; mirror it onto the row it
            # chose. The lists are parallel, so compare position by position.
            index = next(
                (
                    i for i, line in enumerate(state.lines)
                    if line.received_qty > float(rows[i].received_qty)
                ),
                None,
            )
            if index is not None:
                target = rows[index]
                BranchTransferLine.objects.filter(pk=target.pk).update(
                    received_qty=F("received_qty") + 1
                )
                BranchTransferReceiptScan.objects.create(
                    receipt=receipt,
                    line=target,
                    material=outcome.parsed.material,
                    batch=outcome.parsed.batch,
                    serial=outcome.parsed.serial,
                    raw_scan=raw_scan,
                    condition=condition,
                    scanned_by=request.user,
                )

        # Every attempt, accepted or not — this is what the console's status
        # filter reads. See merilv/scanfeed.py.
        ScanLog.objects.create(
            receipt=receipt,
            raw_scan=raw_scan,
            status=outcome.status,
            reason_code=outcome.reason_code,
            message=outcome.message,
            parsed=outcome.parsed.as_dict() if outcome.parsed else {},
        )

    receipt.refresh_from_db()
    return JsonResponse(
        {
            "accepted": outcome.accepted,
            "status": outcome.status,
            "reason_code": outcome.reason_code,
            "message": outcome.message,
            "audio_cue": outcome.audio_cue,
            "condition": condition,
            "state": payload_for(receipt, request.user),
        }
    )


@login_required
@operate_required
# The inbound half of document correction. This endpoint rewrites what a
# received unit recorded and had no capability check at all — any operator
# could change a booked-in unit's condition. Split from the outbound right at
# the client's instruction (23 Sep MoM) so the two can be granted separately.
@capability_required("mv_document_edit_inbound")
@require_POST
def scan_condition(request, pk, scan_pk):
    """Correct one booked-in unit's Good/Damage after the fact.

    The counterpart of the extractor's Row 3.5 control, and needed for the same
    reason: a receiver who books a unit in and only then notices the damage
    should not have to undo and rescan, and should not have to flip the whole
    session's default to fix one past unit.
    """
    receipt = get_scoped_or_404(
        BranchTransferReceipt, request.user, field="received_by", pk=pk,
    )
    scan = get_object_or_404(
        BranchTransferReceiptScan.objects.select_related("receipt"),
        pk=scan_pk, receipt_id=pk,
    )
    if receipt.is_finished:
        return JsonResponse(
            {"ok": False, "message": "This receipt is finished and read-only."}, status=409
        )
    condition = (request.POST.get("condition") or "").strip()
    if not condition:
        return JsonResponse({"ok": False, "message": "Choose a condition."}, status=400)
    scan.condition = condition
    scan.save(update_fields=["condition"])
    _log(request, "inbound_scan_condition", "BranchTransferReceiptScan", scan.pk, condition=condition)
    return JsonResponse(
        {"ok": True, "scan_id": scan.pk, "condition": condition, "state": payload_for(receipt, request.user)}
    )


#: Same floor as outbound_bulk_scan — short tokens pasted in are almost
#: always stray whitespace or a typo, not a scannable barcode.
BULK_SCAN_MIN_LENGTH = 5
BULK_SCAN_MAX_CODES = 2000


@login_required
@operate_required
@require_POST
def bulk_scan(request, pk):
    """Book in many arriving cartons from one paste — Row 5 parity with the
    Outbound console's Bulk Paste tab. A truck unloads as a batch, not one
    carton at a time, so a receiver should not have to scan each barcode
    into this box separately if they were handed the packing list instead.

    Each code runs through exactly the same gates as ``scan``, in order, in
    one transaction — no line can be double-booked across the paste's own
    entries, and a failure partway through cannot leave the plan partially
    updated.
    """
    receipt = get_scoped_or_404(
        BranchTransferReceipt.objects.select_related("plan", "entity"),
        request.user, field="received_by", pk=pk,
    )
    raw_text = request.POST.get("raw_text") or ""
    codes = [c.strip() for c in re.split(r"[\n,\t]+", raw_text) if len(c.strip()) > BULK_SCAN_MIN_LENGTH]

    if not codes:
        return JsonResponse(
            {"accepted_count": 0, "rejected_count": 0, "results": [], "message": "No valid codes found."},
            status=400,
        )
    if len(codes) > BULK_SCAN_MAX_CODES:
        return JsonResponse({"message": f"Paste at most {BULK_SCAN_MAX_CODES:,} codes at a time."}, status=400)
    if receipt.is_finished:
        return JsonResponse({"message": "This receipt is closed."}, status=409)

    entity = receipt.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    results = []
    accepted_count = 0
    with transaction.atomic():
        rows, state = _state_for_scan(receipt, request.user)
        line_increments: dict[int, int] = {}

        for raw_scan in codes:
            outcome = transfers.process_inbound_scan(raw_scan, profile, state, material_master, rules)

            if outcome.accepted:
                index = next(
                    (i for i, line in enumerate(state.lines) if line.received_qty > float(rows[i].received_qty)),
                    None,
                )
                if index is not None:
                    target = rows[index]
                    target.received_qty = float(target.received_qty) + 1
                    line_increments[target.pk] = line_increments.get(target.pk, 0) + 1
                    BranchTransferReceiptScan.objects.create(
                        receipt=receipt,
                        line=target,
                        material=outcome.parsed.material,
                        batch=outcome.parsed.batch,
                        serial=outcome.parsed.serial,
                        raw_scan=raw_scan,
                        scanned_by=request.user,
                    )
                    if outcome.parsed.serial:
                        state.seen.add(f"{outcome.parsed.material}|{outcome.parsed.batch}|{outcome.parsed.serial}")
                    accepted_count += 1

            ScanLog.objects.create(
                receipt=receipt,
                raw_scan=raw_scan,
                status=outcome.status,
                reason_code=outcome.reason_code,
                message=outcome.message,
                parsed=outcome.parsed.as_dict() if outcome.parsed else {},
            )
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
            BranchTransferLine.objects.filter(pk=line_pk).update(received_qty=F("received_qty") + increment)

    receipt.refresh_from_db()
    _log(request, "bt_receipt_bulk_scan", "BranchTransferReceipt", receipt.pk, codes=len(codes), accepted=accepted_count)
    return JsonResponse(
        {
            "accepted_count": accepted_count,
            "rejected_count": len(codes) - accepted_count,
            "results": results,
            "state": payload_for(receipt, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def undo(request, pk):
    """Reverse the most recent booking in this receipt."""
    receipt = get_scoped_or_404(BranchTransferReceipt, request.user, field="received_by", pk=pk)
    with transaction.atomic():
        last = receipt.scans.select_related("line").order_by("-created_at").first()
        if last is None:
            return JsonResponse({"ok": False, "message": "Nothing to undo."}, status=404)
        if last.line_id:
            # Floored at zero so an undo can never drive the counter negative.
            BranchTransferLine.objects.filter(pk=last.line_id, received_qty__gt=0).update(
                received_qty=F("received_qty") - 1
            )
        removed = f"{last.material} {last.serial}".strip()
        undone_raw = last.raw_scan
        last.delete()

        ScanLog.objects.create(
            receipt=receipt,
            raw_scan=undone_raw,
            status=ScanLog.STATUS_WARNING,
            reason_code="UNDONE",
            message=f"Receipt reversed — {removed} returned to in-transit.",
        )

    receipt.refresh_from_db()
    _log(request, "bt_receipt_undo", "BranchTransferReceipt", receipt.pk, removed=removed)
    return JsonResponse(
        {
            "ok": True,
            "removed": removed,
            "raw_scan": undone_raw,
            "state": payload_for(receipt, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def focus(request, pk):
    """Lock scanning to one receiving plant, or clear the lock."""
    receipt = get_scoped_or_404(BranchTransferReceipt, request.user, field="received_by", pk=pk)
    key = (request.POST.get("focus_key") or "").strip()
    # Against the OPERATOR, not the receipt — see merilv/focus.py.
    operator_focus.toggle(receipt, request.user, key)
    return JsonResponse({"ok": True, "state": payload_for(receipt, request.user)})


@login_required
@operate_required
@require_POST
def finish(request, pk):
    receipt = get_scoped_or_404(
        BranchTransferReceipt.objects.select_related("plan"),
        request.user, field="received_by", pk=pk,
    )
    receipt.remarks = (request.POST.get("remarks") or receipt.remarks or "").strip()
    receipt.is_finished = True
    receipt.save(update_fields=["is_finished", "remarks", "updated_at"])

    shortfall = sum(float(line.transit_shortfall) for line in _lines_for(receipt))
    _log(
        request, "bt_receipt_finish", "BranchTransferReceipt", receipt.pk,
        shortfall=shortfall,
    )
    if shortfall > 0:
        messages.warning(
            request,
            f"Receipt closed with {shortfall:g} unit(s) dispatched but never received.",
        )
    else:
        messages.success(request, "Receipt closed — everything dispatched was received.")
    return redirect("merilv:inbound")


@login_required
@access_required
def export_xlsx(request, pk):
    receipt = get_scoped_or_404(
        BranchTransferReceipt.objects.select_related("plan"),
        request.user, field="received_by", pk=pk,
    )
    headers = [
        "Unique Number", "SKU Code", "Sender Plant", "Receiver Plant",
        "Planned", "Dispatched", "Received", "Damaged", "Short (in transit)",
    ]
    # Counted once for the whole sheet rather than per line — the condition
    # lives on the scans, so a per-line count would be a query per row.
    damaged_by_line: dict[int, int] = {}
    for line_id in (
        receipt.scans.exclude(condition="")
        .exclude(condition__iexact="Good")
        .values_list("line_id", flat=True)
    ):
        if line_id is not None:
            damaged_by_line[line_id] = damaged_by_line.get(line_id, 0) + 1
    rows = [
        [
            line.unique_number,
            line.sku_code,
            line.node_code,
            line.receiving_node_code,
            float(line.quantity),
            float(line.picked_qty),
            float(line.received_qty),
            damaged_by_line.get(line.pk, 0),
            float(line.transit_shortfall),
        ]
        for line in _lines_for(receipt)
    ]
    buffer = export.export_generic(str(receipt), headers, rows)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename = export.filename_for("bt_receipt", receipt.pk)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
##
