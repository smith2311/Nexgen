"""Graphical commercial reports over the bid data.

Two scopes, both computed on the server from the rows themselves:

* **Request report** — one RFI in full. Who quoted, who was cheapest, who we
  actually awarded it to, and what the gap between those two cost us.
* **Monthly report** — the same question asked of a whole year. How many RFIs
  ran each month, what the cheapest available quote summed to against what we
  actually committed, and which vendors keep winning.

Why not MerilGPT: the "Create Report" button used to open the GPT drawer with a
canned prompt, which meant the report was prose an LLM had re-derived from the
same numbers — non-deterministic, unavailable when ``TEST_OPENAI_API_KEY`` is not
set, and impossible to reconcile against the screen it came from. A commercial
report that management may act on has to be arithmetic. The GPT drawer is still
there for ad-hoc questions; it is just no longer the only way to get a report.

**The currency rule, which shapes everything here.** ``VendorBid.currency`` is
per bid, and nothing in this project holds exchange rates. Summing across
currencies would invent a number, so every comparison — cheapest, premium,
totals — is computed strictly inside one currency: the one the most vendors
quoted in. Bids in any other currency are still listed, marked as
non-comparable, and excluded from every total, with the report saying so out
loud. Same discipline the analytics dashboard already documents for its tiles.

The two "best" notions are also kept apart, because they answer different
questions and routinely disagree:

* **cheapest** — the lowest comparable total on the request. Arithmetic.
* **awarded** — the vendor we actually went with, from ``BidAward``. A decision,
  which may cost more than the cheapest for reasons the price does not carry
  (capability, blacklisting, capacity). The report's job is to show the gap and
  name it, not to imply the cheaper one should have won.

One consequence worth stating: every vendor is represented by their cheapest
revision (or their staff-selected one), matching the analytics screen — except
the awarded vendor, who is represented by the bid actually named in the award.
Anything else understates the premium, because a vendor can hold a cheaper
revision that nobody awarded.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_date
from django.utils import timezone

from . import bidcompare
from .models import BidAward, BidOutcomeNotice, VendorBid, VendorRequest
from .rbac import can_access_vendorlink, is_vendorlink_admin

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
logger = logging.getLogger(__name__)

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

AI_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "key_points", "risks", "opportunities"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise executive summary based only on the supplied report data.",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 short bullets naming the most important things that happened.",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Commercial risks, caveats, or data limitations. Empty if none.",
        },
        "opportunities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Negotiation, follow-up, or process opportunities. Empty if none.",
        },
    },
}

AI_REPORT_PROMPT = """You write clean procurement reports for Meril Life Sciences.

Use only the JSON report data supplied by the application. Do not invent vendors,
currencies, awards, savings, risks, or dates. Keep currencies explicit, and never
sum or compare different currencies as if they are one money. Highlight what
happened, what mattered, and what management should notice.

Return only structured JSON matching the schema."""

# Same style rule as the evaluator and the chat, imported rather than
# restated so the three cannot drift apart.
from .ai_evaluator import PLAIN_ENGLISH as _PLAIN_ENGLISH

AI_REPORT_PROMPT += _PLAIN_ENGLISH


# ── Shared arithmetic ───────────────────────────────────────────────────────
def bid_total(bid):
    """One bid's quoted total, by the same seven cost lines the screens sum."""
    return sum(
        float(getattr(bid, attr, None) or 0) for attr, _label in bidcompare.COST_LINES
    )


def _vendor_identity(bid):
    """How a bid's vendor is named and keyed.

    Keyed on the vendor registration/vendor where available, matching
    ``views._vendor_bid_groups``, so a company's several revisions collapse
    into one row here exactly as they do on the analytics screen. Falling back
    to the bid's own id keeps a bid with no submitter as its own vendor rather
    than merging every such bid into one.
    """
    registration = getattr(bid, "vendor_registration", None)
    submitter = getattr(bid, "submitted_by", None)
    if registration and registration.vendor_name:
        name = registration.vendor_name
    elif submitter:
        name = submitter.get_full_name() or submitter.username
    else:
        name = "Unknown vendor"
    key = bidcompare.vendor_key_for(bid)
    email = ""
    if registration and registration.email:
        email = registration.email
    elif submitter and submitter.email:
        email = submitter.email
    return key, name, email.strip().lower()


@dataclass
class VendorRow:
    """One vendor's position on one request."""
    key: str
    name: str
    email: str
    currency: str
    total: float
    bid_count: int
    best_bid: object
    costs: dict = field(default_factory=dict)
    comparable: bool = True
    is_cheapest: bool = False
    is_awarded: bool = False
    is_selected: bool = False
    blacklisted: bool = False
    #: How much dearer than the cheapest comparable quote, as a percentage.
    #: None for the cheapest itself and for anything non-comparable.
    premium_pct: float | None = None
    premium_abs: float | None = None


