"""Per-user activity analytics behind VendorLink Admin → Insights.

Answers "what has each person actually done in VendorLink, and when" by reading
the twelve tables that carry a user FK. VendorLink has no audit-log table at
all, so there is no shortcut here — the activity *is* the business rows, and
this module is the only place that stitches them into one timeline.

Three things this module is careful about:

**The timestamp is per source, not a convention.** VendorLink's models do not
agree on a name, and worse, several carry more than one date with genuinely
different meanings. ``VendorRegistration`` has ``submitted_at`` (when the vendor
applied) and ``reviewed_at`` (when an admin acted) — and the user FK is
``reviewed_by``. Reading ``submitted_at`` there would credit an admin's review
to a date weeks earlier, which is not a rounding error, it is the wrong day.
Each :class:`ActivitySource` therefore names its own field, and rows whose
timestamp is NULL (a registration nobody has reviewed yet) are excluded rather
than bucketed at some default.

**Cost.** Every metric is one grouped query per source, keyed on ``(user, day)``
— never a row per event.

**No invented money.** ``VendorBid`` carries seven cost components across
several currencies with no FX table anywhere in the module, so a single "total
spend" figure would be a fiction. Bids are counted, and broken down by currency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate
from django.urls import reverse
from django.utils import timezone

from .models import (
    CapabilityAssessment,
    CountrySearchEvent,
    Vendor,
    VendorBid,
    VendorCommercial,
    VendorEvaluation,
    VendorKPIRecord,
    VendorRegistration,
    VendorRequest,
    VendorScore,
    VendorScorecard,
    VendorSearchEvent,
)
from .rbac import ADMIN_GROUPS, USER_GROUPS, VENDOR_GROUPS, _MERIL_DOMAINS

#: The five kinds of work, in the order a vendor relationship actually
#: progresses: find someone, bring them in, price them, assess them, then
#: score how they performed.
KIND_LABELS: tuple[tuple[str, str], ...] = (
    ("search", "Searching & discovery"),
    ("onboard", "Onboarding"),
    ("commercial", "Commercial & bids"),
    ("assess", "Assessment"),
    ("perform", "Performance scoring"),
)

#: Literal hex per kind. Highcharts takes colours as data and cannot read a CSS
#: custom property, so these are stated here and mirrored by the
#: ``.vla-dot--*`` rules in vendorlink-ui.css. Kept beside KIND_LABELS so a new
#: kind is obviously missing a colour.
KIND_COLORS: dict[str, str] = {
    "search": "#00B5E2",
    "onboard": "#0076C8",
    "commercial": "#8B5CF6",
    "assess": "#F59E0B",
    "perform": "#15803D",
}

CHART_PALETTE: tuple[str, ...] = (
    "#0076C8", "#00B5E2", "#8B5CF6", "#00A891", "#F59E0B",
    "#15803D", "#EF4444", "#6366F1", "#3B82F6", "#F97316",
)

#: Monday-first, matching ``ExtractIsoWeekDay`` (Mon=1 … Sun=7) so the heatmap
#: rows need no remapping.
WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

WINDOW_CHOICES: tuple[tuple[int, str], ...] = (
    (7, "Last 7 days"),
    (30, "Last 30 days"),
    (90, "Last 90 days"),
    (365, "Last 12 months"),
)
WINDOW_DEFAULT = 30

#: Ceiling on the merged event feed of one user's page.
FEED_CAP = 200


@dataclass(frozen=True)
class ActivitySource:
    """One user-attributed table, and how to read a *who* and a *when* from it.

    ``time_field`` is mandatory and per-source on purpose — see the module
    docstring for why assuming ``created_at`` would silently misdate reviews.
    """

    key: str
    label: str
    model: type
    user_field: str
    time_field: str
    icon: str
    kind: str
    #: FKs that :func:`describe` reads, so the feed can ``select_related`` them.
    #: Without this every feed row triggered a lazy fetch per FK touched — an
    #: N+1 that grew with the feed, adding ~35 queries on a 45-event page and
    #: several hundred at the 200-event cap.
    related: tuple = ()


#: Every VendorLink table that records who did something, paired with the
#: timestamp that describes *that* action.
ACTIVITY_SOURCES: tuple[ActivitySource, ...] = (
    ActivitySource("vendor_search", "Vendor searches", VendorSearchEvent, "user", "created_at", "fa-magnifying-glass", "search", ("vendor",)),
    ActivitySource("country_search", "Country searches", CountrySearchEvent, "user", "created_at", "fa-earth-asia", "search"),

    ActivitySource("vendor_created", "Vendors added", Vendor, "created_by", "created_at", "fa-truck-field", "onboard"),
    # reviewed_by + reviewed_at, never submitted_at — see the module docstring.
    ActivitySource("registration_review", "Registrations reviewed", VendorRegistration, "reviewed_by", "reviewed_at", "fa-user-check", "onboard"),

    ActivitySource("request_raised", "RFIs raised", VendorRequest, "raised_by", "created_at", "fa-paper-plane", "commercial"),
    ActivitySource("bid_submitted", "Bids submitted", VendorBid, "submitted_by", "submitted_at", "fa-gavel", "commercial", ("vendor_request", "vendor_registration")),
    ActivitySource("commercial_added", "Commercials recorded", VendorCommercial, "created_by", "created_at", "fa-file-invoice-dollar", "commercial", ("vendor",)),

    ActivitySource("assessment_invited", "Assessments invited", CapabilityAssessment, "invited_by", "created_at", "fa-clipboard-check", "assess", ("vendor",)),
    ActivitySource("evaluation_run", "AI evaluations run", VendorEvaluation, "triggered_by", "triggered_at", "fa-robot", "assess", ("vendor_request",)),

    ActivitySource("score_given", "Vendor scores", VendorScore, "scored_by", "created_at", "fa-star", "perform", ("vendor",)),
    ActivitySource("scorecard_filled", "Scorecards completed", VendorScorecard, "scored_by", "scored_at", "fa-square-poll-vertical", "perform", ("vendor",)),
    ActivitySource("kpi_recorded", "KPI records", VendorKPIRecord, "recorded_by", "created_at", "fa-chart-line", "perform", ("vendor",)),
)

SOURCES_BY_KEY: dict[str, ActivitySource] = {s.key: s for s in ACTIVITY_SOURCES}

_VL_GROUPS = ADMIN_GROUPS | USER_GROUPS | VENDOR_GROUPS


# ── Window ───────────────────────────────────────────────────────────────────
def resolve_window(raw) -> int:
    """Days for the window, from an untrusted query parameter.

    Falls back rather than clamps: an arbitrary ``?days=4000`` clamped to the
    maximum would render a dropdown with nothing selected, which reads as a
    broken page rather than a rejected input.
    """
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return WINDOW_DEFAULT
    return days if days in {d for d, _label in WINDOW_CHOICES} else WINDOW_DEFAULT


def window_bounds(days: int) -> tuple:
    until = timezone.now()
    return until - timedelta(days=days), until


def days_of(since, until) -> int:
    """Recover the window size from its bounds, for building drill-down links
    that land on the same window the chart was drawn from."""
    return max(1, round((until - since).total_seconds() / 86400))


# ── Identity ─────────────────────────────────────────────────────────────────
def _vendor_emails() -> set[str]:
    return {
        email.lower()
        for email in Vendor.objects.exclude(email="").values_list("email", flat=True)
        if email
    }


def classify_user(user, vendor_emails=None) -> str:
    """A short role label for one account.

    The single definition of "what kind of user is this" in VendorLink. The
    admin console's user list delegates here rather than keeping its own copy —
    two classifiers that disagree would put the same person in different buckets
    on two screens of the same console.
    """
    if vendor_emails is None:
        vendor_emails = _vendor_emails()
    if user.is_superuser:
        return "Superuser"
    names = {group.name for group in user.groups.all()}
    email = (user.email or "").lower()
    if names & VENDOR_GROUPS or (email and email in vendor_emails):
        return "Vendor Portal"
    if names & ADMIN_GROUPS:
        return "Admin"
    if user.is_staff and any(email.endswith(domain) for domain in _MERIL_DOMAINS):
        return "Meril Staff"
    return "User"


ROLE_ORDER: tuple[str, ...] = ("Superuser", "Admin", "Meril Staff", "User", "Vendor Portal")


def access_holder_ids() -> set[int]:
    """Everyone who *can* use VendorLink, whether or not they have.

    "Granted access, never touched it" is one of the things an admin opens this
    screen to find, and a query over activity tables alone could never show it.
    Inactive accounts are excluded — a disabled login is not access, and listing
    every ex-employee would bury the roster.
    """
    meril = Q()
    for domain in _MERIL_DOMAINS:
        meril |= Q(email__iendswith=domain)
    return set(
        User.objects.filter(is_active=True)
        .filter(Q(groups__name__in=_VL_GROUPS) | Q(is_superuser=True) | (Q(is_staff=True) & meril))
        .values_list("pk", flat=True)
        .distinct()
    )


def _identity_map(user_ids) -> dict[int, dict]:
    """Identity + role for a set of user ids, in two queries.

    Built as one map rather than resolved per row: the roster renders a role
    chip for every user, and doing that off the ORM's lazy relations is where a
    40-row table turns into 80 queries.
    """
    vendor_emails = _vendor_emails()
    out: dict[int, dict] = {}
    for user in User.objects.filter(pk__in=user_ids).prefetch_related("groups"):
        out[user.pk] = {
            "id": user.pk,
            "name": user.get_full_name() or user.username,
            "username": user.username,
            "email": user.email,
            "role": classify_user(user, vendor_emails),
            "is_active": user.is_active,
            "date_joined": user.date_joined,
            "last_login": user.last_login,
        }
    return out


# ── Grouped reads ────────────────────────────────────────────────────────────
def _base_queryset(source: ActivitySource, since, until):
    """Rows of one source inside the window, with a real actor and a real date.

    Both exclusions matter. A NULL actor is unattributable (the FKs are
    SET_NULL, so a deleted account leaves its work behind). A NULL timestamp
    means the action has not happened yet — an unreviewed registration has a
    ``reviewed_at`` of NULL — and counting it would report work nobody did.
    """
    time_field = source.time_field
    return (
        source.model.objects
        .filter(**{f"{time_field}__gte": since, f"{time_field}__lte": until})
        .exclude(**{f"{source.user_field}__isnull": True})
        .exclude(**{f"{time_field}__isnull": True})
    )


def _daily_rows(source: ActivitySource, since, until, user_ids=None):
    """``(user, day, count, first, last)`` for one source — one grouped query."""
    queryset = _base_queryset(source, since, until)
    if user_ids is not None:
        queryset = queryset.filter(**{f"{source.user_field}__in": list(user_ids)})
    return (
        queryset.annotate(day=TruncDate(source.time_field))
        .values(source.user_field, "day")
        .annotate(
            n=Count("pk"),
            first=Min(source.time_field),
            last=Max(source.time_field),
        )
    )


def _blank_stats() -> dict:
    return {
        "total": 0,
        "by_source": {},
        "by_kind": {kind: 0 for kind, _label in KIND_LABELS},
        "days": {},
        "day_kinds": {},
        "first_seen": None,
        "last_seen": None,
    }


def _accumulate(stats: dict, source: ActivitySource, row: dict) -> None:
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
    # Per *active* day, not per calendar day: someone who works this module two
    # days a week should not read as idle for the other five.
    stats["per_active_day"] = round(stats["total"] / len(days), 1) if days else 0
    return stats


# ── Heatmap ──────────────────────────────────────────────────────────────────
#: Number of shaded steps above "empty" in the heatmap.
HEAT_LEVELS = 5

#: Upper bound on the legend's inverse scan. `peak` is actions in one hour, so
#: it is small in practice; this only stops a pathological value turning legend
#: rendering into a long loop.
_HEAT_SCALE_SCAN_CAP = 5000


def _heat_level(value: int, peak: int) -> int:
    """``value`` on a 0-5 scale against ``peak``.

    Level 0 is reserved for genuinely zero. Any non-zero count gets at least
    level 1, because "one action" and "no actions" are different facts and
    rounding the first down to an empty cell hides someone working a quiet
    hour.
    """
    if not value or not peak:
        return 0
    return max(1, min(HEAT_LEVELS, round(value / peak * HEAT_LEVELS)))


def _heat_scale(peak: int) -> list[dict]:
    """What action count each shaded step represents.

    The inverse of :func:`_heat_level`, so the legend can be labelled with real
    numbers instead of "Less ... More". A reader could otherwise not tell
    whether the dark corner meant four actions or four hundred.

    Computed by asking `_heat_level` itself rather than re-deriving its
    arithmetic. That matters: the boundaries fall on a rounded half-step, and
    with a small peak some levels have NO counts at all — at peak 4, counts
    1/2/3/4 land on levels 1/2/4/5 and level 3 is empty. A formula that assumed
    every level was occupied labelled that empty step "3", which is a number the
    grid can never show. Asking the function cannot drift from it.

    The scan is bounded by ``peak``, which is actions in a single hour, so it is
    small in practice; the cap is there for safety, not because it is expected.
    """
    if not peak:
        return []
    counts_by_level: dict[int, list[int]] = {}
    for count in range(1, min(peak, _HEAT_SCALE_SCAN_CAP) + 1):
        counts_by_level.setdefault(_heat_level(count, peak), []).append(count)

    steps = []
    for level in range(1, HEAT_LEVELS + 1):
        counts = counts_by_level.get(level)
        if not counts:
            # A step no count can reach. Shown as a swatch with no number
            # rather than omitted, so the ramp still reads as five ordered
            # steps and nobody wonders why a colour is missing.
            steps.append({"level": level, "low": None, "high": None, "label": ""})
            continue
        low, high = counts[0], counts[-1]
        steps.append({
            "level": level,
            "low": low,
            "high": high,
            "label": str(low) if low == high else f"{low}–{high}",
        })
    return steps


def heatmap(since, until, user=None) -> dict:
    """Weekday x hour activity matrix — 7 rows, 24 columns.

    The client asked for a heatmap and this is the cell that earns one: a
    weekday/hour matrix shows *shift shape* — Monday mornings heavy, Friday
    afternoons dead — which neither a daily trend nor an hourly histogram can
    show, because each collapses one of the two axes the pattern lives in.

    Rendered as a CSS grid rather than a Highcharts series: the vendored
    Highcharts 12.1.2 bundle registers only area/areaspline/bar/column/line/
    pie/scatter/spline — there is no heatmap module and no colour axis, so
    ``type: 'heatmap'`` would draw nothing at all.

    Hours come out in ``settings.TIME_ZONE`` because ``USE_TZ`` is on, which is
    what makes a real shift legible instead of UTC-shifted.
    """
    grid = [[0] * 24 for _ in range(7)]
    for source in ACTIVITY_SOURCES:
        queryset = _base_queryset(source, since, until)
        if user is not None:
            queryset = queryset.filter(**{source.user_field: user})
        rows = (
            queryset.annotate(
                weekday=ExtractIsoWeekDay(source.time_field),
                hour=ExtractHour(source.time_field),
            )
            .values("weekday", "hour")
            .annotate(n=Count("pk"))
        )
        for row in rows:
            if row["weekday"] is None or row["hour"] is None:
                continue
            # ExtractIsoWeekDay is 1-based Monday..Sunday.
            grid[int(row["weekday"]) - 1][int(row["hour"])] += row["n"]

    peak = max((value for week in grid for value in week), default=0)
    # Returned as rows of cells, not one flat list of 168: a Django template
    # cannot chunk a list into sevens, and doing it here keeps the markup a
    # plain nested loop.
    rows = [
        {
            "weekday": WEEKDAYS[day_index],
            "total": sum(week),
            "cells": [
                {
                    "hour": hour,
                    "n": value,
                    # A 0-5 bucket, not a raw ratio: the cell colour is then a
                    # CSS class rather than an inline rgba() computed here,
                    # which keeps the palette in the stylesheet. Scaled against
                    # the matrix peak so a quiet week still shows its own shape
                    # instead of rendering uniformly blank.
                    "level": _heat_level(value, peak),
                }
                for hour, value in enumerate(week)
            ],
        }
        for day_index, week in enumerate(grid)
    ]
    return {
        "rows": rows,
        "peak": peak,
        "total": sum(value for week in grid for value in week),
        "hours": list(range(24)),
        "levels": list(range(HEAT_LEVELS + 1)),
        # The count each shaded step stands for, so the legend can be labelled
        # with magnitudes rather than just a direction.
        "scale": _heat_scale(peak),
        "by_weekday": [{"weekday": row["weekday"], "n": row["total"]} for row in rows],
        # The busiest cell, named — a heatmap's headline finding, which
        # otherwise makes the reader hunt for the darkest square.
        "busiest": _busiest_cell(rows),
    }


def _busiest_cell(rows) -> dict:
    """The single busiest weekday/hour, or empty when there is no activity."""
    best = {"weekday": "", "hour": None, "n": 0}
    for row in rows:
        for cell in row["cells"]:
            if cell["n"] > best["n"]:
                best = {"weekday": row["weekday"], "hour": cell["hour"], "n": cell["n"]}
    return best


# ── Series helpers ───────────────────────────────────────────────────────────
def _continuous_days(day_map, since, until, pad_to_window=False) -> list:
    """Every date first→last (or across the whole window), gaps included.

    A run of empty days is the point of a trend line; skipping them would put a
    fortnight's silence next to yesterday and make the line lie about the shape.
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


