"""
Create the Grocery-Manager group.

Grocery-Admin already existed as the module-wide role; Grocery-Manager is its
stock-floor counterpart, limited to the operations screens by
grocery_stock.roles.RESTRICTED_SECTIONS.

Seeded here rather than by a management command for the same reason as 0002 —
a fresh environment should come up with the groups in place. get_or_create so
re-running is harmless, and the reverse is a no-op: deleting the group on
rollback would silently strip every member of their access.
"""
from django.db import migrations

from grocery_stock.roles import MANAGER


def create_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=MANAGER)


class Migration(migrations.Migration):

    dependencies = [
        ('grocery_stock', '0005_remove_groceryitem_grocery_item_barcode_idx_and_more'),
        # The group lives in auth, so its tables must already exist.
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_group, migrations.RunPython.noop),
    ]
