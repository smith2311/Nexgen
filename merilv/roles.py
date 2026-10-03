"""Role assignment for Meril-V.

``rbac.py`` decides *what a group can do*; this module is where an admin
decides *who is in which group*, without needing the Django admin site.

Four tiers, matching the primary group names ``rbac.py`` checks for:

``Meril-V Viewer``  — read-only: history, reports, monitoring.
``Meril-V Picker``  — everyday operational use: scanning, validation, transfers.
``Meril-V Manager`` — a picker's supervisor, scoped to their plants.
``Meril-V Admin``   — the above, plus master data and entity configuration.

A user holds at most one of these four at a time. Letting someone accumulate
several would make it unclear which permission actually granted their access
when something needs auditing later, so assigning a new tier replaces the old
one rather than adding to it.

Deliberately absent: suspending or deleting an account from here. ``User`` is
platform-wide (MeriTrack, RS2T and VendorLink all cascade off it), and this
console has no business reaching into another module's data — Revoke, which
only drops the Meril-V groups, is the right-sized action for this screen.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from main.utils import invalidate_user_sessions

from . import permissions as capabilities
from .models import ActivityLog, Entity, PlantMaster, UserPlantMapping, UserScope
from .params import int_or_none
from .rbac import _MERIL_DOMAINS, is_merilv_admin
from .services import export, ingest

admin_required = user_passes_test(is_merilv_admin, login_url="merilv:index")

#: Every action on this screen ends up back on it — there is no other landing
#: place for a role change — so the route name is written once here rather than
#: two dozen times inline.
ROLES_URL = "merilv:masters_roles"

#: Deliberately vague about *why* the lookup failed. The id comes from a form
#: post, so "no such user" and "that id is not a number" are the same mistake
#: from the operator's side, and distinguishing them would only tell someone
#: probing ids which ones exist.
USER_NOT_FOUND_MSG = "That user could not be found."

# ── The four assignable tiers ────────────────────────────────────────────
# Each group name is spelled exactly once, here, and everything below is
# derived from these four. ``MANAGER_ROLE``/``PICKER_ROLE`` in particular are
# referenced by name elsewhere (the demo-seed command imports PICKER_ROLE), so
# they stay as their own constants rather than being inlined.
VIEWER_ROLE = "Meril-V Viewer"
PICKER_ROLE = "Meril-V Picker"
MANAGER_ROLE = "Meril-V Manager"
ADMIN_ROLE = "Meril-V Admin"

#: Paired name + description, in escalating order of access. Manager sits
#: between Picker and Admin: it can do everything a picker can, within the
#: plants it owns, but not touch master data.
#:
#: Kept as a tuple-of-tuples rather than a dict because Django templates can't
#: index a dict by a loop variable without a custom filter, and the view has no
#: other reason to look one up by name.
ROLE_DESCRIPTIONS = (
    (VIEWER_ROLE, "Read-only — history, reports and monitoring."),
    (PICKER_ROLE, "Scanning, validation and transfers. Sees only their manager's plants."),
    (MANAGER_ROLE, "A picker's supervisor. Operates on the plants mapped to them."),
    (ADMIN_ROLE, "Everything, plus master data and entity configuration. Not plant-scoped."),
)

#: The assignable tiers, escalating. Derived from ROLE_DESCRIPTIONS so the two
#: can never fall out of order or out of sync — several callers rely on the
#: last entry being the most privileged (see ``_current_role``).
ROLE_GROUPS = tuple(name for name, _description in ROLE_DESCRIPTIONS)


def _ctx(request, active_tab="", **extra):
    """Delegate to ``masters._ctx`` so the console chrome is built in one place.

    This used to be a copy of that function, which is exactly how the Roles tab
    ended up rendering an empty master picker: the copy never learned about the
    shell context, so ``active_tab`` was silently swallowed as an ordinary
    template variable.

    Imported inside the function on purpose — ``masters`` imports
    :data:`ROLE_GROUPS` from this module, so a module-level import here would be
    a circular one.
    """
    from .masters import _ctx as masters_ctx

    return masters_ctx(request, active_tab=active_tab, **extra)


def _log(request, action, target_type="", target_id="", **metadata):
    ActivityLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def _current_role(user):
    """The highest Meril-V tier a user currently holds, if any.

    Reads ``user.groups.all()``, which the caller prefetches — so this costs no
    query per row. It used to also take a ``groups_by_name`` dict that it never
    looked at, which meant the view built that dict (one extra query) purely to
    pass it into a parameter that was ignored.
    """
    names = {g.name for g in user.groups.all()}
    for role in reversed(ROLE_GROUPS):  # Admin first, so it wins if somehow doubled up
        if role in names:
            return role
    return None


def _is_meril_address(email: str) -> bool:
    email = (email or "").strip().lower()
    return any(email.endswith(domain) for domain in _MERIL_DOMAINS)


def _set_role(user, role: str) -> None:
    """Give ``user`` exactly one tier, dropping any other they held."""
    user.groups.remove(*Group.objects.filter(name__in=[r for r in ROLE_GROUPS if r != role]))
    user.groups.add(Group.objects.get_or_create(name=role)[0])


def _invalidate_access_sessions(user) -> int:
    """Sign out live sessions after a role or scope change."""
    return invalidate_user_sessions(user)


#: Free-text spellings a spreadsheet cell might use for a tier, resolved onto
#: the real group name. Built from ROLE_GROUPS rather than spelled out twice, so
#: a fourth tier would only need adding in one place.
#
# Built as a comprehension rather than a loop plus ``del _full``: the loop
# variable leaked into module scope, and the ``del`` that cleaned it up would
# raise NameError at IMPORT time if ROLE_GROUPS were ever empty — taking the
# module, and everything importing it, down with it. A comprehension has its
# own scope, so there is nothing to leak and nothing to delete.
_ROLE_ALIASES = {
    alias: full
    for full in ROLE_GROUPS
    for alias in (full.strip().lower(), full.replace("Meril-V ", "").strip().lower())
}


def _resolve_role(raw: str) -> str | None:
    """Match a cell value like "Admin" or "meril-v admin" to its real group name."""
    return _ROLE_ALIASES.get((raw or "").strip().lower())


def _upsert_user(email: str, first_name: str, last_name: str, role: str):
    """Create-or-grant one user, shared by the single-row form and bulk import.

    Raises ``ValueError`` on anything the caller should show back rather than
    act on, so both call sites report the same messages for the same mistakes.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("An email address is required.")
    if not _is_meril_address(email):
        raise ValueError(f"{email}: only Meril addresses can be given access.")
    if role not in ROLE_GROUPS:
        raise ValueError(f"{email}: '{role}' is not a valid tier.")

    first, last = (first_name or "").strip(), (last_name or "").strip()
    with transaction.atomic():
        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            # The local part is the natural username, de-duplicated if taken.
            base = email.split("@")[0][:150] or "user"
            username, suffix = base, 1
            while User.objects.filter(username=username).exists():
                suffix += 1
                username = f"{base[:145]}{suffix}"
            user = User.objects.create(
                username=username, email=email, first_name=first, last_name=last,
            )
            user.set_unusable_password()
            user.save()
        elif first or last:
            user.first_name, user.last_name = first or user.first_name, last or user.last_name
            user.save(update_fields=["first_name", "last_name"])

        _set_role(user, role)

    return user, created


