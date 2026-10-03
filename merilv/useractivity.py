"""Per-user activity analytics behind Reports → User Activity.

Answers "what has each person actually done in Meril-V, and when" by reading
the fifteen tables that carry a user FK, rather than the ``ActivityLog`` alone
— that log only covers master-data and configuration edits, so a picker who
scanned four thousand cartons and closed sixty deliveries appears in it exactly
zero times.

Two things this module is deliberately careful about:

**Cost.** Every metric is derived from one grouped query per source, keyed on
``(user, day)`` — never a row per scan. A 90-day window over fifteen sources
returns on the order of (users x days x sources) grouped rows, which is small
and bounded, where fetching timestamps individually would pull millions. The
per-source queries all filter on ``created_at``, which is indexed on the
high-volume tables.

**Honesty about time.** There is no login/logout tracking anywhere in this
module, so "time spent in Meril-V" is not a knowable quantity and this file
does not pretend otherwise. Two things that *are* measurable stand in for it,
and the UI labels both for what they are:

``active_seconds``
    Summed, per day, of (last action - first action). An engagement window, not
    attendance: a picker who scans at 09:00 and again at 17:00 with a lunch
    break in between reads as eight hours.
``session_seconds``
    Summed ``updated_at - created_at`` across the console sessions a user owns
    (outbound, barcode, BT). Closer to real working time, but only covers work
    that happens inside a session — an upload or a master-data edit has no
    duration at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import (
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Max,
    Min,
    Q,
    Sum,
)
from django.db.models.functions import ExtractHour, TruncDate
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActivityLog,
    BranchTransferPlan,
    BranchTransferReceipt,
    BranchTransferReceiptScan,
    BtScan,
    BtScanSession,
    ConsolidationRun,
    DeliveryDump,
    FinishedDelivery,
    OutboundScan,
    OutboundSession,
    ScanSession,
    ValidationRun,
    Zva05Dump,
    Zva05Scan,
)
from .roles import ROLE_GROUPS

#: How the five activity kinds are described on screen, in the order a piece of
#: work naturally flows: pull a file in, open a session against it, scan, close
#: it out. Admin work sits outside that flow, so it comes last.
KIND_LABELS: tuple[tuple[str, str], ...] = (
    ("upload", "Data pulled in"),
    ("session", "Sessions opened"),
    ("scan", "Items scanned"),
    ("close", "Work closed out"),
    ("admin", "Admin & master data"),
)

#: Literal hex per kind, mirroring the ``--mv-accent-*`` tokens the CSS legend
#: dots use. Highcharts is handed colours as data and cannot read a CSS custom
#: property, so the two have to be stated in both places — kept adjacent to
#: KIND_LABELS so a new kind is obviously missing a colour.
KIND_COLORS: dict[str, str] = {
    "upload": "#8B5CF6",   # --mv-accent-violet
    "session": "#0076C8",  # --mv-accent-blue
    "scan": "#00A891",     # --mv-accent-teal
    "close": "#15803D",    # --mv-ok
    "admin": "#F59E0B",    # --mv-accent-amber
}

#: Chart palette for series that are not kind-coded (top-N users, per-source
#: bars). Same order as the billing dashboard's, so the two look related.
CHART_PALETTE: tuple[str, ...] = (
    "#0076C8", "#00B5E2", "#8B5CF6", "#00A891", "#F59E0B",
    "#15803D", "#EF4444", "#6366F1", "#3B82F6", "#F97316",
)

#: Weekday order for the "which days do they work" chart. Monday-first, because
#: a warehouse week starts Monday and Python's ``weekday()`` already agrees.
WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class ActivitySource:
    """One user-attributed table, and how to read a user out of it.

    ``user_field`` is a lookup rather than a plain column name so a source can
    attribute through a relation if it ever needs to; today they are all direct
    FKs. ``duration`` marks the sources whose rows span time (a session) rather
    than being instantaneous (a scan), which is what makes ``session_seconds``
    computable for some sources and meaningless for others.
    """

    key: str
    label: str
    model: type
    user_field: str
    icon: str
    kind: str
    duration: bool = False


#: Every table in Meril-V that records who did something. Order is the order
#: the breakdown renders in, grouped by kind.
#:
#: ``OutboundScan`` is attributed by its own ``scanned_by``, not by
#: ``session__operator``: on a session two people work in turn, the operator
#: field names whoever opened it, and crediting them with a colleague's scans
#: is exactly the misattribution this screen exists to avoid.
ACTIVITY_SOURCES: tuple[ActivitySource, ...] = (
    ActivitySource("delivery_dump", "Delivery dumps uploaded", DeliveryDump, "uploaded_by", "upload", "upload"),
    ActivitySource("bt_plan", "Branch-transfer plans", BranchTransferPlan, "created_by", "transfer", "upload"),
    ActivitySource("zva05_dump", "ZVA05 dumps uploaded", Zva05Dump, "created_by", "upload", "upload"),

    ActivitySource("outbound_session", "Outbound sessions", OutboundSession, "operator", "box", "session", duration=True),
    ActivitySource("btscan_session", "BT scanning sessions", BtScanSession, "created_by", "box", "session", duration=True),
    ActivitySource("scan_session", "Barcode-extractor sessions", ScanSession, "created_by", "barcode", "session", duration=True),
    ActivitySource("validation_run", "Stock validations", ValidationRun, "created_by", "clipboard", "session"),
    ActivitySource("consolidation", "Invoice consolidations", ConsolidationRun, "created_by", "sheet", "session"),

    ActivitySource("outbound_scan", "Outbound scans", OutboundScan, "scanned_by", "scan", "scan"),
    ActivitySource("bt_scan", "BT scans", BtScan, "scanned_by", "scan", "scan"),
    ActivitySource("zva05_scan", "ZVA05 scans", Zva05Scan, "scanned_by", "scan", "scan"),
    ActivitySource("inbound_scan", "Inbound receipt scans", BranchTransferReceiptScan, "scanned_by", "scan", "scan"),

    ActivitySource("delivery_finish", "Deliveries closed", FinishedDelivery, "picker", "truck", "close"),
    ActivitySource("bt_receipt", "Branch transfers received", BranchTransferReceipt, "received_by", "inbox", "close"),

    ActivitySource("admin_action", "Master-data & admin actions", ActivityLog, "actor", "key", "admin"),
)

SOURCES_BY_KEY: dict[str, ActivitySource] = {s.key: s for s in ACTIVITY_SOURCES}

#: Windows offered on screen. Kept here so the view, the template's dropdown
#: and the export all agree on what is selectable.
WINDOW_CHOICES: tuple[tuple[int, str], ...] = (
    (7, "Last 7 days"),
    (30, "Last 30 days"),
    (90, "Last 90 days"),
    (365, "Last 12 months"),
)
WINDOW_DEFAULT = 30

#: Ceiling on the merged event feed of one user's detail page. The feed exists
#: to show the shape of a working day, not to be a second activity log — the
#: full record is one click away on the log screen, which paginates.
FEED_CAP = 250

#: Feed rows per page on the drill-down. The whole capped feed used to render
#: in one go, which on a busy user meant 250 rows below the charts and a page
#: several screens long with no way to skip to the end. The cap stays what it
#: was — it is what keeps the feed to fifteen small indexed reads — and the
#: page only shows a slice of it.
FEED_PAGE_SIZE = 25


def resolve_window(raw) -> int:
    """Days for the window, from an untrusted query parameter.

    Anything unparseable, negative or not on the offered list falls back to the
    default rather than being clamped to it: an arbitrary ``?days=4000`` would
    otherwise silently become a 12-month report that the dropdown then renders
    with nothing selected, which reads as a broken page.
    """
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return WINDOW_DEFAULT
    return days if days in {d for d, _label in WINDOW_CHOICES} else WINDOW_DEFAULT


def window_bounds(days: int) -> tuple:
    """``(since, until)`` for a window of ``days``, ending now."""
    until = timezone.now()
    return until - timedelta(days=days), until


def days_of(since, until) -> int:
    """Recover the window size from its bounds, for building links.

    Round-trips ``window_bounds`` so a chart's drill-down link lands on the same
    window the chart was drawn from — arriving at a 30-day page from a 90-day
    chart makes the numbers appear to change under the reader.
    """
    return max(1, round((until - since).total_seconds() / 86400))


# ── Role and identity ────────────────────────────────────────────────────────
def _role_of(group_names: set[str]) -> str:
    """The user's Meril-V tier, or "" — highest wins.

    Mirrors ``roles._current_role`` (most privileged group wins) but takes a
    pre-fetched name set, so listing N users costs no extra query each.
    """
    for role in reversed(ROLE_GROUPS):
        if role in group_names:
            return role
    return ""


def _identity_map(user_ids) -> dict[int, dict]:
    """Identity, role, plants and manager for a set of user ids, in 3 queries.

    Built as one map rather than looked up per row: the leaderboard renders a
    role badge and a plant list for every user, and doing that off the ORM's
    lazy relations is where a 20-row table turns into 60 queries.
    """
    users = User.objects.filter(pk__in=user_ids).prefetch_related("groups")
    plants: dict[int, list[str]] = {}
    for user_id, plant_code in (
        User.objects.filter(pk__in=user_ids)
        .exclude(merilv_plants__isnull=True)
        .values_list("pk", "merilv_plants__plant_id")
    ):
        plants.setdefault(user_id, []).append(plant_code)

    managers: dict[int, str] = {}
    for user_id, first, last, username in (
        User.objects.filter(pk__in=user_ids, merilv_manager_link__isnull=False)
        .values_list(
            "pk",
            "merilv_manager_link__manager__first_name",
            "merilv_manager_link__manager__last_name",
            "merilv_manager_link__manager__username",
        )
    ):
        managers[user_id] = (f"{first} {last}".strip() or username)

    out: dict[int, dict] = {}
    for user in users:
        names = {g.name for g in user.groups.all()}
        out[user.pk] = {
            "id": user.pk,
            "name": user.get_full_name() or user.username,
            "username": user.username,
            "email": user.email,
            "role": _role_of(names) or ("Superuser" if user.is_superuser else ""),
            "is_superuser": user.is_superuser,
            "is_active": user.is_active,
            "last_login": user.last_login,
            "plants": sorted(plants.get(user.pk, [])),
            "manager": managers.get(user.pk, ""),
        }
    return out


def role_holder_ids() -> set[int]:
    """Everyone who *can* use Meril-V, whether or not they have.

    Included in the leaderboard on purpose: "granted access, never touched it"
    is one of the things an admin opens this screen to find, and a query that
    only reads activity tables can never surface it.

    Active superusers count as holders even with no group, because
    ``rbac.is_merilv_admin`` grants them the module outright — leaving them out
    made the "of N with access" tile undercount the people who have the most of
    it. Inactive accounts are excluded: a disabled login is not access, and
    listing every ex-employee would bury the roster this screen is for.
    """
    return set(
        User.objects.filter(is_active=True)
        .filter(Q(groups__name__in=ROLE_GROUPS) | Q(is_superuser=True))
        .values_list("pk", flat=True)
        .distinct()
    )


# ── Grouped reads ────────────────────────────────────────────────────────────
def _daily_rows(source: ActivitySource, since, until, user_ids=None):
    """``(user, day, count, first, last)`` for one source.

    One grouped query. ``TruncDate``/``Min``/``Max`` do the work in the
    database so the per-day window this feeds is computed without any row ever
    crossing the wire.
    """
    queryset = source.model.objects.filter(created_at__gte=since, created_at__lte=until)
    queryset = queryset.exclude(**{f"{source.user_field}__isnull": True})
    if user_ids is not None:
        queryset = queryset.filter(**{f"{source.user_field}__in": list(user_ids)})
    return (
        queryset.annotate(day=TruncDate("created_at"))
        .values(source.user_field, "day")
        .annotate(n=Count("pk"), first=Min("created_at"), last=Max("created_at"))
    )


def _blank_user_stats() -> dict:
    return {
        "total": 0,
        "by_source": {},
        "by_kind": {kind: 0 for kind, _label in KIND_LABELS},
        "days": {},          # date -> {"n": int, "first": dt, "last": dt}
        "day_kinds": {},     # date -> {kind: int}, for the stacked trend chart
        "first_seen": None,
        "last_seen": None,
    }


def _accumulate(stats: dict, source: ActivitySource, row: dict) -> None:
    """Fold one grouped row into a user's running totals."""
    count, day = row["n"], row["day"]
    stats["total"] += count
    stats["by_source"][source.key] = stats["by_source"].get(source.key, 0) + count
    stats["by_kind"][source.kind] += count

    bucket = stats["days"].setdefault(day, {"n": 0, "first": row["first"], "last": row["last"]})
    bucket["n"] += count
    bucket["first"] = min(bucket["first"], row["first"])
    bucket["last"] = max(bucket["last"], row["last"])

    kinds = stats["day_kinds"].setdefault(day, {})
    kinds[source.kind] = kinds.get(source.kind, 0) + count

    if stats["first_seen"] is None or row["first"] < stats["first_seen"]:
        stats["first_seen"] = row["first"]
    if stats["last_seen"] is None or row["last"] > stats["last_seen"]:
        stats["last_seen"] = row["last"]