def _vendor_rows(bids):
    """Collapse a request's bids into one row per vendor, cheapest-first.

    Each vendor is represented by the bid the rest of the UI represents them
    by: their staff-selected revision if one is marked, otherwise their
    cheapest. Picking a different one here would make the report disagree with
    the analytics screen about the same vendor's number.
    """
    grouped = defaultdict(list)
    for bid in bids:
        grouped[_vendor_identity(bid)[0]].append(bid)

    rows = []
    for group in grouped.values():
        ordered = sorted(group, key=bid_total)
        best = next((b for b in ordered if b.is_selected), ordered[0])
        key, name, email = _vendor_identity(best)
        score = getattr(getattr(best.vendor_registration, "vendor", None), "score", None)
        rows.append(VendorRow(
            key=key, name=name, email=email,
            currency=(best.currency or "").upper(),
            total=bid_total(best),
            bid_count=len(ordered),
            best_bid=best,
            costs={
                label: float(getattr(best, attr, None) or 0)
                for attr, label in bidcompare.COST_LINES
            },
            is_selected=bool(best.is_selected),
            blacklisted=bool(getattr(score, "is_blacklisted", False)),
        ))
    return sorted(rows, key=lambda row: row.total)


def _dominant_currency(rows):
    """The currency the most vendors quoted in — the only one totalled.

    Ties broken alphabetically so the same bid set always reports the same
    currency rather than whichever one a dict happened to yield first.
    """
    tally = Counter(row.currency for row in rows if row.currency)
    if not tally:
        return ""
    top = max(tally.values())
    return sorted(code for code, n in tally.items() if n == top)[0]


# ── Ranking: the single source both reports use ─────────────────────────────
def rank_bids(bids, award=None):
    """Rank one request's bids: rows, currency, cheapest, premiums.

    Both reports call this. They used to each rank their own way, and drifted
    the moment the awarded-bid rule below was added to one of them — the
    monthly report went on labelling an award "cheapest" while that RFI's own
    report reported a 23.3% premium. Two screens disagreeing about the same
    number is worse than either being slightly wrong, so there is one function.
    """
    rows = _vendor_rows(bids)

    awarded_row = None
    if award is not None:
        awarded_key = _vendor_identity(award.bid)[0]
        awarded_row = next((row for row in rows if row.key == awarded_key), None)

    # An award names one BID, so the awarded vendor is represented by that bid
    # rather than by their cheapest revision. _vendor_rows picks the cheapest
    # (or staff-selected) one, which is right for a comparison but wrong here:
    # the premium that matters is what we are committed to paying, and a vendor
    # holding a cheaper unawarded revision would otherwise have that premium
    # quietly understated. Substituted before the ranking, so the sort order,
    # the cheapest flag and every premium follow from the corrected total.
    if awarded_row is not None and awarded_row.best_bid.pk != award.bid_id:
        awarded_row.best_bid = award.bid
        awarded_row.total = bid_total(award.bid)
        awarded_row.currency = (award.bid.currency or "").upper()
        awarded_row.costs = {
            label: float(getattr(award.bid, attr, None) or 0)
            for attr, label in bidcompare.COST_LINES
        }
        awarded_row.is_selected = bool(award.bid.is_selected)
        rows.sort(key=lambda row: row.total)

    if awarded_row is not None:
        awarded_row.is_awarded = True

    currency = _dominant_currency(rows)
    for row in rows:
        row.comparable = bool(currency) and row.currency == currency

    comparable = [row for row in rows if row.comparable]
    cheapest = comparable[0] if comparable else None
    dearest = comparable[-1] if comparable else None
    if cheapest:
        cheapest.is_cheapest = True
        for row in comparable:
            if row is not cheapest and cheapest.total > 0:
                row.premium_abs = row.total - cheapest.total
                row.premium_pct = round((row.total - cheapest.total) / cheapest.total * 100, 1)

    return {
        "rows": rows,
        "comparable": comparable,
        "currency": currency,
        "cheapest": cheapest,
        "dearest": dearest,
        "awarded_row": awarded_row,
    }


def _request_bids(vendor_request):
    return list(
        VendorBid.objects.filter(vendor_request=vendor_request)
        .select_related(
            "submitted_by", "vendor_registration",
            "vendor_registration__vendor", "vendor_registration__vendor__score",
        )
        .order_by("bid_number")
    )