def _role_tabs(query: str, active_role: str) -> list[dict]:
    """The All/Admin/Picker/Viewer filter chips, each carrying its own count.

    Counts are taken over every Meril-V user regardless of the current search or
    tier filter — they answer "how many total", the same way a tab strip's
    badges do not shrink to match whatever you already narrowed down to.
    """
    from urllib.parse import urlencode

    counts = {role: User.objects.filter(groups__name=role).count() for role in ROLE_GROUPS}
    base_url = reverse(ROLES_URL)

    def href(role: str) -> str:
        params = {}
        if role:
            params["role"] = role
        if query:
            params["q"] = query
        return f"{base_url}?{urlencode(params)}" if params else base_url

    tabs = [{
        "role": "", "label": "All", "count": sum(counts.values()),
        "href": href(""), "is_active": not active_role,
    }]
    tabs += [
        {
            "role": role, "label": role.replace("Meril-V ", ""), "count": counts[role],
            "href": href(role), "is_active": role == active_role,
        }
        for role in ROLE_GROUPS
    ]
    return tabs


@login_required
@admin_required
def index(request):
    """List every user holding a Meril-V role, with the controls to manage them."""
    role_users = (
        User.objects.filter(groups__name__in=ROLE_GROUPS)
        .distinct()
        .order_by("username")
        .prefetch_related("groups")
    )

    role_filter = request.GET.get("role", "").strip()
    if role_filter not in ROLE_GROUPS:
        role_filter = ""
    if role_filter:
        role_users = role_users.filter(groups__name=role_filter)

    query = request.GET.get("q", "").strip()
    if query:
        role_users = role_users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )

    # Accounts that exist but hold no Meril-V tier — granting one is a click
    # rather than re-typing an address that is already on the platform.
    without_access = []
    if query:
        without_access = list(
            User.objects.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
            .exclude(groups__name__in=ROLE_GROUPS)
            .distinct()[:20]
        )

    from .models import Entity, PickerManager, PlantMaster

    # Scope data for every listed user, fetched in two queries rather than per
    # row — this list runs to every Meril-V account.
    plants_by_user: dict[int, list[str]] = {}
    for user_id, plant_code in UserPlantMapping.objects.values_list("user_id", "plant_id"):
        plants_by_user.setdefault(user_id, []).append(plant_code)
    from .models import UserEntityMapping

    entities_by_user: dict[int, list[str]] = {}
    for user_id, code in UserEntityMapping.objects.values_list("user_id", "entity__code"):
        entities_by_user.setdefault(user_id, []).append(code)
    # The joined mapping, as "PLANT|ENTITY" strings — one value per posting,
    # which is what the scope form posts back. 67 plants x 5 entities is 335
    # possible pairs, so the form lists the ones a user HAS rather than every
    # combination; see the modal in masters_roles.html.
    pairs_by_user: dict[int, list[str]] = {}
    for user_id, plant_code, entity_code in UserScope.objects.values_list(
        "user_id", "plant_id", "entity__code",
    ):
        pairs_by_user.setdefault(user_id, []).append(f"{plant_code}|{entity_code}")

    managers_by_picker = {
        link.picker_id: link.manager
        for link in PickerManager.objects.select_related("manager")
    }

    pair_plants_by_user: dict[int, list[str]] = {}
    for uid, pairs in pairs_by_user.items():
        pair_plants_by_user[uid] = sorted({p.split("|")[0] for p in pairs})

    rows = []
    for user in role_users:
        role = _current_role(user)
        plants = sorted(plants_by_user.get(user.pk, []))
        manager = managers_by_picker.get(user.pk)
        rows.append({
            "user": user,
            "role": _current_role(user),
            "plants": sorted(plants_by_user.get(user.pk, [])),
            "entities": sorted(entities_by_user.get(user.pk, [])),
            "pairs": sorted(pairs_by_user.get(user.pk, [])),
            "pair_plants": pair_plants_by_user.get(user.pk, []),
            "manager": managers_by_picker.get(user.pk),
            # Rendered as a disabled control rather than hidden, so it is obvious
            # why an admin cannot act on their own row.
            "is_self": user.pk == request.user.pk,
            # A Picker or Manager with neither a plant of their own nor a
            # manager to inherit one from is scoped to NOTHING (rbac.plant_scope
            # fails closed), so every screen they open comes up empty and the
            # sessions they are sent to answer 404. The user sees a banner
            # explaining it; without this the ADMIN — the only person who can
            # fix it — had no way to see it short of logging in as them, which
            # is how it kept arriving as "the Picker role is broken".
            #
            # Admins are never scoped, so the warning would be nonsense on them.
            "unscoped": (
                role in (PICKER_ROLE, MANAGER_ROLE)
                and not plants
                and not pairs_by_user.get(user.pk)
                and manager is None
            ),
        })

    return render(
        request,
        "merilv/masters_roles.html",
        _ctx(
            request,
            active_tab="roles",
            rows=rows,
            result_count=len(rows),
            role_choices=ROLE_GROUPS,
            role_descriptions=ROLE_DESCRIPTIONS,
            role_tabs=_role_tabs(query, role_filter),
            role_filter=role_filter,
            q=query,
            without_access=without_access,
            meril_domains=", ".join(_MERIL_DOMAINS),
            all_plants=list(PlantMaster.objects.order_by("plant_code")),
            # Every active entity, not the admin's own scope: this is the list
            # of what can be GRANTED, and an admin is never entity-scoped.
            all_entities=list(Entity.objects.filter(is_active=True).order_by("sort_order", "code")),
            # Only managers can be reported to, so the picker's dropdown offers
            # exactly them rather than every account on the platform.
            manager_choices=list(
                User.objects.filter(groups__name=MANAGER_ROLE).distinct().order_by("username")
            ),
            manager_role=MANAGER_ROLE,
            picker_role=PICKER_ROLE,
            unscoped_count=sum(1 for row in rows if row["unscoped"]),
            # The granular capability matrix — one row per capability, one
            # column per tier. See merilv/permissions.py.
            capability_groups=_capability_groups(),
            capability_matrix=capabilities.matrix(_capability_groups()),
        ),
    )


