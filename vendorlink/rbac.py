"""Role-based access helpers for the VendorLink platform.

Mirrors the conventions used by the Warehouse Inspection / Material Rework
modules: superusers always have full access, Meril-domain staff get access,
and dedicated groups control viewer vs admin (add/edit/delete) rights.

These gate the whole VendorLink platform (Vendor Master and the modules that
follow it — Commercial Database, KPI Dashboard, Vendor Scorecard, 3PL
Capability Assessment).
"""

#: "Global SCM" is NOT here, though it used to be. It is also an admin group
#: in merilv.rbac.ADMIN_GROUPS — the one name the two modules shared — so
#: holding it granted admin on both at once, and the VendorLink role preset
#: handed it out with every Administrator account. Three people ended up as
#: silent Meril-V admins that way. Rights are per module; a group that means
#: something elsewhere cannot be a VendorLink admin grant.
ADMIN_GROUPS = {"VendorLink Admin", "Vendor Master Admin", "Admin", "admin"}
#: The Editor tier from the admin console's role presets — may add and edit
#: records but never delete them. Kept separate from ADMIN_GROUPS because the
#: Commercial Database spec draws the line exactly there: "Admin can add new
#: rate cards, edit existing ones, and delete records. Editors can add and edit
#: but not delete."
EDITOR_GROUPS = {"VendorLink User", "Vendor Master User"}
USER_GROUPS = {"VendorLink User", "VendorLink Viewer", "Vendor Master User", "Vendor Master Viewer", "VendorUser"} | ADMIN_GROUPS
VENDOR_GROUPS = {"VendorUser"}

_MERIL_DOMAINS = ("@merillife.com", "@meril.com", "@meril.in", "@merillife.in")


def _is_meril_staff(user):
    """Meril employees flagged as staff get READ access without an explicit group.

    Read-only on purpose, matching ``merilv.rbac._is_meril_staff``, which was
    narrowed to exactly this for exactly this reason. It used to feed
    ``is_vendorlink_admin`` and ``can_edit_vendorlink`` as well, so
    ``is_staff`` — a flag set to let somebody into the Django admin, and
    nothing to do with VendorLink — silently granted full add/edit/delete on
    vendors, commercials and other users' accounts. Writing is an
    explicit-grant action: it needs an Admin or Editor group.
    """
    email = (user.email or "").lower()
    return user.is_staff and any(email.endswith(d) for d in _MERIL_DOMAINS)


def can_access_vendorlink(user):
    """Anyone who may view the VendorLink platform."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_meril_staff(user):
        return True
    return user.groups.filter(name__in=USER_GROUPS).exists()


def is_vendorlink_admin(user):
    """Users who may add / edit / delete records.

    Deliberately does NOT honour ``_is_meril_staff`` — see the note there.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=ADMIN_GROUPS).exists()


def can_edit_vendorlink(user):
    """Users who may create and update records, but not necessarily delete them.

    Admins are included — an admin can do everything an editor can. Use this to
    gate add/edit; use :func:`is_vendorlink_admin` for delete, which is the one
    action the Editor tier is deliberately denied.

    External vendor accounts are excluded: they reach the platform through their
    own portal and must never be able to edit internal master data, even though
    ``VendorUser`` sits inside ``USER_GROUPS`` for read access.
    """
    if not user.is_authenticated:
        return False
    if is_vendor_user(user):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=ADMIN_GROUPS | EDITOR_GROUPS).exists()


def is_internal_user(user):
    """Meril-side users: platform access, and not an external vendor account.

    The line the vendor portal draws is view-and-quote. An external vendor
    reads the RFIs aimed at their country and submits bids against them; the
    RFI itself is raised by a Meril requester. Nothing about a vendor account
    should be able to originate one — raising a request mails every active
    vendor in the chosen country, so it is outward-facing on top of being
    internal-only.

    Distinct from :func:`can_edit_vendorlink`, which additionally requires the
    Admin or Editor tier. This one only excludes vendors, leaving every
    internal role that can raise a request today able to keep doing so.
    """
    return can_access_vendorlink(user) and not is_vendor_user(user)


def is_vendor_user(user):
    """True for external vendor accounts created on registration approval."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_meril_staff(user):
        return False
    return user.groups.filter(name__in=VENDOR_GROUPS).exists()


def can_manage_award(user, vendor_request):
    """Whether this user may nominate or withdraw a vendor on THIS request.

    Two routes in, not one: any VendorLink Admin can act on any request, same
    as every other admin-gated action on the platform -- but so can the
    specific person who raised this particular RFI, even without admin group
    membership. It is their own request; requiring an Admin-group escalation
    just to swap a nomination on it would make the common case (a requester
    changing their mind) route through someone else every time.

    Safe to trust ``raised_by`` alone for the second check without re-deriving
    "is this an internal account": a VendorRequest can only ever be raised by
    one (raising one mails every active vendor in the chosen country, which
    ``is_internal_user`` -- see its own docstring -- keeps vendor accounts from
    doing at all), so being the request's raiser already implies it.
    """
    if not user.is_authenticated:
        return False
    if is_vendorlink_admin(user):
        return True
    return vendor_request.raised_by_id == user.id