def _finalise(stats: dict) -> dict:
    """Derive the presentational metrics once a user's raw totals are complete."""
    days = stats["days"]
    active_seconds = sum(
        (bucket["last"] - bucket["first"]).total_seconds() for bucket in days.values()
    )
    busiest_day, busiest_n = None, 0
    for day, bucket in days.items():
        if bucket["n"] > busiest_n:
            busiest_day, busiest_n = day, bucket["n"]

    stats["active_days"] = len(days)
    stats["active_seconds"] = int(active_seconds)
    stats["busiest_day"] = busiest_day
    stats["busiest_day_count"] = busiest_n
    # Per *active* day, not per calendar day in the window: an operator who
    # works two shifts a week should not read as idle for the other five.
    stats["per_active_day"] = round(stats["total"] / len(days), 1) if days else 0
    stats["actions_per_hour"] = (
        round(stats["total"] / (active_seconds / 3600), 1) if active_seconds >= 60 else 0
    )
    return stats


def _continuous_days(day_map: dict, since, until, pad_to_window=False) -> list:
    """Every date from first to last activity, gaps included.

    A run of empty days is the point of a trend line, so they are emitted as
    zeroes rather than skipped — otherwise a fortnight's silence renders as a
    point sitting next to yesterday's and the line lies about the shape.

    ``pad_to_window`` spans the whole reporting window instead of first→last
    activity, which is what the all-users trend wants: "the team did nothing on
    these three days" is information, and starting the axis at the first action
    would hide a quiet opening week.
    """
    if pad_to_window:
        start, end = timezone.localtime(since).date(), timezone.localtime(until).date()
    elif day_map:
        start, end = min(day_map), max(day_map)
    else:
        return []
    out, cursor = [], start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _weekday_series(day_map: dict) -> list[dict]:
    """Actions per weekday — the shift-pattern chart.

    Averaged per occurrence of that weekday, not summed: a 90-day window holds
    ~13 Mondays and ~13 Sundays, but a 30-day window can hold 5 of one and 4 of
    another, and a raw sum would read that calendar accident as a real
    preference for Mondays.
    """
    totals = {index: 0 for index in range(7)}
    seen = {index: 0 for index in range(7)}
    for day, bucket in day_map.items():
        totals[day.weekday()] += bucket["n"]
        seen[day.weekday()] += 1
    return [
        {
            "weekday": WEEKDAYS[index],
            "total": totals[index],
            "avg": round(totals[index] / seen[index], 1) if seen[index] else 0,
            "days": seen[index],
        }
        for index in range(7)
    ]


