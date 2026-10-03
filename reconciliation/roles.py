"""
reconciliation/roles.py — who may do what.
=============================================================================
Two roles, held as Django groups named with a ``Recon-`` prefix to match the
``Grocery-``, ``PR-`` and ``VAT-`` conventions already in this codebase:

    * ``Recon-Admin`` — the whole module. Everything a User can do, plus
      importing files, re-running the rules, bulk-removing and editing the
      billing-type master that drives the classifier.
    * ``Recon-User``  — reads every screen, corrects individual documents, and
      exports.

Django groups are the single source of truth. The admin screen that grants and
revokes access (see ``admin.ReconciliationAccessAdmin``) writes group
membership and nothing else, so what the admin shows and what the module
enforces cannot drift apart.

The split between the two is about **blast radius**, not seniority. Fixing one
document's HSN code affects one document; changing a billing type's Final
Remark silently re-labels several thousand rows on the next re-run, and
uploading the wrong file rewrites whatever it matches. Those belong with the
people who own the filing.

Everything here is read-only and side-effect free, so it is safe to call from a
template, a view or a permission class.
"""
ADMIN = "Recon-Admin"
USER = "Recon-User"

ALL_GROUPS = (ADMIN, USER)

#: What each role is called on screen. Django group names carry the module
#: prefix so they read unambiguously in Django's own group list; people think
#: in terms of "Admin" and "User".
ROLE_LABELS = {
    ADMIN: "Admin",
    USER: "User",
}

#: The capability matrix, stated once.
#:
#: The admin screen renders this so the person granting a role can see exactly
#: what they are granting, and the module guide quotes it. A capability that
#: exists in code but not here would be invisible to whoever hands it out,
#: which is how people end up granting more than they meant to.
CAPABILITIES = (
    ("Open the module and read every screen", (ADMIN, USER)),
    ("Correct an individual document", (ADMIN, USER)),
    ("Export records and the filing summary", (ADMIN, USER)),
    ("Upload and import a reconciliation export", (ADMIN,)),
    ("Re-run the reason rules over stored records", (ADMIN,)),
    ("Remove records in bulk", (ADMIN,)),
    ("Edit the billing-type master", (ADMIN,)),
)


def _groups(user):
    """The module groups this user holds."""
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.filter(name__in=ALL_GROUPS).values_list("name", flat=True))


def role_of(user):
    """``ADMIN``, ``USER`` or None — the single role this user acts with.

    Admin wins when someone holds both, and a Django superuser is always an
    admin here: a platform administrator who could grant themselves the group
    anyway gains nothing from being made to.
    """
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return ADMIN
    held = _groups(user)
    if ADMIN in held:
        return ADMIN
    if USER in held:
        return USER
    return None


def is_admin(user):
    """Holds ``Recon-Admin``, or is a Django superuser."""
    return role_of(user) == ADMIN


def can_access(user):
    """May open the module at all."""
    return role_of(user) is not None


def can_edit(user):
    """May correct an individual document.

    Every role that can see the module can correct a row: a reconciliation
    nobody is allowed to fix is just a list of complaints.
    """
    return can_access(user)


def can_export(user):
    """May download records or the filing summary."""
    return can_access(user)


def can_import(user):
    """May upload a file, re-run the rules, or bulk-remove."""
    return is_admin(user)


def can_manage_masters(user):
    """May edit the billing-type master."""
    return is_admin(user)


def permissions_for(user):
    """The flags the templates and the page script read, in one dict."""
    return {
        "role": role_of(user),
        "role_label": ROLE_LABELS.get(role_of(user), ""),
        "access": can_access(user),
        "edit": can_edit(user),
        "export": can_export(user),
        "import": can_import(user),
        "masters": can_manage_masters(user),
        "admin": is_admin(user),
    }
