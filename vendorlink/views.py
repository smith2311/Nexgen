import io
import json
import logging
import random
import secrets
import string
import time

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

# Reuse the platform's existing async email senders instead of writing new ones.
from product_scanning.feedback_notifications import _send_async

from . import bidcompare
from .utils import (
    canonical_countries,
    canonical_country,
    continent_choices,
    continent_country_map_json,
    continent_name,
    continent_of,
    filter_countries_by_continent,
    geonames_dial_map_json,
    geonames_iso_map_json,
    get_cities_for_country,
)
from .capabilities import (
    active_capabilities,
    active_codes,
    adoption_rows,
    capability_rows,
    copy_capabilities,
)
from .constants import ALLOWED_REQUEST_UPLOAD_EXTS
from .forms import VendorBidForm, VendorForm, VendorRegistrationForm, VendorRequestForm
from . import mailer, vendoraccess
from .models import BidAward, CapabilityAssessment, CountrySearchEvent, GptConversation, GptMessage, Vendor, VendorBid, VendorCommercial, VendorEvaluation, VendorKPIRecord, VendorRegistration, VendorRequest, VendorRequestAttachment, VendorScorecard
from .rbac import (
    can_access_vendorlink, can_edit_vendorlink, can_manage_award, is_internal_user,
    is_vendor_user, is_vendorlink_admin,
)

_SCRIPT_RE = re.compile(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)


def api_cities(request):
    """GET /vendorlink/api/cities/?cc=IN — returns sorted city names for a country ISO code."""
    cc = (request.GET.get("cc") or "").strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return JsonResponse({"cities": []})
    return JsonResponse({"cities": get_cities_for_country(cc)})


def _generate_vendor_password():
    """Generate an 8-char temp password guaranteeing 1 upper, 1 lower, 1 digit, 1 special."""
    specials = "!@#$%^&*"
    pool = string.ascii_uppercase + string.ascii_lowercase + string.digits + specials
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(specials),
    ] + [secrets.choice(pool) for _ in range(4)]
    # Fisher-Yates shuffle via secrets
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _clean_html(value):
    """Light sanitisation of staff-entered rich text — strip <script> blocks."""
    return _SCRIPT_RE.sub("", value or "").strip()


logger = logging.getLogger(__name__)

# ── Access decorators ──────────────────────────────────────────────────────
access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:index")
#: Internal-only screens. Sends an external vendor back to the request list
#: rather than the launcher: the list is the vendor portal's own landing
#: page, so a bounce there reads as "not for you" instead of a redirect loop.
internal_required = user_passes_test(
    is_internal_user, login_url="vendorlink:request_list",
)
_BID_ANALYTICS_URL = "vendorlink:bid_analytics"


def _ctx(request, **extra):
    """Common context shared across module pages."""
    base = {"is_vm_admin": is_vendorlink_admin(request.user)}
    # Every screen with a country picker or country filter needs this, and the
    # ones that forgot it silently fell back to the flat 252-country list — the
    # cascade JS treats a missing map as "no narrowing". Put on the shared
    # context so a new country screen gets it by default rather than by
    # remembering.
    base["continent_choices"] = continent_choices()
    base["continent_country_map"] = continent_country_map_json()
    base.update(extra)
    return base


def _commercial_stat():
    """Launcher tile subtitle for the Commercial Database module.

    Leads with expiring cards when there are any — on a launcher, "3 expiring
    soon" is the line that should pull someone into the module, not the total.
    """
    total = VendorCommercial.objects.count()
    if not total:
        return "No rate cards yet"
    expiring = sum(1 for c in VendorCommercial.objects.all() if c.is_expiring_soon or c.is_expired)
    label = f"{total} rate card{'' if total == 1 else 's'}"
    return f"{label} · {expiring} need attention" if expiring else label


def _bid_stat():
    """Launcher tile subtitle for the Commercial Database card.

    Leads with quotes needing a decision rather than the lifetime bid count:
    the card opens Bid Analytics, and "4 awaiting a decision" is the line that
    should pull someone in.
    """
    total = VendorBid.objects.count()
    if not total:
        return "No bids yet"
    undecided = (
        VendorRequest.objects.filter(bids__isnull=False, award__isnull=True)
        .distinct().count()
    )
    label = f"{total} bid{'' if total == 1 else 's'}"
    return f"{label} · {undecided} awaiting a decision" if undecided else label


def _assessment_stat():
    """Launcher tile subtitle for the 3PL Capability Assessment module.

    Leads with how many are waiting on Meril rather than the lifetime total —
    a launcher tile's job is to say what needs attention today.
    """
    total = CapabilityAssessment.objects.count()
    if not total:
        return "No assessments yet"
    pending = CapabilityAssessment.objects.filter(
        status__in=[CapabilityAssessment.STATUS_SUBMITTED, CapabilityAssessment.STATUS_UNDER_REVIEW]
    ).count()
    label = f"{total} assessment{'' if total == 1 else 's'}"
    return f"{label} · {pending} awaiting review" if pending else label


def _kpi_stat():
    total = VendorKPIRecord.objects.count()
    if not total:
        return "No KPI records yet"
    vendors = VendorKPIRecord.objects.values("vendor").distinct().count()
    return f"{total} record{'' if total == 1 else 's'} · {vendors} vendor{'' if vendors == 1 else 's'} tracked"


def _scorecard_stat():
    scored_vendors = VendorScorecard.objects.values("vendor").distinct().count()
    if not scored_vendors:
        return "No vendors scored yet"
    return f"{scored_vendors} vendor{'' if scored_vendors == 1 else 's'} scored"


def _inquiry_stat(user):
    """Launcher chip for the Inquiries card.

    Scoped exactly as request_list scopes its own queryset, so the number on the
    card is the number of rows the user finds when they open it — a launcher
    that promises 40 and then shows 3 is worse than showing nothing.
    """
    qs = VendorRequest.objects.all()
    if not is_vendorlink_admin(user):
        qs = qs.filter(raised_by=user)
    total = qs.count()
    if not total:
        return "No inquiries yet"
    return f"{total} inquir{'y' if total == 1 else 'ies'} raised"


# ── Country names: one spelling per country ─────────────────────────────────
# Vendor.country is free text, so the column holds "India", "india" and
# " India " side by side. Everything below folds those together before showing
# or counting them — see vendorlink.utils.canonical_country for why the column
# is like that and why the spellings are merged rather than rejected.

def _vendor_country_names(active_only=True):
    """Every country in Vendor Master, one entry per country."""
    vendors = Vendor.objects.filter(is_active=True) if active_only else Vendor.objects.all()
    return canonical_countries(vendors.values_list("country", flat=True))


def _country_variants(name):
    """The raw stored spellings that mean ``name``.

    Filtering has to go through this rather than ``filter(country=name)``:
    picking "India" out of the dropdown must return the vendors saved as
    "india" too, which an exact match quietly drops. ``iexact`` would fix the
    case half only and still miss " India ".
    """
    wanted = canonical_country(name)
    if not wanted:
        return []
    return [
        raw for raw in Vendor.objects.values_list("country", flat=True).distinct()
        if canonical_country(raw) == wanted
    ]


def _vendors_in_country(queryset, name):
    return queryset.filter(country__in=_country_variants(name))


def _top_countries(limit=3):
    """Top-N most-searched countries with their vendor counts."""
    # Search events are logged under whatever spelling was current at the time,
    # so they are folded here too — otherwise one country splits into several
    # chips that each under-report their own search count.
    tally = {}
    for row in CountrySearchEvent.objects.values("country").annotate(searches=Count("id")):
        name = canonical_country(row["country"])
        if name:
            tally[name] = tally.get(name, 0) + row["searches"]

    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]
    return [
        {
            "country": name,
            "searches": searches,
            "vendors": _vendors_in_country(
                Vendor.objects.filter(is_active=True), name
            ).count(),
        }
        for name, searches in ranked
    ]


# ── VendorLink platform launcher ────────────────────────────────────────────
@login_required
@access_required
def index(request):
    """Top-level VendorLink launcher — the five-module 3PL Database landing."""
    # External vendor accounts get a dedicated minimal portal.
    if is_vendor_user(request.user):
        try:
            vendor_reg = VendorRegistration.objects.filter(
                email=request.user.email,
                status=VendorRegistration.STATUS_APPROVED,
            ).order_by("-reviewed_at").first()
        except Exception:
            vendor_reg = None
        # A vendor's own record, on their own portal. Bids are attributed by
        # the submitting user -- that is how VendorBid stores them -- while wins
        # are looked up by email, because an award snapshots the address it was
        # sent to and has to keep counting even when the registration row behind
        # it is replaced on a re-approval.
        email = (request.user.email or "").strip()
        my_bids = list(
            VendorBid.objects.filter(submitted_by=request.user)
            .select_related("vendor_request")
            .order_by("-submitted_at")
        )
        awarded_bid_ids = set(
            BidAward.objects.filter(vendor_email__iexact=email).values_list("bid_id", flat=True)
            if email else []
        )
        # Flagged per bid so the portal can mark the one that actually won,
        # rather than listing eight identical-looking rows and reporting the
        # outcome only as a number in a tile above them.
        #
        # `total_cost` is attached the same way _vendor_bid_groups does it for
        # staff, through the same _bid_total -- the vendor's own screens had no
        # total at all, so a vendor could read back all seven cost lines they
        # quoted and still not see the figure staff were comparing them on.
        # Computing it here rather than a second way keeps the two identical.
        for bid in my_bids:
            bid.is_awarded_to_me = bid.pk in awarded_bid_ids
            bid.total_cost = _bid_total(bid)

        # What they can still act on. The same visibility rule the request list
        # and bid_submit apply -- it lives in vendoraccess precisely so these
        # three cannot drift.
        identity = vendoraccess.identify(request.user)
        quoted_request_ids = {bid.vendor_request_id for bid in my_bids}
        open_to_quote = [
            vendor_request
            for vendor_request in vendoraccess.visible_requests(request.user, identity)
                .order_by("-created_at")[:40]
            if vendor_request.is_bid_open and vendor_request.pk not in quoted_request_ids
        ]

        return render(request, "vendorlink/vendor_portal.html", {
            "vendor_reg": vendor_reg,
            "my_bids": my_bids,
            "bids_submitted": len(my_bids),
            "requests_bid_on": len(quoted_request_ids),
            "bids_won": BidAward.wins_for_email(email),
            # The headline action: requests they may still quote and have not.
            # A vendor's first question on opening the portal is "is there
            # anything waiting on me", and until now the answer was two clicks
            # into a collapsed list.
            "open_to_quote": open_to_quote[:6],
            "open_to_quote_count": len(open_to_quote),
            # Live, not historical: an award the requester has since withdrawn
            # or re-assigned no longer appears here, the same as it no longer
            # appears on the requester's own screens. See BidAward.wins_for_email.
            "won_awards": (
                BidAward.objects.filter(
                    vendor_email__iexact=email,
                ).select_related("vendor_request", "bid").order_by("-awarded_at")[:5]
                if email else []
            ),
        })

    total_vendors = Vendor.objects.filter(is_active=True).count()
    # Computed before `modules` because the Inquiries card quotes it.
    reachable_vendors = Vendor.objects.filter(is_active=True).exclude(email="").count()

    # Five cards, rendered in the platform landing page's .app-card shape (see
    # templates/main/landing_page.html). Each carries a live stat as its first
    # chip — a launcher's job is to say what needs attention today — then two
    # feature labels, matching the density of the Logistics Performance and Visa
    # Management cards on that page.
    #
    # 3PL Capability Assessment is deliberately not here: the launcher is scoped
    # to these five per request. Nothing was deleted — the module and its data
    # are untouched and still reachable from the Admin Console.
    # Order is the one the business asked for: the master data first, then
    # the commercial record, then the performance screens, and inquiries
    # last. Cards render in this sequence, so this list is the ordering.
    modules = [
        {
            "name": "Vendor Master",
            "description": "Central repository of approved 3PL vendors",
            "icon": "fas fa-truck-field",
            "color": "blue",
            "url": reverse("vendorlink:vm_home"),
            "stats": [
                {"icon": "fas fa-database",
                 "text": f"{total_vendors} vendor{'' if total_vendors == 1 else 's'}"},
                {"icon": "fas fa-address-book", "text": "Directory"},
                {"icon": "fas fa-shield-halved", "text": "Capability Matrix"},
            ],
        },
        {
            "name": "Commercial Database",
            "description": "Compare vendor quotations, rate cards and AI bid evaluations",
            "icon": "fas fa-file-invoice-dollar",
            "color": "purple",
            # Bid Analytics, not the rate-card list. The list was the landing
            # view and on this data it opens on an empty table -- standing rate
            # cards are barely used, while the quotes vendors send against RFIs
            # are what people come here to compare. The rate-card screen keeps
            # its URL and every flow on it; it is linked from the Bid Analytics
            # header instead of being the door everyone has to walk through.
            "url": reverse("vendorlink:bid_analytics"),
            "stats": [
                {"icon": "fas fa-gavel", "text": _bid_stat()},
                {"icon": "fas fa-chart-column", "text": "Bid Analytics"},
                {"icon": "fas fa-file-contract", "text": _commercial_stat()},
            ],
        },
        {
            "name": "KPI Dashboard",
            "description": "Vendor performance monitoring, KPI trends and analytics",
            "icon": "fas fa-gauge-high",
            "color": "orange",
            "url": reverse("vendorlink:kpi_dashboard"),
            "stats": [
                {"icon": "fas fa-chart-line", "text": _kpi_stat()},
                {"icon": "fas fa-arrow-trend-up", "text": "KPI Trends"},
                {"icon": "fas fa-scale-balanced", "text": "Vendor Compare"},
            ],
        },
        {
            "name": "Vendor Scorecard",
            "description": "Weighted evaluation & vendor ranking",
            "icon": "fas fa-ranking-star",
            "color": "pink",
            "url": reverse("vendorlink:scorecard_list"),
            "stats": [
                {"icon": "fas fa-star", "text": _scorecard_stat()},
                {"icon": "fas fa-list-check", "text": "Scoring"},
                {"icon": "fas fa-ranking-star", "text": "Ranking"},
            ],
        },
        {
            "name": "Inquiries",
            "description": "Raise RFIs and track how many vendors each one reached",
            "icon": "fas fa-inbox",
            "color": "teal",
            # The existing "My Requests" destination, unchanged — this card only
            # renames and re-homes it. Raise-a-Request lives in that screen's own
            # header (request_list.html), so it is one click further, not gone.
            "url": reverse("vendorlink:request_list"),
            "stats": [
                {"icon": "fas fa-paper-plane", "text": _inquiry_stat(request.user)},
                {"icon": "fas fa-envelope", "text": f"{reachable_vendors} reachable"},
                {"icon": "fas fa-gavel", "text": "Bid Responses"},
            ],
        },
    ]

    is_admin = is_vendorlink_admin(request.user)
    pending_registrations = 0
    reg_stats = None
    recent_regs = []
    if is_admin:
        pending_registrations = VendorRegistration.objects.filter(status=VendorRegistration.STATUS_PENDING).count()
        reg_stats = {
            "total": VendorRegistration.objects.count(),
            "pending": pending_registrations,
            "approved": VendorRegistration.objects.filter(status=VendorRegistration.STATUS_APPROVED).count(),
            "rejected": VendorRegistration.objects.filter(status=VendorRegistration.STATUS_REJECTED).count(),
        }
        recent_regs = list(VendorRegistration.objects.order_by("-submitted_at")[:6])

    return render(request, "vendorlink/index.html", _ctx(
        request,
        modules=modules,
        total_vendors=total_vendors,
        total_countries=len(_vendor_country_names()),
        reachable_vendors=reachable_vendors,
        pending_registrations=pending_registrations,
        reg_stats=reg_stats,
        recent_regs=recent_regs,
    ))