def _session_seconds(user_ids, since, until) -> dict[int, int]:
    """Summed session duration per user, across the sources that have one.

    ``updated_at - created_at`` is the honest reading of a console session's
    span: both are maintained by the model, and a session's last write is the
    last scan into it.
    """
    totals: dict[int, int] = {}
    span = ExpressionWrapper(F("updated_at") - F("created_at"), output_field=DurationField())
    for source in ACTIVITY_SOURCES:
        if not source.duration:
            continue
        rows = (
            source.model.objects.filter(created_at__gte=since, created_at__lte=until)
            .exclude(**{f"{source.user_field}__isnull": True})
            .filter(**{f"{source.user_field}__in": list(user_ids)})
            .annotate(span=span)
            .values(source.user_field)
            .annotate(total=Sum("span"))
        )
        for row in rows:
            delta = row["total"]
            if delta is not None:
                totals[row[source.user_field]] = (
                    totals.get(row[source.user_field], 0) + int(delta.total_seconds())
                )
    return totals


# ── Public API ───────────────────────────────────────────────────────────────
def leaderboard(days: int) -> dict:
    """Every user's activity over the window, plus module-wide totals.

    Costs one grouped query per source, plus a handful for identity — not one
    per user.
    """
    since, until = window_bounds(days)
    per_user: dict[int, dict] = {}
    # Module-wide day/kind totals, folded out of the very same grouped rows the
    # per-user pass reads — the team trend chart therefore costs no extra query.
    team = _blank_user_stats()

    for source in ACTIVITY_SOURCES:
        for row in _daily_rows(source, since, until):
            user_id = row[source.user_field]
            _accumulate(per_user.setdefault(user_id, _blank_user_stats()), source, row)
            _accumulate(team, source, row)

    # Role holders with no activity are shown too — see role_holder_ids().
    for user_id in role_holder_ids():
        per_user.setdefault(user_id, _blank_user_stats())

    for stats in per_user.values():
        _finalise(stats)

    sessions = _session_seconds(per_user.keys(), since, until) if per_user else {}
    identities = _identity_map(per_user.keys()) if per_user else {}

    rows = []
    for user_id, stats in per_user.items():
        identity = identities.get(user_id)
        if identity is None:
            # The FK is SET_NULL, so a deleted account leaves its work behind
            # with a null actor — those rows are excluded by _daily_rows. A
            # missing identity here would mean the user vanished mid-request.
            continue
        rows.append({**identity, **stats, "session_seconds": sessions.get(user_id, 0)})

    rows.sort(key=lambda r: (-r["total"], r["name"].lower()))

    active = [r for r in rows if r["total"]]
    totals = {
        "users_total": len(rows),
        "users_active": len(active),
        "users_idle": len(rows) - len(active),
        "actions": sum(r["total"] for r in rows),
        "scans": sum(r["by_kind"]["scan"] for r in rows),
        "active_seconds": sum(r["active_seconds"] for r in rows),
        "session_seconds": sum(r["session_seconds"] for r in rows),
    }

    by_kind = [
        {"key": kind, "label": label, "n": sum(r["by_kind"][kind] for r in rows)}
        for kind, label in KIND_LABELS
    ]
    by_role: dict[str, dict] = {}
    for row in rows:
        entry = by_role.setdefault(
            row["role"] or "No role", {"role": row["role"] or "No role", "users": 0, "n": 0}
        )
        entry["users"] += 1
        entry["n"] += row["total"]

    by_role_list = sorted(by_role.values(), key=lambda r: -r["n"])
    return {
        "rows": rows,
        "totals": totals,
        "by_kind": by_kind,
        "by_role": by_role_list,
        "since": since,
        "until": until,
        "charts": _all_users_charts(rows, team, by_kind, by_role_list, since, until),
    }


