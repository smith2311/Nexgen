"""Read/write layer over the Capability master.

Every screen that used to iterate ``constants.CAPABILITY_FIELDS`` now comes
through here. The point of the indirection is that a capability is a database
row, so "the list of capabilities" is a query — and a query in nine modules and
seven templates is how a screen ends up showing eight of the nine, or ordering
them differently from the one next to it.

Nothing at module level touches the database: these are imported by ``views``,
``forms`` and ``ai_evaluator`` at import time, and a query there would break
``migrate`` on an empty database.
"""

from __future__ import annotations

from django.db.models import Count, Q

from .constants import DEFAULT_CAPABILITIES
from .models import Capability


def active_capabilities():
    """The capability rows that forms and filters should offer, in sort order."""
    return list(Capability.objects.filter(is_active=True))


def all_capabilities():
    """Every row including retired ones — for the master screen and exports."""
    return list(Capability.objects.all())


def capability_rows():
    """[(code, label, icon), …] for the active capabilities.

    Shaped exactly like the old ``CAPABILITY_FIELDS`` constant so the call sites
    that unpack three-tuples did not all have to change at once. New code should
    prefer :func:`active_capabilities` and read attributes by name.
    """
    return [(c.code, c.label, c.icon) for c in active_capabilities()]


def active_codes():
    """Set of active capability codes — used to validate a ?capability= filter."""
    return set(
        Capability.objects.filter(is_active=True).values_list("code", flat=True)
    )


def capability_by_code(code):
    """One capability by code, or None. Does not care whether it is active:
    a filter link saved before a capability was retired should still resolve to
    the right row rather than silently matching nothing."""
    if not code:
        return None
    return Capability.objects.filter(code=code).first()


def flags_for(holder):
    """[{code, label, icon, yes}, …] for one Vendor or VendorRegistration.

    Covers every active capability, not just the ones the record holds — a
    capability list that omits the No answers reads as "not asked" rather than
    "answered no", which is the difference the bid comparison matrix turns on.
    """
    held = holder.capability_codes
    return [
        {"code": c.code, "label": c.label, "icon": c.icon, "yes": c.code in held}
        for c in active_capabilities()
    ]


def adoption_rows(vendor_total=None):
    """Per-capability adoption across active vendors, highest first.

    One aggregate query rather than a count per capability — the master screen
    and the capability matrix's summary bar both want this, and the loop version
    was N+1 on a table that only grows.
    """
    rows = (
        Capability.objects.filter(is_active=True)
        .annotate(n=Count("vendors", filter=Q(vendors__is_active=True), distinct=True))
        .order_by("-n", "sort_order", "label")
    )
    if vendor_total is None:
        from .models import Vendor

        vendor_total = Vendor.objects.filter(is_active=True).count()
    return [
        {
            "capability": c,
            "code": c.code,
            "label": c.label,
            "icon": c.icon,
            "description": c.description,
            "count": c.n,
            "pct": round((c.n / vendor_total) * 100) if vendor_total else 0,
        }
        for c in rows
    ]


def apply_from_post(holder, data, field_name="capabilities"):
    """Set ``holder``'s capabilities from a POST QueryDict.

    Only ever assigns codes that exist and are active, so a hand-crafted POST
    cannot attach a retired or invented capability. Capabilities the record
    already holds that are now inactive are preserved — the form never showed
    them, so a save through it must not be read as the vendor withdrawing them.
    """
    submitted = set(data.getlist(field_name)) if hasattr(data, "getlist") else set(data or [])
    allowed = {c.code: c for c in active_capabilities()}
    keep_inactive = [
        c for c in holder.capabilities.all() if c.code not in allowed
    ]
    chosen = [allowed[code] for code in submitted if code in allowed]
    holder.capabilities.set(chosen + keep_inactive)
    holder.__dict__.pop("_capability_codes", None)
    return chosen


def copy_capabilities(source, target):
    """Mirror one holder's capabilities onto another.

    Used when a registration is approved into a Vendor record, and when a
    capability assessment is promoted — the vendor's declared set should carry
    across rather than being re-entered.
    """
    target.capabilities.set(list(source.capabilities.all()))
    target.__dict__.pop("_capability_codes", None)


def ensure_seeded():
    """Create the nine spec capabilities if the master is empty.

    The data migration does this for existing databases. This is for the case
    the migration cannot cover: a database where someone deleted every row, or
    a test that builds its fixtures before the master exists. Deliberately a
    no-op when *any* row is present — re-adding a capability an admin
    deliberately deleted would be the code overruling them.
    """
    if Capability.objects.exists():
        return []
    return [
        Capability.objects.create(
            code=code, label=label, icon=icon, sort_order=(index + 1) * 10,
        )
        for index, (code, label, icon) in enumerate(DEFAULT_CAPABILITIES)
    ]