# ── Request report ──────────────────────────────────────────────────────────
def build_request_report(vendor_request):
    """Everything the per-request report renders. No LLM, no side effects."""
    bids = _request_bids(vendor_request)
    award = (
        BidAward.objects.filter(vendor_request=vendor_request)
        .select_related("bid", "awarded_by").first()
    )
    ranked = rank_bids(bids, award)
    rows = ranked["rows"]
    comparable = ranked["comparable"]
    currency = ranked["currency"]
    cheapest = ranked["cheapest"]
    dearest = ranked["dearest"]
    awarded_row = ranked["awarded_row"]

    spread_pct = None
    if cheapest and dearest and cheapest.total > 0 and dearest is not cheapest:
        spread_pct = round((dearest.total - cheapest.total) / cheapest.total * 100, 1)

    return {
        # `vr`, never `request`: a context key called `request` overrides the
        # RequestContext's own, and base.html builds its sidebar and account
        # menu out of request.user / request.path.
        "vr": vendor_request,
        "rows": rows,
        "comparable": comparable,
        "currency": currency,
        "currencies": sorted({row.currency for row in rows if row.currency}),
        "mixed_currency": len({row.currency for row in rows if row.currency}) > 1,
        "excluded": [row for row in rows if not row.comparable],
        "cheapest": cheapest,
        "dearest": dearest,
        "spread_pct": spread_pct,
        "award": award,
        "awarded_row": awarded_row,
        "bid_count": len(bids),
        "vendor_count": len(rows),
        "regrets_sent": BidOutcomeNotice.objects.filter(
            vendor_request=vendor_request,
            kind=BidOutcomeNotice.KIND_REGRET, sent_at__isnull=False,
        ).count(),
        "verdict": _request_verdict(cheapest, awarded_row, award, currency),
        "summary": _request_summary(vendor_request, rows, comparable, cheapest,
                                    dearest, awarded_row, currency, spread_pct),
        "charts": _request_charts(rows, currency),
        "cost_labels": [label for _attr, label in bidcompare.COST_LINES],
        "generated_at": timezone.now(),
    }


def _request_verdict(cheapest, awarded_row, award, currency):
    """The one-line headline: did we take the cheapest, and if not, what did it cost?

    Returned as a dict rather than a string so the template can colour it —
    "we took the cheapest" and "we paid 14% more" are not the same news, and a
    reader should be able to tell which they are looking at without reading.
    """
    if not cheapest:
        return {"tone": "neutral", "headline": "No comparable quotes yet.",
                "detail": "Nothing can be ranked until at least one bid carries costs."}
    if award is None:
        return {
            "tone": "info",
            "headline": "Not yet awarded.",
            "detail": (
                f"{cheapest.name} holds the cheapest comparable quote at "
                f"{currency} {cheapest.total:,.2f}."
            ),
        }
    # `comparable`, not just `is None`: the awarded vendor may well be in the
    # row list and still be unrankable, because their quote is in a currency
    # this report is not totalling. A premium against that is not a number.
    if awarded_row is None or not awarded_row.comparable:
        quoted = ""
        if awarded_row is not None and awarded_row.currency:
            quoted = (
                f" Their quote is in {awarded_row.currency}, and this report totals "
                f"{currency} only."
            )
        return {
            "tone": "warning",
            "headline": f"Awarded to {award.vendor_name}, whose bid is not in this comparison.",
            "detail": (
                "The awarded bid cannot be ranked against the others here, so no premium "
                "over the cheapest quote can be stated." + quoted
            ),
        }
    if awarded_row.is_cheapest:
        return {
            "tone": "good",
            "headline": f"Awarded to the cheapest quote — {awarded_row.name}.",
            "detail": (
                f"{currency} {awarded_row.total:,.2f}. No premium was paid over the "
                f"lowest comparable bid on this request."
            ),
        }
    premium = awarded_row.premium_abs or 0
    return {
        "tone": "warning",
        "headline": (
            f"Awarded to {awarded_row.name} at a "
            f"{awarded_row.premium_pct:.1f}% premium over the cheapest quote."
        ),
        "detail": (
            f"{currency} {premium:,.2f} more than {cheapest.name}. A premium is not "
            f"automatically wrong — capability, capacity and standing are not in the "
            f"price — but it should be a decision someone made on purpose."
        ),
    }


def _request_summary(vendor_request, rows, comparable, cheapest, dearest,
                     awarded_row, currency, spread_pct):
    """The prose paragraph, assembled from the same numbers the charts draw.

    Written as sentences rather than a bullet list because this is the part
    that gets pasted into a mail to someone who will never open the page.
    """
    parts = []
    bid_total_count = sum(row.bid_count for row in rows)
    if not rows:
        return (
            f'No vendor has bid on "{vendor_request.subject}" yet, so there is '
            "nothing to compare."
        )

    parts.append(
        f'{len(rows)} vendor{"s" if len(rows) != 1 else ""} submitted '
        f'{bid_total_count} bid{"s" if bid_total_count != 1 else ""} '
        f'on "{vendor_request.subject}"'
        + (f" ({vendor_request.country})" if vendor_request.country else "")
        + "."
    )

    if cheapest:
        parts.append(
            f"The cheapest comparable quote is {cheapest.name} at "
            f"{currency} {cheapest.total:,.2f}."
        )
        if dearest and dearest is not cheapest and spread_pct is not None:
            parts.append(
                f"The dearest is {dearest.name} at {currency} {dearest.total:,.2f}, "
                f"a spread of {spread_pct:.1f}%."
            )
    if len(comparable) != len(rows):
        others = len(rows) - len(comparable)
        parts.append(
            f'{others} quote{"s" if others != 1 else ""} '
            f'{"are" if others != 1 else "is"} in another currency and '
            "excluded from these totals — this project holds no exchange rates, "
            "so mixing them would invent a number."
        )
    if awarded_row and not awarded_row.comparable:
        parts.append(
            f"The award went to {awarded_row.name}, whose quote is in "
            f"{awarded_row.currency or 'another currency'} and so is not ranked above."
        )
    elif awarded_row and awarded_row.is_cheapest:
        parts.append(f"The award went to {awarded_row.name}, the cheapest quote.")
    elif awarded_row and awarded_row.premium_pct is not None:
        parts.append(
            f"The award went to {awarded_row.name} at {currency} "
            f"{awarded_row.total:,.2f} — {awarded_row.premium_pct:.1f}% above "
            f"the cheapest."
        )
    elif awarded_row:
        parts.append(f"The award went to {awarded_row.name} at {currency} {awarded_row.total:,.2f}.")
    return " ".join(parts)