def _stacked_daily(day_kinds: dict, dates) -> list[dict]:
    """One Highcharts series per activity kind over ``dates``.

    Kinds with nothing in them are dropped rather than emitted as a flat zero
    line — an empty series still claims a legend entry and a colour, which
    makes a two-kind chart look like a five-kind one.
    """
    series = []
    for kind, label in KIND_LABELS:
        values = [day_kinds.get(day, {}).get(kind, 0) for day in dates]
        if any(values):
            series.append({
                "name": label,
                "color": KIND_COLORS[kind],
                "data": values,
            })
    return series


def _all_users_charts(rows, team, by_kind, by_role, since, until) -> dict:
    """JSON-safe series for the all-users dashboard.

    Everything here is primitives — dates already formatted, numbers already
    rounded — because it is serialised into a ``<script type="application/json">``
    block and read by Highcharts. A ``date`` or ``Decimal`` leaking in would
    fail to serialise at render time, which is a 500 on a reporting page.
    """
    dates = _continuous_days(team["days"], since, until, pad_to_window=True)
    ranked = [row for row in rows if row["total"]][:12]
    hourly = _hourly_all(since, until)

    return {
        "has_data": bool(team["total"]),
        "trend": {
            "dates": [day.strftime("%d %b") for day in dates],
            "series": _stacked_daily(team["day_kinds"], dates),
        },
        "top_users": {
            "names": [row["name"] for row in ranked],
            "values": [row["total"] for row in ranked],
            # Full URLs, reversed here rather than pattern-substituted in JS:
            # a template placeholder like ".../0/" has to be string-surgeried
            # client-side, which breaks the moment a real id or a query value
            # happens to be 0.
            "urls": [
                f"{reverse('merilv:user_activity_detail', args=[row['id']])}?days={days_of(since, until)}"
                for row in ranked
            ],
        },
        "mix": [
            {"name": row["label"], "y": row["n"], "color": KIND_COLORS[row["key"]]}
            for row in by_kind if row["n"]
        ],
        "roles": [
            {"name": row["role"], "y": row["n"], "color": CHART_PALETTE[index % len(CHART_PALETTE)]}
            for index, row in enumerate(by_role) if row["n"]
        ],
        "hours": {
            "labels": [f"{row['hour']:02d}" for row in hourly],
            "values": [row["n"] for row in hourly],
        },
        "weekdays": _weekday_series(team["days"]),
        "palette": list(CHART_PALETTE),
    }