def _stacked_daily(day_kinds, dates) -> list[dict]:
    """One Highcharts series per kind. Empty kinds are dropped — a flat zero
    series still claims a legend entry and a colour."""
    series = []
    for kind, label in KIND_LABELS:
        values = [day_kinds.get(day, {}).get(kind, 0) for day in dates]
        if any(values):
            series.append({"name": label, "color": KIND_COLORS[kind], "data": values})
    return series


def _weekday_series(day_map) -> list[dict]:
    """Per-weekday average, not sum: a 30-day window can hold five Mondays and
    four Sundays, and a raw total reads that calendar accident as a preference."""
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


def _totals_by_user(since, until) -> dict[int, int]:
    """``{user_id: action count}`` — twelve index-only aggregations, no day
    grouping. Cheaper than :func:`leaderboard` when only a ranking is wanted."""
    totals: dict[int, int] = {}
    for source in ACTIVITY_SOURCES:
        rows = (
            _base_queryset(source, since, until)
            .values(source.user_field)
            .annotate(n=Count("pk"))
        )
        for row in rows:
            key = row[source.user_field]
            totals[key] = totals.get(key, 0) + row["n"]
    return totals


# ── Business insights ────────────────────────────────────────────────────────
def business_insights(since, until) -> dict:
    """Module-wide VendorLink facts, independent of who did them.

    Deliberately no single "total spend": ``VendorBid`` carries seven cost
    components across several currencies and this module has no FX table, so one
    summed figure would be a fiction. Bids are counted and split by currency
    instead, which is a number that means what it says.
    """
    registrations = dict(
        VendorRegistration.objects.values_list("status")
        .annotate(n=Count("pk"))
        .values_list("status", "n")
    )
    by_currency = list(
        VendorBid.objects.filter(submitted_at__gte=since, submitted_at__lte=until)
        .values("currency")
        .annotate(n=Count("pk"))
        .order_by("-n")
    )
    top_countries = list(
        Vendor.objects.exclude(country="")
        .values("country")
        .annotate(n=Count("pk"))
        .order_by("-n")[:10]
    )
    return {
        "vendors_total": Vendor.objects.count(),
        "vendors_new": Vendor.objects.filter(created_at__gte=since, created_at__lte=until).count(),
        "requests": VendorRequest.objects.filter(created_at__gte=since, created_at__lte=until).count(),
        "bids": VendorBid.objects.filter(submitted_at__gte=since, submitted_at__lte=until).count(),
        "registrations_pending": registrations.get(VendorRegistration.STATUS_PENDING, 0),
        "assessments": CapabilityAssessment.objects.filter(
            created_at__gte=since, created_at__lte=until
        ).count(),
        "by_currency": by_currency,
        "top_countries": top_countries,
        "registrations_by_status": [
            {"status": status, "n": count} for status, count in registrations.items()
        ],
    }