def _request_charts(rows, currency):
    """Chart payloads. Plain lists, handed to the template via json_script."""
    comparable = [row for row in rows if row.comparable]
    return {
        # Totals per vendor. The role list drives the bar colours so cheapest
        # and awarded are legible without reading the axis labels.
        "totals": {
            "labels": [row.name for row in comparable],
            "values": [round(row.total, 2) for row in comparable],
            "roles": [
                "awarded" if row.is_awarded else "cheapest" if row.is_cheapest else "other"
                for row in comparable
            ],
            "currency": currency,
        },
        # Where each vendor's money goes. Stacked, so a vendor who is dearer
        # overall but cheaper on the line that matters is still visible.
        "composition": {
            "labels": [row.name for row in comparable],
            "series": [
                {
                    "label": label,
                    "values": [round(row.costs.get(label, 0.0), 2) for row in comparable],
                }
                for _attr, label in bidcompare.COST_LINES
            ],
            "currency": currency,
        },
    }


def _request_ai_source(report):
    """Small, JSON-safe extract for the OpenAI narrative layer."""
    return {
        "scope": "single_request",
        "subject": report["vr"].subject,
        "reference_id": report["vr"].reference_id,
        "country": report["vr"].country,
        "created_at": report["vr"].created_at.isoformat(),
        "summary": report["summary"],
        "verdict": report["verdict"],
        "currency": report["currency"],
        "bid_count": report["bid_count"],
        "vendor_count": report["vendor_count"],
        "cheapest": (
            {"vendor": report["cheapest"].name, "total": report["cheapest"].total}
            if report["cheapest"] else None
        ),
        "awarded": (
            {
                "vendor": report["award"].vendor_name,
                "status": report["award"].status,
                "amount": float(report["award"].awarded_amount or 0),
                "currency": report["award"].awarded_currency,
            }
            if report["award"] else None
        ),
        "vendors": [
            {
                "name": row.name,
                "currency": row.currency,
                "total": row.total,
                "bid_count": row.bid_count,
                "is_cheapest": row.is_cheapest,
                "is_awarded": row.is_awarded,
                "premium_pct": row.premium_pct,
                "premium_abs": row.premium_abs,
                "blacklisted": row.blacklisted,
                "comparable": row.comparable,
            }
            for row in report["rows"]
        ],
        "mixed_currency": report["mixed_currency"],
        "excluded_currencies": [row.currency for row in report["excluded"]],
    }


def _aggregate_ai_source(report):
    """JSON-safe extract for date-range / selected-request reports."""
    return {
        "scope": "aggregate",
        "title": report["report_title"],
        "scope_label": report["report_scope"],
        "currency": report["currency"],
        "request_count": report["request_count"],
        "bid_count": report["bid_count"],
        "vendor_count": report["vendor_count"],
        "awarded_count": report["awarded_count"],
        "awarded_total": report["awarded_total"],
        "cheapest_total": report["cheapest_total"],
        "premium_total": report["premium_total"],
        "premium_pct": report["premium_pct"],
        "summary": report["summary"],
        "excluded_requests": report["excluded_requests"],
        "months": [
            {
                "month": month["name"],
                "requests": month["requests"],
                "bids": month["bids"],
                "awarded_count": month["awarded_count"],
                "cheapest_sum": month["cheapest_sum"],
                "awarded_sum": month["awarded_sum"],
                "premium": month["premium"],
                "premium_pct": month["premium_pct"],
            }
            for month in report["months"]
        ],
        "outcomes": report["outcomes"],
        "wins": report["wins"],
        "requests": [
            {
                "subject": entry["vr"].subject,
                "reference_id": entry["vr"].reference_id,
                "country": entry["vr"].country,
                "created_at": entry["vr"].created_at.isoformat(),
                "currency": entry["currency"],
                "bid_count": entry["bid_count"],
                "vendor_count": entry["vendor_count"],
                "cheapest_vendor": entry["cheapest"].name if entry["cheapest"] else "",
                "cheapest_total": entry["cheapest"].total if entry["cheapest"] else None,
                "awarded_vendor": entry["award"].vendor_name if entry["award"] else "",
                "award_status": entry["award"].status if entry["award"] else "",
                "awarded_premium_pct": (
                    entry["awarded_row"].premium_pct if entry["awarded_row"] else None
                ),
                "awarded_unranked": entry["awarded_unranked"],
            }
            for entry in report["per_request"][:30]
        ],
    }