def _hourly_all(since, until) -> list[dict]:
    """Actions per hour of day across every user — the team's shift shape."""
    buckets = {hour: 0 for hour in range(24)}
    for source in ACTIVITY_SOURCES:
        rows = (
            source.model.objects.filter(created_at__gte=since, created_at__lte=until)
            .exclude(**{f"{source.user_field}__isnull": True})
            .annotate(hour=ExtractHour("created_at"))
            .values("hour")
            .annotate(n=Count("pk"))
        )
        for row in rows:
            if row["hour"] is not None:
                buckets[int(row["hour"])] += row["n"]
    return [{"hour": hour, "n": buckets[hour]} for hour in range(24)]


def _hourly(user, since, until) -> list[dict]:
    """Actions per hour of day (0-23) for one user, across every source.

    Rendered as the "when does this person work" histogram. Hours come out in
    ``settings.TIME_ZONE`` because ``USE_TZ`` is on, which is what makes a
    warehouse shift pattern legible rather than UTC-shifted.
    """
    buckets = {hour: 0 for hour in range(24)}
    for source in ACTIVITY_SOURCES:
        rows = (
            source.model.objects.filter(created_at__gte=since, created_at__lte=until)
            .filter(**{source.user_field: user})
            .annotate(hour=ExtractHour("created_at"))
            .values("hour")
            .annotate(n=Count("pk"))
        )
        for row in rows:
            if row["hour"] is not None:
                buckets[int(row["hour"])] += row["n"]
    return [{"hour": hour, "n": buckets[hour]} for hour in range(24)]


