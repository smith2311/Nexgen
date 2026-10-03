"""Batch and document correction rights split per console.

23 Sep MoM: "inbound/outbound batch correction/document correction rights need
to be handled individually". One tick-box covered several consoles, so granting
a picker the right to correct a dispatch batch also let them repoint a
branch-transfer line, and the inbound side had no check at all.

    mv_batch_correction  ->  mv_batch_correction_outbound   (Outbound Scanner)
                             mv_batch_correction_transfer   (Branch Transfer)
    mv_document_edit     ->  mv_document_edit_outbound      (Finish modal)
                             mv_document_edit_inbound       (receipt condition)

Nobody loses anything on the way through. A group holding the old combined
right gets BOTH halves, because splitting a permission is a modelling decision
and must not read to the floor as a withdrawal — a picker who could correct a
batch this morning can still correct one this afternoon. Narrowing it is a
tick-box on Master Data -> Roles & Access afterwards, which is the whole point
of splitting it.

mv_document_edit_inbound is the exception worth naming: it gates
``inbound.scan_condition``, which had NO capability check before this, so it is
seeded from its tier rather than carried over from anything.

The old Permission rows are deleted once their grants are copied. Leaving them
would show two dead tick-boxes on the roles screen that no view consults.

Reverse re-creates the old rows and grants them to anyone holding either half,
so a rollback leaves the floor able to work; it does not remove the new ones.
"""

from django.db import migrations


#: old codename -> (new codenames it becomes, label for each)
_SPLIT = {
    "mv_batch_correction": [
        ("mv_batch_correction_outbound", "Meril-V: Batch Correction (Outbound)"),
        ("mv_batch_correction_transfer", "Meril-V: Batch Correction (Branch Transfer)"),
    ],
    "mv_document_edit": [
        ("mv_document_edit_outbound", "Meril-V: Document Correction (Outbound)"),
    ],
}

#: Had no predecessor — seeded to the tiers that hold the outbound half.
_NEW = [("mv_document_edit_inbound", "Meril-V: Document Correction (Inbound)")]

_SEED_TO = ("Meril-V Picker", "Meril-V Manager", "Meril-V Admin")


def _permission(apps, codename, label):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type, _ = ContentType.objects.get_or_create(
        app_label="merilv", model="entity",
    )
    permission, _ = Permission.objects.get_or_create(
        codename=codename, content_type=content_type, defaults={"name": label},
    )
    return permission


def split_rights(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for old_code, replacements in _SPLIT.items():
        new_permissions = [_permission(apps, code, label) for code, label in replacements]
        old = Permission.objects.filter(codename=old_code).first()
        if old is not None:
            # Every group that held the combined right gets each half.
            for group in Group.objects.filter(permissions=old):
                group.permissions.add(*new_permissions)
            old.delete()
        else:
            # Never seeded here (a fresh database runs 0010 first, so this is
            # unusual) — fall back to the tiers the capability list names.
            for name in _SEED_TO:
                group, _ = Group.objects.get_or_create(name=name)
                group.permissions.add(*new_permissions)

    for code, label in _NEW:
        permission = _permission(apps, code, label)
        for name in _SEED_TO:
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.add(permission)


def unsplit_rights(apps, schema_editor):
    """Put the combined rights back for anyone holding either half."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for old_code, replacements in _SPLIT.items():
        label = "Meril-V: " + old_code.replace("mv_", "").replace("_", " ").title()
        old = _permission(apps, old_code, label)
        halves = Permission.objects.filter(
            codename__in=[code for code, _ in replacements]
        )
        for group in Group.objects.filter(permissions__in=halves).distinct():
            group.permissions.add(old)


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0014_grant_document_edit_to_pickers"),
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
                    (
                        "mv_batch_correction_outbound",
                        "Meril-V: Batch Correction (Outbound)",
                    ),
                    (
                        "mv_batch_correction_transfer",
                        "Meril-V: Batch Correction (Branch Transfer)",
                    ),
                    ("mv_undo_scan", "Meril-V: Undo Last Scan"),
                    ("mv_serial_edit", "Meril-V: Serial Correction"),
                    (
                        "mv_document_edit_inbound",
                        "Meril-V: Document Correction (Inbound)",
                    ),
                    ("mv_clear_scans", "Meril-V: Clear Scan Log"),
                    ("mv_delete_scan", "Meril-V: Delete A Scan Row"),
                    ("mv_courier_edit", "Meril-V: Courier Edit"),
                    (
                        "mv_document_edit_outbound",
                        "Meril-V: Document Correction (Outbound)",
                    ),
                    ("mv_master_upload", "Meril-V: Master Data Upload"),
                ],
                "verbose_name": "Entity",
                "verbose_name_plural": "Entities",
            },
        ),
        migrations.RunPython(split_rights, unsplit_rights),
    ]
