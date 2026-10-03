"""Granular capabilities for Meril-V, on top of the four tiers.

``rbac.py`` answers "which tier is this user, and which rows may they see".
That is the right shape for module access and data scope, and it is the wrong
shape for the questions the floor actually asks: *this* picker may correct a
batch, *that* one may not; a manager may edit courier paperwork after the fact
but not clear a scan log. Those cut across the tiers, and encoding them as more
tiers would need one group per combination.

So a tier stays what it is — a starting point — and each capability below is a
real Django ``Permission`` that an admin ticks or unticks per group, from
*Master Data → Roles & Access* or from the Django admin site. Plain Django
permissions on purpose: they are already visible in the admin, already
inherited through group membership, and already survive a user being moved
between tiers.

Resolution, in :func:`can`:

1. Superusers may do anything.
2. If any of the user's groups holds the capability, they may.
3. If the user is in one of the four managed tiers, that is the whole answer —
   an unticked box is a revoke.
4. Otherwise fall back to the tier predicate this capability was seeded from.

Step 3 is what makes the matrix mean anything. Without it, ``can`` would be
"the permission OR the old tier check", and unticking Courier Edit from
Meril-V Manager would change nothing at all — the manager predicate would keep
answering yes.

Step 4 is what makes the module's legacy group names — "Warehouse Manager",
"PS Warehouse", "Global SCM", which are not the four tiers and are never
seeded — keep behaving exactly as they did before this file existed. An
installation that has those groups and not the tiers is unaffected by the
matrix, because nobody has ticked anything for it.

The permissions hang off :class:`~merilv.models.Entity` because Django needs a
concrete model to own a permission and Entity is the module's root
configuration object. The content type is an implementation detail — the
codenames are what the module checks, and they read ``merilv.<codename>``.
"""

from __future__ import annotations

from . import rbac

#: ``(codename, label, description, tier)``.
#:
#: ``tier`` is BOTH the group seeded with this capability by the migration AND
#: the predicate :func:`can` falls back to when no group holds it — the two are
#: the same statement ("this is a picker-level action") and must not drift
#: apart, so they are written once.
#:
#: "picker" therefore means *picker and above*, matching ``rbac.PICKER_GROUPS``:
#: a manager who can scan is not a special case, they are a picker's supervisor.
CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "mv_scan",
        "Scanning",
        "Scan material against a delivery in the outbound console.",
        "picker",
    ),
    # Batch correction, split per console at the client's instruction (23 Sep
    # MoM): "inbound/outbound batch correction rights need to be handled
    # individually". One tick-box used to cover both the Outbound Scanner and
    # Branch Transfer, so allowing a picker to correct a dispatch batch also
    # let them repoint a transfer line, which is a different job on a
    # different screen.
    (
        "mv_batch_correction_outbound",
        "Batch Correction (Outbound)",
        "Change the batch a delivery line expects when the shelf cannot supply "
        "the one SAP named. Outbound Scanner only.",
        "picker",
    ),
    (
        "mv_batch_correction_transfer",
        "Batch Correction (Branch Transfer)",
        "Override the batch a picker should draw for a branch-transfer line. "
        "Branch Transfer is its own console and its own decision.",
        "picker",
    ),
    (
        "mv_undo_scan",
        "Undo Last Scan",
        "Reverse the most recent scan on a delivery.",
        "picker",
    ),
    (
        "mv_serial_edit",
        "Serial Correction",
        "Correct a mistyped serial on a scan already in the log, without "
        "changing what was counted against the delivery.",
        "picker",
    ),
    (
        "mv_document_edit_inbound",
        "Document Correction (Inbound)",
        "Correct what a booked-in unit recorded — its Good/Damage condition — "
        "after the receipt was scanned. The inbound half of the same right, "
        "and until now it had no check at all.",
        "picker",
    ),
    (
        "mv_clear_scans",
        "Clear Scan Log",
        "Clear every scan on one delivery at once. Wider than Undo — it throws "
        "away a whole delivery's picking.",
        "manager",
    ),
    (
        "mv_delete_scan",
        "Delete A Scan Row",
        "Remove one individual scan from the log.",
        "manager",
    ),
    (
        "mv_courier_edit",
        "Courier Edit",
        "Change the courier, docket, vehicle and weight on a delivery that has "
        "already been finished.",
        "manager",
    ),
    (
        "mv_document_edit_outbound",
        "Document Correction (Outbound)",
        "Edit a finished delivery's paperwork from the console's Finish "
        "modal — the Edit button on a closed delivery.",
        # Opened from manager to picker at the client's instruction: the person
        # who packed the box is the one who notices the docket was mistyped,
        # and making them find a manager to fix a typo is how the wrong docket
        # ships. It reaches the paperwork only — the serials are not editable
        # from there, and the docket-conflict rule still applies to a changed
        # docket, so a correction cannot land one docket on two customers.
        #
        # This is the seeded default, not a ceiling: tightening it back is a
        # tick-box on Master Data → Roles & Access, not a deploy.
        "picker",
    ),
    (
        "mv_master_upload",
        "Master Data Upload",
        "Import master-data spreadsheets (customers, couriers, materials, "
        "plants, cartons).",
        "admin",
    ),
)

