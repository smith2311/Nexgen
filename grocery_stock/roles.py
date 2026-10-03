"""
Grocery Stock & Cost Management — roles and API permissions
─────────────────────────────────────────────────────────────────────────────
The module has two roles, both Django Groups named with a ``Grocery-`` prefix
to match the ``Visa-Department`` / ``PR-Manager`` convention already used in
this codebase:

    * ``Grocery-Admin``   — the whole module, and the only role that may write
      master data.
    * ``Grocery-Manager`` — the stock floor: goods receipts, issues,
      adjustments, and the two live stock views behind them.

The specification's section 18 listed a further six roles (Purchase Manager,
Store Manager, Warehouse Executive, Finance, Department User, Auditor). They
were dropped by migration 0007; everything they used to write is now
Grocery-Admin's, and everything they used to read is covered by the two roles
above. Both groups are created by migration 0002, so a fresh environment has
them without anyone running a management command by hand.
"""
from rest_framework import permissions

# ── Group names ─────────────────────────────────────────────────────────────

ADMIN = 'Grocery-Admin'
MANAGER = 'Grocery-Manager'

ALL_GROUPS = [
    ADMIN,
    MANAGER,
]


# ── Module sections (what the home page groups the screens into) ────────────
#
# Grocery-Admin sees the whole module; Grocery-Manager runs the stock floor and
# sees only the operations screens — goods receipts, issues, adjustments, and
# the two live stock views behind them. The distinction is section-level rather
# than per-screen because that is how the landing page is already organised, so
# one flag hides the card and blocks the URL together.

SECTION_INSIGHTS = 'insights'
SECTION_OPERATIONS = 'operations'
SECTION_MASTERS = 'masters'

ALL_SECTIONS = frozenset({SECTION_INSIGHTS, SECTION_OPERATIONS, SECTION_MASTERS})

# Roles confined to part of the module. A role absent from this map may open
# every section — which today means Grocery-Admin, handled by the superuser
# short-circuit in visible_sections as well.
RESTRICTED_SECTIONS = {
    MANAGER: frozenset({SECTION_OPERATIONS}),
}


def user_groups(user):
    """Grocery group names this user belongs to, lowercased comparison-safe."""
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.filter(name__in=ALL_GROUPS).values_list('name', flat=True))


def is_grocery_admin(user):
    """Grocery-Admin, or a Django superuser, which we always treat as full access."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or ADMIN in user_groups(user)


def has_role(user, *roles):
    """True when the user holds any of ``roles`` — or is a Grocery-Admin."""
    if is_grocery_admin(user):
        return True
    return bool(user_groups(user) & set(roles))


def visible_sections(user):
    """
    Which module sections this user may open.

    A user holding several roles gets the union rather than the intersection,
    so adding a role can only ever widen access — the only reading that does
    not make granting a role take permissions away.
    """
    if is_grocery_admin(user):
        return set(ALL_SECTIONS)

    groups = user_groups(user)
    if not groups:
        return set()

    allowed = set()
    for group in groups:
        allowed |= RESTRICTED_SECTIONS.get(group, ALL_SECTIONS)
    return allowed


def can_see(user, section):
    """True when ``section`` is one this user's roles may open."""
    return section in visible_sections(user)


class GroceryRolePermission(permissions.BasePermission):
    """
    Read for either grocery role, write only for the roles a view names.

    Both roles need to *read* — a Grocery-Manager cannot record a goods receipt
    without seeing the item and supplier lists — so reads are open across the
    two roles and only writes are gated per view.

    Reads still require *some* grocery role rather than merely being signed in.
    The module exposes inventory valuation, supplier spend and cost variance,
    and every page already gates on grocery membership
    (grocery_stock.views_pages._require_grocery). Leaving the API open to any
    authenticated employee would have made the JSON strictly easier to reach
    than the screen rendering it.

    A view sets the roles allowed to write::

        class GoodsReceiptViewSet(DocumentViewSet):
            write_roles = (roles.MANAGER,)

    Grocery-Admin and Django superusers always pass. A view with no
    ``write_roles`` is admin-only to write, which is the safe default and is
    what every master endpoint now relies on.
    """

    message = 'Your grocery role does not allow this action.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return user.is_superuser or bool(user_groups(user))
        return has_role(user, *getattr(view, 'write_roles', ()))