def build_ai_report(source, requested=False):
    """Optional OpenAI narrative for a deterministic report.

    Report pages render instantly without this. The OpenAI call is made only
    from URLs that explicitly ask for it (``?ai=1``), so a normal report never
    blocks on the network and tests keep their reproducible arithmetic path.
    """
    empty = {
        "requested": requested,
        "available": False,
        "summary": "",
        "key_points": [],
        "risks": [],
        "opportunities": [],
        "error": "",
    }
    if not requested:
        return empty
    if not os.getenv("TEST_OPENAI_API_KEY"):
        empty["error"] = "Key Points are unavailable right now."
        return empty

    try:
        from .ai_evaluator import DEFAULT_MODEL, _get_client

        client = _get_client()
        if client is None:
            raise RuntimeError("OpenAI client is unavailable.")
        model = DEFAULT_MODEL
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": AI_REPORT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Create the executive narrative for this VendorLink report.\n\n"
                        + json.dumps(source, indent=2, default=str)
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "vendorlink_report_narrative",
                    "strict": True,
                    "schema": AI_REPORT_SCHEMA,
                },
            },
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        return {
            "requested": True,
            "available": True,
            "summary": (parsed.get("summary") or "").strip(),
            "key_points": parsed.get("key_points") or [],
            "risks": parsed.get("risks") or [],
            "opportunities": parsed.get("opportunities") or [],
            "error": "",
        }
    except Exception:
        logger.exception("Report narrative failed")
        empty["error"] = "Key Points are unavailable right now."
        return empty


def _ai_url(request):
    params = request.GET.copy()
    params["ai"] = "1"
    return f"{request.path}?{params.urlencode()}"


@login_required
@access_required
def request_report(request, pk):
    vendor_request = get_object_or_404(VendorRequest, pk=pk)
    report = build_request_report(vendor_request)
    wants_ai = request.GET.get("ai") == "1"
    return render(request, "vendorlink/report_request.html", dict(
        report,
        ai_report=build_ai_report(_request_ai_source(report), requested=wants_ai),
        ai_url=_ai_url(request),
        is_vm_admin=is_vendorlink_admin(request.user),
        can_edit=can_access_vendorlink(request.user),
    ))


def _download_response(rows, headers, filename, file_format):
    if file_format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Report"
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        output = io.BytesIO()
        workbook.save(output)
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    response["Content-Disposition"] = f'attachment; filename="{filename}.{file_format}"'
    return response


@login_required
@access_required
def request_report_export(request, pk, file_format):
    if file_format not in ("csv", "xlsx"):
        return HttpResponse(status=404)
    report = build_request_report(get_object_or_404(VendorRequest, pk=pk))
    rows = [[
        row.name, row.email, row.currency, row.bid_count, row.total,
        row.premium_pct, "Awarded" if row.is_awarded else "",
        "Cheapest" if row.is_cheapest else "",
    ] for row in report["rows"]]
    return _download_response(
        rows,
        ["Vendor", "Email", "Currency", "Bids", "Best Total", "Premium %", "Awarded", "Cheapest"],
        f"vendorlink-request-{pk}",
        file_format,
    )


