"""VendorLink Admin Console.

A dedicated, admin-only command centre that consolidates every VendorLink
master and all platform users into one polished workspace:

  • Console   — KPI overview + quick links + recent activity
  • Users     — every account that can touch VendorLink (portal vendors + staff)
  • Masters   — a directory of all master datasets (vendors, commercials,
                countries, capabilities, registrations, requests)

Everything here is gated by ``is_vendorlink_admin``. No new database models are
introduced — the console derives its data from the existing VendorLink models
plus Django's auth User/Group tables.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from main.utils import invalidate_user_sessions

from . import useractivity
from .capabilities import adoption_rows
from .constants import CAPABILITY_ICON_GROUPS
from .forms import CapabilityForm
from .utils import continent_choices, continent_country_map_json
from .models import (
    Capability,
    Vendor,
    VendorBid,
    VendorRegistration,
    VendorRequest,
)
from .rbac import (
    ADMIN_GROUPS,
    USER_GROUPS,
    VENDOR_GROUPS,
    _MERIL_DOMAINS,
    is_vendorlink_admin,
)
from .utils import canonical_countries

# Reuse the shared password helpers already living in views.py.
from . import views as _views

admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:index")

_VL_GROUPS = ADMIN_GROUPS | USER_GROUPS | VENDOR_GROUPS

# Role presets for new VendorLink-scoped accounts. Each maps to the set of
# groups that grant the matching level of access *within this module only*
# (no is_staff / is_superuser, so they cannot reach other modules or Django admin).
VL_ROLE_GROUPS = {
    "admin": ["VendorLink Admin", "Vendor Master Admin"],
    "editor": ["VendorLink User", "Vendor Master User"],
    "viewer": ["VendorLink Viewer", "Vendor Master Viewer"],
}

#: Groups this preset used to grant and must now actively take away.
#:
#: "Global SCM" was in the admin preset, and it is ALSO an admin group in
#: Meril-V (see merilv.rbac.ADMIN_GROUPS — the only name the two modules
#: share). Granting somebody VendorLink Admin therefore made them a Meril-V
#: Admin as well, silently and with no Meril-V screen ever showing it. Three
#: accounts reached production that way.
#:
#: A module's role preset may only hand out groups that module owns. Cleared
#: on every role change so an account granted the old preset stops carrying it
#: the next time its role is touched.
VL_LEGACY_GROUPS = ["Global SCM"]

VL_ROLE_META = [
    {
        "key": "admin", "label": "Administrator", "icon": "fa-shield-halved", "accent": "violet",
        "desc": "Full control of VendorLink — manage vendors, commercials, registrations, requests and users.",
        "groups": VL_ROLE_GROUPS["admin"],
    },
    {
        "key": "editor", "label": "Editor", "icon": "fa-pen-to-square", "accent": "blue",
        "desc": "Can add and edit vendors, commercials and requests, but cannot manage other users.",
        "groups": VL_ROLE_GROUPS["editor"],
    },
    {
        "key": "viewer", "label": "Viewer", "icon": "fa-eye", "accent": "cyan",
        "desc": "Read-only access to the VendorLink directory and dashboards.",
        "groups": VL_ROLE_GROUPS["viewer"],
    },
]


# ── Role assignment ─────────────────────────────────────────────────────────
# Who may hand out which tier. The rule, in the words it was given in:
#
#   "The admin can update and give rights for the viewer and editor, and if
#    admin rights I want to change — from admin, downgrade someone to viewer or
#    editor — then that right should be given to a superuser only."
#
# So a VendorLink admin manages the two lower tiers freely, and every operation
# that GRANTS or REMOVES Administrator is a superuser's to make. Granting is
# included deliberately: if an admin could mint another admin, the restriction
# on demotion would be one account-creation away from meaningless.
def _is_external_vendor(user) -> bool:
    """Whether this account signs in with a local password rather than Zoho.

    Meril staff authenticate through Zoho SSO, so a temporary password is
    never something they use and "you must change your password" is a wall
    with nothing behind it. Only external vendors are issued local passwords,
    and only they should be made to rotate one.
    """
    email = (user.email or "").strip().lower()
    return not any(email.endswith(d) for d in _MERIL_DOMAINS)


def _vl_role_of(user):
    """Which VendorLink tier ``user`` currently holds, or ``None``.

    Admin is checked first so somebody carrying groups from two tiers — which
    the old create-only flow could not produce but a hand-edited account can —
    reads as the higher of them rather than whichever happened to be tested.
    """
    names = set(user.groups.values_list("name", flat=True))
    for key in ("admin", "editor", "viewer"):
        if set(VL_ROLE_GROUPS[key]) & names:
            return key
    return None


def _role_change_block(actor, target, new_role):
    """Why ``actor`` may not set ``target`` to ``new_role`` — "" if they may."""
    if target is not None and actor.pk == target.pk:
        # Not a privilege question: a lone admin demoting themselves would lose
        # the very screen they would need to undo it.
        return "You cannot change your own VendorLink role."
    if actor.is_superuser:
        return ""
    if new_role == "admin":
        return "Only a superuser can grant Administrator rights. Choose Editor or Viewer."
    if target is not None and _vl_role_of(target) == "admin":
        return "Only a superuser can change an Administrator's role."
    return ""


def _vl_roles_of(user):
    """EVERY VendorLink tier ``user`` holds, highest first.

    A companion to :func:`_vl_role_of`, which answers "what is this person,
    in one word" for display. This one is the truth: a superuser may grant
    more than one tier at a time, so the set is what has to be edited.
    """
    names = set(user.groups.values_list("name", flat=True))
    return [key for key in ("admin", "editor", "viewer")
            if set(VL_ROLE_GROUPS[key]) & names]


def _apply_vl_roles(user, roles):
    """Set ``user``'s VendorLink tiers to exactly ``roles``.

    Only VendorLink's own groups are touched. An account that also holds, say,
    "Warehouse Inspection Admin" keeps it — the whole point of a module-scoped
    role is that it says nothing about the other modules.

    Passing more than one tier is legitimate and is a superuser's call: the
    tiers are group bundles, not an enum, so "viewer and editor" is a real
    combination rather than a contradiction. Passing none removes VendorLink
    access altogether.
    """
    roles = [r for r in roles if r in VL_ROLE_GROUPS]
    wanted = {name for r in roles for name in VL_ROLE_GROUPS[r]}
    others = ({name for names in VL_ROLE_GROUPS.values() for name in names}
              | set(VL_LEGACY_GROUPS)) - wanted
    user.groups.remove(*Group.objects.filter(name__in=others))
    for name in sorted(wanted):
        group, _ = Group.objects.get_or_create(name=name)
        user.groups.add(group)
    return sorted(wanted), sorted(others)


def _apply_vl_role(user, role):
    """Single-tier convenience wrapper over :func:`_apply_vl_roles`."""
    return _apply_vl_roles(user, [role])


def _assignable_roles(actor):
    """The role presets ``actor`` is allowed to choose from."""
    if actor.is_superuser:
        return VL_ROLE_META
    return [r for r in VL_ROLE_META if r["key"] != "admin"]


# ── helpers ─────────────────────────────────────────────────────────────────
def _adm_ctx(request, active="console", **extra):
    """Context shared across all admin-console pages (drives the sub-nav)."""
    base = {
        "is_vm_admin": True,
        "adm_active": active,
        "pending_regs": VendorRegistration.objects.filter(
            status=VendorRegistration.STATUS_PENDING
        ).count(),
        # Console screens print "n/total capable"; the total is the master's
        # size, not a literal nine.
        "capability_total": Capability.objects.filter(is_active=True).count(),
        "continent_choices": continent_choices(),
        "continent_country_map": continent_country_map_json(),
    }
    base.update(extra)
    return base


def _classify_user(user, vendor_emails):
    """Return a short role label for a user account.

    Delegates to :func:`useractivity.classify_user` so the console's user list
    and its Insights dashboard cannot put the same person in different buckets
    on two screens of the same console. That function also reads groups off the
    prefetched relation rather than issuing `.filter().exists()` per user, which
    is what this copy used to do — two queries per row on a 40-row table.
    """
    return useractivity.classify_user(user, vendor_emails)


def _vl_user_queryset():
    """Every account that can access VendorLink, in one queryset."""
    meril_q = Q()
    for d in _MERIL_DOMAINS:
        meril_q |= Q(email__iendswith=d)
    return (
        User.objects.filter(
            Q(groups__name__in=_VL_GROUPS)
            | Q(is_superuser=True)
            | (Q(is_staff=True) & meril_q)
        )
        .distinct()
        .prefetch_related("groups")
        .order_by("-last_login", "-date_joined")
    )


# ── Console dashboard ─────────────────────────────────────────────────────────
@login_required
@admin_required
def console(request):
    active_vendors = Vendor.objects.filter(is_active=True).count()
    total_vendors = Vendor.objects.count()

    vendor_emails = set(
        e.lower()
        for e in Vendor.objects.exclude(email="").values_list("email", flat=True)
    )
    all_users = list(_vl_user_queryset())
    portal_users = sum(
        1 for u in all_users if _classify_user(u, vendor_emails) == "Vendor Portal"
    )
    staff_users = len(all_users) - portal_users

    stats = {
        "active_vendors": active_vendors,
        "total_vendors": total_vendors,
        "portal_users": portal_users,
        "staff_users": staff_users,
        "total_users": len(all_users),
        # Canonicalised, not a raw DISTINCT — "India" and "india" are one
        # country, and counting them as two overstated this tile for years.
        "countries": len(canonical_countries(
            Vendor.objects.filter(is_active=True).values_list("country", flat=True)
        )),
        "requests": VendorRequest.objects.count(),
        "bids": VendorBid.objects.count(),
        "reg_pending": VendorRegistration.objects.filter(
            status=VendorRegistration.STATUS_PENDING
        ).count(),
        "reg_total": VendorRegistration.objects.count(),
    }

    return render(request, "vendorlink/admin/console.html", _adm_ctx(
        request, active="console", stats=stats,
    ))


# ── Users ──────────────────────────────────────────────────────────────────────
@login_required
@admin_required
def users(request):
    q = (request.GET.get("q") or "").strip()
    role = (request.GET.get("role") or "").strip()
    status = (request.GET.get("status") or "").strip()

    vendor_emails = set(
        e.lower()
        for e in Vendor.objects.exclude(email="").values_list("email", flat=True)
    )
    qs = _vl_user_queryset()
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    # Classify every (search-filtered) user once.
    classified = []
    counts = {}
    for u in qs:
        label = _classify_user(u, vendor_emails)
        counts[label] = counts.get(label, 0) + 1
        classified.append((u, label))

    rows = [
        {
            "obj": u,
            "role": label,
            "groups": ", ".join(g.name for g in u.groups.all()) or "—",
        }
        for u, label in classified
        if not role or label == role
    ]

    ROLE_ORDER = ["Superuser", "Admin", "Meril Staff", "Vendor Portal", "User"]
    roles = [{"label": r, "count": counts.get(r, 0)} for r in ROLE_ORDER]

    return render(request, "vendorlink/admin/users.html", _adm_ctx(
        request, active="users",
        rows=rows, q=q, role=role, status=status,
        total=len(rows), all_total=len(classified), roles=roles,
    ))


@login_required
@admin_required
def user_detail(request, pk):
    user = get_object_or_404(User, pk=pk)
    vendor_emails = set(
        e.lower()
        for e in Vendor.objects.exclude(email="").values_list("email", flat=True)
    )
    role = _classify_user(user, vendor_emails)
    vendor = None
    if user.email:
        vendor = Vendor.objects.filter(email__iexact=user.email).first()
    registration = None
    if user.email:
        registration = (
            VendorRegistration.objects.filter(email__iexact=user.email)
            .order_by("-submitted_at")
            .first()
        )
    bids = (
        VendorBid.objects.filter(submitted_by=user)
        .select_related("vendor_request")
        .order_by("-submitted_at")[:10]
    )
    try:
        profile = user.profile
    except Exception:
        profile = None

    # Every tier held, not just the highest: the panel offers one button per
    # tier the user does NOT have, so "which ones are held" is what it needs.
    # Reading the single _vl_role_of here instead left the panel saying "no
    # VendorLink access" for an administrator, and offering to set the role
    # they already had.
    held = _vl_roles_of(user)
    labels = {r["key"]: r["label"] for r in VL_ROLE_META}
    return render(request, "vendorlink/admin/user_detail.html", _adm_ctx(
        request, active="users",
        u=user, role=role, vendor=vendor, registration=registration,
        bids=bids, profile=profile,
        groups=list(user.groups.values_list("name", flat=True)),
        vl_held=[labels[key] for key in held],
        # Only a superuser may hold more than one tier at once, so only a
        # superuser is shown the checkbox form that can express it.
        vl_multi=request.user.is_superuser,
        # Per role: whether it is held, and whether THIS actor may set it on
        # THIS user — with the reason it is refused, for the button's tooltip.
        vl_role_options=[
            {
                **r,
                "held": r["key"] in held,
                "blocked": _role_change_block(request.user, user, r["key"]),
            }
            for r in VL_ROLE_META
        ],
    ))


@login_required
@admin_required
@require_POST
def user_action(request, pk):
    """Toggle active state or reset password for a VendorLink user."""
    user = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")

    if user == request.user and action in {"deactivate"}:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("vendorlink:admin_user_detail", pk=pk)

    if action == "activate":
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, f"{user.get_full_name() or user.username} has been activated.")

    elif action == "deactivate":
        if user.is_superuser:
            messages.error(request, "Superuser accounts cannot be deactivated from here.")
        else:
            user.is_active = False
            user.save(update_fields=["is_active"])
            messages.success(request, f"{user.get_full_name() or user.username} has been deactivated.")

    elif action == "reset_password":
        if not user.email:
            messages.error(request, "This account has no email address — cannot send new credentials.")
            return redirect("vendorlink:admin_user_detail", pk=pk)
        temp_pw = _reset_user_password(user)
        if temp_pw:
            _send_user_credentials(user, temp_pw)
            messages.success(request, f"Password reset — fresh credentials sent to {user.email}.")
        else:
            messages.error(request, "Password reset failed. Please try again.")

    else:
        messages.error(request, "Unknown action.")

    return redirect("vendorlink:admin_user_detail", pk=pk)


def _reset_user_password(user):
    """Generate a temp password, apply it, and force a change on next login."""
    from main.models import UserProfile
    try:
        temp_pw = _views._generate_vendor_password()
        user.set_password(temp_pw)
        user.is_active = True
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        # Only an external vendor is made to rotate it — see _is_external_vendor.
        profile.force_password_change = _is_external_vendor(user)
        profile.save()
        return temp_pw
    except Exception:
        return None


def _send_user_credentials(user, temp_pw):
    from django.conf import settings
    from django.core.mail import send_mail
    try:
        site_url = getattr(settings, "SITE_URL", "https://nexgen.merillife.com")
        name = user.get_full_name() or user.username
        send_mail(
            subject="Meril VendorLink — Your Password Has Been Reset",
            message=(
                f"Dear {name},\n\n"
                f"Your Meril VendorLink account password has been reset by an administrator.\n\n"
                f"  Login URL : {site_url}/\n"
                f"  Username  : {user.username}\n"
                f"  Password  : {temp_pw}\n\n"
                f"This is a temporary password. You will be asked to set a new permanent "
                f"password the first time you log in.\n\n"
                f"— Meril Global SCM"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception:
        pass


# ── Create a VendorLink-scoped admin/user ───────────────────────────────────────
@login_required
@admin_required
def user_create(request):
    """Create a new account scoped to VendorLink only and assign module roles.

    The new account is **not** a Django superuser or staff member, so it can only
    reach VendorLink — never other platform modules or the Django admin.
    """
    import re
    email_re = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,10}$")

    if request.method == "POST":
        first = (request.POST.get("first_name") or "").strip()
        last = (request.POST.get("last_name") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        role = (request.POST.get("role") or "admin").strip()

        # An email that is already on the platform is NOT an error. It is the
        # normal case: email is the login username platform-wide, so anyone
        # added for any other module already has an account. Refusing here left
        # VendorLink with no way at all to grant rights to such a person —
        # they cannot be created, and they do not appear in the users list
        # either (see _vl_user_queryset), so there was nothing to edit.
        existing = None
        if email:
            existing = (
                User.objects.filter(username__iexact=email).first()
                or User.objects.filter(email__iexact=email).first()
            )
        confirmed = request.POST.get("assign_existing") == "1"

        errors = {}
        if not email:
            errors["email"] = "Email address is required."
        elif not email_re.match(email):
            errors["email"] = "Enter a valid email address."
        # A name is needed to CREATE somebody; granting a role to an account
        # that already exists does not need one — theirs is already on file.
        if existing is None and not first:
            errors["first_name"] = "First name is required."
        if role not in VL_ROLE_GROUPS:
            role = "admin"
        block = _role_change_block(request.user, existing, role)
        if block:
            errors["role"] = block

        def _rerender(**extra):
            context = dict(
                active="users", roles=_assignable_roles(request.user),
                errors=errors,
                form={"first_name": first, "last_name": last, "email": email, "role": role},
            )
            context.update(extra)
            return render(request, "vendorlink/admin/user_create.html",
                          _adm_ctx(request, **context))

        if errors:
            messages.error(request, "Please correct the errors below.")
            return _rerender(
                existing_user=existing,
                existing_role=_vl_role_of(existing) if existing else None,
                existing_groups=sorted(existing.groups.values_list("name", flat=True)) if existing else [],
            )

        if existing is not None:
            if not confirmed:
                # Show who they are and what they already hold, and make the
                # admin say yes. Granting access to the wrong person on a typo
                # is the accident this step exists to prevent.
                return _rerender(
                    existing_user=existing,
                    existing_role=_vl_role_of(existing),
                    existing_groups=sorted(existing.groups.values_list("name", flat=True)),
                )
            previous = _vl_role_of(existing)
            added, removed = _apply_vl_role(existing, role)
            signed_out = invalidate_user_sessions(existing)
            messages.success(
                request,
                f"{existing.get_full_name() or existing.username} now has the VendorLink "
                f"{role} role"
                + (f" (was {previous})" if previous else " (had no VendorLink access)")
                + f". Groups added: {', '.join(added)}."
                + (f" Removed: {', '.join(removed)}." if removed else "")
                + " Their access to other modules is unchanged, and no new account was created."
                + (" Existing sessions were signed out so the new access applies immediately." if signed_out else ""),
            )
            return redirect("vendorlink:admin_user_detail", pk=existing.pk)

        # Create the account.
        from main.models import UserProfile
        user = User.objects.create(
            username=email, email=email,
            first_name=first, last_name=last,
            is_active=True, is_staff=False, is_superuser=False,
        )
        temp_pw = _views._generate_vendor_password()
        user.set_password(temp_pw)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.force_password_change = _is_external_vendor(user)
        profile.save()

        # Assign every group for the chosen module role.
        assigned = []
        for gname in VL_ROLE_GROUPS[role]:
            group, _ = Group.objects.get_or_create(name=gname)
            user.groups.add(group)
            assigned.append(gname)

        _send_new_admin_credentials(user, temp_pw, role)
        messages.success(
            request,
            f"VendorLink {role} account created for {user.get_full_name() or email}. "
            f"Credentials emailed to {email}. Roles assigned: {', '.join(assigned)}.",
        )
        return redirect("vendorlink:admin_user_detail", pk=user.pk)

    allowed = _assignable_roles(request.user)
    return render(request, "vendorlink/admin/user_create.html", _adm_ctx(
        request, active="users", roles=allowed,
        errors={}, form={"role": allowed[0]["key"]},
    ))


@login_required
@admin_required
@require_POST
def user_role(request, pk):
    """Change one account's VendorLink tier.

    Separate from :func:`user_action`, which only ever toggled active state and
    reset passwords — there was no way to change a role after creation at all.
    The permission rule lives in :func:`_role_change_block`.
    """
    user = get_object_or_404(User, pk=pk)

    # A superuser posts checkboxes and may send several, or none to revoke
    # access; everyone else posts a single value. getlist covers both.
    roles = [r.strip() for r in request.POST.getlist("role") if r.strip()]
    unknown = [r for r in roles if r not in VL_ROLE_GROUPS]
    if unknown:
        messages.error(request, f"Unknown role: {', '.join(unknown)}.")
        return redirect("vendorlink:admin_user_detail", pk=pk)

    if len(roles) > 1 and not request.user.is_superuser:
        messages.error(request, "Only a superuser can give someone more than one VendorLink role.")
        return redirect("vendorlink:admin_user_detail", pk=pk)
    if not roles and not request.user.is_superuser:
        messages.error(request, "Only a superuser can remove VendorLink access entirely.")
        return redirect("vendorlink:admin_user_detail", pk=pk)

    previous = _vl_roles_of(user)

    # Every tier being ADDED and every tier being TAKEN AWAY must be one this
    # actor may touch. Checking only the new list would make "leave admin out
    # of the checkboxes" a way to demote an administrator without the right
    # to do it.
    for role in set(roles) | set(previous):
        block = _role_change_block(request.user, user, role)
        if block:
            messages.error(request, block)
            return redirect("vendorlink:admin_user_detail", pk=pk)

    if sorted(roles) == sorted(previous):
        messages.info(
            request,
            f"{user.get_full_name() or user.username} already has exactly that: "
            f"{', '.join(previous) or 'no VendorLink role'}.",
        )
        return redirect("vendorlink:admin_user_detail", pk=pk)

    added, removed = _apply_vl_roles(user, roles)
    signed_out = invalidate_user_sessions(user)
    messages.success(
        request,
        f"{user.get_full_name() or user.username}: "
        f"{', '.join(previous) or 'no VendorLink role'} -> "
        f"{', '.join(roles) or 'no VendorLink access'}. "
        f"Groups now: {', '.join(added) or 'none'}."
        + (f" Removed: {', '.join(removed)}." if removed else "")
        + (" Existing sessions were signed out so the new access applies immediately." if signed_out else ""),
    )
    return redirect("vendorlink:admin_user_detail", pk=pk)


def _send_new_admin_credentials(user, temp_pw, role):
    from django.conf import settings
    from django.core.mail import send_mail
    try:
        site_url = getattr(settings, "SITE_URL", "https://nexgen.merillife.com")
        name = user.get_full_name() or user.username
        send_mail(
            subject="Meril VendorLink — Your Administrator Account",
            message=(
                f"Dear {name},\n\n"
                f"A VendorLink {role} account has been created for you on the Meril "
                f"NexGen platform.\n\n"
                f"  Login URL : {site_url}/\n"
                f"  Username  : {user.username}\n"
                f"  Password  : {temp_pw}\n\n"
                f"This is a temporary password. You will be asked to set a permanent "
                f"password the first time you log in.\n\n"
                f"Your account has access to the VendorLink module only.\n\n"
                f"— Meril Global SCM"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception:
        pass


# ── Masters directory ──────────────────────────────────────────────────────────
@login_required
@admin_required
def masters(request):
    # The console dashboard is now the single hub for all masters.
    return redirect("vendorlink:admin_console")


@login_required
@admin_required
def capabilities(request):
    """Capability master — add, edit, retire, and see adoption per dimension.

    Was a read-only rendering of a hardcoded nine-item list. It is a real master
    now: the rows are :class:`Capability` records, and adding one here puts it on
    the vendor form, the public registration form, the capability matrix, the
    directory filter, the bid comparison matrix and the AI evaluator's service
    catalogue at once — which is the whole reason for the table.
    """
    form = CapabilityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        capability = form.save()
        messages.success(
            request,
            f'Capability "{capability.label}" added. It now appears on the vendor '
            f"and registration forms.",
        )
        return redirect("vendorlink:admin_capabilities")
    if request.method == "POST":
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    active_total = Vendor.objects.filter(is_active=True).count()
    rows = adoption_rows(vendor_total=active_total)
    # Retired capabilities are listed separately rather than hidden: they are
    # still attached to vendors that declared them, so an admin wondering where
    # a dimension went needs to be able to see it and switch it back on.
    retired = list(Capability.objects.filter(is_active=False))

    return render(request, "vendorlink/admin/capabilities.html", _adm_ctx(
        request, active="masters",
        rows=rows, retired=retired, active_total=active_total, form=form,
        icon_groups=CAPABILITY_ICON_GROUPS,
    ))


@login_required
@admin_required
def capability_edit(request, pk):
    """Edit one capability's label, icon, note or position."""
    capability = get_object_or_404(Capability, pk=pk)
    form = CapabilityForm(request.POST or None, instance=capability)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f'Capability "{capability.label}" updated.')
        return redirect("vendorlink:admin_capabilities")
    if request.method == "POST":
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    return render(request, "vendorlink/admin/capability_form.html", _adm_ctx(
        request, active="masters",
        form=form, capability=capability,
        vendor_count=capability.vendor_count,
        icon_groups=CAPABILITY_ICON_GROUPS,
    ))