# ── Vendor Master landing (sub-launcher + Top-3 widget) ─────────────────────
@login_required
@access_required
def vm_home(request):
    total_vendors = Vendor.objects.filter(is_active=True).count()
    total_countries = len(_vendor_country_names())
    # Vendors declaring every capability the master currently lists. Counted by
    # comparing each vendor's link count to the master's size rather than
    # AND-ing nine columns, so the tile stays right the day a tenth is added.
    active_caps = active_capabilities()
    fully_capable = (
        Vendor.objects.filter(is_active=True)
        .annotate(held=Count("capabilities", filter=Q(capabilities__in=active_caps), distinct=True))
        .filter(held=len(active_caps))
        .count()
    ) if active_caps else 0

    return render(request, "vendorlink/home.html", _ctx(
        request,
        total_vendors=total_vendors,
        total_countries=total_countries,
        fully_capable=fully_capable,
        capability_total=len(active_caps),
        top_countries=_top_countries(3),
        recent_vendors=Vendor.objects.filter(is_active=True).order_by("-created_at")[:5],
    ))


# ── Vendor directory (table + search + filters) ─────────────────────────────
@login_required
@access_required
def vendor_list(request):
    q = (request.GET.get("q") or "").strip()
    continent = (request.GET.get("continent") or "").strip().upper()
    country = (request.GET.get("country") or "").strip()
    capability = (request.GET.get("capability") or "").strip()

    vendors = Vendor.objects.filter(is_active=True).prefetch_related("capabilities")

    if q:
        vendors = vendors.filter(
            Q(vendor_name__icontains=q)
            | Q(city__icontains=q)
            | Q(state__icontains=q)
            | Q(country__icontains=q)
            | Q(contact_person__icontains=q)
            | Q(warehouse_location__icontains=q)
        )
        # If the query names a country we hold vendors in, log it for the Top-3
        # widget — under the canonical spelling, so searching "india" and
        # "India" feed one chip instead of two.
        typed = canonical_country(q)
        if typed and typed in _vendor_country_names(active_only=False):
            CountrySearchEvent.objects.create(
                country=typed,
                user=request.user if request.user.is_authenticated else None,
            )

    if continent:
        # Narrowed by the continent each vendor's country sits in. Applied
        # before the country filter so picking a continent alone is a valid
        # query on its own — "who do we have in Africa" is a question the
        # directory could not answer before.
        in_continent = [
            raw for raw in Vendor.objects.values_list("country", flat=True).distinct()
            if continent_of(raw) == continent
        ]
        vendors = vendors.filter(country__in=in_continent)

    if country:
        vendors = _vendors_in_country(vendors, country)
        CountrySearchEvent.objects.create(
            country=canonical_country(country),
            user=request.user if request.user.is_authenticated else None,
        )

    if capability in active_codes():
        vendors = vendors.filter(capabilities__code=capability)

    return render(request, "vendorlink/vendor_list.html", _ctx(
        request,
        vendors=vendors,
        q=q,
        # Echoed back canonically so the dropdown re-selects the option the user
        # picked even when the URL carries a variant spelling.
        country=canonical_country(country),
        continent=continent,
        capability=capability,
        countries=filter_countries_by_continent(
            _vendor_country_names(active_only=False), continent,
        ),
        capability_fields=capability_rows(),
        capability_total=len(capability_rows()),
        result_count=vendors.count(),
    ))


def _ensure_vendor_registration(vendor, actor):
    """Give an admin-created vendor the registration row the portal keys on.

    Everything a vendor login can do is resolved through an approved
    :class:`VendorRegistration`: the requests they see, the bids they may
    submit, and ``VendorBid.vendor_registration``, which is how the comparison
    matrix and the AI evaluator identify who quoted. A vendor added through
    Vendor Master and granted portal access had none, so they could log in and
    then do nothing — the RFI list was empty and bidding refused them.

    Writing the row here rather than special-casing the missing case in every
    reader keeps one shape of "a vendor on this platform". Marked approved and
    reviewed by whoever created the vendor, which is what actually happened: an
    admin vouched for them directly instead of reviewing an application.

    Idempotent — re-granting access for a vendor that already has a
    registration updates the link rather than adding a second row.
    """
    from django.utils import timezone

    existing = (
        VendorRegistration.objects
        .filter(email__iexact=vendor.email)
        .order_by("-pk")
        .first()
    )
    if existing:
        # Already applied through the public form; approve and link it rather
        # than creating a rival row for the same mailbox.
        changed = []
        if existing.status != VendorRegistration.STATUS_APPROVED:
            existing.status = VendorRegistration.STATUS_APPROVED
            existing.reviewed_by = actor
            existing.reviewed_at = timezone.now()
            changed += ["status", "reviewed_by", "reviewed_at"]
        if existing.vendor_id != vendor.pk:
            existing.vendor = vendor
            changed.append("vendor")
        for field in ("country", "state", "city", "warehouse_location"):
            value = getattr(vendor, field)
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                changed.append(field)
        if changed:
            existing.save(update_fields=changed)
        return existing

    registration = VendorRegistration.objects.create(
        vendor_name=vendor.vendor_name,
        country=vendor.country,
        state=vendor.state,
        city=vendor.city,
        warehouse_location=vendor.warehouse_location,
        contact_person=vendor.contact_person,
        email=vendor.email,
        mobile=vendor.mobile,
        website=vendor.website,
        status=VendorRegistration.STATUS_APPROVED,
        reviewed_by=actor,
        reviewed_at=timezone.now(),
        vendor=vendor,
    )
    copy_capabilities(vendor, registration)
    return registration


def _create_vendor_portal_account(vendor, reviewer):
    """Create (or reset) a Django login account for a Vendor record.

    Returns (temp_password, created) where created=True if the account is new.
    Raises no exceptions — failures are swallowed and (None, False) returned.
    """
    from django.contrib.auth.models import Group, User
    from main.models import UserProfile

    if not vendor.email:
        return None, False
    try:
        temp_password = _generate_vendor_password()
        name_parts = (vendor.contact_person or "").split(None, 1)
        vendor_user, created = User.objects.get_or_create(
            username=vendor.email,
            defaults={
                "email": vendor.email,
                "first_name": name_parts[0] if name_parts else "",
                "last_name": name_parts[1] if len(name_parts) > 1 else "",
                "is_active": True,
            },
        )
        # Always reset the password so admin can re-send fresh credentials.
        vendor_user.set_password(temp_password)
        vendor_user.is_active = True
        vendor_user.save()

        profile, _ = UserProfile.objects.get_or_create(user=vendor_user)
        profile.force_password_change = True
        profile.save()

        group, _ = Group.objects.get_or_create(name="VendorUser")
        vendor_user.groups.add(group)
        return temp_password, created
    except Exception:
        return None, False


def _send_vendor_credentials_async(vendor, temp_password, created=True):
    """Send the credentials mail without holding the response.

    A real save measured 3.7 ms for the row, 184 ms for the account and 916 ms
    for this email -- and EMAIL_TIMEOUT is 20, so an unreachable mail server
    held the vendor form for twenty seconds. Threaded, matching how award
    notices already leave this module.

    The DB connection is closed after the send: a thread that borrows one and
    walks away leaks it out of the pool.
    """
    import threading

    def _worker():
        try:
            _send_vendor_credentials(vendor, temp_password, created=created)
        except Exception:
            logger.exception("Vendor credentials email failed for %s", vendor.email)
        finally:
            from django.db import connection
            connection.close()

    threading.Thread(target=_worker, daemon=True).start()


def _send_vendor_credentials(vendor, temp_password, created=True):
    """Email portal credentials to the vendor, on the shared branded template.

    Was a plain-text mail assembled inline here: no branding, no logo, and a
    different shape from every other message VendorLink sends. It now renders
    vendorlink/email/portal_credentials.{html,txt} through
    :mod:`vendorlink.mailer`, which attaches the Meril logo inline so it shows
    without the recipient having to allow remote images.
    """
    if not vendor.email or not temp_password:
        return

    from . import mailer

    site_url = (getattr(settings, "SITE_URL", "") or "https://nexgen.merillife.com").rstrip("/")
    subject = (
        "Meril VendorLink — Your Vendor Portal Access" if created
        else "Meril VendorLink — Updated Portal Credentials"
    )
    # async_=False: the caller decides whether to thread. vendor_create already
    # calls this from a background thread, and nesting one inside another buys
    # nothing while making a failure harder to trace.
    mailer.send_template(
        subject,
        vendor.email,
        "vendorlink/email/portal_credentials.html",
        text_template="vendorlink/email/portal_credentials.txt",
        context={
            "vendor_name": vendor.vendor_name,
            "contact_person": vendor.contact_person or "",
            "login_email": vendor.email,
            "temp_password": temp_password,
            "portal_url": f"{site_url}/",
            "created": created,
        },
        async_=False,
    )


@login_required
@access_required
def vendor_detail(request, pk):
    from django.contrib.auth import get_user_model
    vendor = get_object_or_404(Vendor, pk=pk)
    User = get_user_model()
    portal_user = User.objects.filter(username=vendor.email).first() if vendor.email else None
    # No vendor scoring here any more. The 1-5 facility/support/compliance/
    # delivery ratings are performance data, and performance data has two
    # purpose-built homes — the KPI Dashboard (monthly readings) and the Vendor
    # Scorecard (weighted attributes with a mandatory comment per score). A
    # third, unweighted, uncommented set of ratings on the master page invited
    # two people to answer the same question in two places and get different
    # answers. The VendorScore model itself stays: ai_evaluator.py reads it as
    # reputation context, and Django admin remains the place to set it.
    return render(request, "vendorlink/vendor_detail.html", _ctx(
        request,
        vendor=vendor,
        capability_fields=capability_rows(),
        capability_total=len(capability_rows()),
        portal_user=portal_user,
        # Phase 2: every rate card held against this vendor, newest contract
        # first — the plan's "vendor detail view shows all rate cards linked to
        # a specific vendor".
        commercials=vendor.commercials.order_by("-contract_start", "-created_at"),
        can_edit_commercial=can_edit_vendorlink(request.user),
    ))


# ── Capability matrix sub-module ────────────────────────────────────────────
@login_required
@access_required
def capability_matrix(request):
    vendors = list(
        Vendor.objects.filter(is_active=True)
        .order_by("vendor_name")
        # One tick per capability per row — without this the grid is a query
        # per cell.
        .prefetch_related("capabilities")
    )
    columns = capability_rows()
    return render(request, "vendorlink/capability_matrix.html", _ctx(
        request,
        vendors=vendors,
        capability_fields=columns,
        capability_total=len(columns),
        # Per-capability adoption for the summary bar — one aggregate rather
        # than the count-per-capability loop this replaced.
        summary=adoption_rows(vendor_total=len(vendors)),
    ))


# ── Create / Edit / Delete (admin) ──────────────────────────────────────────
@login_required
@admin_required
def vendor_create(request):
    if request.method == "POST":
        form = VendorForm(request.POST)
        if form.is_valid():
            vendor = form.save(commit=False)
            vendor.created_by = request.user
            vendor.save()
            # save(commit=False) skips M2M, so the capability checkboxes would
            # be silently dropped without this.
            form.save_m2m()
            grant = request.POST.get("grant_portal_access") == "1"
            if grant and vendor.email:
                temp_pw, created = _create_vendor_portal_account(vendor, request.user)
                _ensure_vendor_registration(vendor, request.user)
                if temp_pw:
                    # Off-thread: see _send_vendor_credentials_async. The
                    # message says "being sent" because by the time this page
                    # renders the send has not finished -- and even before,
                    # fail_silently meant "sent" was never verified.
                    _send_vendor_credentials_async(vendor, temp_pw, created=True)
                    messages.success(request, f'Vendor "{vendor.vendor_name}" added. Portal credentials are being emailed to {vendor.email}.')
                else:
                    messages.warning(request, f'Vendor "{vendor.vendor_name}" added, but portal account creation failed. Try granting access from the vendor detail page.')
            else:
                messages.success(request, f'Vendor "{vendor.vendor_name}" added successfully.')
            return redirect("vendorlink:vendor_detail", pk=vendor.pk)
    else:
        form = VendorForm()
    return render(request, "vendorlink/vendor_form.html", _ctx(
        request, form=form, mode="create",
        country_iso_map=geonames_iso_map_json(),
        country_dial_map=geonames_dial_map_json(),
    ))


