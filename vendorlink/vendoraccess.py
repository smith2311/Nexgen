"""What a vendor portal login is allowed to see and bid on.

One module because the answer was previously computed twice, differently:
``views.request_list`` decided what a vendor could *see* and ``views.bid_submit``
decided what they could *bid on*, and the two disagreed in ways nobody could
have noticed from either one alone.

Both had the same two holes:

* They resolved the vendor's country only from an approved
  :class:`VendorRegistration`. A vendor added by an admin through Vendor Master
  and granted portal access has no such row — so the list showed them nothing
  at all and bidding told them "you can only bid on requests for your
  registered country", which was true and completely unhelpful.
* They compared ``request.country != registration.country`` as exact strings.
  ``Vendor.country`` is free text with years of variant spellings behind it
  (see :func:`utils.canonical_country`), so a vendor filed under "india" was
  locked out of every request raised for "India".

The grace window is the third piece: a request raised in the morning, before the
vendor was on the system, was invisible to them for the rest of its life. They
can now reach back ``VENDORLINK_LATE_BID_GRACE_HOURS`` from the moment they
became a vendor. A request they were explicitly emailed is always reachable
whatever the window says — they are holding the invitation.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q

from .models import Vendor, VendorRegistration, VendorRequest
from .utils import canonical_country

#: Hours before a vendor joined that they may still reach back to. Default 24 —
#: the case this exists for is "raised this morning, vendor onboarded this
#: afternoon", and a same-day window covers it without quietly opening up a
#: month of history.
DEFAULT_GRACE_HOURS = 24


def grace_hours():
    """The configured grace window, in hours.

    Read per call rather than at import so ``override_settings`` in a test and a
    change in ``.env`` both take effect without a reload. A negative or
    unparseable value falls back to the default instead of raising: this gates a
    vendor-facing screen, and a typo in an env var should not take the portal
    down.
    """
    raw = getattr(settings, "VENDORLINK_LATE_BID_GRACE_HOURS", DEFAULT_GRACE_HOURS)
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_GRACE_HOURS
    return hours if hours >= 0 else DEFAULT_GRACE_HOURS


def registration_for(user):
    """The approved registration behind a portal login, or None.

    Ordered newest-reviewed-first because ``VendorRegistration.email`` carries
    no unique constraint — a resubmitted application leaves several rows for one
    address, and the most recently reviewed one is the current truth.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return None
    return (
        VendorRegistration.objects
        .filter(email__iexact=email, status=VendorRegistration.STATUS_APPROVED)
        .order_by(F("reviewed_at").desc(nulls_last=True), "-pk")
        .first()
    )


def vendor_for(user):
    """The Vendor master row behind a portal login, or None.

    The fallback for admin-created vendors. ``vendor_create`` now also writes a
    registration row, so this mostly covers vendors created before that change —
    which is exactly the population that would otherwise stay locked out.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return None
    return Vendor.objects.filter(email__iexact=email, is_active=True).order_by("-pk").first()


class VendorIdentity:
    """Who a portal login is, as far as request access is concerned.

    ``country`` is canonical. ``joined_at`` is when they became a vendor on this
    platform, which is what the grace window measures back from.
    """

    __slots__ = ("registration", "vendor", "country", "joined_at")

    def __init__(self, registration=None, vendor=None):
        self.registration = registration
        self.vendor = vendor
        source = registration or vendor
        self.country = canonical_country(getattr(source, "country", "")) if source else ""
        if registration is not None:
            # submitted_at, not reviewed_at: the vendor's own sense of "when I
            # joined" is when they applied, and approval can lag by days. Using
            # the review date would make the grace window start after the
            # requests it exists to reach.
            self.joined_at = registration.submitted_at
        elif vendor is not None:
            self.joined_at = vendor.created_at
        else:
            self.joined_at = None

    def __bool__(self):
        return bool(self.country and self.joined_at)

    @property
    def reachable_from(self):
        """Earliest ``created_at`` a request may have and still be reachable."""
        if not self.joined_at:
            return None
        return self.joined_at - timedelta(hours=grace_hours())


def identify(user):
    """Resolve a portal login to a :class:`VendorIdentity`. Always returns one;
    test it for truthiness rather than for None."""
    return VendorIdentity(registration_for(user), vendor_for(user))


def _country_variants(country):
    """Every raw spelling in ``VendorRequest.country`` that means ``country``.

    Needed because the column is free text: filtering on the canonical spelling
    alone silently hides every request stored under a variant.
    """
    return [
        raw for raw in VendorRequest.objects.values_list("country", flat=True).distinct()
        if canonical_country(raw) == country
    ]


def visible_requests(user, identity=None):
    """Requests a vendor login may open, as a queryset.

    Country match, then either inside the grace window or explicitly addressed
    to them. ``recipients`` is the send-time snapshot of addresses, so the
    second clause is "they were sent this" — which outranks the window in both
    directions: a vendor holding an invitation can always open it.
    """
    identity = identity or identify(user)
    if not identity:
        return VendorRequest.objects.none()

    variants = _country_variants(identity.country)
    if not variants:
        return VendorRequest.objects.none()

    email = (getattr(user, "email", "") or "").strip()
    addressed = Q(recipients__icontains=email) if email else Q(pk__in=[])
    return VendorRequest.objects.filter(
        Q(country__in=variants) & (Q(created_at__gte=identity.reachable_from) | addressed)
    )


def can_bid(user, vendor_request, identity=None):
    """``(allowed, reason)`` for one vendor and one request.

    The reason is the message shown to the vendor, so each branch says which of
    the three things is wrong — not on the platform as a vendor, wrong country,
    or raised before they could reach it. "You can only bid on requests for your
    registered country" used to cover all three, including the case where the
    country matched perfectly.
    """
    identity = identity or identify(user)
    if not identity:
        return False, (
            "Your account is not linked to a vendor record yet. Contact "
            "scm.digitalsupport@merillife.com so your registration can be completed."
        )

    if canonical_country(vendor_request.country) != identity.country:
        return False, (
            f"This request is for {vendor_request.country}. You are registered "
            f"in {identity.country}, so you cannot bid on it."
        )

    email = (getattr(user, "email", "") or "").strip()
    was_addressed = bool(email) and email.lower() in (vendor_request.recipients or "").lower()
    if not was_addressed and vendor_request.created_at < identity.reachable_from:
        return False, (
            "This request was raised before your vendor account was created and "
            f"is outside the {grace_hours()}-hour joining window, so it is closed "
            "to you. Contact the Meril SCM team if you should be included."
        )

    return True, ""
