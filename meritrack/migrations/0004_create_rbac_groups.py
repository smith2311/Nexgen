"""Create MeriTrack RBAC groups (MT Rep, MT Manager, MT Logistics)."""
from django.db import migrations


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in ('MT Rep', 'MT Manager', 'MT Logistics'):
        Group.objects.get_or_create(name=name)


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['MT Rep', 'MT Manager', 'MT Logistics']).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('meritrack', '0003_remove_hospital_name_unique'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