# ── Public API ───────────────────────────────────────────────────────────────
def leaderboard(days: int) -> dict:
    """Every user's VendorLink activity over the window, plus module totals."""
    since, until = window_bounds(days)
    per_user: dict[int, dict] = {}
    team = _blank_stats()

    for source in ACTIVITY_SOURCES:
        for row in _daily_rows(source, since, until):
            user_id = row[source.user_field]
            _accumulate(per_user.setdefault(user_id, _blank_stats()), source, row)
            _accumulate(team, source, row)

    for user_id in access_holder_ids():
        per_user.setdefault(user_id, _blank_stats())

    for stats in per_user.values():
        _finalise(stats)
    _finalise(team)

    identities = _identity_map(per_user.keys()) if per_user else {}
    rows = []
    for user_id, stats in per_user.items():
        identity = identities.get(user_id)
        if identity is None:
            continue
        rows.append({**identity, **stats})
    rows.sort(key=lambda row: (-row["total"], row["name"].lower()))

    active = [row for row in rows if row["total"]]
    totals = {
        "users_total": len(rows),
        "users_active": len(active),
        "users_idle": len(rows) - len(active),
        "actions": team["total"],
        "active_days": team["active_days"],
    }

    by_kind = [
        {"key": kind, "label": label, "n": team["by_kind"][kind]}
        for kind, label in KIND_LABELS
    ]
    by_role: dict[str, dict] = {}
    for row in rows:
        entry = by_role.setdefault(row["role"], {"role": row["role"], "users": 0, "n": 0})
        entry["users"] += 1
        entry["n"] += row["total"]
    by_role_list = sorted(
        by_role.values(),
        key=lambda entry: ROLE_ORDER.index(entry["role"]) if entry["role"] in ROLE_ORDER else 99,
    )

    grid = heatmap(since, until)
    insights = business_insights(since, until)
    return {
        "rows": rows,
        "totals": totals,
        "by_kind": by_kind,
        "by_role": by_role_list,
        "heat": grid,
        "insights": insights,
        "since": since,
        "until": until,
        "charts": _team_charts(rows, team, by_kind, by_role_list, grid, insights, since, until),
    }


