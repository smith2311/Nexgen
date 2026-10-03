"""Document Edit widens from manager to picker.

The 23 Sep client meeting: the person who packed the box is the one who notices
the docket was mistyped, and sending them to find a manager to correct a typo
is how the wrong docket ships. It reaches the paperwork only — the serials are
not editable from the Finish modal, and the docket-conflict rule still applies
to a changed docket, so a correction cannot land one docket on two customers.

Changing the tier in ``permissions.CAPABILITIES`` is not enough on its own.
That tier is two things: what :func:`permissions.can` falls back to, and what
``seed_map()`` grants — but the grant only happens when a seeding migration
runs. An installation that already ran 0010 has the Picker group without this
permission, and :func:`can` treats a managed tier's grants as the whole answer,
so a picker would keep being refused until somebody ticked the box by hand.

Viewers are deliberately not included: read-only stays read-only.

Reverse is a no-op, matching 0010 and 0012 — who holds what is an
administrative decision recorded in the database, and rolling a schema back is
not a reason to discard it.
"""

from django.db import migrations

_CODENAME = "mv_document_edit"
_LABEL = "Meril-V: Document Edit"

#: Manager and Admin already hold it from 0010; Picker is the change. Listed in
#: full rather than as a delta so a re-run repairs a partially-granted install.
_GRANT_TO = ("Meril-V Picker", "Meril-V Manager", "Meril-V Admin")


def grant_document_edit(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="merilv", model="entity",
    )
    permission, _ = Permission.objects.get_or_create(
        codename=_CODENAME,
        content_type=content_type,
        defaults={"name": _LABEL},
    )
    for group_name in _GRANT_TO:
        # get_or_create, not get: an installation may not have opened Roles &
        # Access yet, so the tier groups are not guaranteed to exist.
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.add(permission)


def ungrant_document_edit(apps, schema_editor):
    """Deliberately a no-op — see the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0013_user_entity_access"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(grant_document_edit, ungrant_document_edit),
    ]
