"""Tier 1 redesign screens: AI Bid Evaluation and Bid Comparison Heatmap.

Kept out of views.py deliberately — that file is already ~2,400 lines across
Vendor Master, requests, registration, bid analytics and the evaluator, and a
sixth concern bolted on would make it the file nobody wants to open. Same
reasoning that put commercial.py and admin_console.py in their own modules.

Both screens are per-request views of data the module already holds; neither
adds a model or an endpoint of its own. The existing evaluate / callback /
status endpoints in views.py stay the single way an evaluation is run, so this
module reads and renders only.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import bidcompare, fx
from .capabilities import capability_rows
from .models import BidAward, BidOutcomeNotice, VendorBid, VendorEvaluation, VendorRequest
from .rbac import can_access_vendorlink, can_edit_vendorlink, can_manage_award, is_vendorlink_admin

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
edit_required = user_passes_test(can_edit_vendorlink, login_url="vendorlink:bid_analytics")


def _ctx(request, **extra):
    base = {
        "is_vm_admin": is_vendorlink_admin(request.user),
        "can_edit": can_access_vendorlink(request.user),
    }
    base.update(extra)
    return base


def _bids_for(vendor_request):
    return list(
        VendorBid.objects.filter(vendor_request=vendor_request)
        .select_related("vendor_registration", "vendor_registration__vendor", "submitted_by")
        .order_by("submitted_by_id", "bid_number", "submitted_at")
    )


def _vendor_name(bid):
    """Whatever this bid's vendor should be called on screen.

    A bid identifies its vendor through the registration it was submitted
    against; that row can be missing (deleted, or a staff-entered bid), so the
    submitter is the fallback rather than rendering a blank column header.
    """
    registration = bid.vendor_registration
    if registration and registration.vendor_name:
        return registration.vendor_name
    submitter = bid.submitted_by
    if submitter:
        return submitter.get_full_name() or submitter.username
    return "Unknown vendor"


def _vendor_record(bid):
    """The Vendor row behind a bid, when the registration is linked to one.

    Only a linked Vendor carries the capability booleans, so an unlinked bid
    shows "no data" in the capability rows rather than a column of crosses that
    would read as "this vendor cannot do any of it".
    """
    registration = bid.vendor_registration
    return getattr(registration, "vendor", None) if registration else None


def _evaluation_for(vendor_request):
    try:
        evaluation = vendor_request.evaluation
    except VendorEvaluation.DoesNotExist:
        return None
    # Recovers a run whose worker died mid-flight, so the page offers a retry
    # instead of showing "Processing" for ever.
    evaluation.mark_stale_if_needed()
    return evaluation


def _chat_context(vendor_request, bids):
    """Context chips + starter prompts for the MerilGPT panel.

    The chips state what the model can actually see. Without them "compare the
    bids" is ambiguous about which bids, and a wrong assumption is invisible.
    """
    currencies = sorted({(b.currency or "").upper() for b in bids if b.currency})
    chips = [
        f"RFI #{vendor_request.pk}",
        f"{len(bids)} bid{'' if len(bids) == 1 else 's'}",
    ]
    if vendor_request.country:
        chips.append(vendor_request.country)
    chips.extend(currencies)

    suggestions = [
        {"label": "Compare top 3 bids",
         "prompt": f"Compare the three cheapest bids on request #{vendor_request.pk} "
                   f"and say which is the better commercial choice, and why."},
        {"label": "Explain the outlier",
         "prompt": f"Which bid on request #{vendor_request.pk} is the outlier on price, "
                   f"and what explains it?"},
        {"label": "Who covers every service?",
         "prompt": f"For request #{vendor_request.pk}, which vendors can deliver every "
                   f"service the request requires?"},
    ]
    return chips, suggestions


# ── 1.2 — AI Bid Evaluation ──────────────────────────────────────────────────
@login_required
@access_required
def evaluation_detail(request, pk):
    """The flagship evaluation view for one request.

    Renders whichever of the five states the evaluation is actually in —
    never run, processing, failed, completed, or completed-with-gaps — rather
    than an empty results table that leaves the reader guessing which.
    """
    vendor_request = get_object_or_404(VendorRequest, pk=pk)
    bids = _bids_for(vendor_request)
    evaluation = _evaluation_for(vendor_request)
    chips, suggestions = _chat_context(vendor_request, bids)

    ranked = evaluation.ranked_vendors if evaluation else []
    top_score = None
    for entry in ranked:
        score = entry.get("score") if isinstance(entry, dict) else None
        if isinstance(score, (int, float)):
            top_score = score if top_score is None else max(top_score, score)

    # Bids missing every cost line can still be ranked by the model on services
    # alone, which is worth flagging rather than presenting as a like-for-like
    # comparison — Tier 1's "partial data" state.
    incomplete = [
        _vendor_name(bid) for bid in bids
        if not any(getattr(bid, attr, None) for attr, _ in bidcompare.COST_LINES)
    ]

    return render(request, "vendorlink/evaluation.html", _ctx(
        request,
        vendor_request=vendor_request,
        evaluation=evaluation,
        ranked=ranked,
        top_score=top_score,
        bids=bids,
        bid_count=len(bids),
        incomplete_vendors=incomplete,
        capability_fields=[(attr, label) for attr, label, _icon in capability_rows()],
        chat_chips=chips,
        chat_suggestions=suggestions,
    ))


# ── 1.3 — Bid Comparison Heatmap ─────────────────────────────────────────────
@login_required
@access_required
def bid_heatmap(request, pk):
    """Cost + capability matrix across every bid on one request."""
    vendor_request = get_object_or_404(VendorRequest, pk=pk)
    bids = _bids_for(vendor_request)
    chips, suggestions = _chat_context(vendor_request, bids)

    # Rates for every currency on this request in one pass, so the columns do
    # not each look one up. Missing rates come back as None and those columns
    # stay un-converted rather than being converted at a guess.
    rates = fx.rates_for({bid.currency for bid in bids})
    columns = [
        bidcompare.column_for(
            bid, _vendor_name(bid), vendor=_vendor_record(bid),
            inr_rate=rates.get((bid.currency or "").upper()),
        )
        for bid in bids
    ]
    columns.sort(key=lambda col: (
        (col["vendor_name"] or "").casefold(),
        col["vendor_key"],
        col["bid_number"] or 0,
        col["pk"] or 0,
    ))
    matrix = bidcompare.build(
        columns,
        capability_fields=[(attr, label) for attr, label, _icon in capability_rows()],
    )

    # The award is the request's one outcome, and this screen had no
    # representation of it at all — so the per-vendor "chosen revision" badges
    # were the only marks on the page and read as if they named winners. Bid is
    # select_related because the template matches the awarded column on bid pk.
    try:
        award = BidAward.objects.select_related("bid").get(vendor_request=vendor_request)
    except BidAward.DoesNotExist:
        award = None

    return render(request, "vendorlink/bid_heatmap.html", _ctx(
        request,
        vendor_request=vendor_request,
        matrix=matrix,
        award=award,
        evaluation=_evaluation_for(vendor_request),
        view_mode=(request.GET.get("view") or "all").strip(),
        chat_chips=chips,
        chat_suggestions=suggestions,
    ))


# ── 2.1 — Bid Analytics Dashboard ────────────────────────────────────────────
def _spread_pct(totals):
    """How far the dearest bid sits above the cheapest, as a percentage.

    The single number that says whether a request drew real competition: a
    spread near zero means every vendor quoted the same and there is nothing to
    negotiate, while a wide one is where the money is. Returns None rather than
    zero when there is nothing to compare, so the tile can say "—" instead of
    implying the bids agreed.
    """
    usable = [t for t in totals if t]
    if len(usable) < 2:
        return None
    low, high = min(usable), max(usable)
    return round((high - low) / low * 100, 1) if low else None


@login_required
@access_required
def analytics_dashboard(request):
    """Tier 2.1 — the parent screen for the per-request Tier 1 pages.

    Deliberately currency-aware: bid totals across requests are summed only
    within a currency, and the "average bid" tile reports the currency that
    carries the most bids rather than averaging across all of them, which
    would produce a number that means nothing.
    """
    requests_qs = (
        VendorRequest.objects.annotate(bid_count=Count("bids"))
        .select_related("evaluation")
        .order_by("-created_at")
    )

    bids = list(
        VendorBid.objects.select_related("vendor_request", "vendor_registration", "submitted_by")
    )

    by_currency = defaultdict(list)
    for bid in bids:
        by_currency[(bid.currency or "").upper()].append(bidcompare.column_for(
            bid, _vendor_name(bid))["total"])

    # The currency most bids are quoted in — the only one whose average and
    # cheapest are worth putting on a tile without qualification.
    main_currency, main_totals = "", []
    if by_currency:
        main_currency, main_totals = max(by_currency.items(), key=lambda kv: len(kv[1]))

    per_vendor = defaultdict(float)
    per_vendor_currency = {}
    for bid in bids:
        if (bid.currency or "").upper() != main_currency:
            continue
        name = _vendor_name(bid)
        total = bidcompare.column_for(bid, name)["total"]
        # A vendor's figure is its cheapest bid on each request, summed — a
        # later revision replaces an earlier one rather than adding to it.
        per_vendor[name] = per_vendor.get(name, 0.0) + (total or 0.0)
        per_vendor_currency[name] = main_currency

    vendor_chart = sorted(per_vendor.items(), key=lambda kv: kv[1], reverse=True)[:10]

    # Capability coverage across every vendor that has bid, for the donut.
    # Master read once outside the loop — the labels on the donut have to be the
    # master's, and re-reading per vendor would query per vendor.
    catalogue = capability_rows()
    coverage = Counter()
    seen_vendors = set()
    for bid in bids:
        vendor = _vendor_record(bid)
        if vendor is None or vendor.pk in seen_vendors:
            continue
        seen_vendors.add(vendor.pk)
        for attr, label, _icon in catalogue:
            if vendor.has_capability(attr):
                coverage[label] += 1

    # Bids per day for the sparkline, oldest first.
    per_day = Counter()
    for bid in bids:
        per_day[bid.submitted_at.date()] += 1
    timeline = sorted(per_day.items())

    evaluated = sum(
        1 for r in requests_qs
        if getattr(getattr(r, "evaluation", None), "status", "") == "completed"
    )

    return render(request, "vendorlink/analytics_dashboard.html", _ctx(
        request,
        requests=requests_qs,
        request_count=len(requests_qs),
        bid_count=len(bids),
        evaluated_count=evaluated,
        main_currency=main_currency,
        avg_bid=round(sum(main_totals) / len(main_totals), 2) if main_totals else None,
        cheapest_bid=min(main_totals) if main_totals else None,
        spread_pct=_spread_pct(main_totals),
        mixed_currency=len(by_currency) > 1,
        currencies=sorted(c for c in by_currency if c),
        vendor_chart_labels=[name for name, _total in vendor_chart],
        vendor_chart_values=[round(total, 2) for _name, total in vendor_chart],
        coverage_labels=[label for label, _n in coverage.most_common()],
        coverage_values=[n for _label, n in coverage.most_common()],
        timeline_labels=[d.strftime("%d %b") for d, _n in timeline],
        timeline_values=[n for _d, n in timeline],
        vendor_count=len({_vendor_name(b) for b in bids}),
    ))


# ── 2.2 — Vendor Bid Detail ──────────────────────────────────────────────────
@login_required
@access_required
def bid_detail(request, pk):
    """One bid, in full — cost breakdown beside notes and provenance."""
    bid = get_object_or_404(
        VendorBid.objects.select_related(
            "vendor_request", "vendor_registration", "vendor_registration__vendor", "submitted_by"
        ),
        pk=pk,
    )
    column = bidcompare.column_for(bid, _vendor_name(bid), vendor=_vendor_record(bid))

    siblings = [
        b for b in _bids_for(bid.vendor_request) if b.pk != bid.pk
    ]
    # Where this bid sits on price among the bids quoted in the same currency —
    # ranking it against another currency would be meaningless.
    same_currency = [
        b for b in _bids_for(bid.vendor_request)
        if (b.currency or "").upper() == (bid.currency or "").upper()
    ]
    totals = sorted(
        bidcompare.column_for(b, _vendor_name(b))["total"] for b in same_currency
    )
    rank = totals.index(column["total"]) + 1 if column["total"] in totals else None

    cost_lines = [
        {"label": label, "value": column["costs"].get(attr)}
        for attr, label in bidcompare.COST_LINES
    ]

    chips, suggestions = _chat_context(bid.vendor_request, _bids_for(bid.vendor_request))

    return render(request, "vendorlink/bid_detail.html", _ctx(
        request,
        bid=bid,
        column=column,
        cost_lines=cost_lines,
        sibling_count=len(siblings),
        rank=rank,
        rank_of=len(totals),
        # Resolved here rather than in the template: Django cannot do a dynamic
        # getattr, and a per-field template filter would be a lot of machinery
        # for nine booleans.
        capabilities=[
            {"label": label, "yes": column["vendor"].has_capability(attr)}
            for attr, label, _icon in capability_rows()
        ] if column["vendor"] is not None else [],
        chat_chips=chips,
        chat_suggestions=suggestions,
        # The request's award, if it has one -- which may belong to a DIFFERENT
        # vendor's bid than the one on screen. The template needs both facts so
        # it can say "already awarded to someone else" rather than just hiding
        # the button with no explanation.
        award=BidAward.objects.filter(
            vendor_request=bid.vendor_request,
        ).select_related("bid").first(),
        # Separate from is_vm_admin: the award actions are also open to
        # whoever raised THIS request, even without admin group membership --
        # every other admin-gated control on this screen (Shortlist, Reject,
        # comparison select) stays admin/editor-only. See rbac.can_manage_award.
        can_manage_award=can_manage_award(request.user, bid.vendor_request),
        # How many losing bidders have been written to. Shown because "did the
        # runners-up get told?" is otherwise invisible to the buyer, and the
        # answer changes whether they can still go back to one of them.
        regrets_sent=BidOutcomeNotice.objects.filter(
            vendor_request=bid.vendor_request,
            kind=BidOutcomeNotice.KIND_REGRET,
            sent_at__isnull=False,
        ).count(),
    ))


@login_required
@edit_required
@require_POST
def bid_set_status(request, pk):
    """Shortlist or reject a bid.

    VendorBid has carried these statuses since it was written but nothing ever
    set them — only ``bid_select`` existed, and that flips is_selected, which is
    a different axis: a bid can be shortlisted without being the winner. The
    status is whitelisted against the model's own choices so an arbitrary POST
    cannot write a value the UI has no chip for.
    """
    bid = get_object_or_404(VendorBid, pk=pk)
    wanted = (request.POST.get("status") or "").strip()
    valid = {value for value, _label in VendorBid.STATUS_CHOICES}
    if wanted not in valid:
        messages.error(request, "That is not a bid status.")
        return redirect("vendorlink:bid_detail", pk=bid.pk)

    bid.status = wanted
    bid.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Bid marked {bid.get_status_display().lower()}.")
    return redirect("vendorlink:bid_detail", pk=bid.pk)