def _team_charts(rows, team, by_kind, by_role, grid, insights, since, until) -> dict:
    """JSON-safe series for the all-users dashboard.

    Primitives only — this is serialised into a ``json_script`` block, and a
    date or Decimal leaking in is a 500 at render time, not a wrong pixel.
    """
    dates = _continuous_days(team["days"], since, until, pad_to_window=True)
    ranked = [row for row in rows if row["total"]][:12]
    window = days_of(since, until)

    return {
        "has_data": bool(team["total"]),
        "trend": {
            "dates": [day.strftime("%d %b") for day in dates],
            "series": _stacked_daily(team["day_kinds"], dates),
        },
        "top_users": {
            "names": [row["name"] for row in ranked],
            "values": [row["total"] for row in ranked],
            # Reversed server-side: a "/0/" placeholder substituted in JS breaks
            # the moment a real id or query value happens to be 0.
            "urls": [
                f"{reverse('vendorlink:admin_insights_user', args=[row['id']])}?days={window}"
                for row in ranked
            ],
        },
        "mix": [
            {"name": row["label"], "y": row["n"], "color": KIND_COLORS[row["key"]]}
            for row in by_kind if row["n"]
        ],
        "roles": [
            {"name": row["role"], "y": row["n"],
             "color": CHART_PALETTE[index % len(CHART_PALETTE)]}
            for index, row in enumerate(by_role) if row["n"]
        ],
        "weekdays": _weekday_series(team["days"]),
        "heat_by_weekday": grid["by_weekday"],
        # Business context alongside the people context — the admin asking "who
        # is doing what" usually also wants "and is the pipeline moving".
        "countries": {
            "names": [row["country"] for row in insights["top_countries"]],
            "values": [row["n"] for row in insights["top_countries"]],
        },
        "currencies": {
            "names": [row["currency"] for row in insights["by_currency"]],
            "values": [row["n"] for row in insights["by_currency"]],
        },
    }


