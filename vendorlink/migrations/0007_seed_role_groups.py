from django.db import migrations

#: Every group VendorLink's access rules read, with the tier it belongs to.
#:
#: These are the names hard-coded in ``vendorlink/rbac.py`` and offered as role
#: presets by the admin console (``VL_ROLE_GROUPS``). Until now they only came
#: into existence when somebody happened to create them by hand, so a fresh
#: install had none of them and the role pickers pointed at groups that did not
#: exist. Seeding them here means the tiers are visible in Django admin on any
#: deployment, and memberships can be managed there like any other group.
#:
#: What a group MEANS is decided in rbac.py, not by Django permissions — see
#: the note in ``seed_groups``.
ROLE_GROUPS = [
    # (group name, tier, what the tier may do)
    ("VendorLink Admin", "Administrator", "add, edit and delete; manage users"),
    ("Vendor Master Admin", "Administrator", "add, edit and delete; manage users"),
    ("Global SCM", "Administrator", "add, edit and delete; manage users"),
    ("VendorLink User", "Editor", "add and edit; no delete, no user management"),
    ("Vendor Master User", "Editor", "add and edit; no delete, no user management"),
    ("VendorLink Viewer", "Viewer", "read-only"),
    ("Vendor Master Viewer", "Viewer", "read-only"),
    ("VendorUser", "Vendor portal", "external vendor: own RFIs and bids only"),
]


def seed_groups(apps, schema_editor):
    """Create any of the role groups that are missing.

    ``get_or_create`` rather than ``create``: most deployments already have
    several of these made by hand, and their existing memberships and
    permissions must survive untouched. Only the genuinely absent ones are
    added — on the current dev database that is ``Vendor Master Viewer``.

    No permissions are attached deliberately. VendorLink authorises on GROUP
    MEMBERSHIP (``rbac.can_access_vendorlink``, ``is_vendorlink_admin``,
    ``can_edit_vendorlink`` all test ``user.groups``), not on Django model
    permissions, so attaching a permission set here would render a picture of
    the rights in the admin that the application does not actually consult —
    worse than showing none, because it would look authoritative. Changing who
    is in a group changes their VendorLink access; changing a group's
    permissions does not.
    """
    Group = apps.get_model("auth", "Group")
    for name, _tier, _rights in ROLE_GROUPS:
        Group.objects.get_or_create(name=name)


def unseed_groups(apps, schema_editor):
    """Reverse: deliberately a no-op.

    Dropping a group would silently revoke every membership attached to it,
    including ones made long before this migration ran. An unmigrate is not a
    reason to take people's access away, and the groups are harmless if the
    migration is rolled back.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("vendorlink", "0006_vendor_state_and_gptmessage_source"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_groups, unseed_groups),
    ]