def _capability_groups():
    """The four tiers as Group rows, created on demand and in tier order.

    ``get_or_create`` because a tier nobody has been assigned to yet has no
    Group — and a matrix missing a column is worse than one with an empty
    column, which is at least something an admin can tick.
    """
    return [
        Group.objects.prefetch_related("permissions").get(
            pk=Group.objects.get_or_create(name=name)[0].pk
        )
        for name in ROLE_GROUPS
    ]


@login_required
@admin_required
@require_POST
def set_permissions(request):
    """Replace the four tiers' capability grants with the submitted matrix.

    Replace rather than add, for the same reason :func:`set_plants` does: the
    form posts the complete intended state, and diffing against it is the only
    way an unticked box can actually remove a capability.

    Scoped to the module's OWN capabilities — the ones listed in
    ``permissions.CAPABILITIES``. A tier may hold Django permissions from
    elsewhere on the platform (VendorLink, Material Rework), and this screen
    has no business dropping them because they were not on a form it rendered.
    """
    from django.contrib.auth.models import Permission

    ours = {
        permission.codename: permission
        for permission in Permission.objects.filter(
            codename__in=[code for code, *_rest in capabilities.CAPABILITIES]
        )
    }

    changes = {}
    with transaction.atomic():
        for group in _capability_groups():
            # "<codename>:<group pk>" per checkbox, so one flat getlist carries
            # the whole grid without the template having to name every cell.
            wanted = {
                value.split(":", 1)[0]
                for value in request.POST.getlist("capability")
                if value.endswith(f":{group.pk}")
            }
            held = {
                permission.codename
                for permission in group.permissions.all()
                if permission.codename in ours
            }
            granted, revoked = wanted - held, held - wanted
            if granted:
                group.permissions.add(*[ours[code] for code in granted if code in ours])
            if revoked:
                group.permissions.remove(*[ours[code] for code in revoked])
            if granted or revoked:
                changes[group.name] = {
                    "granted": sorted(granted), "revoked": sorted(revoked),
                }

    if not changes:
        messages.info(request, "No capability changes to save.")
        return redirect(ROLES_URL)

    _log(request, "capabilities_update", "Group", "", changes=changes)
    # Every member of a changed tier is signed out, for the same reason a role
    # change signs one user out: permissions are read from the session's user
    # on each request, but a page already rendered still shows controls the
    # user no longer has, and the fastest way to make the new rules true
    # everywhere is to make everyone pick up a fresh session.
    signed_out = 0
    for name in changes:
        for member in User.objects.filter(groups__name=name).distinct():
            signed_out += _invalidate_access_sessions(member)
    messages.success(
        request,
        f"Capabilities updated for {len(changes)} role(s)."
        + (f" {signed_out} active session(s) were signed out so the change applies immediately."
           if signed_out else ""),
    )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def create(request):
    """Add a Meril-V user: the account if it is new, plus exactly one tier.

    No password is set. Everyone signs in through Zoho SSO, so a local password
    would be a second, unmanaged way in; ``set_unusable_password`` is what the
    Warehouse Inspection console does for the same reason.

    An address that already has an account is not an error — it just gets the
    role. Refusing would leave an admin stuck whenever someone already existed
    from another module.
    """
    role = (request.POST.get("role") or "").strip()
    try:
        user, created = _upsert_user(
            request.POST.get("email", ""),
            request.POST.get("first_name", ""),
            request.POST.get("last_name", ""),
            role,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(ROLES_URL)

    _log(request, "user_create" if created else "role_assign", "User", user.pk, role=role, email=user.email)
    signed_out = _invalidate_access_sessions(user) if not created else 0
    messages.success(
        request,
        f"{'Added' if created else 'Granted'} {user.get_full_name() or user.username} as {role}."
        + (" Active sessions were signed out so the new access applies immediately." if signed_out else ""),
    )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def update(request):
    """Edit one user's name, address and tier from the list."""
    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    email = (request.POST.get("email") or "").strip().lower()
    if email and not _is_meril_address(email):
        messages.error(request, "Only Meril addresses are accepted.")
        return redirect(ROLES_URL)
    if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        messages.error(request, f"{email} already belongs to another account.")
        return redirect(ROLES_URL)

    user.first_name = (request.POST.get("first_name") or "").strip()
    user.last_name = (request.POST.get("last_name") or "").strip()
    if email:
        user.email = email
    user.save(update_fields=["first_name", "last_name", "email"])

    role = (request.POST.get("role") or "").strip()
    if role in ROLE_GROUPS:
        _set_role(user, role)
    signed_out = _invalidate_access_sessions(user)

    _log(request, "user_update", "User", user.pk, role=role or None)
    messages.success(
        request,
        f"Updated {user.get_full_name() or user.username}."
        + (" Active sessions were signed out so the new access applies immediately." if signed_out else ""),
    )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def assign(request):
    """Give a user exactly one Meril-V role, replacing whatever they had."""
    user_id = request.POST.get("user_id")
    role = request.POST.get("role", "").strip()

    if role not in ROLE_GROUPS:
        messages.error(request, "Choose a valid role.")
        return redirect(ROLES_URL)

    user = User.objects.filter(pk=int_or_none(user_id)).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    # One transaction: _set_role removes the old groups and then adds the
    # new one. Unwrapped, a failure between the two leaves the user with
    # *no* Meril-V group at all — locked out of the module until an admin
    # notices. _upsert_user already wraps its identical call this way.
    with transaction.atomic():
        _set_role(user, role)
    signed_out = _invalidate_access_sessions(user)

    _log(request, "role_assign", "User", user.pk, role=role)
    messages.success(
        request,
        f"{user.get_full_name() or user.username} is now a {role}."
        + (" Active sessions were signed out so the new access applies immediately." if signed_out else ""),
    )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def remove(request):
    """Remove all Meril-V role groups from a user — back to no access at all."""
    user_id = request.POST.get("user_id")
    user = User.objects.filter(pk=int_or_none(user_id)).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    if user == request.user:
        messages.error(request, "You cannot remove your own Meril-V access.")
        return redirect(ROLES_URL)

    role_groups = Group.objects.filter(name__in=ROLE_GROUPS)
    user.groups.remove(*role_groups)
    signed_out = _invalidate_access_sessions(user)

    _log(request, "role_remove", "User", user.pk)
    messages.success(
        request,
        f"Removed Meril-V access for {user.get_full_name() or user.username}."
        + (" Active sessions were signed out immediately." if signed_out else ""),
    )
    return redirect(ROLES_URL)


# ─── Plant scope: manager → plants, picker → manager ─────────────────────────
@login_required
@admin_required
@require_POST
def set_plants(request):
    """Replace a manager's plant mapping with the submitted set.

    Replace rather than add: the form posts the complete intended set, so
    diffing it against what is there is the only way an unticked plant can
    actually be removed.
    """
    from .models import PlantMaster, UserPlantMapping

    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    wanted = set(request.POST.getlist("plants"))
    valid = set(PlantMaster.objects.filter(plant_code__in=wanted).values_list("plant_code", flat=True))
    unknown = wanted - valid
    if unknown:
        messages.warning(request, f"Ignored {len(unknown)} unknown plant code(s).")

    with transaction.atomic():
        current = set(user.merilv_plants.values_list("plant_id", flat=True))
        UserPlantMapping.objects.filter(user=user, plant_id__in=current - valid).delete()
        UserPlantMapping.objects.bulk_create(
            [UserPlantMapping(user=user, plant_id=code) for code in valid - current],
            ignore_conflicts=True,
        )

    _log(request, "role_set_plants", "User", user.pk, plants=sorted(valid))
    signed_out = _invalidate_access_sessions(user)
    name = user.get_full_name() or user.username
    if valid:
        messages.success(
            request,
            f"{name} is now scoped to {len(valid)} plant(s)."
            + (" Active sessions were signed out so the new scope applies immediately." if signed_out else ""),
        )
    else:
        # An empty plant mapping is NOT "no restriction". rbac.plant_scope
        # fails closed: they fall back to their manager's plants, and with no
        # manager either they are scoped to nothing and their screens come up
        # empty. This message used to promise the opposite, which is how an
        # admin removes someone's access believing they have widened it.
        link = getattr(user, "merilv_manager_link", None)
        if link is not None:
            messages.success(
                request,
                f"{name} now follows their manager’s plants ({link.manager.get_full_name() or link.manager.username}).",
            )
        else:
            messages.warning(
                request,
                f"{name} has no plants and no manager, so Meril-V screens will be empty for them. "
                "Give them plants here, or map them to a manager.",
            )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def set_entities(request):
    """Replace a user's entity mapping with the submitted set.

    The other axis to :func:`set_plants`: a plant is a site, an entity is a
    business unit with its own masters and parse rules, and a user can be
    scoped on either or both.

    Replace rather than add, for the same reason — the form posts the complete
    intended set, so diffing is the only way an unticked entity is removed.

    An empty set here really does mean unrestricted, unlike plants. That
    asymmetry is decided in ``rbac.entity_scope`` and explained there; the
    message below states which of the two the admin just did.
    """
    from .models import Entity, UserEntityMapping

    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    wanted = set(request.POST.getlist("entities"))
    valid = dict(
        Entity.objects.filter(code__in=wanted).values_list("code", "pk")
    )
    unknown = wanted - set(valid)
    if unknown:
        messages.warning(request, f"Ignored {len(unknown)} unknown entity code(s).")

    with transaction.atomic():
        current = set(user.merilv_entities.values_list("entity__code", flat=True))
        UserEntityMapping.objects.filter(
            user=user, entity__code__in=current - set(valid),
        ).delete()
        UserEntityMapping.objects.bulk_create(
            [
                UserEntityMapping(user=user, entity_id=pk)
                for code, pk in valid.items() if code not in current
            ],
            ignore_conflicts=True,
        )

    _log(request, "role_set_entities", "User", user.pk, entities=sorted(valid))
    name = user.get_full_name() or user.username
    if valid:
        messages.success(
            request,
            f"{name} is now limited to {', '.join(sorted(valid))}.",
        )
    else:
        messages.success(
            request, f"Entity restriction cleared for {name} — they may work in any entity.",
        )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def set_scopes(request):
    """Replace a user's plant+entity pairs with the submitted set.

    The joined mapping the 23 Sep MoM asks for, editable by hand. The Excel
    import loads it in bulk; this is for the correction afterwards, which is
    the half an admin actually needs at 6pm when somebody has been posted to
    the wrong site.

    Replace rather than add, matching :func:`set_plants` and
    :func:`set_entities`: the form posts the complete intended set, so diffing
    is the only way a removed pair actually goes.

    Each value arrives as ``PLANT|ENTITY``. Both halves are validated against
    the masters — a stale option on a page left open overnight must not be able
    to invent a mapping — and anything unrecognised is counted and reported
    rather than silently dropped.

    Clearing every pair does NOT mean "no access": it hands the user back to
    the two single-axis tables, which is where they were before anybody mapped
    them. ``rbac.pair_scope`` decides that, and the message below says which of
    the two just happened so an admin is not left guessing.
    """
    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    plant_ids = set(PlantMaster.objects.values_list("plant_code", flat=True))
    entity_ids = dict(Entity.objects.values_list("code", "pk"))

    wanted: set[tuple[str, str]] = set()
    unknown = 0
    for raw in request.POST.getlist("pairs"):
        plant, _, entity = (raw or "").partition("|")
        plant, entity = plant.strip(), entity.strip()
        if plant in plant_ids and entity in entity_ids:
            wanted.add((plant, entity))
        else:
            unknown += 1
    if unknown:
        messages.warning(request, f"Ignored {unknown} unrecognised plant/entity pair(s).")

    with transaction.atomic():
        current = set(
            user.merilv_scopes.values_list("plant_id", "entity__code")
        )
        for plant, entity in current - wanted:
            UserScope.objects.filter(
                user=user, plant_id=plant, entity__code=entity,
            ).delete()
        UserScope.objects.bulk_create(
            [
                UserScope(user=user, plant_id=plant, entity_id=entity_ids[entity])
                for plant, entity in wanted - current
            ],
            ignore_conflicts=True,
        )

    _log(request, "role_set_scopes", "User", user.pk,
         pairs=sorted(f"{p}|{e}" for p, e in wanted))
    signed_out = _invalidate_access_sessions(user)
    name = user.get_full_name() or user.username
    tail = (
        " Active sessions were signed out so the new scope applies immediately."
        if signed_out else ""
    )
    if wanted:
        shown = ", ".join(sorted(f"{p}/{e}" for p, e in wanted)[:6])
        more = f" and {len(wanted) - 6} more" if len(wanted) > 6 else ""
        messages.success(
            request, f"{name} is now limited to {shown}{more}." + tail,
        )
    else:
        messages.success(
            request,
            f"Plant+entity pairs cleared for {name} — their plant and entity "
            f"lists decide again." + tail,
        )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def set_manager(request):
    """Point a picker at the manager whose plant scope they inherit."""
    from .models import PickerManager

    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    manager_id = (request.POST.get("manager_id") or "").strip()
    if not manager_id:
        PickerManager.objects.filter(picker=user).delete()
        signed_out = _invalidate_access_sessions(user)
        _log(request, "role_clear_manager", "User", user.pk)
        messages.success(
            request,
            f"{user.get_full_name() or user.username} no longer reports to a manager — "
            "they are unscoped until a manager or plant is assigned."
            + (" Active sessions were signed out so the new scope applies immediately." if signed_out else ""),
        )
        return redirect(ROLES_URL)

    manager = User.objects.filter(pk=int_or_none(manager_id)).first()
    if manager is None:
        messages.error(request, "That manager could not be found.")
        return redirect(ROLES_URL)
    if manager.pk == user.pk:
        messages.error(request, "A user cannot report to themselves.")
        return redirect(ROLES_URL)

    PickerManager.objects.update_or_create(picker=user, defaults={"manager": manager})
    signed_out = _invalidate_access_sessions(user)
    _log(request, "role_set_manager", "User", user.pk, manager=manager.pk)
    messages.success(
        request,
        f"{user.get_full_name() or user.username} now reports to "
        f"{manager.get_full_name() or manager.username}."
        + (" Active sessions were signed out so the new scope applies immediately." if signed_out else ""),
    )
    return redirect(ROLES_URL)


@login_required
@admin_required
@require_POST
def save_scope(request):
    """Save all scope data for a user in one POST: plants, entities, pairs, manager.

    Replaces the four individual save endpoints in the modal with a single
    submit so the admin clicks once.  Each section is still replace-semantics:
    the form posts the complete intended set and anything absent is removed.
    """
    from .models import (
        Entity, PickerManager, PlantMaster,
        UserEntityMapping, UserPlantMapping, UserScope,
    )

    user = User.objects.filter(pk=int_or_none(request.POST.get("user_id"))).first()
    if user is None:
        messages.error(request, USER_NOT_FOUND_MSG)
        return redirect(ROLES_URL)

    role = _current_role(user)
    is_admin = role == ADMIN_ROLE
    is_manager = role == MANAGER_ROLE
    is_picker = role == PICKER_ROLE
    changes: list[str] = []

    with transaction.atomic():
        if not is_admin:
            # ── Pairs ──
            plant_ids = set(PlantMaster.objects.values_list("plant_code", flat=True))
            entity_map = dict(Entity.objects.values_list("code", "pk"))
            wanted_pairs: set[tuple[str, str]] = set()
            for raw in request.POST.getlist("pairs"):
                plant, _, entity = (raw or "").partition("|")
                plant, entity = plant.strip(), entity.strip()
                if plant in plant_ids and entity in entity_map:
                    wanted_pairs.add((plant, entity))
            current_pairs = set(user.merilv_scopes.values_list("plant_id", "entity__code"))
            for plant, entity in current_pairs - wanted_pairs:
                UserScope.objects.filter(user=user, plant_id=plant, entity__code=entity).delete()
            UserScope.objects.bulk_create(
                [UserScope(user=user, plant_id=p, entity_id=entity_map[e])
                 for p, e in wanted_pairs - current_pairs],
                ignore_conflicts=True,
            )
            if wanted_pairs != current_pairs:
                changes.append("postings")

            # ── Entities ──
            wanted_entities = set(request.POST.getlist("entities"))
            valid_entities = dict(Entity.objects.filter(code__in=wanted_entities).values_list("code", "pk"))
            current_entities = set(user.merilv_entities.values_list("entity__code", flat=True))
            UserEntityMapping.objects.filter(user=user, entity__code__in=current_entities - set(valid_entities)).delete()
            UserEntityMapping.objects.bulk_create(
                [UserEntityMapping(user=user, entity_id=pk)
                 for code, pk in valid_entities.items() if code not in current_entities],
                ignore_conflicts=True,
            )
            if set(valid_entities) != current_entities:
                changes.append("entities")

        if is_manager and not is_admin:
            # ── Plants (managers only) ──
            wanted_plants = set(request.POST.getlist("plants"))
            valid_plants = set(PlantMaster.objects.filter(plant_code__in=wanted_plants).values_list("plant_code", flat=True))
            current_plants = set(user.merilv_plants.values_list("plant_id", flat=True))
            UserPlantMapping.objects.filter(user=user, plant_id__in=current_plants - valid_plants).delete()
            UserPlantMapping.objects.bulk_create(
                [UserPlantMapping(user=user, plant_id=code) for code in valid_plants - current_plants],
                ignore_conflicts=True,
            )
            if valid_plants != current_plants:
                changes.append("plants")

        if is_picker and not is_admin:
            # ── Manager (pickers only) ──
            manager_id = (request.POST.get("manager_id") or "").strip()
            current_link = getattr(user, "merilv_manager_link", None)
            current_manager_pk = str(current_link.manager_id) if current_link else ""
            if manager_id != current_manager_pk:
                if not manager_id:
                    PickerManager.objects.filter(picker=user).delete()
                else:
                    manager = User.objects.filter(pk=int_or_none(manager_id)).first()
                    if manager and manager.pk != user.pk:
                        PickerManager.objects.update_or_create(picker=user, defaults={"manager": manager})
                changes.append("manager")

    name = user.get_full_name() or user.username
    if changes:
        _log(request, "scope_save", "User", user.pk, sections=changes)
        signed_out = _invalidate_access_sessions(user)
        messages.success(
            request,
            f"Saved scope for {name} ({', '.join(changes)})."
            + (" Active sessions were signed out so the new scope applies immediately." if signed_out else ""),
        )
    else:
        messages.info(request, f"No scope changes for {name}.")
    return redirect(ROLES_URL)


#: The bulk-import sheet's columns — also what :func:`import_template` hands
#: back blank, so the two can never disagree on what a row looks like.
#: Plant and Entity are one fact, not two. The business team's sheet lists a
#: person once per posting — "Ramesh, Ahmedabad, MHPL" then "Ramesh, Kolkata,
#: MLSIPL" — and each line becomes one UserScope row, so he gets those two
#: pairs and NOT Ahmedabad-MLSIPL. See models.UserScope.
#:
#: Both are optional: a sheet with neither column still imports users and
#: roles exactly as it did before, leaving their scope alone.
USER_IMPORT_HEADERS = ("Email", "First Name", "Last Name", "Role", "Plant", "Entity")
USER_IMPORT_COLUMNS = {
    "email": ("Email", "Email Address", "User Email"),
    "first_name": ("First Name", "First"),
    "last_name": ("Last Name", "Last"),
    "role": ("Role", "Tier", "Access Level"),
    "plant": ("Plant", "Plant Code", "Plant Name"),
    "entity": ("Entity", "Entity Code", "Business Unit"),
}


@login_required
@admin_required
def import_template(request):
    """A header-only workbook for the bulk user import, filled in and re-uploaded."""
    buffer = export.export_generic("Meril-V Users Template", list(USER_IMPORT_HEADERS), [])
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="merilv_users_template.xlsx"'
    return response


@login_required
@admin_required
@require_POST
def bulk_import(request):
    """Add or grant many users at once from an uploaded spreadsheet.

    Each row goes through the same :func:`_upsert_user` the single-add form
    uses, so a bulk file can never create an account the form itself would
    refuse — and this is what "Role" being "Admin"/"Picker"/"Viewer" instead of
    the full "Meril-V Admin" group name resolves against (see
    :data:`_ROLE_ALIASES`), since that is the natural way to type it in a sheet.

    Rows are applied independently, not inside one transaction: a bulk file is
    realistically going to have a handful of typos in a hundred rows, and the
    99 good ones should not be held hostage by 1 bad one (the same reasoning
    ``customers_import`` in masters.py already uses).
    """
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose an .xlsx or .csv file to import.")
        return redirect(ROLES_URL)
    if not upload.name.lower().endswith((".xlsx", ".xls", ".csv")):
        messages.error(request, "Only .xlsx, .xls and .csv files are accepted.")
        return redirect(ROLES_URL)

    try:
        result = ingest.parse_simple_master(
            # Deduped on the whole pair, not the email: a person legitimately
            # appears once per plant+entity they work in, and keying on the
            # email would keep the first posting and silently drop the rest.
            upload, USER_IMPORT_COLUMNS,
            dedupe=("email", "plant", "entity"), filename=upload.name, key="email",
        )
    except ingest.IngestError as exc:
        messages.error(request, str(exc))
        return redirect(ROLES_URL)

    created = updated = scoped = 0
    problems: list[str] = []
    for line, record in enumerate(result.records, start=2):  # row 1 is the header
        role = _resolve_role(record.get("role", ""))
        if role is None:
            problems.append(f"Row {line}: '{record.get('role', '')}' is not Admin, Picker or Viewer.")
            continue
        try:
            _user, was_created = _upsert_user(
                record.get("email", ""), record.get("first_name", ""), record.get("last_name", ""), role,
            )
        except ValueError as exc:
            problems.append(f"Row {line}: {exc}")
            continue
        if not was_created:
            _invalidate_access_sessions(_user)
        created += was_created
        updated += not was_created

        # The plant+entity pair, when the sheet carries one. Granted rather
        # than replacing what the user already has: rows are applied
        # independently here, so a half-filled sheet must not read as "revoke
        # everything not mentioned". Removing a posting is done on this screen.
        plant_code = (record.get("plant", "") or "").strip()
        entity_code = (record.get("entity", "") or "").strip()
        if not plant_code and not entity_code:
            continue
        if not plant_code or not entity_code:
            problems.append(
                f"Row {line}: Plant and Entity go together — give both, or neither."
            )
            continue
        plant = PlantMaster.objects.filter(plant_code__iexact=plant_code).first()
        entity = Entity.objects.filter(code__iexact=entity_code).first()
        if plant is None:
            problems.append(f"Row {line}: no plant with code '{plant_code}'.")
            continue
        if entity is None:
            problems.append(f"Row {line}: no entity with code '{entity_code}'.")
            continue
        _scope, made = UserScope.objects.get_or_create(
            user=_user, plant=plant, entity=entity,
        )
        if made:
            scoped += 1
            _invalidate_access_sessions(_user)

    _log(
        request, "users_bulk_import", "User", "",
        created=created, updated=updated, scoped=scoped, problems=len(problems),
    )
    for warning in result.warnings:
        messages.warning(request, warning)
    for problem in problems[:10]:
        messages.warning(request, problem)
    if len(problems) > 10:
        messages.warning(request, f"…and {len(problems) - 10} more row(s) skipped.")
    summary = f"Imported {created:,} new and updated {updated:,} existing user(s)."
    if scoped:
        # Worth saying: a sheet whose Plant/Entity columns are misspelt
        # imports the people and silently scopes nobody otherwise.
        summary += f" Granted {scoped:,} plant+entity access row(s)."
    messages.success(request, summary)
    return redirect(ROLES_URL)
