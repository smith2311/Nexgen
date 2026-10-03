"""Tier 3 redesign screens — the core master data surfaces.

Five screens: the launcher, Vendor Master, Commercial Database, Vendor
Scorecard and the KPI Dashboard.

These are redesigns of screens that already exist and work, so they are served
on new routes beside the originals rather than swapped in place. Two reasons:
the originals are covered by tests that assert their current markup, and a
half-migrated module is easier to reason about when both versions are
reachable. Once Tier 4 lands, the old routes point here and .vlx is deleted —
the plan documented at the top of vendorlink-tokens.css.

Query work is reused from the modules that own it (performance.py for KPI and
scorecard maths, commercial.py for rate-card filtering) so the redesign cannot
drift from the numbers the current screens report.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from .capabilities import active_codes, adoption_rows, flags_for
from .constants import KPI_METRIC_FIELDS, SCORECARD_ATTRIBUTES
from .models import (
    CapabilityAssessment, Vendor, VendorBid, VendorCommercial, VendorEvaluation,
    VendorKPIRecord, VendorRegistration, VendorRequest, VendorScorecard,
)
from .rbac import can_access_vendorlink, can_edit_vendorlink, is_vendorlink_admin
from .utils import (
    canonical_countries, canonical_country, continent_choices,
    continent_country_map_json, continent_name, continent_of,
    filter_countries_by_continent,
)

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")


def _ctx(request, **extra):
    base = {
        "is_vm_admin": is_vendorlink_admin(request.user),
        "can_edit": can_edit_vendorlink(request.user),
    }
    # Every screen with a country picker or country filter needs this, and the
    # ones that forgot it silently fell back to the flat 252-country list — the
    # cascade JS treats a missing map as "no narrowing". Put on the shared
    # context so a new country screen gets it by default rather than by
    # remembering.
    base["continent_choices"] = continent_choices()
    base["continent_country_map"] = continent_country_map_json()
    base.update(extra)
    return base


# ── 3.1 — Dashboard / Launcher ───────────────────────────────────────────────
@login_required
@access_required
def dashboard(request):
    """Seven tiles, a quick-action rail, and the two panels that matter:
    what happened recently, and what is waiting on someone."""
    today = timezone.localdate()
    soon = today + timedelta(days=VendorCommercial.EXPIRY_WARNING_DAYS)

    cards = list(VendorCommercial.objects.select_related("vendor"))
    expiring = [c for c in cards if c.expiry_state == "soon"]
    expired = [c for c in cards if c.expiry_state == "expired"]

    kpis = {
        "vendors": Vendor.objects.filter(is_active=True).count(),
        "pending_regs": VendorRegistration.objects.filter(
            status=VendorRegistration.STATUS_PENDING).count(),
        "open_requests": sum(
            1 for r in VendorRequest.objects.all() if r.bid_window_status == "open"),
        "bids": VendorBid.objects.count(),
        "expiring": len(expiring),
        # Submitted and under-review both sit in someone's queue; invited
        # and draft do not, so they are not "pending" for a reviewer.
        "pending_assessments": CapabilityAssessment.objects.filter(
            status__in=[CapabilityAssessment.STATUS_SUBMITTED,
                        CapabilityAssessment.STATUS_UNDER_REVIEW]).count(),
        "evaluations": VendorEvaluation.objects.filter(status="completed").count(),
    }

    # "Needs attention" is deliberately a list of things a person must act on,
    # not a second copy of the tiles — a count nobody can click is a statistic,
    # not a queue.
    attention = []
    for card in sorted(expired, key=lambda c: c.expiry_date or today)[:5]:
        attention.append({
            "kind": "expired", "label": f"{card.vendor.vendor_name} rate card expired",
            "when": card.expiry_date, "url": reverse("vendorlink:commercial_detail", args=[card.pk]),
        })
    for card in sorted(expiring, key=lambda c: c.expiry_date or today)[:5]:
        attention.append({
            "kind": "expiring", "label": f"{card.vendor.vendor_name} expires in {card.days_to_expiry}d",
            "when": card.expiry_date, "url": reverse("vendorlink:commercial_detail", args=[card.pk]),
        })
    if kpis["pending_regs"]:
        attention.append({
            "kind": "pending", "label": f"{kpis['pending_regs']} registration(s) awaiting review",
            "when": None, "url": reverse("vendorlink:registration_list"),
        })

    # Recent activity, newest first, from the three streams a buyer cares about.
    activity = []
    for r in VendorRequest.objects.order_by("-created_at")[:6]:
        activity.append({"kind": "request", "when": r.created_at,
                         "text": f"Request raised — {r.subject}",
                         "url": reverse("vendorlink:request_detail", args=[r.pk])})
    for b in VendorBid.objects.select_related("vendor_registration").order_by("-submitted_at")[:6]:
        name = b.vendor_registration.vendor_name if b.vendor_registration else "A vendor"
        activity.append({"kind": "bid", "when": b.submitted_at,
                         "text": f"{name} submitted a bid",
                         "url": reverse("vendorlink:bid_detail", args=[b.pk])})
    for e in VendorEvaluation.objects.select_related("vendor_request").order_by("-triggered_at")[:4]:
        activity.append({"kind": "evaluation", "when": e.triggered_at,
                         "text": f"AI evaluation {e.get_status_display().lower()} — {e.vendor_request.subject}",
                         "url": reverse("vendorlink:evaluation_detail", args=[e.vendor_request.pk])})
    activity.sort(key=lambda a: a["when"], reverse=True)

    return render(request, "vendorlink/t3_dashboard.html", _ctx(
        request, kpis=kpis, attention=attention, activity=activity[:10],
        warning_days=VendorCommercial.EXPIRY_WARNING_DAYS, soon=soon,
    ))


# ── 3.2 — Vendor Master ──────────────────────────────────────────────────────
@login_required
@access_required
def vendor_master(request):
    """Table-first vendor directory with saved filters in a left rail.

    Country filtering goes through canonical_country for the same reason the
    original screen does: the column is free text and holds "India", "india"
    and " India " at once, so an exact match silently hides rows.
    """
    query = (request.GET.get("q") or "").strip()
    continent = (request.GET.get("continent") or "").strip().upper()
    country = (request.GET.get("country") or "").strip()
    capability = (request.GET.get("capability") or "").strip()
    view = (request.GET.get("view") or "table").strip()

    vendors = Vendor.objects.filter(is_active=True)
    if query:
        vendors = vendors.filter(
            Q(vendor_name__icontains=query) | Q(city__icontains=query)
            | Q(state__icontains=query) | Q(country__icontains=query)
            | Q(contact_person__icontains=query)
        )
    # Continent and country are resolved in one pass over the distinct raw
    # spellings, so the two filters cannot disagree about which raw values mean
    # which place — and picking a continent alone is a complete query.
    if continent or country:
        wanted = canonical_country(country) if country else ""
        matching = []
        for raw in Vendor.objects.values_list("country", flat=True).distinct():
            name = canonical_country(raw)
            if not name:
                continue
            if wanted and name != wanted:
                continue
            if continent and continent_of(raw) != continent:
                continue
            matching.append(raw)
        vendors = vendors.filter(country__in=matching)
    if capability in active_codes():
        vendors = vendors.filter(capabilities__code=capability)

    # prefetch: the table renders a tick per capability per vendor, which is one
    # query each across the join without it.
    vendors = list(vendors.order_by("vendor_name").prefetch_related("capabilities"))
    total_active = Vendor.objects.filter(is_active=True).count()
    all_countries = filter_countries_by_continent(
        canonical_countries(
            Vendor.objects.filter(is_active=True).values_list("country", flat=True)
        ),
        continent,
    )
    # Counts folded into the field list rather than kept in a parallel dict:
    # Django templates cannot look a dict up by a loop variable, so a separate
    # mapping would be unreachable from the rail.
    # One aggregate over the master instead of a count per capability, which
    # was N+1 and now would be N+1 across a join.
    capabilities = [
        {"attr": row["code"], "label": row["label"], "count": row["count"]}
        for row in adoption_rows(vendor_total=total_active)
    ]

    return render(request, "vendorlink/t3_vendor_master.html", _ctx(
        request,
        vendors=vendors, result_count=len(vendors),
        total_count=total_active,
        q=query, country=canonical_country(country), continent=continent,
        # Spelled out for the rail heading — the template has the code, not the
        # name, and the choices list is a sequence it cannot index by key.
        continent_label=continent_name(continent),
        capability=capability, view=view,
        countries=all_countries,
        capabilities=capabilities,
        capability_total=len(capabilities),
        filtered=bool(query or country or continent or capability),
    ))


@login_required
@access_required
def vendor_drawer(request, pk):
    """The right-side detail drawer, loaded on row click.

    A fragment, not a page — it renders without base.html so the table stays
    put and only the drawer swaps. That is what keeps the list's scroll
    position and filters intact, which a full navigation would throw away.
    """
    vendor = get_object_or_404(Vendor, pk=pk)
    return render(request, "vendorlink/_t3_vendor_drawer.html", _ctx(
        request,
        vendor=vendor,
        capabilities=flags_for(vendor),
        cards=vendor.commercials.all() if hasattr(vendor, "commercials") else [],
        # No weighted score here. It is the Vendor Scorecard's own number, shown
        # with its four attribute scores and the comment behind each on
        # /scorecard/ and the KPI dashboard. Reprinting just the roll-up in a
        # drawer gave a figure with none of the reasoning next to it, which is
        # the one form of that number nobody should act on.
        kpi=vendor.latest_kpi_record,
    ))


# ── 3.3 — Commercial Database ────────────────────────────────────────────────
@login_required
@access_required
def commercial(request):
    """Rate cards with expiry front and centre."""
    from . import commercial as commercial_module

    rows, applied = commercial_module.filtered_cards(request)
    everything = commercial_module.all_entries()
    states = [row.expiry_state for row in everything]

    # Contract value is summed per currency and never across — the same rule
    # the Tier 2 dashboard follows, for the same reason.
    value_by_currency = defaultdict(float)
    for row in everything:
        if row.total_cost:
            value_by_currency[(row.currency or "").upper()] += float(row.total_cost)

    return render(request, "vendorlink/t3_commercial.html", _ctx(
        request,
        rows=rows, result_count=len(rows),
        total=len(everything),
        expiring=states.count("soon"), expired=states.count("expired"),
        value_by_currency=sorted(value_by_currency.items()),
        query=applied["query"], status=applied["status"],
        currency=applied["currency"], source=applied["source"],
        currencies=[c for c, _ in VendorCommercial.CURRENCY_CHOICES],
        warning_days=VendorCommercial.EXPIRY_WARNING_DAYS,
        filtered=bool(applied["query"] or applied["status"] or applied["currency"]),
    ))


# ── 3.4 — Vendor Scorecard ───────────────────────────────────────────────────
@login_required
@access_required
def scorecard(request):
    """Radar of the four scored attributes, with history underneath."""
    vendor_pk = (request.GET.get("vendor") or "").strip()
    vendors = list(Vendor.objects.filter(is_active=True).order_by("vendor_name"))

    selected = None
    if vendor_pk.isdigit():
        selected = Vendor.objects.filter(pk=int(vendor_pk)).first()
    if selected is None:
        selected = next((v for v in vendors if v.current_scorecard), vendors[0] if vendors else None)

    history = list(
        VendorScorecard.objects.filter(vendor=selected).order_by("-scored_at")
    ) if selected else []
    latest = history[0] if history else None

    return render(request, "vendorlink/t3_scorecard.html", _ctx(
        request,
        vendors=vendors, selected=selected, latest=latest, history=history,
        attributes=SCORECARD_ATTRIBUTES,
        radar_labels=[label for _name, label, _w in SCORECARD_ATTRIBUTES],
        radar_values=[
            getattr(latest, f"{name}_score", 0) for name, _label, _w in SCORECARD_ATTRIBUTES
        ] if latest else [],
        # Oldest first so the trend line reads left to right.
        trend_labels=[s.scored_at.strftime("%b %y") for s in reversed(history)],
        trend_values=[float(s.final_score) for s in reversed(history)],
    ))


# ── 3.5 — KPI Dashboard ──────────────────────────────────────────────────────
@login_required
@access_required
def kpi(request):
    """Eight metric tiles with a sparkline and a month-on-month delta each."""
    vendor_pk = (request.GET.get("vendor") or "").strip()
    vendors = list(Vendor.objects.filter(is_active=True).order_by("vendor_name"))

    records = VendorKPIRecord.objects.select_related("vendor").order_by("period")
    selected = None
    if vendor_pk.isdigit():
        selected = Vendor.objects.filter(pk=int(vendor_pk)).first()
        if selected:
            records = records.filter(vendor=selected)
    records = list(records)

    # One series per metric, oldest first, so a tile can draw a sparkline and
    # compare the last two readings without a second query.
    tiles = []
    for name, label, unit in KPI_METRIC_FIELDS:
        series = [
            (r.period, float(getattr(r, name)))
            for r in records if getattr(r, name, None) is not None
        ]
        current = series[-1][1] if series else None
        previous = series[-2][1] if len(series) > 1 else None
        delta = None
        if current is not None and previous:
            delta = round((current - previous) / previous * 100, 1)
        tiles.append({
            "key": name, "label": label, "unit": unit,
            "current": current, "delta": delta,
            "spark": [value for _period, value in series][-12:],
            # Damage rate, response time, CAPA days and complaints are all
            # "lower is better", so a rise is bad and must not render green.
            "lower_is_better": name in {
                "damage_rate", "response_time_hours", "capa_closure_days",
                "internal_complaints", "cost_per_shipment",
            },
        })

    periods = sorted({r.period for r in records})
    return render(request, "vendorlink/t3_kpi.html", _ctx(
        request,
        vendors=vendors, selected=selected, tiles=tiles, records=records[::-1],
        metric_fields=KPI_METRIC_FIELDS,
        chart_labels=[p.strftime("%b %y") for p in periods],
        has_data=bool(records),
    ))