def _build_aggregate_report(
    requests, title, scope_label, summary_builder, year=None, country="",
    date_from=None, date_to=None,
):
    """Shared aggregate report for a year, selected RFIs, or a date range."""
    requests = list(requests)
    awards = {
        award.vendor_request_id: award
        for award in BidAward.objects.filter(vendor_request__in=requests)
        .select_related("bid")
    }

    # One query for every request's bids rather than one per request: this
    # loop was 12 of the monthly report's 27 queries. Same select_related
    # chain as _request_bids, so the ranking sees identical objects.
    bids_by_request = defaultdict(list)
    for bid in (
        VendorBid.objects.filter(vendor_request__in=requests)
        .select_related(
            "submitted_by", "vendor_registration",
            "vendor_registration__vendor", "vendor_registration__vendor__score",
        )
        .order_by("bid_number")
    ):
        bids_by_request[bid.vendor_request_id].append(bid)

    per_request = []
    for vendor_request in requests:
        bids = bids_by_request.get(vendor_request.pk, [])
        if not bids:
            continue
        # Same ranking the per-request report runs, so the two screens cannot
        # report a different premium for the same RFI.
        ranked = rank_bids(bids, awards.get(vendor_request.pk))
        awarded_row = ranked["awarded_row"]
        per_request.append({
            "vr": vendor_request,
            "month": timezone.localtime(vendor_request.created_at).month,
            "currency": ranked["currency"],
            "bid_count": len(bids),
            "vendor_count": len(ranked["rows"]),
            "cheapest": ranked["cheapest"],
            "award": awards.get(vendor_request.pk),
            # Only a comparable awarded row can carry a premium; an award in
            # another currency is reported as unranked rather than as free.
            "awarded_row": awarded_row if (awarded_row and awarded_row.comparable) else None,
            "awarded_unranked": bool(awarded_row and not awarded_row.comparable),
        })

    # One currency for the whole report, for the same reason as per request:
    # a year's "total awarded" that mixes EUR and INR is not a number.
    currency = _dominant_currency([
        VendorRow(key="", name="", email="", currency=entry["currency"],
                  total=0.0, bid_count=0, best_bid=None)
        for entry in per_request if entry["currency"]
    ])
    in_currency = [entry for entry in per_request if entry["currency"] == currency]
    excluded_requests = len(per_request) - len(in_currency)

    months = []
    for month in range(1, 13):
        entries = [entry for entry in in_currency if entry["month"] == month]
        if not entries:
            continue
        cheapest_sum = sum(e["cheapest"].total for e in entries if e["cheapest"])
        awarded_entries = [e for e in entries if e["awarded_row"]]
        awarded_sum = sum(e["awarded_row"].total for e in awarded_entries)
        # Compared against the cheapest of the SAME requests only. Measuring
        # awarded spend against the cheapest across every request, awarded or
        # not, would show a phantom saving on requests nobody has decided yet.
        awarded_cheapest_sum = sum(e["cheapest"].total for e in awarded_entries if e["cheapest"])
        months.append({
            "month": month,
            "name": MONTH_NAMES[month],
            "short": MONTH_SHORT[month],
            "requests": len(entries),
            "bids": sum(e["bid_count"] for e in entries),
            "cheapest_sum": cheapest_sum,
            "awarded_count": len(awarded_entries),
            "awarded_sum": awarded_sum,
            "premium": awarded_sum - awarded_cheapest_sum,
            "premium_pct": (
                round((awarded_sum - awarded_cheapest_sum) / awarded_cheapest_sum * 100, 1)
                if awarded_cheapest_sum else None
            ),
        })

    # There is no vendor response left to distinguish: an award either exists
    # (the requester decided) or it does not.
    outcomes = Counter()
    for entry in per_request:
        outcomes["Awarded" if entry["award"] else "Not awarded"] += 1

    # Grouped by email, not by the name snapshotted on each award: a vendor
    # renamed between two awards would otherwise split into two bars, each
    # undercounting them. The most recent name is the one shown.
    win_tally = Counter()
    win_names = {}
    for award in (
        BidAward.objects.filter(vendor_request__in=requests).order_by("awarded_at")
    ):
        key = (award.vendor_email or "").strip().lower() or f"award{award.pk}"
        win_tally[key] += 1
        win_names[key] = award.vendor_name
    wins = [
        {"vendor_name": win_names[key], "n": count}
        for key, count in sorted(
            win_tally.items(), key=lambda kv: (-kv[1], win_names[kv[0]]),
        )[:8]
    ]

    awarded_total = sum(m["awarded_sum"] for m in months)
    premium_total = sum(m["premium"] for m in months)
    cheapest_total = sum(m["cheapest_sum"] for m in months)

    # Distinct vendors who bid anywhere in scope, counted with the same key
    # _vendor_identity groups by, so this agrees with the per-request rows.
    vendor_count = len({
        _vendor_identity(bid)[0]
        for bid in VendorBid.objects.filter(
            vendor_request__in=[entry["vr"].pk for entry in in_currency]
        ).select_related("submitted_by", "vendor_registration")
    })

    return {
        "report_title": title,
        "report_scope": scope_label,
        "year": year,
        "country": country,
        "date_from": date_from,
        "date_to": date_to,
        "is_custom_report": year is None,
        "months": months,
        "currency": currency,
        "request_count": len(per_request),
        "excluded_requests": excluded_requests,
        "bid_count": sum(m["bids"] for m in months),
        "awarded_count": sum(m["awarded_count"] for m in months),
        "awarded_total": awarded_total,
        "cheapest_total": cheapest_total,
        "premium_total": premium_total,
        "premium_pct": (
            round(premium_total / (awarded_total - premium_total) * 100, 1)
            if (awarded_total - premium_total) else None
        ),
        "vendor_count": vendor_count,
        "outcomes": [{"label": k, "value": v} for k, v in sorted(outcomes.items())],
        "wins": wins,
        "per_request": sorted(
            in_currency, key=lambda e: e["vr"].created_at, reverse=True,
        ),
        "summary": summary_builder(
            months, per_request, in_currency, currency,
            awarded_total, premium_total, outcomes,
        ),
        "charts": _monthly_charts(months, outcomes, wins, currency),
        "generated_at": timezone.now(),
    }


# ── Monthly report ──────────────────────────────────────────────────────────
def build_monthly_report(year, country=""):
    """A year of RFIs, month by month: what was cheapest against what we took."""
    requests = (
        VendorRequest.objects.filter(created_at__year=year)
        .order_by("created_at")
    )
    if country:
        requests = requests.filter(country=country)

    return _build_aggregate_report(
        requests,
        f"Monthly Bid Report — {year}",
        f"{year}" + (f" · {country}" if country else ""),
        lambda months, per_request, in_currency, currency, awarded_total, premium_total, outcomes:
            _monthly_summary(
                year, country, months, per_request, in_currency, currency,
                awarded_total, premium_total, outcomes,
            ),
        year=year,
        country=country,
    )


