"""
Rename the ``Recon-Member`` group to ``Recon-User``.

The module has always had two roles; this only changes what the second one is
called, to the word people actually use for it.

Renaming the row rather than creating a new group and deleting the old one is
the point: group membership hangs off the group's primary key, so a
delete-and-recreate would silently revoke everyone who had the role. This keeps
the key and moves the name.

Idempotent in both directions, and safe on a database where someone has already
made a ``Recon-User`` group by hand — in that case the two are merged rather
than colliding on the unique name.
"""
from django.db import migrations

OLD = "Recon-Member"
NEW = "Recon-User"


def _merge_or_rename(Group, source_name, target_name):
    """Point ``source_name``'s members at ``target_name``, then rename or drop."""
    source = Group.objects.filter(name=source_name).first()
    if source is None:
        Group.objects.get_or_create(name=target_name)
        return

    target = Group.objects.filter(name=target_name).first()
    if target is None:
        source.name = target_name
        source.save(update_fields=["name"])
        return

    # Both exist: move every member across, then remove the empty one. Nobody
    # loses access either way.
    for user in source.user_set.all():
        user.groups.add(target)
    source.delete()


def rename_forwards(apps, schema_editor):
    _merge_or_rename(apps.get_model("auth", "Group"), OLD, NEW)


def rename_backwards(apps, schema_editor):
    _merge_or_rename(apps.get_model("auth", "Group"), NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0003_roles"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(rename_forwards, rename_backwards),
    ]