@login_required
@admin_required
@require_POST
def capability_toggle(request, pk):
    """Retire or reinstate a capability.

    Retire rather than delete, always. The vendor links are the record of what
    each vendor declared and when; deleting the master row would take that with
    it, and there is no version of "we no longer track cold chain" that also
    means "no vendor ever claimed it". A retired capability leaves the forms and
    filters and stays on the vendors that hold it.
    """
    capability = get_object_or_404(Capability, pk=pk)
    capability.is_active = not capability.is_active
    capability.save(update_fields=["is_active", "updated_at"])
    if capability.is_active:
        messages.success(request, f'"{capability.label}" is back on the capability forms.')
    else:
        messages.success(
            request,
            f'"{capability.label}" retired. It is off the forms and filters; the '
            f"{capability.vendor_count} vendor(s) that declared it keep it on their record.",
        )
    return redirect("vendorlink:admin_capabilities")


# ── Insights — per-user activity analytics ────────────────────────────────────
# Admin-only and deliberately unscoped: this is the one screen whose whole point
# is cross-user visibility, so `admin_required` (is_vendorlink_admin, true for
# superusers) is what keeps it out of an editor's or a vendor's reach. A vendor
# portal login reaching it would see every other vendor's bid activity.
@login_required
@admin_required
def insights(request):
    """All-users dashboard, or a redirect into one user's own drill-down.

    The `user` parameter is the picker at the top of the screen. It redirects
    rather than branching in place so the individual view keeps its own URL —
    shareable, bookmarkable and back-button-correct.
    """
    days = useractivity.resolve_window(request.GET.get("days"))
    picked = (request.GET.get("user") or "").strip()
    if picked:
        try:
            target = int(picked)
        except ValueError:
            # A junk pk is an operator typo, not something to 500 over — fall
            # through to the all-users view.
            target = None
        if target:
            return redirect(
                f"{reverse('vendorlink:admin_insights_user', args=[target])}?days={days}"
            )

    data = useractivity.leaderboard(days)
    return render(request, "vendorlink/admin/insights.html", _adm_ctx(
        request, active="insights",
        days=days,
        windows=useractivity.WINDOW_CHOICES,
        kind_labels=useractivity.KIND_LABELS,
        **data,
    ))


@login_required
@admin_required
def insights_user(request, pk):
    """One user's end-to-end VendorLink flow."""
    days = useractivity.resolve_window(request.GET.get("days"))
    subject = get_object_or_404(User, pk=pk)
    data = useractivity.user_detail(subject, days)
    return render(request, "vendorlink/admin/insights_user.html", _adm_ctx(
        request, active="insights",
        subject=subject,
        days=days,
        windows=useractivity.WINDOW_CHOICES,
        **data,
    ))