def user_detail(user, days: int) -> dict:
    """One user's end-to-end VendorLink flow."""
    since, until = window_bounds(days)
    stats = _blank_stats()
    for source in ACTIVITY_SOURCES:
        for row in _daily_rows(source, since, until, user_ids=[user.pk]):
            _accumulate(stats, source, row)
    _finalise(stats)

    identity = _identity_map([user.pk]).get(user.pk, {})

    timeline = []
    for day in _continuous_days(stats["days"], since, until):
        bucket = stats["days"].get(day)
        timeline.append({
            "day": day,
            "n": bucket["n"] if bucket else 0,
            "seconds": int((bucket["last"] - bucket["first"]).total_seconds()) if bucket else 0,
        })

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
    # picker — asking for them twice cost twelve needless aggregations.
    counts = _totals_by_user(since, until)
    grid = heatmap(since, until, user=user)

    return {
        "identity": identity,
        "stats": stats,
        "timeline": timeline,
        "breakdown": [row for row in breakdown if row["n"]],
        "breakdown_all": breakdown,
        "by_kind": by_kind,
        "weekdays": _weekday_series(stats["days"]),
        "peers": _peer_standing(user, counts),
        "picker": picker_options(counts=counts),
        "heat": grid,
        "events": recent_events(user, since, until),
        "since": since,
        "until": until,
        "charts": _user_charts(stats, timeline, by_kind, breakdown, grid),
    }