def _monthly_summary(year, country, months, per_request, in_currency, currency,
                     awarded_total, premium_total, outcomes):
    if not per_request:
        scope = f" in {country}" if country else ""
        return f"No RFI{scope} received a bid in {year}, so there is nothing to report."

    parts = [
        f'{len(per_request)} RFI{"s" if len(per_request) != 1 else ""} '
        f'{"were" if len(per_request) != 1 else "was"} bid on in {year}'
        + (f" for {country}" if country else "")
        + f', across {len(months)} month{"s" if len(months) != 1 else ""}.'
    ]
    awarded = outcomes.get("Accepted", 0) + outcomes.get("Awaiting reply", 0) + outcomes.get("Declined", 0)
    if awarded:
        parts.append(
            f'{awarded} {"have" if awarded != 1 else "has"} been awarded, of which '
            f'{outcomes.get("Accepted", 0)} '
            f'{"have" if outcomes.get("Accepted", 0) != 1 else "has"} been accepted by the vendor.'
        )
    if awarded_total:
        parts.append(f"Committed spend on accepted and pending awards totals {currency} {awarded_total:,.2f}.")
        if premium_total > 0:
            parts.append(
                f"That is {currency} {premium_total:,.2f} above the cheapest quote "
                f"available on those same RFIs."
            )
        elif premium_total == 0:
            parts.append("Every award went to the cheapest comparable quote.")
    busiest = max(months, key=lambda m: m["requests"]) if months else None
    if busiest and len(months) > 1:
        parts.append(f'{busiest["name"]} was the busiest month with {busiest["requests"]} RFIs.')
    if len(in_currency) != len(per_request):
        others = len(per_request) - len(in_currency)
        parts.append(
            f'{others} RFI{"s" if others != 1 else ""} quoted in another currency '
            f'{"are" if others != 1 else "is"} excluded from these totals.'
        )
    return " ".join(parts)


def build_custom_report(vendor_requests, title, scope_label, date_from=None, date_to=None):
    """Aggregate report for selected requests or an arbitrary created-date range."""
    return _build_aggregate_report(
        vendor_requests,
        title,
        scope_label,
        lambda months, per_request, in_currency, currency, awarded_total, premium_total, outcomes:
            _custom_summary(
                scope_label, months, per_request, in_currency, currency,
                awarded_total, premium_total, outcomes,
            ),
        date_from=date_from,
        date_to=date_to,
    )


def _custom_summary(scope_label, months, per_request, in_currency, currency,
                    awarded_total, premium_total, outcomes):
    if not per_request:
        return f"No RFI in {scope_label} received a bid, so there is nothing to report."

    parts = [
        f'{len(per_request)} RFI{"s" if len(per_request) != 1 else ""} in '
        f'{scope_label} received bids across {len(months)} active '
        f'month{"s" if len(months) != 1 else ""}.'
    ]
    awarded = outcomes.get("Accepted", 0) + outcomes.get("Awaiting reply", 0) + outcomes.get("Declined", 0)
    if awarded:
        parts.append(
            f'{awarded} {"have" if awarded != 1 else "has"} an award decision; '
            f'{outcomes.get("Accepted", 0)} accepted, '
            f'{outcomes.get("Awaiting reply", 0)} awaiting vendor reply, and '
            f'{outcomes.get("Declined", 0)} declined.'
        )
    if awarded_total:
        parts.append(f"Committed value totals {currency} {awarded_total:,.2f}.")
        if premium_total > 0:
            parts.append(
                f"That is {currency} {premium_total:,.2f} above the cheapest comparable "
                "quotes on the same awarded RFIs."
            )
        else:
            parts.append("Every comparable award in this scope went to the cheapest quote.")
    if len(in_currency) != len(per_request):
        parts.append(
            f'{len(per_request) - len(in_currency)} RFI'
            f'{"s" if len(per_request) - len(in_currency) != 1 else ""} '
            "quoted in another currency and were excluded from the totals."
        )
    return " ".join(parts)


def _monthly_charts(months, outcomes, wins, currency):
    return {
        "volume": {
            "labels": [m["short"] for m in months],
            "requests": [m["requests"] for m in months],
            "bids": [m["bids"] for m in months],
        },
        "spend": {
            "labels": [m["short"] for m in months],
            "awarded": [round(m["awarded_sum"], 2) for m in months],
            "cheapest": [round(m["cheapest_sum"], 2) for m in months],
            "currency": currency,
        },
        "outcomes": {
            "labels": [k for k, _v in sorted(outcomes.items())],
            "values": [v for _k, v in sorted(outcomes.items())],
        },
        "wins": {
            "labels": [w["vendor_name"] for w in wins],
            "values": [w["n"] for w in wins],
        },
    }