def _recent_events(user, since, until, cap=FEED_CAP) -> list[dict]:
    """The user's most recent actions across all sources, merged newest-first.

    Each source contributes at most ``cap`` rows before the merge, so the feed
    costs fifteen small indexed reads regardless of how much history exists.
    """
    events: list[dict] = []
    for source in ACTIVITY_SOURCES:
        rows = (
            source.model.objects.filter(created_at__gte=since, created_at__lte=until)
            .filter(**{source.user_field: user})
            .order_by("-created_at")[:cap]
        )
        for row in rows:
            events.append({
                "at": row.created_at,
                "source": source,
                "label": source.label,
                "icon": source.icon,
                "kind": source.kind,
                "what": _describe(source, row),
            })
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:cap]


def _describe(source: ActivitySource, row) -> str:
    """A one-line "what was this" for a feed row.

    Deliberately per-source rather than ``str(row)``: several of these models'
    ``__str__`` return a bare pk ("Session #11"), which tells an auditor
    nothing about which delivery or file was involved.
    """
    if source.key == "admin_action":
        from . import activitylog

        target = f" · {row.target_type} {row.target_id}".rstrip() if row.target_type else ""
        return f"{activitylog.humanise_action(row.action)}{target}"
    if source.key == "delivery_finish":
        return f"Delivery {row.delivery_no}" + (" (partial)" if row.is_partial else "")
    if source.key == "outbound_scan":
        return f"{row.material} · {row.batch}".strip(" ·")
    if source.key in ("bt_scan", "zva05_scan", "inbound_scan"):
        return getattr(row, "material", "") or getattr(row, "raw_scan", "")[:40]
    if source.key == "delivery_dump":
        return row.original_filename or "delivery dump"
    if source.key == "validation_run":
        return row.name or f"run #{row.pk}"
    return str(row)[:80]


