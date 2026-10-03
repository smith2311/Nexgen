"""ZVA05 auto-assign.

Used when SAP deliveries have not been created yet: scans are assigned straight
to open sales-order lines. Matching is on **material only** — there is no batch
and no delivery to check against, so the guard rails are the ordered quantity
and an optional focus on one customer order.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import ActivityLog, ScanLog, Zva05Dump, Zva05Line, Zva05Scan
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
from .services.batching import bulk_create_in_chunks
from .services import export, ingest, transfers

access_required = user_passes_test(can_access_merilv, login_url="landing_page")
operate_required = user_passes_test(can_operate_merilv, login_url="merilv:index")


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


def _selected_entity(request):
    code = request.POST.get("entity") or request.GET.get("entity") or request.session.get("merilv_entity")
    queryset = entities_for(request.user)
    entity = queryset.filter(code=code).first() if code else None
    return entity or queryset.first()


def payload_for(dump, user=None):
    """Console state for ONE operator — focus is theirs alone (merilv/focus.py)."""
    """Counters and per-order progress for the console."""
    lines = list(dump.lines.all())
    groups: dict[str, dict] = {}
    for line in lines:
        group = groups.setdefault(
            line.focus_key,
            {
                "focus_key": line.focus_key,
                "customer": line.customer,
                "sales_document": line.sales_document,
                "required": 0.0,
                "scanned": 0.0,
                "lines": 0,
            },
        )
        group["required"] += float(line.required_qty)
        group["scanned"] += float(line.scanned_qty)
        group["lines"] += 1

    for group in groups.values():
        group["pending"] = max(group["required"] - group["scanned"], 0)
        group["percent"] = (
            round(min(group["scanned"] / group["required"], 1) * 100, 1) if group["required"] else 0
        )
        group["complete"] = group["pending"] <= 0

    required = sum(float(line.required_qty) for line in lines)
    scanned = sum(float(line.scanned_qty) for line in lines)
    return {
        "groups": sorted(groups.values(), key=lambda g: g["focus_key"]),
        "lines": [
            {
                "id": line.pk,
                "material": line.material,
                "customer": line.customer,
                "sales_document": line.sales_document,
                "item_no": line.item_no,
                "focus_key": line.focus_key,
                "required": float(line.required_qty),
                "scanned": float(line.scanned_qty),
                "pending": float(line.pending_qty),
            }
            for line in lines
        ],
        "totals": {
            "required": required,
            "scanned": scanned,
            "pending": max(required - scanned, 0),
            "percent": round(min(scanned / required, 1) * 100, 1) if required else 0,
        },
        "focus_key": operator_focus.get(dump, user).value,
    }


#: Where the ZVA05 landing used to be. It is not a module of its own any more —
#: ZVA05 is a *mode* of the Outbound Scanner, because it was never a different
#: tool, only a different source document for the same picking job. Kept as a
#: redirect rather than deleted so existing bookmarks and links still land
#: somewhere useful.
ZVA05_HOME = "/meril-v/outbound/?mode=zva05"


@login_required
@access_required
def index(request):
    """Legacy entry point — now the Outbound Scanner's ZVA05 mode."""
    return redirect(ZVA05_HOME)