@login_required
@access_required
def monthly_report(request):
    """The year view. Filters mirror Bid Analytics so the button can carry them."""
    now = timezone.now()
    try:
        year = int(request.GET.get("year") or now.year)
    except ValueError:
        year = now.year
    country = (request.GET.get("country") or "").strip()

    report = build_monthly_report(year, country)
    wants_ai = request.GET.get("ai") == "1"
    return render(request, "vendorlink/report_monthly.html", dict(
        report,
        ai_report=build_ai_report(_aggregate_ai_source(report), requested=wants_ai),
        ai_url=_ai_url(request),
        years=range(now.year, now.year - 5, -1),
        all_countries=VendorRequest.objects.values_list("country", flat=True)
                                   .distinct().order_by("country"),
        is_vm_admin=is_vendorlink_admin(request.user),
        can_edit=can_access_vendorlink(request.user),
    ))


@login_required
@access_required
def monthly_report_export(request, file_format):
    if file_format not in ("csv", "xlsx"):
        return HttpResponse(status=404)
    now = timezone.now()
    try:
        year = int(request.GET.get("year") or now.year)
    except ValueError:
        year = now.year
    country = (request.GET.get("country") or "").strip()
    selected_ids = [
        int(value) for value in request.GET.get("ids", "").split(",")
        if value.strip().isdigit() and int(value) > 0
    ]
    date_from = parse_date(request.GET.get("date_from") or "")
    date_to = parse_date(request.GET.get("date_to") or "")
    if selected_ids or date_from or date_to:
        requests = VendorRequest.objects.order_by("created_at")
        if selected_ids:
            requests = requests.filter(pk__in=selected_ids)
        if date_from:
            requests = requests.filter(created_at__date__gte=date_from)
        if date_to:
            requests = requests.filter(created_at__date__lte=date_to)
        if country:
            requests = requests.filter(country=country)
        scope = "selected requests" if selected_ids else "custom date range"
        report = build_custom_report(requests, "Bid Report", scope, date_from, date_to)
    else:
        report = build_monthly_report(year, country)
    rows = [[
        month["name"], month["requests"], month["bids"], month["awarded_count"],
        month["cheapest_sum"], month["awarded_sum"], month["premium"], "",
    ] for month in report["months"]]
    rows.extend([[
        entry["vr"].created_at.date(), entry["vr"].subject, entry["vr"].country,
        entry["vendor_count"], entry["bid_count"],
        entry["cheapest"].name if entry["cheapest"] else "",
        entry["cheapest"].total if entry["cheapest"] else "",
        entry["award"].vendor_name if entry["award"] else "Not awarded",
    ] for entry in report["per_request"]])
    return _download_response(
        rows,
        [
            "Month / Raised", "RFIs / RFI", "Bids / Country", "Awarded / Vendors",
            "Cheapest Available / Bids", "Committed / Cheapest Vendor",
            "Premium / Cheapest Total", "Awarded To",
        ],
        f"vendorlink-monthly-report-{year}",
        file_format,
    )


@login_required
@access_required
def bulk_report(request):
    """Aggregate report for selected RFIs or an arbitrary created-date range."""
    raw_ids = []
    if request.GET.get("ids"):
        raw_ids.extend(request.GET.get("ids", "").split(","))
    raw_ids.extend(request.GET.getlist("request_ids"))
    request_ids = []
    for raw in raw_ids:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in request_ids:
            request_ids.append(value)

    date_from = parse_date(request.GET.get("date_from") or "")
    date_to = parse_date(request.GET.get("date_to") or "")
    country = (request.GET.get("country") or "").strip()

    requests = VendorRequest.objects.order_by("created_at")
    if request_ids:
        requests = requests.filter(pk__in=request_ids)
        title = "Selected Requests Report"
        scope_label = f'{len(request_ids)} selected RFI{"s" if len(request_ids) != 1 else ""}'
    else:
        if date_from:
            requests = requests.filter(created_at__date__gte=date_from)
        if date_to:
            requests = requests.filter(created_at__date__lte=date_to)
        if country:
            requests = requests.filter(country=country)
        if date_from and date_to:
            scope_label = f"{date_from:%d %b %Y} to {date_to:%d %b %Y}"
        elif date_from:
            scope_label = f"from {date_from:%d %b %Y}"
        elif date_to:
            scope_label = f"up to {date_to:%d %b %Y}"
        else:
            current_year = timezone.now().year
            requests = requests.filter(created_at__year=current_year)
            scope_label = str(current_year)
        if country:
            scope_label += f" · {country}"
        title = "Bid Report — " + scope_label

    report = build_custom_report(requests, title, scope_label, date_from, date_to)
    wants_ai = request.GET.get("ai") == "1"
    return render(request, "vendorlink/report_monthly.html", dict(
        report,
        ai_report=build_ai_report(_aggregate_ai_source(report), requested=wants_ai),
        ai_url=_ai_url(request),
        years=range(timezone.now().year, timezone.now().year - 5, -1),
        all_countries=VendorRequest.objects.values_list("country", flat=True)
                                   .distinct().order_by("country"),
        selected_ids=",".join(str(pk) for pk in request_ids),
        is_vm_admin=is_vendorlink_admin(request.user),
        can_edit=can_access_vendorlink(request.user),
    ))
