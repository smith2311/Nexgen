"""Who may reach Material Rework at all.

Separate from the per-section checks in ``views.py`` (``_is_production``,
``_can_om_edit`` and the rest). Those answer "may this person act at this
stage"; this answers the earlier question of whether the module appears to them
in the first place.

It exists because the two answers had drifted apart, and the gap only showed up
in production. The per-stage checks were already pure group membership — assign
``MR PPIC`` and the PPIC screens accept you. But the two ENTRY points each
carried their own hand-written list of groups, and neither matched the module's
own seven roles:

* the sidebar (``core.context_processors.nav_access``) listed only
  ``Material Rework Admin`` and ``Material Rework User``;
* the dashboard tile (``main.views.landing_page``) added ``MR Production`` and
  ``MR Warehouse`` and stopped there.

So ``MR Order Management`` and ``MR PPIC`` appeared in neither, and
``MR Production`` / ``MR Warehouse`` got a tile but no sidebar link. Assigning
someone a role granted them permissions they had no way to navigate to — which
reads, correctly, as "the role did nothing". On a test server a superuser never
notices, because superuser short-circuits both lists.

``Root User`` is included deliberately. It is the module's designed
non-superuser administrator — a single holder, set with
``manage.py set_root_user <email>`` — and is what ``views.py`` already accepts
alongside ``is_superuser`` for user and role management. It was missing from
both entry lists, so the one role created for running this module in production
without a superuser account could not see the module.
"""

from __future__ import annotations

#: Full module administration across every section.
ADMIN_GROUP = "Material Rework Admin"

#: Raises requests and tracks their own uploads.
USER_GROUP = "Material Rework User"

#: Manages users, roles and masters without being a Django superuser. One
#: holder at a time — see material_rework/management/commands/set_root_user.py.
ROOT_GROUP = "Root User"

#: The stage roles. MR HOD is retired from routing but still assignable, so its
#: existing members keep their access until they are moved off it.
WORKFLOW_GROUPS = (
    "MR HOD",
    "MR Order Management",
    "MR PPIC",
    "MR Production",
    "MR Warehouse",
)

#: Every group that should put Material Rework in front of someone. The single
#: list both entry points read, so adding a role here is the only edit needed.
MODULE_GROUPS = (ADMIN_GROUP, USER_GROUP, ROOT_GROUP) + WORKFLOW_GROUPS


def can_access_material_rework(user) -> bool:
    """True when the module should be visible and reachable for ``user``.

    Holding any Material Rework role is enough. Superusers are admitted too,
    but as a convenience rather than the mechanism — the whole point is that a
    production administrator who is not a superuser can assign a role and have
    it work.

    This does NOT decide what someone may do once inside; each screen still
    applies its own check. A ``Material Rework User`` reaching the warehouse URL
    directly is still refused by ``warehouse_view``.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=MODULE_GROUPS).exists()