@login_required
@operate_required
@require_POST
def upload(request):
    """Ingest a ZVA05 open-order extract and open a console against it."""
    entity = _selected_entity(request)
    upload_file = request.FILES.get("source_file")
    if entity is None or not upload_file:
        messages.error(request, "Select an entity and upload a ZVA05 extract.")
        return redirect(ZVA05_HOME)
    try:
        result = ingest.parse_zva05(upload_file, upload_file.name)
    except ingest.IngestError as exc:
        messages.error(request, str(exc))
        return redirect(ZVA05_HOME)

    with transaction.atomic():
        dump = Zva05Dump.objects.create(
            entity=entity,
            name=(request.POST.get("name") or upload_file.name).strip(),
            source_file=upload_file,
            original_filename=upload_file.name,
            row_count=len(result.records),
            skipped_count=result.skipped,
            warnings=result.warnings,
            created_by=request.user,
        )
        bulk_create_in_chunks(
            Zva05Line.objects,
            (
                Zva05Line(
                    dump=dump,
                    sequence=record["sequence"],
                    material=record["material"],
                    required_qty=record["required_qty"],
                    customer=record["customer"],
                    sales_document=record["sales_document"],
                    item_no=record["item_no"],
                    raw_data=record["raw_data"],
                )
                for record in result.records
            ),
            batch_size=1000,
        )

    _log(request, "zva05_upload", "Zva05Dump", dump.pk, lines=len(result.records))
    for warning in result.warnings:
        messages.warning(request, warning)
    messages.success(request, f"Loaded {len(result.records):,} open order lines.")
    return redirect("merilv:zva05_detail", pk=dump.pk)


@login_required
@access_required
def detail(request, pk):
    dump = get_scoped_or_404(
        Zva05Dump.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )

    # Built from ScanLog rather than dump.scans so rejected attempts survive a
    # reload — see merilv/scanfeed.py for why an accepted-then-undone code must
    # not stay in the Assigned tab.
    feed, counts = build_scan_feed(
        dump.logs.order_by("-created_at")[:200],
        {
            scan.raw_scan: (scan.line.sales_document if scan.line_id else "")
            for scan in dump.scans.select_related("line")
            if scan.raw_scan
        },
    )
    return render(
        request,
        "merilv/zva05_detail.html",
        _ctx(
            request,
            dump=dump,
            entity=dump.entity,
            payload=payload_for(dump, request.user),
            feed=feed,
            counts=counts,
        ),
    )