def _peer_standing(user, counts: dict) -> dict:
    """Rank and team median. Performance only means something relative.

    Median, not mean: one bulk import of 200 vendors drags an average far
    enough to make everyone else look idle.
    """
    active = sorted((n for n in counts.values() if n), reverse=True)
    mine = counts.get(user.pk, 0)
    if not active:
        return {"rank": 0, "of": 0, "median": 0, "top": 0, "mine": mine, "vs_median_pct": 0}

    middle = len(active) // 2
    median = (
        active[middle] if len(active) % 2
        else round((active[middle - 1] + active[middle]) / 2, 1)
    )
    return {
        # `active` is descending and .index finds the first match, so ties share
        # the better rank — standard competition ranking.
        "rank": active.index(mine) + 1 if mine else 0,
        "of": len(active),
        "median": median,
        "top": active[0],
        "mine": mine,
        "vs_median_pct": round((mine - median) / median * 100) if median else 0,
    }


def _user_charts(stats, timeline, by_kind, breakdown, grid) -> dict:
    dates = [row["day"] for row in timeline]
    used = [row for row in breakdown if row["n"]]
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
            "average": round(stats["total"] / len(timeline), 1) if timeline else 0,
        },
        "mix": [
            {"name": row["label"], "y": row["n"], "color": KIND_COLORS[row["key"]]}
            for row in by_kind if row["n"]
        ],
        "sources": {
            "names": [row["label"] for row in used],
            "values": [row["n"] for row in used],
        },
        "weekdays": _weekday_series(stats["days"]),
        "heat_by_weekday": grid["by_weekday"],
    }


