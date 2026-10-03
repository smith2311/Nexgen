"""Tier 4 redesign screens — workflows and forms.

Four surfaces: the RFI list and detail, the public self-registration wizard,
the 3PL capability assessment, and the admin console.

One boundary runs through this whole module: **it renders, it does not write.**
Registration, assessment submission, OTP verification and every admin action
stay on their existing endpoints in views.py, assessment.py and
admin_console.py, which already carry the validation, the OTP flow, the captcha
and the audit trail. A redesign that reimplemented any of those would be
re-deriving a working public write path from scratch, on a form that creates
real vendor records — the risk is all downside.

The self-registration wizard is the clearest case: it looks like four steps but
posts one payload to the same ``vendor_register`` endpoint, with the stepping
done client-side. Server-side validation, OTP and captcha are untouched.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .constants import CAPABILITY_ASSESSMENT_SECTIONS
from .models import (
    CapabilityAssessment, VendorBid, VendorRegistration, VendorRequest,
    VendorRequestAttachment,
)
from .rbac import can_access_vendorlink, can_edit_vendorlink, is_vendorlink_admin

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:index")


def _ctx(request, **extra):
    base = {
        "is_vm_admin": is_vendorlink_admin(request.user),
        "can_edit": can_edit_vendorlink(request.user),
    }
    base.update(extra)
    return base


# ── 4.1 — Vendor Requests / RFI ──────────────────────────────────────────────
def _window(vendor_request):
    """Bid-window progress, as a percentage elapsed plus a countdown.

    Returns None when the request has no window set — a progress bar with no
    endpoints would have to invent them, and a bar at an arbitrary position
    reads as real information.
    """
    opens = vendor_request.bid_open_at
    closes = vendor_request.bid_close_at
    if not opens or not closes or closes <= opens:
        return None

    now = timezone.now()
    span = (closes - opens).total_seconds()
    elapsed = (now - opens).total_seconds()
    pct = max(0, min(100, round(elapsed / span * 100)))

    remaining = closes - now
    if remaining.total_seconds() <= 0:
        countdown = "closed"
    else:
        days = remaining.days
        hours = remaining.seconds // 3600
        countdown = f"{days}d {hours}h left" if days else f"{hours}h left"

    return {"pct": pct, "countdown": countdown, "opens": opens, "closes": closes}


@login_required
@access_required
def request_list(request):
    """RFI list, filterable by country and window state."""
    country = (request.GET.get("country") or "").strip()
    state = (request.GET.get("state") or "").strip()
    query = (request.GET.get("q") or "").strip()

    rows = VendorRequest.objects.annotate(bid_count=Count("bids")).order_by("-created_at")
    if country:
        rows = rows.filter(country=country)
    if query:
        rows = rows.filter(Q(subject__icontains=query) | Q(product_verticals__icontains=query))

    rows = list(rows)
    if state in {"open", "closed", "not_started"}:
        rows = [r for r in rows if r.bid_window_status == state]

    countries = sorted({r.country for r in VendorRequest.objects.all() if r.country})
    everything = VendorRequest.objects.all()
    return render(request, "vendorlink/t4_request_list.html", _ctx(
        request,
        rows=rows, result_count=len(rows), total=everything.count(),
        countries=countries, country=country, state=state, q=query,
        open_count=sum(1 for r in everything if r.bid_window_status == "open"),
        filtered=bool(country or state or query),
    ))


@login_required
@access_required
def request_detail(request, pk):
    """One RFI across five tabs, with the bid window as a progress bar."""
    vendor_request = get_object_or_404(VendorRequest, pk=pk)
    bids = list(
        VendorBid.objects.filter(vendor_request=vendor_request)
        .select_related("vendor_registration", "submitted_by")
        .order_by("bid_number", "submitted_at")
    )
    attachments = list(
        VendorRequestAttachment.objects.filter(request=vendor_request)
    )

    # The recipient snapshot is stored as text on the request so history stays
    # accurate even when a vendor's email later changes; split for display
    # rather than re-querying live vendors, which would show today's list
    # against an old request.
    recipients = [
        line.strip() for line in (vendor_request.recipients or "").replace(",", "\n").splitlines()
        if line.strip()
    ]

    timeline = [{"when": vendor_request.created_at, "text": "Request raised", "kind": "request"}]
    for bid in bids:
        name = bid.vendor_registration.vendor_name if bid.vendor_registration else "A vendor"
        timeline.append({"when": bid.submitted_at, "kind": "bid",
                         "text": f"{name} submitted bid #{bid.bid_number}"})
    try:
        evaluation = vendor_request.evaluation
        timeline.append({"when": evaluation.triggered_at, "kind": "evaluation",
                         "text": f"AI evaluation {evaluation.get_status_display().lower()}"})
    except Exception:
        evaluation = None
    timeline.sort(key=lambda t: t["when"], reverse=True)

    return render(request, "vendorlink/t4_request_detail.html", _ctx(
        request,
        vendor_request=vendor_request, bids=bids, bid_count=len(bids),
        attachments=attachments, recipients=recipients,
        recipient_count=vendor_request.recipient_count or len(recipients),
        timeline=timeline, evaluation=evaluation,
        window=_window(vendor_request),
    ))


# ── 4.2 — Vendor Self-Registration (public) ──────────────────────────────────
#: The wizard's steps. Field names match VendorRegistrationForm so the stepper
#: can show/hide real inputs rather than duplicating them — one payload, one
#: endpoint, all validation still server-side.
REGISTRATION_STEPS = [
    {"key": "company", "title": "Company",
     "hint": "Who you are and what you are registered as."},
    {"key": "contact", "title": "Contact",
     "hint": "Who we should reach, and where to send the verification code."},
    {"key": "location", "title": "Location",
     "hint": "Where your warehousing sits."},
    {"key": "capabilities", "title": "Capabilities",
     "hint": "What your operation can actually do."},
]


def registration_wizard(request):
    """The public registration form, presented as a stepper.

    Deliberately GET-only here. The form posts to the existing
    ``vendorlink:vendor_register`` endpoint, which owns validation, the email
    OTP and the captcha — none of which this view reimplements. Stepping is a
    presentation concern handled in the browser, so a visitor with JavaScript
    off still sees one long form that works.
    """
    from .forms import VendorRegistrationForm

    from .utils import continent_country_map_json

    return render(request, "vendorlink/t4_register.html", {
        "form": VendorRegistrationForm(),
        "steps": REGISTRATION_STEPS,
        # This view builds its own context rather than going through a _ctx
        # helper, so the cascade map has to be passed explicitly.
        "continent_country_map": continent_country_map_json(),
    })


# ── 4.3 — 3PL Capability Assessment ──────────────────────────────────────────
def _assessment_progress(assessment):
    """Per-section completion, for the left rail.

    Counts only the fields the SOW marks required — a section is "done" when
    nothing mandatory is missing, not when every optional box is filled, or the
    rail would never reach 100% and would stop meaning anything.
    """
    sections = []
    done_total = required_total = 0
    for title, rows in CAPABILITY_ASSESSMENT_SECTIONS:
        required = [name for name, _label, _hint, is_required, _kind in rows if is_required]
        filled = [name for name in required if (getattr(assessment, name, "") or "").strip()]
        done_total += len(filled)
        required_total += len(required)
        sections.append({
            "title": title,
            "fields": [
                {"name": name, "label": label, "hint": hint,
                 "required": is_required, "kind": kind,
                 "value": getattr(assessment, name, "")}
                for name, label, hint, is_required, kind in rows
            ],
            "required": len(required),
            "filled": len(filled),
            "complete": len(filled) == len(required) and bool(required),
        })
    pct = round(done_total / required_total * 100) if required_total else 0
    return sections, pct


@login_required
@admin_required
def assessment_review(request, pk):
    """Admin's read-only mirror of a submitted assessment.

    Read-only on purpose: approve / reject / promote stay on their existing
    endpoints in assessment.py, which handle the vendor promotion and the
    notification email. This screen shows what was answered and links to those.
    """
    assessment = get_object_or_404(CapabilityAssessment, pk=pk)
    sections, pct = _assessment_progress(assessment)
    return render(request, "vendorlink/t4_assessment_review.html", _ctx(
        request, assessment=assessment, sections=sections, percent=pct,
    ))


# ── 4.4 — Admin Console ──────────────────────────────────────────────────────
@login_required
@admin_required
def admin_console(request):
    """Overview with the module's counts and a route into each admin area."""
    return render(request, "vendorlink/t4_admin.html", _ctx(
        request,
        stats={
            "users": VendorRegistration.objects.count(),
            "pending_regs": VendorRegistration.objects.filter(
                status=VendorRegistration.STATUS_PENDING).count(),
            "requests": VendorRequest.objects.count(),
            "bids": VendorBid.objects.count(),
            "assessments": CapabilityAssessment.objects.count(),
            "awaiting_review": CapabilityAssessment.objects.filter(
                status__in=[CapabilityAssessment.STATUS_SUBMITTED,
                            CapabilityAssessment.STATUS_UNDER_REVIEW]).count(),
        },
        sections=[
            {"label": "Users", "hint": "Accounts, roles and access",
             "url": "vendorlink:admin_users"},
            {"label": "Registrations", "hint": "Approve or reject vendor sign-ups",
             "url": "vendorlink:registration_list"},
            {"label": "Assessments", "hint": "3PL capability submissions",
             "url": "vendorlink:assessment_list"},
            {"label": "Masters", "hint": "Reference data",
             "url": "vendorlink:admin_masters"},
            {"label": "Capability config", "hint": "What vendors can be scored on",
             "url": "vendorlink:admin_capabilities"},
            {"label": "Insights", "hint": "Per-user activity",
             "url": "vendorlink:admin_insights"},
        ],
    ))
