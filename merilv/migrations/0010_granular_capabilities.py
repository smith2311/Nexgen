"""Granular Meril-V capabilities, and the four tiers' starting grants.

The tiers alone could only say "picker" or "manager"; the floor needs to say
"this picker may correct a batch and that one may not". Each capability is a
real Django Permission so it shows up in the admin, is inherited through group
membership, and survives a user being moved between tiers.

The AlterModelOptions is what Django's own ``create_permissions`` reads to
create the Permission rows — but that runs on post_migrate, AFTER every
migration in the run, so the seeding below cannot wait for it and creates the
rows itself. ``get_or_create`` on both halves means the two cannot collide, and
re-running this migration on an installation that already has them is a no-op.

Reverse leaves the grants alone and only drops the model options: unticking a
capability is an administrative decision recorded in the database, and a
rollback of the schema is not a reason to throw away who was allowed what.
"""

from django.db import migrations

# Spelled out rather than imported from merilv.permissions: a migration has to
# keep working when the module it was written against has moved on, and this is
# the list as it stood when the capabilities were introduced.
_SEED = {
    "Meril-V Picker": ["mv_scan", "mv_batch_correction", "mv_undo_scan"],
    "Meril-V Manager": [
        "mv_scan", "mv_batch_correction", "mv_undo_scan",
        "mv_clear_scans", "mv_delete_scan", "mv_courier_edit", "mv_document_edit",
    ],
    "Meril-V Admin": [
        "mv_scan", "mv_batch_correction", "mv_undo_scan",
        "mv_clear_scans", "mv_delete_scan", "mv_courier_edit", "mv_document_edit",
        "mv_master_upload",
    ],
    # Read-only: every capability here is a write.
    "Meril-V Viewer": [],
}

_LABELS = {
    "mv_scan": "Meril-V: Scanning",
    "mv_batch_correction": "Meril-V: Batch Correction",
    "mv_undo_scan": "Meril-V: Undo Last Scan",
    "mv_clear_scans": "Meril-V: Clear Scan Log",
    "mv_delete_scan": "Meril-V: Delete A Scan Row",
    "mv_courier_edit": "Meril-V: Courier Edit",
    "mv_document_edit": "Meril-V: Document Edit",
    "mv_master_upload": "Meril-V: Master Data Upload",
}


def seed_capabilities(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="merilv", model="entity",
    )
    by_code = {}
    for codename, label in _LABELS.items():
        permission, _ = Permission.objects.get_or_create(
            codename=codename,
            content_type=content_type,
            defaults={"name": label},
        )
        by_code[codename] = permission

    for group_name, codenames in _SEED.items():
        # get_or_create, not get: a fresh install runs this before anybody has
        # opened Roles & Access, so the tier groups may not exist yet.
        group, _ = Group.objects.get_or_create(name=group_name)
        wanted = [by_code[code] for code in codenames]
        if wanted:
            group.permissions.add(*wanted)


def unseed_capabilities(apps, schema_editor):
    """Deliberately a no-op — see the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0008_alter_entity_aggregate_delivery_lines"),
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
        migrations.RunPython(seed_capabilities, unseed_capabilities),
    ]