def picker_options(days: int = WINDOW_DEFAULT, counts: dict | None = None) -> list[dict]:
    """Everyone the user-picker offers, busiest first with totals inline.

    A bare alphabetical list of forty names makes an admin open them one at a
    time to find the one that matters. Pass ``counts`` when the caller already
    has per-user totals for the same window.
    """
    if counts is None:
        counts = _totals_by_user(*window_bounds(days))
    identities = _identity_map(set(counts) | access_holder_ids())
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


def recent_events(user, since, until, cap=FEED_CAP) -> list[dict]:
    """The user's most recent actions across all sources, merged newest-first.

    Each source contributes at most ``cap`` rows before the merge, so the feed
    costs twelve small indexed reads regardless of how much history exists.
    """
    events = []
    for source in ACTIVITY_SOURCES:
        queryset = _base_queryset(source, since, until).filter(**{source.user_field: user})
        if source.related:
            # Joins what describe() will read, instead of letting each row
            # trigger its own lazy FK fetch.
            queryset = queryset.select_related(*source.related)
        rows = queryset.order_by(f"-{source.time_field}")[:cap]
        for row in rows:
            events.append({
                "at": getattr(row, source.time_field),
                "label": source.label,
                "icon": source.icon,
                "kind": source.kind,
                "what": describe(source, row),
            })
    events.sort(key=lambda event: event["at"], reverse=True)
    return events[:cap]


