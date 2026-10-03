"""Serial Correction becomes a capability, and everyone who had it keeps it.

``outbound_edit_scan`` — correcting a mistyped serial on a scan already in the
log — was the one mutating endpoint in the module with no capability check at
all. Its sibling, batch correction, has been gated since the capabilities were
introduced; this one was simply missed, so any account that could operate the
console could rewrite a recorded serial.

Gating it is only half the change. The endpoint was open, which means every
picker has been using it, and a capability nobody holds is a capability that
stops the floor: a picker who mistypes a serial at 2am would have no way to
correct it and no way to be granted one until an admin logs in. So the grant
below gives it to the three tiers that already hold batch correction, which
keeps behaviour identical for everyone and changes only what an admin is now
*able* to revoke.

The seeding repeats 0010's shape for the same reason it gave: Django's own
``create_permissions`` runs on post_migrate, after every migration in the run,
so this cannot wait for it and creates the row itself. ``get_or_create`` on
both halves makes a re-run a no-op.

Reverse drops the model option and leaves the grants alone — unticking a
capability is an administrative decision recorded in the database, and rolling
back a schema is not a reason to discard who was allowed what.
"""

from django.db import migrations

_CODENAME = "mv_serial_edit"
_LABEL = "Meril-V: Serial Correction"

#: The tiers that already hold batch correction, which is this capability's
#: sibling and was seeded to exactly these three in 0010.
_GRANT_TO = ("Meril-V Picker", "Meril-V Manager", "Meril-V Admin")


def grant_serial_correction(apps, schema_editor):
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


def ungrant_serial_correction(apps, schema_editor):
    """Deliberately a no-op — see the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0011_focus_belongs_to_the_operator"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="entity",
            options={
                "ordering": ["sort_order", "code"],
                "permissions": [
                    ("mv_scan", "Meril-V: Scanning"),
                    ("mv_batch_correction", "Meril-V: Batch Correction"),
                    ("mv_undo_scan", "Meril-V: Undo Last Scan"),
                    ("mv_serial_edit", "Meril-V: Serial Correction"),
                    ("mv_clear_scans", "Meril-V: Clear Scan Log"),
                    ("mv_delete_scan", "Meril-V: Delete A Scan Row"),
                    ("mv_courier_edit", "Meril-V: Courier Edit"),
                    ("mv_document_edit", "Meril-V: Document Edit"),
                    ("mv_master_upload", "Meril-V: Master Data Upload"),
                ],
                "verbose_name": "Entity",
                "verbose_name_plural": "Entities",
            },
        ),
        migrations.RunPython(grant_serial_correction, ungrant_serial_correction),
    ]
