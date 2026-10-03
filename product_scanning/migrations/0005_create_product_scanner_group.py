"""
Data migration: create the 'Product Scanner' group with all product_scanning permissions.
"""
from django.db import migrations


def create_scanner_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    group, _ = Group.objects.get_or_create(name='Product Scanner')

    ct_ids = ContentType.objects.filter(app_label='product_scanning').values_list('id', flat=True)
    perms = Permission.objects.filter(content_type_id__in=ct_ids)
    group.permissions.set(perms)


def remove_scanner_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='Product Scanner').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0004_sessionattachment_binary_storage'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(create_scanner_group, remove_scanner_group),
    ]