@login_required
@operate_required
@require_POST
def scan(request, pk):
    """Assign one scan to an open order line."""
    dump = get_scoped_or_404(
        Zva05Dump.objects.select_related("entity"),
        request.user, field="created_by", pk=pk,
    )
    raw_scan = (request.POST.get("raw_scan") or "").strip()
    if not raw_scan:
        return JsonResponse({"accepted": False, "message": "Nothing scanned."}, status=400)
    if dump.is_finished:
        return JsonResponse(
            {"accepted": False, "reason_code": "CLOSED", "message": "This extract is closed."},
            status=409,
        )

    entity = dump.entity
    profile = entity_service.get_profile(entity)
    rules = entity_service.get_positional_rules(entity)
    material_master = entity_service.build_material_master(entity)

    # Locked for the whole read-decide-write cycle so two guns cannot both
    # consume the last unit of a line.
    with transaction.atomic():
        rows = list(dump.lines.select_for_update())
        state = transfers.Zva05State(
            lines=[
                transfers.OrderLine(
                    material=row.material,
                    required_qty=float(row.required_qty),
                    customer=row.customer,
                    sales_document=row.sales_document,
                    item_no=row.item_no,
                    scanned_qty=float(row.scanned_qty),
                )
                for row in rows
            ],
            focus_key=operator_focus.get(dump, request.user).value,
            seen={f"{s.material}|{s.batch}|{s.serial}" for s in dump.scans.all() if s.serial},
        )
        outcome = transfers.process_zva05_scan(raw_scan, profile, state, material_master, rules)

        if outcome.accepted:
            # The engine incremented its in-memory copy; find which one and
            # mirror it onto the matching row, since the two lists are parallel.
            index = next(
                (i for i, order in enumerate(state.lines) if order.scanned_qty > float(rows[i].scanned_qty)),
                None,
            )
            if index is not None:
                target = rows[index]
                Zva05Line.objects.filter(pk=target.pk).update(scanned_qty=F("scanned_qty") + 1)
                Zva05Scan.objects.create(
                    dump=dump,
                    line=target,
                    material=outcome.parsed.material,
                    batch=outcome.parsed.batch,
                    serial=outcome.parsed.serial,
                    raw_scan=raw_scan,
                    scanned_by=request.user,
                )

        # Logged for EVERY attempt, accepted or not — deliberately outside the
        # branch above. Zva05Scan only ever holds accepted assignments (its rows
        # drive the quantity counters and the duplicate-serial `seen` set, so a
        # rejected row in there would corrupt both), which left rejections with
        # nowhere to live: they existed only in the operator's browser until the
        # next reload. Without this the status filter on the console could not
        # answer "which codes did I try that did not go through?".
        ScanLog.objects.create(
            zva05_dump=dump,
            raw_scan=raw_scan,
            status=outcome.status,
            reason_code=outcome.reason_code,
            message=outcome.message,
            parsed=outcome.parsed.as_dict() if outcome.parsed else {},
        )

    dump.refresh_from_db()
    return JsonResponse(
        {
            "accepted": outcome.accepted,
            "status": outcome.status,
            "reason_code": outcome.reason_code,
            "message": outcome.message,
            "audio_cue": outcome.audio_cue,
            "state": payload_for(dump, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def undo(request, pk):
    """Reverse the most recent assignment."""
    dump = get_scoped_or_404(Zva05Dump, request.user, field="created_by", pk=pk)
    with transaction.atomic():
        last = dump.scans.select_related("line").order_by("-created_at").first()
        if last is None:
            return JsonResponse({"ok": False, "message": "Nothing to undo."}, status=404)
        if last.line_id:
            Zva05Line.objects.filter(pk=last.line_id, scanned_qty__gt=0).update(
                scanned_qty=F("scanned_qty") - 1
            )
        removed = f"{last.material} {last.serial}".strip()
        undone_raw = last.raw_scan
        last.delete()

        # Append the reversal rather than deleting the original accept: the log
        # is an audit trail, and a code that was assigned then undone must not
        # keep reading as "Assigned" in the status filter.
        ScanLog.objects.create(
            zva05_dump=dump,
            raw_scan=undone_raw,
            status=ScanLog.STATUS_WARNING,
            reason_code="UNDONE",
            message=f"Assignment reversed — {removed} returned to pending.",
        )

    dump.refresh_from_db()
    _log(request, "zva05_undo", "Zva05Dump", dump.pk, removed=removed)
    return JsonResponse(
        {
            "ok": True,
            "removed": removed,
            "raw_scan": undone_raw,
            "message": f"Assignment reversed — {removed} returned to pending.",
            "state": payload_for(dump, request.user),
        }
    )


@login_required
@operate_required
@require_POST
def focus(request, pk):
    """Lock scanning to one customer order, or clear the lock."""
    dump = get_scoped_or_404(Zva05Dump, request.user, field="created_by", pk=pk)
    key = (request.POST.get("focus_key") or "").strip()
    # Against the OPERATOR, not the dump: the dump is shared, their place in
    # it is not. See merilv/focus.py.
    operator_focus.toggle(dump, request.user, key)
    return JsonResponse({"ok": True, "state": payload_for(dump, request.user)})


@login_required
@operate_required
@require_POST
def finish(request, pk):
    dump = get_scoped_or_404(Zva05Dump, request.user, field="created_by", pk=pk)
    dump.is_finished = True
    dump.save(update_fields=["is_finished", "updated_at"])
    _log(request, "zva05_finish", "Zva05Dump", dump.pk)
    messages.success(request, "ZVA05 extract closed.")
    return redirect("merilv:zva05")


@login_required
@access_required
def export_xlsx(request, pk):
    dump = get_scoped_or_404(Zva05Dump, request.user, field="created_by", pk=pk)
    headers = ["Customer", "Sales Document", "Item", "Material", "Ordered", "Assigned", "Pending"]
    rows = [
        [
            line.customer,
            line.sales_document,
            line.item_no,
            line.material,
            float(line.required_qty),
            float(line.scanned_qty),
            float(line.pending_qty),
        ]
        for line in dump.lines.all()
    ]
    buffer = export.export_generic(str(dump), headers, rows)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{export.filename_for("zva05", dump.pk)}"'
    return response