@login_required
@admin_required
def vendor_edit(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    if request.method == "POST":
        form = VendorForm(request.POST, instance=vendor)
        if form.is_valid():
            form.save()
            messages.success(request, f'Vendor "{vendor.vendor_name}" updated.')
            return redirect("vendorlink:vendor_detail", pk=vendor.pk)
    else:
        form = VendorForm(instance=vendor)
    return render(request, "vendorlink/vendor_form.html", _ctx(
        request, form=form, mode="edit", vendor=vendor,
        country_iso_map=geonames_iso_map_json(),
        country_dial_map=geonames_dial_map_json(),
    ))


@login_required
@admin_required
@require_POST
def vendor_grant_access(request, pk):
    """Grant (or re-send) portal access for an admin-created vendor."""
    vendor = get_object_or_404(Vendor, pk=pk)
    if not vendor.email:
        messages.error(request, "Cannot grant portal access — this vendor has no email address. Add an email first.")
        return redirect("vendorlink:vendor_detail", pk=pk)
    temp_pw, created = _create_vendor_portal_account(vendor, request.user)
    if temp_pw:
        # Same registration backfill as the create path — granting access from
        # here has to leave the vendor in the same state, or the portal works
        # for one route and not the other.
        _ensure_vendor_registration(vendor, request.user)
        _send_vendor_credentials_async(vendor, temp_pw, created=created)
        action = "created, credentials on their way" if created else "reset — new credentials on their way"
        messages.success(request, f"Portal account {action} to {vendor.email}.")
    else:
        messages.error(request, "Portal account creation failed. Check that the email is valid and try again.")
    return redirect("vendorlink:vendor_detail", pk=pk)


@login_required
@admin_required
@require_POST
def vendor_delete(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    name = vendor.vendor_name
    vendor.delete()
    messages.success(request, f'Vendor "{name}" deleted.')
    return redirect("vendorlink:vendor_list")


# ── Excel export / import ───────────────────────────────────────────────────
#: The vendor's own text columns. Capability columns are appended at call time
#: by :func:`_export_columns` — they come from the Capability master, which is a
#: query, so this cannot be one module-level list any more. Building it at
#: import would also read the database before ``migrate`` has run.
_VENDOR_TEXT_COLUMNS = [
    ("vendor_name", "Vendor Name"),
    ("country", "Country"),
    ("state", "State"),
    ("city", "City"),
    ("warehouse_location", "Warehouse Location"),
    ("contact_person", "Contact Person"),
    ("email", "Email"),
    ("mobile", "Mobile"),
    ("website", "Website"),
]


def _export_columns():
    """[(field, header label), …] — text columns, then one per capability.

    The export and the import template come from this same function, which is
    what lets an admin download the template, fill it in and upload it back
    after a capability has been added: the new column is in both halves.
    """
    return _VENDOR_TEXT_COLUMNS + [
        (code, label) for code, label, _icon in capability_rows()
    ]


def _build_workbook(vendors, sheet_title="Vendor Master"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title

    columns = _export_columns()
    cap_codes = {code for code, _label, _icon in capability_rows()}

    header_fill = PatternFill("solid", fgColor="0076C8")
    header_font = Font(bold=True, color="FFFFFF", size=11)

    headers = ["SR#"] + [label for _, label in columns]
    ws.append(headers)
    for col, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for idx, v in enumerate(vendors, start=1):
        row = [idx]
        for field, _ in columns:
            # Capability columns are links, not attributes — asked through
            # has_capability so a missing one reads No rather than blank.
            val = v.has_capability(field) if field in cap_codes else getattr(v, field)
            row.append("Yes" if val is True else "No" if val is False else (val or ""))
        ws.append(row)

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 20
    ws.freeze_panes = "A2"
    return wb


@login_required
@access_required
def export_excel(request):
    vendors = Vendor.objects.filter(is_active=True).order_by("vendor_name")
    wb = _build_workbook(vendors)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="vendor_master_export.xlsx"'
    return resp


@login_required
@admin_required
def import_template(request):
    wb = _build_workbook(Vendor.objects.none(), sheet_title="Template")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="vendor_master_template.xlsx"'
    return resp


def _truthy(val):
    return str(val).strip().lower() in {"yes", "y", "true", "1", "✓"}


@login_required
@admin_required
def import_excel(request):
    if request.method != "POST" or "file" not in request.FILES:
        return redirect("vendorlink:vendor_list")

    try:
        wb = openpyxl.load_workbook(request.FILES["file"], data_only=True)
    except Exception:
        messages.error(request, "Could not read the file. Please upload a valid .xlsx file.")
        return redirect("vendorlink:vendor_list")

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        messages.warning(request, "The file has no data rows.")
        return redirect("vendorlink:vendor_list")

    # Map header label -> column index (case-insensitive, ignores SR#).
    label_to_field = {label.lower(): field for field, label in _export_columns()}
    header = [str(h or "").strip().lower() for h in rows[0]]
    col_map = {}
    for i, h in enumerate(header):
        if h in label_to_field:
            col_map[label_to_field[h]] = i

    if "vendor_name" not in col_map or "country" not in col_map:
        messages.error(request, "Template must include at least 'Vendor Name' and 'Country' columns.")
        return redirect("vendorlink:vendor_list")

    cap_objects = {c.code: c for c in active_capabilities()}
    # Whether the uploaded sheet carried ANY capability column. A sheet of just
    # contact details must not be read as "these vendors have no capabilities" —
    # that would silently blank the master data the sheet never mentioned.
    has_cap_columns = any(field in cap_objects for field in col_map)
    created = updated = skipped = 0

    for raw in rows[1:]:
        def cell(field):
            i = col_map.get(field)
            return raw[i] if i is not None and i < len(raw) else None

        name = str(cell("vendor_name") or "").strip()
        if not name:
            skipped += 1
            continue

        data, declared = {}, []
        for field in col_map:
            if field == "vendor_name":
                continue
            value = cell(field)
            if field in cap_objects:
                # Collected, not put in `data`: an M2M cannot ride in
                # update_or_create's defaults.
                if _truthy(value):
                    declared.append(cap_objects[field])
            else:
                data[field] = str(value).strip() if value is not None else ""

        vendor, was_created = Vendor.objects.update_or_create(
            vendor_name=name, defaults=data,
        )
        if has_cap_columns:
            vendor.capabilities.set(declared)
        if was_created:
            vendor.created_by = request.user
            vendor.save(update_fields=["created_by"])
            created += 1
        else:
            updated += 1

    messages.success(
        request,
        f"Import complete — {created} added, {updated} updated"
        + (f", {skipped} skipped." if skipped else "."),
    )
    return redirect("vendorlink:vendor_list")


# ── Requester module — raise a request to vendors of one country ────────────
def _reachable_by_country():
    """Map of country -> count of active vendors with an email on file.

    Keyed canonically, so a country whose vendors are split across spelling
    variants reports one honest total instead of two halves that each look too
    small to be worth raising a request to.
    """
    rows = (
        Vendor.objects.filter(is_active=True).exclude(email="")
        .values("country").annotate(c=Count("id"))
    )
    merged = {}
    for row in rows:
        name = canonical_country(row["country"])
        if name:
            merged[name] = merged.get(name, 0) + row["c"]
    return merged


def _emailable_vendors(country):
    """Active vendors of one country that have an email, deduped by mailbox.

    Two vendor rows can legitimately share one address (a group with several
    registered entities). Sending twice to the same inbox looks like a bug to
    the recipient, so both the picker and the send path collapse them — and
    they must use the same rule, or the count shown on screen would disagree
    with the number of emails actually delivered.
    """
    seen, out = set(), []
    for v in (
        Vendor.objects.filter(is_active=True, country=country)
        .exclude(email="")
        .order_by("vendor_name")
    ):
        key = (v.email or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


@login_required
@access_required
def api_vendors_by_country(request):
    """GET /vendorlink/api/vendors-by-country/?country=India

    Feeds the recipient picker on Raise a Request. Unlike :func:`api_cities`,
    which serves static geography, this returns vendor contact addresses — so
    it carries the platform's auth decorators and must never be opened up.
    """
    country = (request.GET.get("country") or "").strip()
    if not country:
        return JsonResponse({"vendors": []})
    return JsonResponse({
        "vendors": [
            {
                "id": v.pk,
                "name": v.vendor_name,
                "email": (v.email or "").strip(),
                "state": v.state or "",
                "city": v.city or "",
            }
            for v in _emailable_vendors(country)
        ]
    })


def _has_request_body(vendor_req):
    """Whether the request carries any content below its header banner.

    Every card in the left column of the detail screen is conditional, so when
    all of these are blank the column collapses to nothing and the page looks
    broken rather than empty. Drives the explicit empty state instead.
    """
    return any((
        vendor_req.intro_message,
        vendor_req.header_message,
        vendor_req.scope_of_work,
        vendor_req.footer_message,
        vendor_req.product_verticals,
        vendor_req.warehouse_space_range,
        vendor_req.storage_temp_range,
        vendor_req.monthly_shipment_range,
        vendor_req.inbound_range,
        vendor_req.outbound_range,
        vendor_req.attachments.exists(),
    ))


def _filter_chips(active):
    """One removable chip per filter in force.

    ``remove_query`` is the querystring for the same screen with this one
    filter dropped and every other kept, so dismissing "Germany" does not also
    throw away the search term the user typed to find it.
    """
    from urllib.parse import urlencode

    labels = {"q": "Search", "country": "Country", "status": "Status"}
    chips = []
    for key in ("q", "country", "status"):
        value = active.get(key)
        if not value:
            continue
        remaining = {k: v for k, v in active.items() if v and k != key}
        chips.append({
            "key": labels[key],
            "value": value,
            # Empty string when this was the only filter — the template turns
            # that into a bare path, which clears the screen back to unfiltered.
            "remove_query": urlencode(remaining),
        })
    return chips


#: The quoted cost components, in the order a buyer reads them.
#: `additional_charges` and `notes` are free text and so cannot be compared
#: numerically — they stay in the per-bid detail cards rather than the matrix.
_BID_COST_ROWS = [
    ("storage_cost",      "Storage"),
    ("inbound_cost",      "Inbound"),
    ("outbound_cost",     "Outbound"),
    ("pick_cost",         "Pick"),
    ("packing_cost",      "Packing"),
    ("last_mile_charges", "Last mile"),
    ("manpower_charges",  "Manpower"),
]


def _bid_vendor_label(bid):
    """Best available display name for whoever submitted a bid.

    Prefers the registered company name over the login, because a buyer
    comparing quotes thinks in companies, not usernames.
    """
    registration = bid.vendor_registration
    if registration and registration.vendor_name:
        return registration.vendor_name
    user = bid.submitted_by
    if user:
        return user.get_full_name() or user.username
    return "Unknown vendor"


def _bid_comparison(bids):
    """Lay the latest bid from each vendor side by side, cheapest cell marked.

    Only the newest bid per vendor is compared. A vendor who revises a quote
    has superseded their own earlier numbers, and showing all of them side by
    side would let a stale figure win a row.

    ``comparable`` is the important flag. ``VendorBid.currency`` is per bid and
    spans twelve currencies, so one request can hold quotes in USD and EUR at
    once; there is no rate table in this system, and marking the numerically
    smallest cell across mixed currencies would confidently recommend the
    wrong vendor. When the currencies disagree the matrix still renders, but
    nothing is flagged as best and the template says why.

    Deliberately no total row: the components carry different units — storage
    is per sqm per month, the rest are per shipment or per movement — so their
    sum would not be a price. ``quoted`` counts how many of the seven each
    vendor actually filled in, which is the honest completeness signal.
    """
    if not bids:
        return None

    latest = {}
    for bid in bids:
        key = bidcompare.vendor_key_for(bid)
        current = latest.get(key)
        if current is None or (bid.bid_number, bid.pk) > (current.bid_number, current.pk):
            latest[key] = bid

    columns = sorted(latest.values(), key=lambda b: (_bid_vendor_label(b).lower(), b.pk))
    currencies = sorted({b.currency for b in columns if b.currency})
    comparable = len(currencies) <= 1

    rows = []
    for field, label in _BID_COST_ROWS:
        values = [getattr(b, field) for b in columns]
        present = [v for v in values if v is not None]
        if not present:
            continue                      # nobody quoted this line - omit the row
        cheapest = min(present) if comparable else None
        # Only flag a winner when the quotes actually differ. A lone quote has
        # nothing to have been cheaper than, and a row where every vendor named
        # the same figure (all seven quoted Manpower as 0.00 on real data) has
        # no winner either - lighting every cell implies a decision that is not
        # there.
        has_spread = len(present) > 1 and min(present) < max(present)
        rows.append({
            "label": label,
            "cells": [
                {
                    "value": value,
                    "is_best": (
                        comparable and has_spread
                        and value is not None and value == cheapest
                    ),
                }
                for value in values
            ],
            "tied": comparable and len(present) > 1 and not has_spread,
        })

    # Every revision each vendor filed, newest last, so a column can be
    # switched client-side. Values are floats rather than Decimals because this
    # goes through json_script into the page.
    by_vendor = {}
    for bid in bids:
        by_vendor.setdefault(bid.submitted_by_id, []).append(bid)
    for vendor_bids in by_vendor.values():
        vendor_bids.sort(key=lambda b: (b.bid_number, b.pk))

    revision_data = []
    for bid in columns:
        vendor_bids = by_vendor.get(bid.submitted_by_id, [bid])
        revision_data.append({
            "label": _bid_vendor_label(bid),
            # Which entry the server rendered, so the picker opens on it.
            "selected": next(
                (i for i, b in enumerate(vendor_bids) if b.pk == bid.pk), 0
            ),
            "revisions": [
                {
                    "bid_number": b.bid_number,
                    "currency": b.currency or "",
                    "submitted": b.submitted_at.strftime("%d %b %Y, %H:%M"),
                    "quoted": sum(
                        1 for field, _ in _BID_COST_ROWS
                        if getattr(b, field) is not None
                    ),
                    "values": [
                        None if getattr(b, field) is None else float(getattr(b, field))
                        for field, _ in _BID_COST_ROWS
                    ],
                    "notes": (b.notes or "").strip(),
                    "additional": (b.additional_charges or "").strip(),
                }
                for b in vendor_bids
            ],
        })

    return {
        "columns": [
            {
                "bid": bid,
                "label": _bid_vendor_label(bid),
                "quoted": sum(
                    1 for field, _ in _BID_COST_ROWS if getattr(bid, field) is not None
                ),
                "revision_count": len(by_vendor.get(bid.submitted_by_id, [bid])),
            }
            for bid in columns
        ],
        "rows": rows,
        "currency": currencies[0] if len(currencies) == 1 else "",
        "currencies": currencies,
        "comparable": comparable,
        "row_total": len(_BID_COST_ROWS),
        "revision_data": revision_data,
    }


def _request_recipients(vendor_req):
    """The request's recipient addresses, each with its vendor name where known.

    ``VendorRegistration.email`` carries no unique constraint, so one address
    can appear on several rows — a resubmitted application, or one contact
    covering two verticals. Ordering by review date descending and keeping the
    first hit picks the most recently reviewed row deliberately, instead of
    letting an arbitrary one win.
    """
    emails = [e.strip() for e in (vendor_req.recipient_list or []) if e and e.strip()]
    if not emails:
        return []

    names = {}
    registrations = (
        VendorRegistration.objects
        .filter(email__in=emails, status=VendorRegistration.STATUS_APPROVED)
        # nulls_last: an unreviewed row must not outrank a reviewed one.
        .order_by(F("reviewed_at").desc(nulls_last=True), "-pk")
        .values_list("email", "vendor_name")
    )
    for email, vendor_name in registrations:
        if vendor_name:
            names.setdefault(email.strip().lower(), vendor_name)

    seen, out = set(), []
    for email in emails:
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"email": email, "vendor_name": names.get(key, "")})
    return out


@login_required
@access_required
def request_list(request):
    """History of requests raised. Admins see all; vendors see their country; others see their own."""
    from itertools import groupby
    from django.utils import timezone

    qs = VendorRequest.objects.select_related("raised_by").all()
    vendor_identity = None
    if is_vendor_user(request.user):
        # Requests for the vendor's country, reaching back over the joining
        # grace window plus anything they were explicitly emailed. See
        # vendoraccess — the rule lives there because bid_submit has to apply
        # exactly the same one, and it previously did not.
        vendor_identity = vendoraccess.identify(request.user)
        qs = vendoraccess.visible_requests(request.user, vendor_identity).select_related("raised_by")
    elif not is_vendorlink_admin(request.user):
        qs = qs.filter(raised_by=request.user)

    # ── Filters ────────────────────────────────────────────────────
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    if q:
        qs = qs.filter(
            Q(subject__icontains=q)
            | Q(reference_id__icontains=q)
            | Q(country__icontains=q)
            | Q(product_verticals__icontains=q)
            | Q(raised_by__first_name__icontains=q)
            | Q(raised_by__last_name__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")
    total = qs.count()

    # ── The vendor's own view of the same list ─────────────────────
    # A flat, already-open list rather than the requester's date-grouped
    # history, and carrying their own bid against each row. The two audiences
    # are asking different questions of the same table: a requester is
    # browsing what they sent and when, a vendor is asking "which of these
    # still needs a quote from me, and what did I quote last time". The
    # grouped/collapsed presentation answers the first well and the second not
    # at all -- it opened showing nothing but date headers.
    vendor_rows = []
    if is_vendor_user(request.user):
        requests_list = list(qs)
        bids_by_request = {}
        for bid in (
            VendorBid.objects.filter(
                submitted_by=request.user, vendor_request__in=requests_list,
            ).order_by("bid_number")
        ):
            bid.total_cost = _bid_total(bid)
            bids_by_request.setdefault(bid.vendor_request_id, []).append(bid)
        awarded_request_ids = set(
            BidAward.objects.filter(
                vendor_email__iexact=(request.user.email or "").strip(),
                vendor_request__in=requests_list,
            ).values_list("vendor_request_id", flat=True)
        ) if (request.user.email or "").strip() else set()

        for vendor_request in requests_list:
            bids = bids_by_request.get(vendor_request.pk, [])
            vendor_rows.append({
                "request": vendor_request,
                "bids": bids,
                "bid_count": len(bids),
                # The most recent revision is what they last stood behind, and
                # the only one worth quoting back at them in a list.
                "latest_bid": bids[-1] if bids else None,
                "is_awarded_to_me": vendor_request.pk in awarded_request_ids,
                "window": vendor_request.bid_window_status,
            })

    # ── Group by local date ────────────────────────────────────────
    today = timezone.localdate()
    grouped = []
    for date_val, items in groupby(qs, key=lambda r: r.created_at.date()):
        items_list = list(items)
        grouped.append({
            "date": date_val,
            "is_today": date_val == today,
            "requests": items_list,
            "count": len(items_list),
        })

    return render(request, "vendorlink/request_list.html", _ctx(
        request,
        grouped_requests=grouped,
        request_count=total,
        reachable_total=Vendor.objects.filter(is_active=True).exclude(email="").count(),
        q=q,
        status=status,
        # Whether any filter is in force. The template keeps non-today date
        # groups collapsed, which is right for browsing and wrong for a
        # search: a query matching only older requests rendered every row
        # `hidden`, so the screen read as "no results" while holding them.
        filters_active=bool(q or status),
        filter_chips=_filter_chips({"q": q, "status": status}),
        is_vendor=is_vendor_user(request.user),
        # Empty for staff -- the template branches on is_vendor and never
        # reads this on their side.
        vendor_rows=vendor_rows,
        vendor_open_count=sum(1 for row in vendor_rows if row["window"] == "open"),
        vendor_todo_count=sum(
            1 for row in vendor_rows if row["window"] == "open" and not row["bids"]
        ),
        # Shown on the vendor's own list so an empty screen says why: the
        # country they are filed under, and how far back they can reach.
        vendor_country=vendor_identity.country if vendor_identity else "",
        vendor_grace_hours=vendoraccess.grace_hours() if vendor_identity else None,
    ))


@login_required
@access_required
@internal_required
def request_create(request):
    """Compose a structured request and email it to all vendors of a country.

    Internal-only. The Raise a Request button was already hidden from vendor
    accounts, but the view itself accepted them: an onboarded vendor could open
    this URL directly and POST a real RFI, which then mails every active vendor
    in the chosen country. Hiding a button is not a permission.
    """
    reachable = _reachable_by_country()

    if request.method == "POST":
        form = VendorRequestForm(request.POST)
        upload_files = request.FILES.getlist("attachments")

        # ── File validations ────────────────────────────────────────────
        _MAX_FILES = 10
        _MAX_FILE_MB = 10

        if len(upload_files) > _MAX_FILES:
            form.add_error(None, f"Too many attachments — maximum {_MAX_FILES} files per request.")

        bad_ext = [
            f.name for f in upload_files
            if os.path.splitext(f.name)[1].lower() not in ALLOWED_REQUEST_UPLOAD_EXTS
        ]
        if bad_ext:
            form.add_error(None, f"Unsupported file type(s): {', '.join(bad_ext)}. "
                                 "Allowed: PDF, Word, Excel, JPG/PNG.")

        oversized = [
            f.name for f in upload_files
            if f.size > _MAX_FILE_MB * 1024 * 1024
        ]
        if oversized:
            form.add_error(None, f"File(s) exceed the {_MAX_FILE_MB} MB limit: {', '.join(oversized)}.")

        # ── Recipient selection ─────────────────────────────────────────
        # The picker posts one recipient_ids value per ticked vendor, plus a
        # hidden marker. The marker is what separates "the user unticked
        # everybody" (an error worth showing) from "this client never rendered
        # a picker at all" — scripting off, or an older cached form — where
        # falling back to the whole country preserves the original behaviour.
        picked_ids = request.POST.getlist("recipient_ids")
        has_picker = request.POST.get("recipient_selection") == "1"
        if has_picker and not picked_ids:
            form.add_error(None, "Select at least one recipient — no vendor is currently ticked.")

        if form.is_valid():
            vendor_req = form.save(commit=False)
            vendor_req.raised_by = request.user
            # Auto-build the standard intro from the requester's name + verticals.
            requester_name = request.user.get_full_name() or request.user.username
            verticals = (vendor_req.product_verticals or "").strip() or "our core domains"
            vendor_req.intro_message = (
                "Greetings from Meril Life Sciences India.<br><br>"
                f"I am {requester_name}, writing on behalf of Meril Life Sciences Pvt. Ltd., "
                f"a global MedTech organization with a strong presence across the {verticals}."
                "<br><br>Website: "
                '<a href="https://www.merillife.com">www.merillife.com</a>'
            )
            # Sanitise the rich-text sections.
            vendor_req.header_message = _clean_html(vendor_req.header_message)
            vendor_req.scope_of_work = _clean_html(vendor_req.scope_of_work)
            vendor_req.footer_message = _clean_html(vendor_req.footer_message)

            country = vendor_req.country
            # Recipients: active, emailable vendors of THIS country, deduped.
            recipients = _emailable_vendors(country)

            if has_picker:
                # Intersect with the ticked ids rather than trusting them. A
                # posted id is just a number from the client — it could name a
                # vendor in a different country, or one deactivated since the
                # page loaded. Filtering the server-built country list means a
                # forged id can only ever remove recipients, never add one.
                picked = {int(x) for x in picked_ids if x.isdigit()}
                recipients = [v for v in recipients if v.pk in picked]

            if not recipients:
                vendor_req.status = VendorRequest.STATUS_NO_RECIPIENTS
                vendor_req.recipient_count = 0
                vendor_req.save()
                messages.warning(
                    request,
                    f"No active vendors with an email were found for {country}, so nothing was sent.",
                )
                return redirect("vendorlink:request_detail", pk=vendor_req.pk)

            vendor_req.recipients = ",".join(v.email for v in recipients)
            vendor_req.recipient_count = len(recipients)
            vendor_req.status = VendorRequest.STATUS_SENT
            vendor_req.save()

            # Persist attachments and read their bytes once for emailing.
            mail_attachments = []
            for f in upload_files:
                content = f.read()
                VendorRequestAttachment.objects.create(
                    request=vendor_req,
                    file=ContentFile(content, name=f.name),
                    original_name=f.name,
                )
                mail_attachments.append((f.name, content, f.content_type or "application/octet-stream"))

            raised_by_name = request.user.get_full_name() or request.user.username
            subject = f"[Meril VendorLink] {vendor_req.subject}"
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")

            # Build portal user lookup for onboarding-flag detection (best-effort).
            from django.contrib.auth.models import User as _User
            portal_users = {
                u.email.lower(): u
                for u in _User.objects.filter(
                    email__in=[v.email.lower() for v in recipients], is_active=True
                ).select_related("profile")
            }

            # Build + send one personalised message per vendor, reusing the
            # platform's existing async sender (which supports attachments).
            for v in recipients:
                pu = portal_users.get((v.email or "").lower())
                needs_onboarding = False
                try:
                    needs_onboarding = pu.profile.force_password_change if pu else False
                except Exception:
                    pass

                site_url = getattr(settings, "SITE_URL", "https://nexgen.merillife.com")
                request_url = site_url + reverse("vendorlink:request_detail", kwargs={"pk": vendor_req.pk})

                html_body = render_to_string("vendorlink/email/vendor_request.html", {
                    "vendor": v,
                    "req": vendor_req,
                    "raised_by_name": raised_by_name,
                    "raised_by_email": request.user.email,
                    "needs_onboarding": needs_onboarding,
                    "site_url": site_url,
                })
                text_body = (
                    f"Dear {v.vendor_name},\n\n"
                    f"{vendor_req.subject}\n\n"
                    + (
                        "IMPORTANT: Please log in and set your new password to complete "
                        "your account setup before you can submit a bid.\n\n"
                        if needs_onboarding else ""
                    )
                    + f"View the request and submit your bid: {request_url}\n\n"
                    "Please open this email in an HTML-capable client to view the full request.\n\n"
                    f"— {raised_by_name}, Meril Life Sciences"
                )
                msg = EmailMultiAlternatives(subject, text_body, from_email, [v.email])
                msg.attach_alternative(html_body, "text/html")
                # cid: in the footer needs the image on the message — see vendorlink.mailer.
                mailer.attach_logo(msg)
                for name, content, ctype in mail_attachments:
                    msg.attach(name, content, ctype)
                _send_async(msg)

            messages.success(
                request,
                f"Request sent to {len(recipients)} vendor"
                f"{'' if len(recipients) == 1 else 's'} in {country}"
                + (f" with {len(mail_attachments)} attachment{'' if len(mail_attachments) == 1 else 's'}." if mail_attachments else "."),
            )
            return redirect("vendorlink:request_detail", pk=vendor_req.pk)
    else:
        form = VendorRequestForm()

    return render(request, "vendorlink/request_form.html", _ctx(
        request,
        form=form,
        reachable_by_country=reachable,
        reachable_by_country_json=json.dumps(reachable),
        reachable_total=sum(reachable.values()),
        country_iso_map=geonames_iso_map_json(),
    ))


@login_required
@access_required
def request_detail(request, pk):
    vendor_req = get_object_or_404(VendorRequest, pk=pk)

    if is_vendor_user(request.user):
        # Vendors may only view requests targeting their country.
        vendor_reg = VendorRegistration.objects.filter(
            email=request.user.email,
            status=VendorRegistration.STATUS_APPROVED,
        ).order_by("-reviewed_at").first()
        vendor_country = vendor_reg.country if vendor_reg else None
        if vendor_req.country != vendor_country:
            messages.error(request, "You can only view requests for your registered country.")
            return redirect("vendorlink:request_list")

        # Check if vendor needs to complete onboarding (approved but password not yet set)
        try:
            needs_onboarding = request.user.profile.force_password_change
        except Exception:
            needs_onboarding = False

        vendor_bids = VendorBid.objects.filter(
            vendor_request=vendor_req, submitted_by=request.user
        ).order_by("bid_number")
        bid_window = vendor_req.bid_window_status
        # Show form only if onboarded and window is open
        bid_form = VendorBidForm() if (not needs_onboarding and bid_window == "open") else None

        # Whether THIS vendor holds the award. Matched on the email the award
        # snapshotted, the same identity the portal counts wins by. Only their
        # own outcome is exposed -- never who else bid, what they quoted, or
        # that another vendor was chosen, which is the same line the regret
        # letter holds (see vendorlink/awards.py).
        my_award = None
        email = (request.user.email or "").strip()
        if email:
            my_award = (
                BidAward.objects.filter(vendor_request=vendor_req, vendor_email__iexact=email)
                .select_related("bid").first()
            )
        awarded_bid_ids = {my_award.bid_id} if my_award else set()
        vendor_bids = list(vendor_bids)
        for bid in vendor_bids:
            bid.is_awarded_to_me = bid.pk in awarded_bid_ids
            # Same total the staff comparison screens rank on -- see _bid_total.
            bid.total_cost = _bid_total(bid)

        return render(request, "vendorlink/request_detail.html", _ctx(
            request,
            vendor_req=vendor_req,
            is_vendor=True,
            vendor_bids=vendor_bids,
            bid_form=bid_form,
            vendor_reg=vendor_reg,
            needs_onboarding=needs_onboarding,
            bid_window=bid_window,
            my_award=my_award,
            has_request_body=_has_request_body(vendor_req),
        ))

    if not is_vendorlink_admin(request.user) and vendor_req.raised_by_id != request.user.id:
        messages.error(request, "You can only view requests you raised.")
        return redirect("vendorlink:request_list")

    # No admin check here: the guard directly above already redirected anyone
    # who is neither a VendorLink admin nor the person who raised this
    # request. Gating on admin as well only hid the responses to a
    # requester's own request from the requester.
    #
    # Ordering by submitted_by is what makes the template's {% regroup %} safe
    # — regroup only collapses *adjacent* rows, so an unsorted queryset would
    # silently split one vendor across several groups.
    bids = list(
        vendor_req.bids
        .select_related("submitted_by", "vendor_registration")
        .order_by("submitted_by", "bid_number")
    )

    # BidAward is a OneToOne, so the reverse accessor raises rather than
    # returning None when there is no award yet. Caught explicitly instead of
    # via getattr(..., None): RelatedObjectDoesNotExist happens to subclass
    # AttributeError, which makes the getattr form work by accident and read
    # as though a missing attribute were being tolerated.
    try:
        award = (
            BidAward.objects
            .select_related("bid", "awarded_by", "vendor_registration")
            .get(vendor_request=vendor_req)
        )
    except BidAward.DoesNotExist:
        award = None

    return render(request, "vendorlink/request_detail.html", _ctx(
        request,
        vendor_req=vendor_req,
        bids=bids,
        comparison=_bid_comparison(bids),
        recipients=_request_recipients(vendor_req),
        award=award,
        is_vendor=False,
        bid_window=vendor_req.bid_window_status,
        has_request_body=_has_request_body(vendor_req),
    ))


@login_required
@access_required
@require_POST
def bid_submit(request, pk):
    """Vendor submits a new price bid on a request. Multiple bids per vendor are allowed."""
    vendor_req = get_object_or_404(VendorRequest, pk=pk)

    if not is_vendor_user(request.user):
        messages.error(request, "Only registered vendors can submit bids.")
        return redirect("vendorlink:request_detail", pk=pk)

    # Onboarding gate: vendor must set their password before bidding.
    try:
        if request.user.profile.force_password_change:
            messages.warning(
                request,
                "Please complete your account setup — set your new password first before submitting a bid.",
            )
            return redirect("vendorlink:vendor_change_password")
    except Exception:
        pass

    # Bid window gate.
    bid_window = vendor_req.bid_window_status
    if bid_window == "closed":
        messages.error(request, "This request is no longer accepting bids — the bid window has closed.")
        return redirect("vendorlink:request_detail", pk=pk)
    if bid_window == "not_started":
        messages.error(request, "Bidding has not yet opened for this request.")
        return redirect("vendorlink:request_detail", pk=pk)

    identity = vendoraccess.identify(request.user)
    allowed, reason = vendoraccess.can_bid(request.user, vendor_req, identity)
    if not allowed:
        messages.error(request, reason)
        return redirect("vendorlink:request_list")
    # May be None for a vendor who only has a Vendor master row. The FK is
    # nullable and bidcompare already falls back to submitted_by for the vendor
    # identity, so a bid without one is a supported shape — not a reason to
    # refuse a vendor who is otherwise eligible.
    vendor_reg = identity.registration

    # Determine next bid number for this vendor on this request.
    from django.db.models import Max
    last = VendorBid.objects.filter(
        vendor_request=vendor_req, submitted_by=request.user
    ).aggregate(m=Max("bid_number"))["m"]
    next_bid_number = (last or 0) + 1

    form = VendorBidForm(request.POST)
    if form.is_valid():
        bid = form.save(commit=False)
        bid.vendor_request = vendor_req
        bid.submitted_by = request.user
        bid.vendor_registration = vendor_reg
        bid.bid_number = next_bid_number
        bid.save()
        messages.success(request, f"Bid #{next_bid_number} submitted successfully.")
    else:
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, f"{field}: {err}")
    return redirect("vendorlink:request_detail", pk=pk)


@login_required
@admin_required
@require_POST
def bid_select(request, pk):
    """Staff marks the one chosen bid on this request.

    One selection per REQUEST, across every vendor and every revision.

    This used to clear only the chosen vendor's own other revisions, so a
    request could carry a selected bid from each vendor at once — one vendor
    with a single quote and another with three could both show as chosen, which
    reads as two winners on a request that has one outcome. Selecting now
    clears every other bid on the request.

    Distinct from BidAward, which is the awarded outcome and the thing the
    vendor is told about. This flag is the internal "this is the quote we are
    working from".
    """
    bid = get_object_or_404(VendorBid, pk=pk)
    with transaction.atomic():
        # Every other bid on this request, whoever submitted it.
        VendorBid.objects.filter(
            vendor_request_id=bid.vendor_request_id
        ).exclude(pk=bid.pk).update(is_selected=False)
        bid.is_selected = True
        bid.save(update_fields=["is_selected"])
    return JsonResponse({"ok": True, "bid_id": bid.pk, "request_id": bid.vendor_request_id})


@login_required
def attachment_download(request, pk):
    """Serve a VendorRequestAttachment file.

    Vendors may only access attachments on requests targeting their country.
    Staff/requester may access their own requests. Admins may access all.

    ?dl=1 forces download; omitting it serves previewable types (PDF, images)
    inline so the browser can display them without downloading.
    """
    import mimetypes

    att = get_object_or_404(VendorRequestAttachment, pk=pk)
    vendor_req = att.request

    # ── permission gate ───────────────────────────────────────────────────────
    if is_vendor_user(request.user):
        reg = VendorRegistration.objects.filter(
            email=request.user.email,
            status=VendorRegistration.STATUS_APPROVED,
        ).order_by("-reviewed_at").first()
        if not reg or vendor_req.country != reg.country:
            messages.error(request, "You don't have permission to access this file.")
            return redirect("vendorlink:request_list")
    elif not is_vendorlink_admin(request.user) and vendor_req.raised_by_id != request.user.id:
        messages.error(request, "You don't have permission to access this file.")
        return redirect("vendorlink:request_list")

    # ── serve the file ────────────────────────────────────────────────────────
    filename = att.original_name or os.path.basename(att.file.name)
    ext = os.path.splitext(filename.lower())[1]
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or "application/octet-stream"

    force_download = request.GET.get("dl") == "1"
    want_preview   = request.GET.get("preview") == "1"

    # Excel preview — render as HTML table
    if want_preview and not force_download and ext in (".xls", ".xlsx"):
        try:
            return _excel_preview_response(att, filename)
        except Exception as exc:
            messages.error(request, f'Could not render preview for "{filename}": {exc}')
            return redirect("vendorlink:request_detail", pk=vendor_req.pk)

    _INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}
    disposition = "attachment" if force_download else (
        "inline" if content_type in _INLINE_TYPES else "attachment"
    )

    try:
        response = FileResponse(att.file.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response
    except (FileNotFoundError, OSError):
        messages.error(request, f'File "{filename}" could not be found on the server.')
        return redirect("vendorlink:request_detail", pk=vendor_req.pk)


def _excel_preview_response(att, filename):
    """Render an .xlsx / .xls file as a self-contained HTML page."""
    wb = openpyxl.load_workbook(att.file.open("rb"), read_only=True, data_only=True)

    def _cell(val):
        if val is None:
            return ""
        return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    sheets = []
    for name in wb.sheetnames:
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            sheets.append((name, "<p class='xl-empty'>Empty sheet.</p>"))
            continue
        tbl = ['<div class="xl-scroll"><table class="xl-tbl">']
        for ri, row in enumerate(rows[:1000]):
            tbl.append("<tr>")
            tag = "th" if ri == 0 else "td"
            tbl.extend(f"<{tag}>{_cell(c)}</{tag}>" for c in row)
            tbl.append("</tr>")
        tbl.append("</table></div>")
        note = f'<p class="xl-note">Showing first 1,000 rows of {len(rows):,} total.</p>' if len(rows) > 1000 else ""
        sheets.append((name, "".join(tbl) + note))

    # Build tab switcher HTML
    tabs_html = "".join(
        f'<button class="xl-tab{" xl-tab-active" if i == 0 else ""}" onclick="xlSwitch(this,{i})">{name}</button>'
        for i, (name, _) in enumerate(sheets)
    )
    panels_html = "".join(
        f'<div class="xl-panel{" xl-panel-active" if i == 0 else ""}" id="xl-p-{i}">{body}</div>'
        for i, (_, body) in enumerate(sheets)
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{filename}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;color:#1f2937;min-height:100vh}}
.xl-header{{background:linear-gradient(135deg,#0076c8,#00b5e2);padding:14px 22px;
  display:flex;align-items:center;gap:12px;box-shadow:0 2px 10px rgba(0,118,200,.2)}}
.xl-header-icon{{font-size:1.4rem;color:#fff}}
.xl-header h1{{font-size:1rem;font-weight:800;color:#fff;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.xl-dl-btn{{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;
  background:#fff;color:#0076c8;font-size:.82rem;font-weight:700;text-decoration:none;white-space:nowrap}}
.xl-dl-btn:hover{{background:#e0f0ff}}
.xl-tabs{{display:flex;gap:4px;padding:12px 22px 0;border-bottom:2px solid #e5e7eb;background:#fff;flex-wrap:wrap}}
.xl-tab{{padding:8px 16px;border:none;background:none;cursor:pointer;font-size:.84rem;font-weight:600;
  color:#64748b;border-radius:8px 8px 0 0;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}}
.xl-tab:hover{{color:#0076c8;background:#f0f7ff}}
.xl-tab-active{{color:#0076c8;border-bottom-color:#0076c8;background:#eff6ff}}
.xl-body{{padding:18px 22px}}
.xl-panel{{display:none}}
.xl-panel-active{{display:block}}
.xl-scroll{{overflow-x:auto;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
.xl-tbl{{border-collapse:collapse;width:100%;font-size:.84rem;background:#fff}}
.xl-tbl th{{background:#0076c8;color:#fff;padding:9px 14px;text-align:left;
  border:1px solid #005fa3;white-space:nowrap;font-weight:700;position:sticky;top:0;z-index:1}}
.xl-tbl td{{padding:7px 14px;border:1px solid #e5e7eb;white-space:nowrap}}
.xl-tbl tr:nth-child(even) td{{background:#f8fafc}}
.xl-tbl tr:hover td{{background:#eff6ff}}
.xl-empty{{padding:20px;color:#9ca3af;font-style:italic}}
.xl-note{{padding:8px 2px;font-size:.76rem;color:#94a3b8}}
</style>
</head>
<body>
<div class="xl-header">
  <i class="xl-header-icon">📊</i>
  <h1>{filename}</h1>
  <a href="?dl=1" class="xl-dl-btn">⬇ Download</a>
</div>
<div class="xl-tabs">{tabs_html}</div>
<div class="xl-body">{panels_html}</div>
<script>
function xlSwitch(btn, idx) {{
  document.querySelectorAll('.xl-tab').forEach(function(t){{t.classList.remove('xl-tab-active');}});
  document.querySelectorAll('.xl-panel').forEach(function(p){{p.classList.remove('xl-panel-active');}});
  btn.classList.add('xl-tab-active');
  document.getElementById('xl-p-' + idx).classList.add('xl-panel-active');
}}
</script>
</body>
</html>"""

    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ── Vendor Self-Registration (PUBLIC — no auth required) ───────────────────

def _make_captcha_image(a, b):
    """Generate a PNG image showing '{a} + {b} = ?' with noise and distortion."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    W, H = 280, 90
    img = Image.new("RGB", (W, H), color=(248, 249, 252))
    draw = ImageDraw.Draw(img)
    for _ in range(300):
        x, y = random.randint(0, W - 1), random.randint(0, H - 1)
        c = random.randint(175, 215)
        draw.point((x, y), fill=(c, c, c + 10))
    for _ in range(6):
        draw.line(
            [(random.randint(0, W), random.randint(0, H)),
             (random.randint(0, W), random.randint(0, H))],
            fill=(180, 190, 210), width=1,
        )
    text = f"{a}  +  {b}  =  ?"
    try:
        font = ImageFont.load_default(size=38)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = (W - tw) // 2, (H - th) // 2
    draw.text((x + 2, y + 2), text, font=font, fill=(205, 210, 225))
    draw.text((x, y), text, font=font, fill=(20, 55, 120))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.7))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def captcha_image(request):
    """Serve a fresh math CAPTCHA image and store the answer in the session."""
    a, b = random.randint(1, 15), random.randint(1, 15)
    request.session["reg_captcha"] = {
        "answer": str(a + b),
        "generated_at": time.time(),
    }
    request.session.modified = True
    resp = HttpResponse(_make_captcha_image(a, b), content_type="image/png")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@require_POST
def check_captcha(request):
    from django.http import JsonResponse
    answer = request.POST.get("answer", "").strip()
    captcha_data = request.session.get("reg_captcha", {})
    if not captcha_data:
        return JsonResponse({"ok": False, "error": "CAPTCHA expired. Refresh the image and try again."})
    if answer == captcha_data.get("answer"):
        captcha_data["verified"] = True
        request.session["reg_captcha"] = captcha_data
        request.session.modified = True
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "error": "Wrong answer. Try again."})


@require_POST
def send_email_otp(request):
    """AJAX: validate email and send a 6-digit OTP."""
    from django.http import JsonResponse
    from .forms import validate_vendor_email
    email = request.POST.get("email", "").strip().lower()

    ok, error, suggestion = validate_vendor_email(email)
    if not ok:
        return JsonResponse({"ok": False, "error": error, "suggestion": suggestion})

    # Duplicate checks — pending, approved, and vendor master
    if VendorRegistration.objects.filter(email=email, status=VendorRegistration.STATUS_PENDING).exists():
        return JsonResponse({"ok": False, "code": "pending", "error": "A registration with this email is already pending review. Please wait for the outcome.", "suggestion": None})
    if VendorRegistration.objects.filter(email=email, status=VendorRegistration.STATUS_APPROVED).exists():
        return JsonResponse({"ok": False, "code": "approved", "error": "This email is already registered as an approved vendor. Contact the SCM team if you need to update your details.", "suggestion": None})
    if Vendor.objects.filter(email__iexact=email, is_active=True).exists():
        return JsonResponse({"ok": False, "code": "exists", "error": "This email already exists in our active vendor directory.", "suggestion": None})

    # Rate-limit: no more than one OTP per 60 s for the same address
    otp_data = request.session.get("reg_otp", {})
    if otp_data.get("email") == email and otp_data.get("sent_at", 0) > time.time() - 60:
        remaining = int(60 - (time.time() - otp_data["sent_at"]))
        return JsonResponse({"ok": False, "error": f"Please wait {remaining}s before requesting another code.", "suggestion": None})

    code = str(random.randint(100000, 999999))
    request.session["reg_otp"] = {
        "email": email,
        "code": code,
        "sent_at": time.time(),
        "expires_at": time.time() + 600,
        "verified": False,
        "attempts": 0,
    }
    request.session.modified = True

    try:
        from django.core.mail import send_mail
        send_mail(
            subject="Your Meril VendorLink verification code",
            message=(
                f"Your one-time verification code is:\n\n"
                f"  {code}\n\n"
                f"This code is valid for 10 minutes. Do not share it with anyone.\n\n"
                f"If you did not request this, please ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:
        return JsonResponse({"ok": False, "error": "Failed to send email. Please check the address and try again.", "suggestion": None})

    return JsonResponse({"ok": True, "message": f"Code sent to {email}. Check your inbox."})


@require_POST
def verify_email_otp(request):
    """AJAX: verify the 6-digit OTP entered by the user."""
    from django.http import JsonResponse
    code = request.POST.get("code", "").strip()
    otp_data = request.session.get("reg_otp", {})

    if not otp_data:
        return JsonResponse({"ok": False, "error": "No code found. Please request a new one."})
    if time.time() > otp_data.get("expires_at", 0):
        return JsonResponse({"ok": False, "error": "Code has expired. Please request a new one."})

    attempts = otp_data.get("attempts", 0)
    if attempts >= 5:
        return JsonResponse({"ok": False, "error": "Too many incorrect attempts. Please request a new code."})

    if code != otp_data.get("code"):
        otp_data["attempts"] = attempts + 1
        request.session["reg_otp"] = otp_data
        request.session.modified = True
        remaining = 5 - otp_data["attempts"]
        return JsonResponse({"ok": False, "error": f"Incorrect code. {remaining} attempt(s) remaining."})

    otp_data["verified"] = True
    request.session["reg_otp"] = otp_data
    request.session.modified = True
    return JsonResponse({"ok": True, "message": "Email verified successfully!"})


def vendor_register(request):
    """Public self-registration form for 3PL vendors. No login required."""
    otp_data = request.session.get("reg_otp", {})
    captcha_data = request.session.get("reg_captcha", {})

    def _ctx_reg(form, verified_email, captcha_error=None):
        return {
            "form": form,
            "otp_verified_email": verified_email,
            "captcha_error": captcha_error,
            "country_iso_map": geonames_iso_map_json(),
            "country_dial_map": geonames_dial_map_json(),
            # register.html is a standalone page — it does not go through _ctx,
            # so the cascade map has to be handed over explicitly here.
            "continent_country_map": continent_country_map_json(),
        }

    if request.method == "POST":
        submitted_email = request.POST.get("email", "").strip().lower()

        # Server-side OTP gate
        if not (otp_data.get("verified") and otp_data.get("email") == submitted_email):
            form = VendorRegistrationForm(request.POST)
            form.add_error("email", "Please verify your email address before submitting.")
            return render(request, "vendorlink/register.html", _ctx_reg(form, ""))

        # Server-side CAPTCHA gate — compare submitted answer against session
        captcha_data = request.session.get("reg_captcha", {})
        submitted_captcha = request.POST.get("captcha_answer", "").strip()
        if not captcha_data or submitted_captcha != captcha_data.get("answer", ""):
            request.session.pop("reg_captcha", None)
            form = VendorRegistrationForm(request.POST)
            return render(request, "vendorlink/register.html", _ctx_reg(
                form, submitted_email,
                "Wrong answer — a new problem has been generated. Please try again.",
            ))

        form = VendorRegistrationForm(request.POST)
        if form.is_valid():
            reg = form.save()
            request.session.pop("reg_otp", None)
            request.session.pop("reg_captcha", None)

            # Confirmation email to vendor
            try:
                from django.core.mail import send_mail
                send_mail(
                    subject="Your Meril VendorLink registration has been received",
                    message=(
                        f"Dear {reg.contact_person},\n\n"
                        f"Thank you for registering with Meril VendorLink.\n\n"
                        f"We have received your application for {reg.vendor_name} and it is currently "
                        f"under review by our SCM team. You will be notified once a decision is made.\n\n"
                        f"Registration details:\n"
                        f"  Company : {reg.vendor_name}\n"
                        f"  Country : {reg.country}\n"
                        f"  Contact : {reg.contact_person}\n"
                        f"  Email   : {reg.email}\n\n"
                        f"For any queries, contact scm.digitalsupport@merillife.com\n\n"
                        f"Regards,\nMeril Life Sciences — Global SCM Team"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[reg.email],
                    fail_silently=True,
                )
            except Exception:
                pass

            # Notify admins
            from django.contrib.auth import get_user_model
            User = get_user_model()
            admin_emails = list(
                User.objects.filter(is_active=True)
                .filter(
                    Q(email__endswith="@merillife.com")
                    | Q(email__endswith="@meril.com")
                    | Q(groups__name__in=["VendorLink Admin", "Vendor Master Admin", "Global SCM", "Admin"])
                )
                .values_list("email", flat=True)
                .distinct()
            )
            if admin_emails:
                subject = f"[VendorLink] New vendor registration — {reg.vendor_name}"
                body = (
                    f"A new vendor has registered and is pending your approval.\n\n"
                    f"Company : {reg.vendor_name}\n"
                    f"Country : {reg.country}\n"
                    f"Contact : {reg.contact_person} <{reg.email}>\n\n"
                    f"Review at: {request.build_absolute_uri('/vendorlink/registrations/')}"
                )
                try:
                    from django.core.mail import send_mail
                    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, admin_emails, fail_silently=True)
                except Exception:
                    pass

            return redirect("vendorlink:register_success")

        return render(request, "vendorlink/register.html", _ctx_reg(form, submitted_email))

    form = VendorRegistrationForm()
    verified_email = otp_data.get("email", "") if otp_data.get("verified") else ""
    return render(request, "vendorlink/register.html", _ctx_reg(form, verified_email))


def vendor_register_success(request):
    """Confirmation page shown after successful self-registration."""
    return render(request, "vendorlink/register_success.html")


# ── Registration admin queue (admin-protected) ──────────────────────────────

@login_required
@admin_required
def registration_list(request):
    """Admin queue showing pending, approved and rejected registrations."""
    status_filter = request.GET.get("status", "pending")
    q = request.GET.get("q", "").strip()
    qs = VendorRegistration.objects.select_related("reviewed_by", "vendor")
    if status_filter in ("pending", "approved", "rejected"):
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(
            Q(vendor_name__icontains=q)
            | Q(email__icontains=q)
            | Q(country__icontains=q)
            | Q(state__icontains=q)
            | Q(contact_person__icontains=q)
            | Q(city__icontains=q)
        )
    counts = {
        "pending": VendorRegistration.objects.filter(status="pending").count(),
        "approved": VendorRegistration.objects.filter(status="approved").count(),
        "rejected": VendorRegistration.objects.filter(status="rejected").count(),
    }
    return render(request, "vendorlink/registration_list.html", _ctx(
        request,
        registrations=qs,
        status_filter=status_filter,
        counts=counts,
        q=q,
    ))


@login_required
@admin_required
@require_POST
def approve_registration(request, pk):
    """Approve a pending registration: create Vendor master record and portal login account."""
    from django.contrib.auth.models import Group, User
    from django.utils import timezone
    from main.models import UserProfile

    reg = get_object_or_404(VendorRegistration, pk=pk)
    if reg.status != VendorRegistration.STATUS_PENDING:
        messages.warning(request, f"Registration for {reg.vendor_name} is already {reg.get_status_display().lower()}.")
        return redirect("vendorlink:registration_list")

    # Create or update the Vendor master record.
    vendor, _ = Vendor.objects.update_or_create(
        vendor_name=reg.vendor_name,
        defaults=dict(
            country=reg.country,
            state=reg.state,
            city=reg.city,
            warehouse_location=reg.warehouse_location,
            contact_person=reg.contact_person,
            email=reg.email,
            mobile=reg.mobile,
            website=reg.website,
            is_active=True,
        ),
    )
    # Capabilities are a join now, so they cannot ride in `defaults` — carried
    # across explicitly so an approved vendor keeps what they self-declared
    # rather than arriving in the master with none.
    copy_capabilities(reg, vendor)
    reg.status = VendorRegistration.STATUS_APPROVED
    reg.vendor = vendor
    reg.reviewed_by = request.user
    reg.reviewed_at = timezone.now()
    reg.save()

    # Create the vendor portal login account.
    temp_password = None
    if reg.email:
        try:
            temp_password = _generate_vendor_password()
            name_parts = (reg.contact_person or "").split(None, 1)
            vendor_user, _ = User.objects.get_or_create(
                username=reg.email,
                defaults={
                    "email": reg.email,
                    "first_name": name_parts[0] if name_parts else "",
                    "last_name": name_parts[1] if len(name_parts) > 1 else "",
                    "is_active": True,
                },
            )
            vendor_user.set_password(temp_password)
            vendor_user.save()

            profile, _ = UserProfile.objects.get_or_create(user=vendor_user)
            profile.force_password_change = True
            profile.save()

            group, _ = Group.objects.get_or_create(name="VendorUser")
            vendor_user.groups.add(group)
        except Exception:
            temp_password = None

    # Send email — credentials if user was created, simple approval otherwise.
    if reg.email:
        try:
            from django.core.mail import send_mail
            if temp_password:
                site_url = getattr(settings, "SITE_URL", "https://nexgen.merillife.com")
                body = (
                    f"Dear {reg.contact_person},\n\n"
                    f"Your vendor registration for {reg.vendor_name} has been approved "
                    f"by the Meril Global SCM team.\n\n"
                    f"A vendor portal account has been created for you:\n\n"
                    f"  Login URL : {site_url}/\n"
                    f"  Username  : {reg.email}\n"
                    f"  Password  : {temp_password}\n\n"
                    f"This is a temporary password. You will be prompted to set a new "
                    f"password the first time you log in.\n\n"
                    f"After changing your password you will have access to the Vendor "
                    f"Bidding portal where you can view and respond to price requests.\n\n"
                    f"Regards,\nMeril Life Sciences — Global SCM"
                )
                subject = "Meril VendorLink — Approved & Login Credentials"
            else:
                body = (
                    f"Dear {reg.contact_person},\n\n"
                    f"Your registration for {reg.vendor_name} has been approved.\n\n"
                    f"Regards,\nMeril Life Sciences — Global SCM"
                )
                subject = "Your Meril VendorLink registration has been approved"

            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[reg.email],
                fail_silently=True,
            )
        except Exception:
            pass

    messages.success(request, f"{reg.vendor_name} approved — credentials sent to {reg.email}.")
    return redirect("vendorlink:registration_list")


@login_required
@admin_required
@require_POST
def reject_registration(request, pk):
    """Reject a pending registration with an optional reason."""
    from django.utils import timezone
    reg = get_object_or_404(VendorRegistration, pk=pk)
    if reg.status != VendorRegistration.STATUS_PENDING:
        messages.warning(request, f"Registration for {reg.vendor_name} is already {reg.get_status_display().lower()}.")
        return redirect("vendorlink:registration_list")

    reason = request.POST.get("reason", "").strip()
    reg.status = VendorRegistration.STATUS_REJECTED
    reg.rejection_reason = reason
    reg.reviewed_by = request.user
    reg.reviewed_at = timezone.now()
    reg.save()

    # Notify the vendor.
    if reg.email:
        try:
            from django.core.mail import send_mail
            body = (
                f"Dear {reg.contact_person},\n\n"
                f"Thank you for your interest in partnering with Meril Life Sciences.\n\n"
                f"After review, we are unable to proceed with your registration for "
                f"{reg.vendor_name} at this time."
            )
            if reason:
                body += f"\n\nReason: {reason}"
            body += "\n\nRegards,\nMeril Life Sciences — Global SCM"
            send_mail(
                subject="Your Meril VendorLink registration status",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[reg.email],
                fail_silently=True,
            )
        except Exception:
            pass

    messages.info(request, f"Registration for {reg.vendor_name} rejected.")
    return redirect("vendorlink:registration_list")


@login_required
@admin_required
@require_POST
def bulk_registration_action(request):
    """Bulk approve or reject a list of pending registrations."""
    from django.contrib.auth.models import Group, User
    from django.utils import timezone
    from main.models import UserProfile

    action = request.POST.get("action", "")
    pks = request.POST.getlist("pks")
    reason = request.POST.get("reason", "").strip()

    if action not in ("approve", "reject") or not pks:
        messages.error(request, "Invalid bulk action request.")
        return redirect("vendorlink:registration_list")

    regs = VendorRegistration.objects.filter(pk__in=pks, status=VendorRegistration.STATUS_PENDING)
    approved_count = rejected_count = 0

    for reg in regs:
        if action == "approve":
            vendor, _ = Vendor.objects.update_or_create(
                vendor_name=reg.vendor_name,
                defaults=dict(
                    country=reg.country,
                    state=reg.state,
                    city=reg.city,
                    warehouse_location=reg.warehouse_location,
                    contact_person=reg.contact_person, email=reg.email,
                    mobile=reg.mobile, website=reg.website,
                    is_active=True,
                ),
            )
            copy_capabilities(reg, vendor)
            reg.status = VendorRegistration.STATUS_APPROVED
            reg.vendor = vendor
            reg.reviewed_by = request.user
            reg.reviewed_at = timezone.now()
            reg.save()

            temp_password = None
            if reg.email:
                try:
                    temp_password = _generate_vendor_password()
                    name_parts = (reg.contact_person or "").split(None, 1)
                    vendor_user, _ = User.objects.get_or_create(
                        username=reg.email,
                        defaults={
                            "email": reg.email,
                            "first_name": name_parts[0] if name_parts else "",
                            "last_name": name_parts[1] if len(name_parts) > 1 else "",
                            "is_active": True,
                        },
                    )
                    vendor_user.set_password(temp_password)
                    vendor_user.save()
                    profile, _ = UserProfile.objects.get_or_create(user=vendor_user)
                    profile.force_password_change = True
                    profile.save()
                    group, _ = Group.objects.get_or_create(name="VendorUser")
                    vendor_user.groups.add(group)
                except Exception:
                    temp_password = None

            if reg.email:
                try:
                    from django.core.mail import send_mail
                    site_url = getattr(settings, "SITE_URL", "https://nexgen.merillife.com")
                    if temp_password:
                        body = (
                            f"Dear {reg.contact_person},\n\nYour vendor registration for {reg.vendor_name} "
                            f"has been approved by the Meril Global SCM team.\n\n"
                            f"Login URL : {site_url}/\nUsername  : {reg.email}\nPassword  : {temp_password}\n\n"
                            f"This is a temporary password. You will be prompted to set a new password on first login.\n\n"
                            f"Regards,\nMeril Life Sciences — Global SCM"
                        )
                        subject = "Meril VendorLink — Approved & Login Credentials"
                    else:
                        body = f"Dear {reg.contact_person},\n\nYour registration for {reg.vendor_name} has been approved.\n\nRegards,\nMeril Life Sciences — Global SCM"
                        subject = "Your Meril VendorLink registration has been approved"
                    send_mail(subject=subject, message=body, from_email=settings.DEFAULT_FROM_EMAIL,
                              recipient_list=[reg.email], fail_silently=True)
                except Exception:
                    pass
            approved_count += 1

        elif action == "reject":
            reg.status = VendorRegistration.STATUS_REJECTED
            reg.rejection_reason = reason
            reg.reviewed_by = request.user
            reg.reviewed_at = timezone.now()
            reg.save()
            if reg.email:
                try:
                    from django.core.mail import send_mail
                    body = (
                        f"Dear {reg.contact_person},\n\nThank you for your interest in partnering with Meril Life Sciences.\n\n"
                        f"After review, we are unable to proceed with your registration for {reg.vendor_name} at this time."
                    )
                    if reason:
                        body += f"\n\nReason: {reason}"
                    body += "\n\nRegards,\nMeril Life Sciences — Global SCM"
                    send_mail(subject="Your Meril VendorLink registration status", message=body,
                              from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[reg.email], fail_silently=True)
                except Exception:
                    pass
            rejected_count += 1

    if approved_count:
        messages.success(request, f"{approved_count} vendor{'s' if approved_count > 1 else ''} approved and credentials sent.")
    if rejected_count:
        messages.info(request, f"{rejected_count} vendor{'s' if rejected_count > 1 else ''} rejected.")
    return redirect("vendorlink:registration_list")


# ── Bid Analytics ────────────────────────────────────────────────────────────

def _bid_total(bid):
    cost_fields = (
        "storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
        "packing_cost", "last_mile_charges", "manpower_charges",
    )
    return sum(float(getattr(bid, field) or 0) for field in cost_fields)


def _filtered_bid_requests(country, month, year, q="", continent=""):
    requests = VendorRequest.objects.order_by("-created_at")
    if continent or country:
        # Resolved through canonical_country rather than matched literally.
        # VendorRequest.country is free text, so `filter(country=country)`
        # returned only the requests stored under the exact spelling the
        # dropdown happened to offer and silently hid the rest — the same fault
        # canonical_country exists for on the vendor side.
        wanted = canonical_country(country) if country else ""
        keep = []
        for raw in VendorRequest.objects.values_list("country", flat=True).distinct():
            name = canonical_country(raw)
            if not name:
                continue
            if wanted and name != wanted:
                continue
            if continent and continent_of(raw) != continent:
                continue
            keep.append(raw)
        requests = requests.filter(country__in=keep)
    if year:
        requests = requests.filter(created_at__year=int(year))
    if month:
        requests = requests.filter(created_at__month=int(month))
    if q:
        requests = requests.filter(
            Q(subject__icontains=q) | Q(reference_id__icontains=q)
        )
    return requests


def _vendor_bid_groups(bids):
    from collections import defaultdict

    groups = defaultdict(list)
    for bid in bids:
        bid.total_cost = _bid_total(bid)
        groups[bidcompare.vendor_key_for(bid)].append(bid)

    vendor_groups = []
    for grouped_bids in groups.values():
        bids_sorted = sorted(grouped_bids, key=lambda bid: bid.total_cost)
        selected = next((bid for bid in bids_sorted if bid.is_selected), None)
        best = selected or bids_sorted[0]
        registration = best.vendor_registration
        score = None
        if registration and registration.vendor_id:
            try:
                score = registration.vendor.score
            except Exception:
                pass
        vendor_groups.append({
            "vendor_name": (
                registration.vendor_name
                if registration
                else (best.submitted_by.get_full_name() or best.submitted_by.username)
            ),
            "reg": registration,
            "score": score,
            "bids": bids_sorted,
            "best": best,
            "bid_count": len(bids_sorted),
            "currency": best.currency,
        })
    return sorted(vendor_groups, key=lambda group: group["best"].total_cost)


#: Distinguishes "caller did not supply an award" from "this request has
#: none". Passing None for the latter re-ran the very query the batch was
#: there to avoid, for every request without an award.
_UNSET = object()


def _bid_analytics_row(vendor_request, bids=None, award=_UNSET, user=None):
    """One request's analytics row.

    ``bids`` and ``award`` may be passed in already fetched. This used to run
    three queries per request -- bids, evaluation, award -- which on a screen
    that loops every request came to 3N: 32 queries for eleven requests. The
    caller now batches them and hands them over.

    Both stay optional so a caller holding a single request (the heatmap tests,
    and anything reaching for one row) still works without arranging a batch.

    ``user`` is who is viewing the screen, needed to compute per-row whether
    THIS request's award can be managed by them -- the raiser of one request is
    not necessarily the raiser of the next, so this cannot be a single
    page-wide flag the way ``is_vm_admin`` is. Optional and defaults to "no" so
    a caller with no user in scope (the heatmap tests) still gets a row back.
    """
    if bids is None:
        bids = list(
            VendorBid.objects.filter(vendor_request=vendor_request)
            .select_related("submitted_by", "vendor_registration")
            .order_by("bid_number")
        )
    if not bids:
        return None

    evaluation = None
    try:
        # Free when the caller used select_related("evaluation"); one query
        # otherwise. Still wrapped: a reverse OneToOne raises when absent.
        evaluation = vendor_request.evaluation
        evaluation.mark_stale_if_needed()
    except Exception:
        pass

    if award is _UNSET:
        award = (
            BidAward.objects.filter(vendor_request=vendor_request)
            .select_related("bid").first()
        )
    vendor_groups = _vendor_bid_groups(bids)
    # Flagged per vendor group so the collapsed row can show who holds the
    # award without the template having to work out vendor identity itself.
    awarded_bid_ids = {b.pk for b in bids if award and b.pk == award.bid_id}
    for group in vendor_groups:
        group["is_awarded"] = any(b.pk in awarded_bid_ids for b in group["bids"])

    return {
        "request": vendor_request,
        "vendor_groups": vendor_groups,
        "evaluation": evaluation,
        "award": award,
        "total_bids": len(bids),
        "can_manage_award": can_manage_award(user, vendor_request) if user is not None else False,
    }


def _group_bid_analytics(data):
    from itertools import groupby
    from django.utils import timezone

    today = timezone.localdate()
    grouped_data = []
    for date_value, items in groupby(
        data,
        key=lambda item: timezone.localtime(item["request"].created_at).date(),
    ):
        items_list = list(items)
        grouped_data.append({
            "date": date_value,
            "is_today": date_value == today,
            "items": items_list,
            "count": len(items_list),
        })

    # Which groups start open. Previously only a group dated *today* did, which
    # meant that on any day nobody happened to raise an RFI — i.e. most days —
    # every group rendered collapsed and the Active RFIs panel showed nothing
    # but a stack of date headers. The newest group is always opened instead, so
    # the panel always leads with actual content; "today" still opens on top of
    # that when it exists.
    for index, group in enumerate(grouped_data):
        group["expanded"] = index == 0 or group["is_today"]

    return grouped_data


def _bid_analytics_context(request):
    """Every context key _bid_analytics_body.html and its GPT widget need.

    Shared by the standalone Bid Analytics page and the Commercial Database's
    'Bid Analytics' tab, so the two cannot quietly drift into showing different
    numbers for the same underlying bids. Callers add their own ``ba_clear_url``
    on top, since that is the one thing that legitimately differs between them.
    """
    from django.utils import timezone

    from . import gptfiles, quoteheatmap

    continent = request.GET.get("continent", "").strip().upper()
    country = request.GET.get("country", "").strip()
    month = request.GET.get("month", "").strip()
    year = request.GET.get("year", "").strip()
    q = request.GET.get("q", "").strip()

    # Three batched queries instead of three per request. `evaluation` is a
    # reverse OneToOne so it select_relates; bids and awards come back in one
    # query each and are indexed by request id.
    from collections import defaultdict as _defaultdict

    requests = list(
        _filtered_bid_requests(country, month, year, q, continent)
        .select_related("evaluation")
    )
    bids_by_request = _defaultdict(list)
    for bid in (
        VendorBid.objects.filter(vendor_request__in=requests)
        .select_related("submitted_by", "vendor_registration")
        .order_by("bid_number")
    ):
        bids_by_request[bid.vendor_request_id].append(bid)

    awards_by_request = {
        award.vendor_request_id: award
        for award in BidAward.objects.filter(vendor_request__in=requests).select_related("bid")
    }

    data = [
        row for vendor_request in requests
        if (row := _bid_analytics_row(
            vendor_request,
            bids=bids_by_request.get(vendor_request.pk, []),
            award=awards_by_request.get(vendor_request.pk),
            user=request.user,
        )) is not None
    ]
    now = timezone.now()
    total_bids_count = sum(d["total_bids"] for d in data)
    active_requests_count = sum(1 for d in data if d["request"].bid_window_status == "open")
    vendor_ids = {
        bidcompare.vendor_key_for(bid)
        for row in data
        for vendor_group in row["vendor_groups"]
        for bid in vendor_group["bids"]
    }
    evaluated_count = sum(
        1 for d in data
        if d["evaluation"] and d["evaluation"].status == "completed"
    )
    # Canonicalised and de-duplicated, then narrowed to the chosen continent —
    # the raw distinct() listed "India", "india" and " India " as three options
    # that each filtered to a third of the requests.
    all_countries = filter_countries_by_continent(
        canonical_countries(VendorRequest.objects.values_list("country", flat=True)),
        continent,
    )
    years = range(now.year, now.year - 5, -1)
    months = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]

    return {
        "grouped_data": _group_bid_analytics(data),
        "data": data,
        "continent": continent,
        "country": canonical_country(country),
        "month": month,
        "year": year,
        "q": q,
        "all_countries": all_countries,
        "years": years,
        "months": months,
        "total_bids_count": total_bids_count,
        "active_requests_count": active_requests_count,
        "vendor_count": len(vendor_ids),
        "evaluated_count": evaluated_count,
        # Limits and the accept list come from the module that enforces them, so
        # the UI cannot advertise something the server then rejects.
        "gpt_files": gptfiles.describe_support(),
        # Vendor x RFI price-premium matrix, built from the same rows this page
        # already renders — no second pass over the bids.
        "heat": quoteheatmap.build(data),
    }


@login_required
@access_required
def quote_heatmap_excel(request):
    """The Vendor x RFI price-premium heatmap, as a workbook.

    Built from `_bid_analytics_context`, the same call that renders the matrix
    on screen, so the download always matches what the user was looking at --
    including the active country/month/year/search filters. Recomputing it here
    would let the file and the page drift apart.
    """
    from django.utils import timezone

    from . import heatmapexport

    context = _bid_analytics_context(request)
    heat = context["heat"]

    # Reference ids are not on the heat dict (quoteheatmap takes no Django
    # imports and keeps only the pks), but "RFI-2026-000004" is how procurement
    # refers to a request, so they are resolved here and passed in.
    references = dict(
        VendorRequest.objects
        .filter(pk__in=[meta["request_pk"] for meta in heat["columns"]])
        .values_list("pk", "reference_id")
    )
    filters = {
        "Search": context["q"],
        # Named on the sheet so a downloaded heatmap says which slice it is.
        # The continent was a filter the export did not record, which is how a
        # "Europe only" heatmap ends up circulating labelled as everything.
        "Continent": continent_name(context["continent"]),
        "Country": context["country"],
        "Month": context["month"],
        "Year": context["year"],
    }

    payload = heatmapexport.workbook_bytes(heat, references=references, filters=filters)
    response = HttpResponse(
        payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = (
        f'attachment; filename="vendorlink-price-premium-heatmap-{stamp}.xlsx"'
    )
    return response


@login_required
@access_required
def bid_analytics(request):
    """Quote comparison dashboard — all vendor bids filterable by country / month / year."""
    return render(request, "vendorlink/bid_analytics.html", _ctx(
        request,
        **_bid_analytics_context(request),
        # Where _bid_analytics_body.html's two "Clear filters" links point —
        # this page's own URL. The Commercial Database's "Bid Analytics" tab
        # renders the same partial with its own base URL instead.
        ba_clear_url=reverse("vendorlink:bid_analytics"),
    ))


# ── Meril GPT conversation history ──────────────────────────────────────────
#: Prior turns replayed to the model. Enough for a line of questioning to hold
#: together, capped because every turn is re-sent on every request and the
#: prompt also carries the request payload and the retrieved passages.
GPT_HISTORY_TURNS = 8

#: Each replayed turn is trimmed to this. An earlier answer that itself
#: contained a long table would otherwise dominate the context it is only
#: meant to provide the thread of.
GPT_HISTORY_CHARS = 1500


def _resolve_gpt_conversation(user, conversation_id, question):
    """Find or start the thread this question belongs to.

    Returns ``(conversation, created_here)``. ``created_here`` lets the caller
    undo a thread it started if the answer then fails — a thread whose only
    turn errored is noise in the recent-chats list, which is what the previous
    create-after-answer ordering protected.

    Split out of :func:`_record_gpt_turn` because documents are now stored
    against the conversation and retrieved from it, so it has to exist before
    the model is called rather than after it answers.
    """
    if not getattr(user, "is_authenticated", False):
        return None, False

    if conversation_id:
        # Scoped to the owner, so a guessed id cannot append to someone else's
        # thread or read it back through the load endpoint.
        existing = GptConversation.objects.filter(pk=conversation_id, user=user).first()
        if existing is not None:
            return existing, False

    return GptConversation.objects.create(
        user=user, title=GptConversation.title_from(question),
    ), True


def _gpt_history_for_prompt(conversation, turns=GPT_HISTORY_TURNS):
    """The thread's previous turns, oldest first, for the model.

    Every question used to be sent standalone, so a follow-up like "now show
    that as a table" had no referent for "that" and the model answered
    something adjacent. This is what makes a conversation a conversation.

    Only the text is replayed. A stored report is not re-sent as JSON: the
    answer text beside it already says what it showed, and replaying the
    payload would spend the context window restating a table the user can
    still see on screen.
    """
    if conversation is None:
        return []

    recent = list(
        conversation.messages.order_by("-created_at", "-id")[:turns]
    )
    recent.reverse()

    history = []
    for message in recent:
        body = (message.body or "").strip()
        if not body:
            continue
        if len(body) > GPT_HISTORY_CHARS:
            body = body[:GPT_HISTORY_CHARS] + " […trimmed]"
        history.append({"role": message.role, "content": body})
    return history


def _gpt_previous_source(conversation):
    """What answered the last turn of this thread — "" if there was no last turn.

    The router needs it because a follow-up often names no subject: "create a
    report with graphs included", "summarise this in tabular form", "retry
    again". Read on its own, each of those is a question about nothing in
    particular, and the router sent them to the portal while the thread was
    about an attached quotation.
    """
    if conversation is None:
        return ""
    last = (
        conversation.messages
        .filter(role=GptMessage.ROLE_ASSISTANT)
        .exclude(source="")
        .order_by("-created_at", "-id")
        .first()
    )
    return last.source if last else ""


def _record_gpt_turn(conversation, question, result, attachment_names):
    """Append one question/answer pair to an already-resolved thread."""
    if conversation is None:
        return None

    GptMessage.objects.create(
        conversation=conversation, role=GptMessage.ROLE_USER,
        body=question, attachments=", ".join(attachment_names)[:500],
    )
    GptMessage.objects.create(
        conversation=conversation, role=GptMessage.ROLE_ASSISTANT,
        body=result.get("answer") or "",
        report_json=json.dumps(result["report"]) if result.get("report") else "",
        source=(result.get("source") or "")[:12],
    )
    conversation.save(update_fields=["updated_at"])   # bumps it to the top of the list
    # Trimmed here rather than by a scheduled job: this is the only place the
    # thread count can grow, and the thread just saved is now the most recent,
    # so it can never be the one dropped.
    GptConversation.prune_for(conversation.user)
    return conversation


@login_required
@access_required
def merilgpt_conversations(request):
    """The signed-in user's recent MerilGPT threads, newest first.

    Capped at GptConversation.KEEP_RECENT. Opening the list also prunes, which
    catches history left over from before the cap existed — a user who never
    asks another question would otherwise keep their old threads forever.
    """
    GptConversation.prune_for(request.user)
    # order_by is explicit, not inherited from Meta: annotate() adds a GROUP BY
    # and the model's default "-updated_at" did not survive it -- the list came
    # back oldest-first, so "recent chats" showed the least recent.
    rows = (
        GptConversation.objects.filter(user=request.user)
        .annotate(turns=Count("messages"))
        .order_by("-updated_at", "-pk")[:GptConversation.KEEP_RECENT]
    )
    return JsonResponse({
        "kept": GptConversation.KEEP_RECENT,
        "conversations": [
            {
                "id": c.pk,
                "title": c.title or "Untitled",
                "turns": c.turns,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in rows
        ],
    })


@login_required
@access_required
def merilgpt_conversation(request, pk):
    """Replay one thread, so the panel can reopen it where it left off."""
    conversation = get_object_or_404(GptConversation, pk=pk, user=request.user)
    return JsonResponse({
        "id": conversation.pk,
        "title": conversation.title,
        "messages": [
            {
                "role": m.role,
                "body": m.body,
                "report": m.report,
                # So a reopened thread shows the same "answered from" chip it
                # showed live, instead of losing it on reload.
                "source": m.source,
                "attachments": m.attachments,
                "at": m.created_at.isoformat(),
            }
            for m in conversation.messages.all()
        ],
    })


@login_required
@access_required
@require_POST
def merilgpt_conversation_delete(request, pk):
    conversation = get_object_or_404(GptConversation, pk=pk, user=request.user)
    conversation.delete()
    return JsonResponse({"ok": True})


@login_required
@admin_required
@require_POST
def request_evaluate(request, pk):
    """Kick off the bid evaluator for a vendor request.

    Starts the direct in-process OpenAI evaluator. The page polls the existing
    status endpoint while the background worker completes the evaluation.
    """
    from .ai_evaluator import run_evaluation_async

    vr = get_object_or_404(VendorRequest, pk=pk)

    if not VendorBid.objects.filter(vendor_request=vr).exists():
        messages.warning(request, "No bids have been submitted for this request yet.")
        return redirect(_BID_ANALYTICS_URL)

    # Create or reset the evaluation record.
    from django.utils import timezone

    evaluation, _ = VendorEvaluation.objects.get_or_create(vendor_request=vr)
    # `triggered_at` is auto_now_add, which fires only on INSERT — so a re-run
    # kept the date of the very first evaluation and the results panel showed
    # "Triggered 26 Aug" for a run that had just happened. A fresh result
    # stamped two weeks ago reads as a stale one, which is precisely the
    # "nothing happened" the label was meant to disprove. Assigning explicitly
    # is honoured on UPDATE, so the field now means what it says: when this
    # evaluation was last started.
    evaluation.triggered_at = timezone.now()
    evaluation.status = VendorEvaluation.STATUS_PROCESSING
    evaluation.triggered_by = request.user
    evaluation.recommended_vendor_name = ""
    evaluation.reasoning = ""
    evaluation.ranked_vendors_json = ""
    evaluation.required_services_json = ""
    evaluation.decision_points_json = ""
    evaluation.model_used = ""
    evaluation.error_message = ""
    evaluation.completed_at = None
    evaluation.save()

    if not os.getenv("TEST_OPENAI_API_KEY"):
        message = "AI evaluation is unavailable right now."
        evaluation.status = VendorEvaluation.STATUS_FAILED
        evaluation.error_message = message
        evaluation.save()
        messages.error(request, message)
        return redirect(_BID_ANALYTICS_URL)

    run_evaluation_async(vr.pk)
    messages.success(request, "AI evaluation started. Results will appear on this page shortly.")
    # ?evaluated=<pk> so the destination can open the right RFI. Both the date
    # group and the RFI card render collapsed, and the evaluator's result block
    # lives inside the card body -- without this the banner promised results
    # that were rendered but hidden two accordions deep, which read as the
    # evaluation having produced nothing at all.
    return redirect(f"{reverse(_BID_ANALYTICS_URL)}?evaluated={vr.pk}")


@login_required
@access_required
def api_evaluation_data(request, pk):
    """JSON API: the exact payload sent to the OpenAI evaluator (bids + scores + services).

    Kept as an inspection/debug endpoint — the evaluator itself builds this
    payload in-process via ai_evaluator.build_payload().
    """
    from .ai_evaluator import build_payload

    vr = get_object_or_404(VendorRequest, pk=pk)
    return JsonResponse(build_payload(vr))


@login_required
@access_required
def api_evaluation_status(request, pk):
    """Polling endpoint — returns current evaluation status as JSON."""
    try:
        ev = VendorEvaluation.objects.get(vendor_request_id=pk)
        ev.mark_stale_if_needed()
        return JsonResponse({
            "status": ev.status,
            "recommended_vendor": ev.recommended_vendor_name,
            "reasoning": ev.reasoning,
            "required_services": ev.required_services,
            "decision_points": ev.decision_points,
            "ranked_vendors": ev.ranked_vendors,
            "error": ev.error_message,
        })
    except VendorEvaluation.DoesNotExist:
        return JsonResponse({"status": "none"})


@login_required
@access_required
@require_POST
def merilgpt_ask(request):
    """Meril GPT — free-text Q&A over every request's bids, plus attachments.

    Accepts up to ``gptfiles.MAX_FILES`` documents/images. Validation and text
    extraction happen in :mod:`vendorlink.gptfiles` before the model is called,
    so a bad file produces a named, per-file message instead of a rejected API
    request or a silently ignored upload.
    """
    from . import gptfiles, gptrag
    from .ai_evaluator import ask_meril_gpt

    question = (request.POST.get("question") or "").strip()
    uploads = request.FILES.getlist("files")

    # A question is optional *only* when something is attached — see
    # ask_meril_gpt, which supplies a default task in that case.
    if not question and not uploads:
        return JsonResponse({"error": "Ask a question first."}, status=400)
    if not os.getenv("TEST_OPENAI_API_KEY"):
        return JsonResponse({"error": "AI features are unavailable right now."}, status=503)

    attachments, file_errors = gptfiles.collect(uploads)
    # Every attachment rejected and nothing to fall back on: report rather than
    # quietly answering from the request payload alone, which would look like
    # the files had been read.
    if uploads and not attachments:
        return JsonResponse(
            {"error": "None of the attached files could be read.", "file_errors": file_errors},
            status=400,
        )

    # ── The thread, resolved before the model is called ──────────────────
    # Documents are stored against the conversation and retrieved from it, so
    # the thread has to exist before retrieval runs — otherwise a question
    # asked in the same request as its own upload could not see that upload.
    # _record_gpt_turn used to create the conversation only after a successful
    # answer; `created_here` preserves what that was protecting, by removing a
    # thread this request started if the answer then fails.
    conversation, created_here = _resolve_gpt_conversation(
        request.user, request.POST.get("conversation_id"), question,
    )

    gptrag.index_attachments(conversation, attachments)

    # Retrieval covers every document in the thread, not only the ones just
    # uploaded — which is the whole point: the follow-up question is usually
    # the real one, and before this it reached the model with no document at
    # all while the panel still showed the file chip.
    retrieved, sources = gptrag.retrieve(
        conversation, question or "summarise the attached documents",
    )

    # A document attached in this very request whose text was NOT selected by
    # retrieval still goes in directly. Otherwise a two-line upload could be
    # out-scored by an older document and silently ignored on the turn it
    # arrived — the one turn where the user is certain it was read.
    if attachments and not sources:
        text_block, _images = gptfiles.as_prompt_parts(attachments)
        retrieved = text_block or retrieved

    history = _gpt_history_for_prompt(conversation)

    try:
        result = ask_meril_gpt(
            question, attachments=attachments, retrieved=retrieved, history=history,
            previous_source=_gpt_previous_source(conversation),
        )
    except Exception as exc:
        if created_here and conversation is not None:
            # A thread whose only turn errored is noise in the recent-chats
            # list, which is what the original ordering protected.
            conversation.delete()
        return JsonResponse({"error": str(exc), "file_errors": file_errors}, status=502)

    _record_gpt_turn(
        conversation,
        question,
        result,
        [a.name for a in attachments],
    )

    return JsonResponse({
        "answer": result["answer"],
        "report": result.get("report"),
        # Which of the three sources answered: portal / document / general /
        # both. Shown in the chat so a reader can see how the question was
        # read before they judge the answer.
        "source": result.get("source"),
        "conversation_id": conversation.pk if conversation else None,
        # What retrieval actually drew on, so the reader can tell an answer
        # grounded in a document from one grounded in the request payload.
        "sources": sources,
        "sources_label": gptrag.describe_sources(sources),
        # Echoed back so the UI can state what was actually read — including
        # any file that was truncated, which the reader needs to know.
        "files": [
            {
                "name": attachment.name,
                "size": attachment.pretty_size,
                "kind": attachment.kind,
                "truncated": attachment.truncated,
                "notes": attachment.notes,
            }
            for attachment in attachments
        ],
        "file_errors": file_errors,
    })


@login_required
def vendor_change_password(request):
    """First-login password change for vendor accounts with force_password_change=True."""
    from django.contrib.auth import update_session_auth_hash
    from main.models import UserProfile

    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return redirect("vendorlink:index")

    if not profile.force_password_change:
        return redirect("vendorlink:index")

    error = None
    if request.method == "POST":
        new_pw = request.POST.get("new_password", "")
        confirm_pw = request.POST.get("confirm_password", "")
        if len(new_pw) < 8:
            error = "Password must be at least 8 characters."
        elif new_pw != confirm_pw:
            error = "Passwords do not match. Please try again."
        else:
            request.user.set_password(new_pw)
            request.user.save()
            profile.force_password_change = False
            profile.save()
            update_session_auth_hash(request, request.user)
            return redirect("vendorlink:index")

    return render(request, "vendorlink/vendor_change_password.html", {"error": error})