def user_detail(user, days: int) -> dict:
    """Everything the per-user drill-down renders.

    Same grouped-read strategy as :func:`leaderboard`, narrowed to one user,
    plus the two things only a single-user page can afford: an hour-of-day
    histogram and a merged event feed.
    """
    since, until = window_bounds(days)
    stats = _blank_user_stats()
    for source in ACTIVITY_SOURCES:
        for row in _daily_rows(source, since, until, user_ids=[user.pk]):
            _accumulate(stats, source, row)
    _finalise(stats)

    stats["session_seconds"] = _session_seconds([user.pk], since, until).get(user.pk, 0)
    identity = _identity_map([user.pk]).get(user.pk, {})

    # Daily series ascending, gaps included: a run of empty days is the point
    # of a timeline, and skipping them would compress a fortnight's silence
    # into a bar sitting next to yesterday's.
    timeline = []
    if stats["days"]:
        cursor = min(stats["days"])
        last_day = max(stats["days"])
        while cursor <= last_day:
            bucket = stats["days"].get(cursor)
            timeline.append({
                "day": cursor,
                "n": bucket["n"] if bucket else 0,
                "first": bucket["first"] if bucket else None,
                "last": bucket["last"] if bucket else None,
                "seconds": int((bucket["last"] - bucket["first"]).total_seconds()) if bucket else 0,
            })
            cursor += timedelta(days=1)

    # Same reasoning as the leaderboard's bars: heights are data, not markup.
    # Scaled against each chart's own peak so a quiet week still shows shape.
    peak_day = max((row["n"] for row in timeline), default=0)
    for row in timeline:
        row["pct"] = round(row["n"] / peak_day * 100, 1) if peak_day else 0
        row["is_peak"] = bool(row["n"]) and row["n"] == peak_day

    hourly = _hourly(user, since, until)
    peak_hour = max((row["n"] for row in hourly), default=0)
    for row in hourly:
        row["pct"] = round(row["n"] / peak_hour * 100, 1) if peak_hour else 0
        row["is_peak"] = bool(row["n"]) and row["n"] == peak_hour

    breakdown = [
        {
            "key": source.key,
            "label": source.label,
            "icon": source.icon,
            "kind": source.kind,
            "n": stats["by_source"].get(source.key, 0),
            "pct": round(stats["by_source"].get(source.key, 0) / stats["total"] * 100, 1)
            if stats["total"] else 0,
        }
        for source in ACTIVITY_SOURCES
    ]

    by_kind = [
        {"key": kind, "label": label, "n": stats["by_kind"][kind]}
        for kind, label in KIND_LABELS
    ]
    # One set of per-user totals serves both the peer ranking and the user
    # picker. They used to be fetched separately, which was fifteen identical
    # aggregations run twice on every drill-down.
    counts = _totals_by_user(since, until)
    peers = _peer_standing(user, counts)
    return {
        "identity": identity,
        "stats": stats,
        "timeline": timeline,
        "breakdown": [b for b in breakdown if b["n"]],
        "breakdown_all": breakdown,
        "by_kind": by_kind,
        "hourly": hourly,
        "weekdays": _weekday_series(stats["days"]),
        "peers": peers,
        "picker": picker_options(counts=counts),
        "events": _recent_events(user, since, until),
        "since": since,
        "until": until,
        "charts": _user_charts(stats, timeline, hourly, by_kind, breakdown, peers),
    }


