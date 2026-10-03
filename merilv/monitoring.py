"""Live monitoring (PRD §5.3).

Shows who is working on what right now: which pickers have open outbound
sessions, which delivery each is focused on, and how far along they are.

Activity is *derived* from the timestamp of each session's most recent scan
rather than from a client heartbeat. A picker who closes their laptop or walks
off with a dead tablet stops counting as active on their own, with no
cooperation needed from the browser — which is the only way this stays honest.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from . import focus
from .models import OutboundScan, OutboundSession, ScanSession
from .rbac import (
    can_access_merilv,
    can_operate_merilv,
    entities_for,
    is_merilv_admin,
    scope_queryset_by_team,
)

access_required = user_passes_test(can_access_merilv, login_url="landing_page")

#: How long after their last scan an operator still counts as working.
ACTIVE_WINDOW_MINUTES = 30


def _session_focus(session) -> str:
    """What the operator this row names is standing on.

    One string, because the board shows one line per session. Blank when that
    operator has focused nothing — including when somebody else on the same
    session has, which is the whole point of focus being per operator.
    """
    if not session.operator_id:
        return ""
    current = focus.get(session, session.operator)
    return current.value or current.customer_code or ""


def _operator_name(user):
    if user is None:
        return "Unassigned"
    return user.get_full_name() or user.username


def build_payload(user):
    """Assemble the whole monitor view in a handful of queries.

    Scoped to ``user``'s team: this board names people and says what they are
    working on, so an unscoped version would show a picker on one site the
    live activity of every other site. A manager still sees their whole team,
    which is the point of the screen.
    """
    now = timezone.now()
    cutoff = now - timedelta(minutes=ACTIVE_WINDOW_MINUTES)

    sessions = (
        scope_queryset_by_team(
            OutboundSession.objects.filter(status=OutboundSession.STATUS_ACTIVE),
            user, field="operator",
        )
        .select_related("entity", "operator", "dump")
        .annotate(scan_count=Count("scans", distinct=True), last_scan=Max("scans__created_at"))
        .order_by("-updated_at")
    )

    outbound = []
    for session in sessions:
        lines = list(session.dump.lines.all()) if session.dump else []
        required = sum(float(line.required_qty) for line in lines)
        scanned = sum(float(line.scanned_qty) for line in lines)
        outbound.append(
            {
                "id": session.pk,
                "name": str(session),
                "entity": session.entity.code,
                "operator": _operator_name(session.operator),
                # Distinguishes focused picking from ad-hoc scanning, which the
                # PRD calls out as "picker vs manual activity".
                #
                # The SESSION OPERATOR's focus, not "the session's" — focus is
                # per operator now (merilv/focus.py), and a session several
                # people are picking has several. This board is one row per
                # session, so it reports the person it names.
                "focus": _session_focus(session),
                "is_focused": bool(_session_focus(session)),
                "required": required,
                "scanned": scanned,
                "pending": max(required - scanned, 0),
                "percent": round(min(scanned / required, 1) * 100, 1) if required else 0,
                "scan_count": session.scan_count,
                "last_scan": session.last_scan.isoformat() if session.last_scan else None,
                "is_active": bool(session.last_scan and session.last_scan >= cutoff),
            }
        )

    scan_sessions = [
        {
            "id": session.pk,
            "name": str(session),
            "entity": session.entity.code,
            "mode": session.get_mode_display(),
            "operator": _operator_name(session.created_by),
            "total": session.total_count,
            "found": session.found_count,
            "updated": session.updated_at.isoformat(),
            "is_active": session.updated_at >= cutoff,
        }
        for session in (
            scope_queryset_by_team(
                ScanSession.objects.filter(is_finished=False), user, field="created_by",
            )
            .select_related("entity", "created_by")
            .order_by("-updated_at")[:25]
        )
    ]

    active_operators = sorted({row["operator"] for row in outbound if row["is_active"]})
    return {
        "outbound": outbound,
        "scan_sessions": scan_sessions,
        "summary": {
            "active_operators": len(active_operators),
            "operator_names": active_operators,
            "open_sessions": len(outbound),
            "idle_sessions": sum(1 for row in outbound if not row["is_active"]),
            "scans_in_window": scope_queryset_by_team(
                OutboundScan.objects.filter(created_at__gte=cutoff),
                user, field="session__operator",
            ).count(),
            "window_minutes": ACTIVE_WINDOW_MINUTES,
        },
        "generated_at": now.isoformat(),
    }


@login_required
@access_required
def board(request):
    return render(
        request,
        "merilv/monitoring.html",
        {
            "is_mv_admin": is_merilv_admin(request.user),
            "can_operate": can_operate_merilv(request.user),
            "entities": entities_for(request.user),
            "payload": build_payload(request.user),
        },
    )


@login_required
@access_required
def state(request):
    """Polled by the board so it refreshes without a page reload."""
    return JsonResponse(build_payload(request.user))
