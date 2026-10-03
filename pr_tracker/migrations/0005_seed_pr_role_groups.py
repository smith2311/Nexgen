from django.db import migrations

#: The three groups PR Tracker's access rules read.
#:
#: These are the names hard-coded in ``_pr_roles`` (pr_tracker/views.py) and
#: created by the ``setup_pr_roles`` management command. Until now that command
#: was the only thing that made them, and nothing ran it as part of a deploy —
#: so whether a group existed depended on somebody having thought to.
#:
#: PR-Manager and PR-Member turned up anyway, because the User Master screen
#: get_or_creates those two when it saves somebody into them. PR-Admin has no
#: such path: it is the module's administrator tier, it grants every role at
#: once, and there was no way to reach it short of creating the group by hand.
#: On this dev database it was simply absent while the other two had 11 and 20
#: members.
ROLE_GROUPS = [
    # (group name, what holding it means)
    ("PR-Manager", "assigns PRs, views the dashboard"),
    ("PR-Admin", "the module's administrator — carries every role"),
    ("PR-Member", "fills in the details of a PR assigned to them"),
]


def seed_groups(apps, schema_editor):
    """Create any of the three role groups that are missing.

    ``get_or_create`` rather than ``create``: PR-Manager and PR-Member already
    exist on every deployment that has ever used the User Master screen, and
    their memberships must survive untouched. Only the absent ones are added.

    No permissions are attached deliberately. PR Tracker authorises on GROUP
    MEMBERSHIP — ``_pr_roles`` tests ``user.groups`` and nothing consults
    Django model permissions — so attaching a permission set here would render
    a picture of the rights in the admin that the application never reads.
    Changing who is in a group changes their access; changing the group's
    permissions does not.
    """
    Group = apps.get_model("auth", "Group")
    for name, _meaning in ROLE_GROUPS:
        Group.objects.get_or_create(name=name)


def unseed_groups(apps, schema_editor):
    """Reverse: deliberately a no-op.

    Dropping a group would silently revoke every membership attached to it,
    including ones made long before this migration ran. An unmigrate is not a
    reason to take people's access away, and an unused group is harmless.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("pr_tracker", "0004_prdocument"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_groups, unseed_groups),
    ]