def _peer_standing(user, counts: dict) -> dict:
    """Where this user sits against everyone else in the same window.

    "Performance" only means something relative, so the drill-down carries a
    rank and the team median rather than leaving an admin to eyeball whether
    4,000 scans is a lot. Median, not mean: one bulk-paste session of 300 scans
    drags an average far enough to make everyone else look idle.

    Takes a precomputed ``counts`` map rather than a window, because the
    drill-down needs the same fifteen aggregations for its user picker — asking
    for them twice cost 15 needless queries per page load.
    """
    active = sorted((n for n in counts.values() if n), reverse=True)
    mine = counts.get(user.pk, 0)

    if not active:
        return {"rank": 0, "of": 0, "median": 0, "top": 0, "vs_median_pct": 0, "mine": mine}

    middle = len(active) // 2
    median = (
        active[middle] if len(active) % 2
        else round((active[middle - 1] + active[middle]) / 2, 1)
    )
    # `active` is already descending, and .index finds the first match, so ties
    # share the better rank — standard competition ranking.
    rank = active.index(mine) + 1 if mine else 0
    return {
        "rank": rank,
        "of": len(active),
        "median": median,
        "top": active[0],
        "mine": mine,
        # Signed percentage against the median, so "+140%" and "-60%" both read
        # at a glance. Guarded because a median of zero cannot be divided by.
        "vs_median_pct": round((mine - median) / median * 100) if median else 0,
    }


def _totals_by_user(since, until) -> dict[int, int]:
    """``{user_id: action count}`` over the window — no day grouping.

    Cheaper than :func:`leaderboard` when only the ranking is wanted: fifteen
    index-only aggregations with no Min/Max and no TruncDate.
    """
    totals: dict[int, int] = {}
    for source in ACTIVITY_SOURCES:
        rows = (
            source.model.objects.filter(created_at__gte=since, created_at__lte=until)
            .exclude(**{f"{source.user_field}__isnull": True})
            .values(source.user_field)
            .annotate(n=Count("pk"))
        )
        for row in rows:
            key = row[source.user_field]
            totals[key] = totals.get(key, 0) + row["n"]
    return totals


def _user_charts(stats, timeline, hourly, by_kind, breakdown, peers) -> dict:
    """JSON-safe series for one user's drill-down. Primitives only — see
    :func:`_all_users_charts` for why."""
    dates = [row["day"] for row in timeline]
    used = [row for row in breakdown if row["n"]]

    # Cumulative actions — the performance trajectory. A flattening line is a
    # user who stopped working, which the per-day bars alone make you infer.
    running, cumulative = 0, []
    for row in timeline:
        running += row["n"]
        cumulative.append(running)

    return {
        "has_data": bool(stats["total"]),
        "trend": {
            "dates": [day.strftime("%d %b") for day in dates],
            "series": _stacked_daily(stats["day_kinds"], dates),
            "cumulative": cumulative,
            # Drawn as a dashed plotLine so a day can be read as above or below
            # this person's own norm without arithmetic.
            "average": round(stats["total"] / len(timeline), 1) if timeline else 0,
        },
        "hours": {
            "labels": [f"{row['hour']:02d}" for row in hourly],
            "values": [row["n"] for row in hourly],
        },
        "mix": [
            {"name": row["label"], "y": row["n"], "color": KIND_COLORS[row["key"]]}
            for row in by_kind if row["n"]
        ],
        "sources": {
            "names": [row["label"] for row in used],
            "values": [row["n"] for row in used],
            "colors": [KIND_COLORS[row["kind"]] for row in used],
        },
        "weekdays": _weekday_series(stats["days"]),
        "peers": peers,
        "minutes_per_day": [
            {"date": row["day"].strftime("%d %b"), "minutes": round(row["seconds"] / 60)}
            for row in timeline
        ],
    }


def picker_options(days: int = WINDOW_DEFAULT, counts: dict | None = None) -> list[dict]:
    """Everyone the user-picker dropdown offers, busiest first.

    Carries each person's total so the dropdown itself says who is worth
    looking at — a bare alphabetical list of forty names makes an admin open
    them one at a time to find the one that matters.

    Pass ``counts`` when the caller already has per-user totals for the same
    window (``user_detail`` does, for the peer ranking) to skip re-running
    fifteen aggregations.
    """
    if counts is None:
        counts = _totals_by_user(*window_bounds(days))
    ids = set(counts) | role_holder_ids()
    identities = _identity_map(ids)
    options = [
        {
            "id": user_id,
            "name": identity["name"],
            "role": identity["role"],
            "total": counts.get(user_id, 0),
        }
        for user_id, identity in identities.items()
    ]
    options.sort(key=lambda option: (-option["total"], option["name"].lower()))
    return options


def humanise_seconds(seconds) -> str:
    """``9315`` -> ``2h 35m``. Blank for nothing, so a table shows a dash."""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m" if minutes else "<1m"
