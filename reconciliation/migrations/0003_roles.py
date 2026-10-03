"""
Create the two module groups, so a fresh environment has them without anyone
running a command by hand.

The groups carry no Django model permissions: access is decided by
``reconciliation/roles.py``, which every view calls. Adding model permissions
here as well would create a second, silent answer to "may this person import a
file", and the two would eventually disagree.
"""
from django.db import migrations

GROUPS = ("Recon-Admin", "Recon-Member")


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in GROUPS:
        Group.objects.get_or_create(name=name)


def drop_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    # Only removes the groups themselves; a user's other memberships are
    # untouched, and the groups come back on re-apply.
    Group.objects.filter(name__in=GROUPS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0002_seed_billing_types"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_groups, drop_groups),
    ]