#: Codename -> the rest of its row, for the lookups below.
_BY_CODE = {code: (label, help_text, tier) for code, label, help_text, tier in CAPABILITIES}

#: What Django's permission framework is told to create. Consumed by
#: ``Entity.Meta.permissions`` and by the migration that seeds the groups, so
#: adding a capability above is the only edit a new one needs.
MODEL_PERMISSIONS: list[tuple[str, str]] = [
    (code, f"Meril-V: {label}") for code, label, _help, _tier in CAPABILITIES
]

#: The tier predicate each capability falls back to. Named rather than looked
#: up by string at call time so a typo in a tier name fails at import.
_TIER_PREDICATES = {
    "picker": rbac.can_operate_merilv,
    "manager": rbac.is_merilv_manager,
    "admin": rbac.is_merilv_admin,
}

#: The group seeded with each tier, for the migration and for the matrix UI.
#: Viewer is here with no capabilities of its own — the group still has to
#: exist, and the matrix still has to show its (empty) column, or an admin has
#: no way to grant a viewer anything.
TIER_GROUPS = {
    "viewer": "Meril-V Viewer",
    "picker": "Meril-V Picker",
    "manager": "Meril-V Manager",
    "admin": "Meril-V Admin",
}

#: Escalating, so the matrix seeds a tier with its own capabilities and every
#: lower tier's — a manager who cannot scan is not a manager.
TIER_ORDER = ("viewer", "picker", "manager", "admin")

#: The tiers this module manages. Membership of one of these is what makes the
#: matrix authoritative for a user (step 3 in the module docstring).
MANAGED_GROUPS = frozenset(TIER_GROUPS.values())


def seed_map() -> dict[str, list[str]]:
    """``{group name: [codename, …]}`` — what each tier starts out holding.

    A tier gets its own capabilities and every lower tier's, which is the same
    containment ``rbac``'s group sets already express (PICKER_GROUPS includes
    MANAGER_GROUPS includes ADMIN_GROUPS, read the other way round). Viewer is
    absent on purpose: a viewer is read-only and every capability here is a
    write.
    """
    seeded: dict[str, list[str]] = {}
    for tier in TIER_ORDER:
        allowed = TIER_ORDER[: TIER_ORDER.index(tier) + 1]
        seeded[TIER_GROUPS[tier]] = [
            code for code, _l, _h, t in CAPABILITIES if t in allowed
        ]
    return seeded


def can(user, codename: str) -> bool:
    """May ``user`` do ``codename``? See the module docstring for the order."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    entry = _BY_CODE.get(codename)
    if entry is None:
        # An unknown codename is a bug, not a grant.
        return False
    if user.has_perm(f"merilv.{codename}"):
        return True
    if _in_managed_tier(user):
        # In a tier this screen manages: the matrix has already answered, and
        # the answer was no. Falling through to the predicate here is what
        # would make unticking a box do nothing.
        return False
    return _TIER_PREDICATES[entry[2]](user)


def _in_managed_tier(user) -> bool:
    """Is this user in one of the four tiers the matrix governs?

    Memoised on the user for the life of the request. ``_ctx`` asks ``can``
    once per capability to decide which controls to draw, and without this that
    is one ``EXISTS`` query per capability on every page of the module — the
    ``has_perm`` half is already cached by Django's own ``_perm_cache``.
    """
    cached = getattr(user, "_mv_managed_tier", None)
    if cached is None:
        cached = user.groups.filter(name__in=MANAGED_GROUPS).exists()
        user._mv_managed_tier = cached
    return cached


def capability_required(codename: str):
    """View decorator form of :func:`can`.

    Answers 403 rather than redirecting: every endpoint these guard is a POST
    from the console, and a redirect to a login page reaches the fetch() that
    sent it as an opaque success.
    """
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not can(request.user, codename):
                raise PermissionDenied(
                    f"Your role does not include “{_BY_CODE[codename][0]}”."
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def matrix(groups):
    """Rows for the Roles & Access permission matrix.

    ``groups`` is the ordered list of ``Group`` objects the screen shows. Each
    row carries the capability and one cell per group, so the template is a
    plain nested loop rather than a dict lookup it cannot do.
    """
    held = {
        (group.pk, perm.codename)
        for group in groups
        for perm in group.permissions.all()
    }
    return [
        {
            "codename": code,
            "label": label,
            "help": help_text,
            "tier": tier,
            "cells": [
                {"group": group, "held": (group.pk, code) in held}
                for group in groups
            ],
        }
        for code, label, help_text, tier in CAPABILITIES
    ]


__all__ = [
    "CAPABILITIES",
    "MODEL_PERMISSIONS",
    "TIER_GROUPS",
    "can",
    "capability_required",
    "matrix",
    "seed_map",
]