def describe(source: ActivitySource, row) -> str:
    """A one-line "what was this" for a feed row.

    Per-source rather than ``str(row)``: several of these models' ``__str__``
    lead with a timestamp or a bare pk, which tells an auditor nothing about
    which vendor or request was involved.
    """
    vendor = getattr(row, "vendor", None)
    if source.key == "vendor_search" and vendor is not None:
        return vendor.vendor_name
    if source.key == "country_search":
        return getattr(row, "country", "") or "country search"
    if source.key == "vendor_created":
        return row.vendor_name
    if source.key == "registration_review":
        # VendorRegistration's own label is vendor_name, not company_name — the
        # latter belongs to CapabilityAssessment.
        return f"{row.vendor_name} — {row.get_status_display()}"
    if source.key == "request_raised":
        # RFIs are labelled `subject`; there is no `title` on this model.
        return row.subject or f"RFI #{row.pk}"
    if source.key == "bid_submitted":
        # A bid has no `vendor` FK — it links to the RFI and to the
        # `vendor_registration` the bidder signed up through, so the bidder's
        # name comes from there. Reading `row.vendor` returned None and every
        # feed line read "— → subject", naming the RFI twice and the bidder
        # never.
        request = getattr(row, "vendor_request", None)
        registration = getattr(row, "vendor_registration", None)
        who = registration.vendor_name if registration else ""
        subject = request.subject if request else "RFI"
        return f"{who} → {subject}" if who else subject
    if source.key == "commercial_added" and vendor is not None:
        return vendor.vendor_name
    if source.key == "assessment_invited":
        # An invite sent before the vendor filled anything in has a blank
        # company_name and no vendor FK yet — the invited address is all that
        # identifies it, and is far more use than the word "assessment".
        return (
            getattr(row, "company_name", "")
            or (vendor.vendor_name if vendor else "")
            or getattr(row, "vendor_email", "")
            or f"assessment #{row.pk}"
        )
    if source.key == "evaluation_run":
        request = getattr(row, "vendor_request", None)
        return request.subject if request else f"evaluation #{row.pk}"
    if vendor is not None:
        return vendor.vendor_name
    return str(row)[:80]


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
