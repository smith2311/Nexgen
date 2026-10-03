"""
Drop the six specialised grocery roles, leaving Grocery-Admin and
Grocery-Manager.

Purchase Manager, Store Manager, Warehouse Executive, Finance, Department User
and Auditor came from specification section 18. Everything they could write is
now Grocery-Admin's (master data) or Grocery-Manager's (goods receipts, issues,
adjustments), so the groups have no permissions left to carry.

The names are spelled out here rather than imported from grocery_stock.roles:
the constants are gone from that module as of this change, and a migration has
to keep describing the state of the database at its own point in history.

Deleting a Group cascades its user memberships, so anyone who held only one of
these roles loses grocery access and needs Grocery-Admin or Grocery-Manager
assigned. The reverse re-creates the groups empty — the memberships cannot be
recovered, but a rollback at least leaves nothing dangling behind.
"""
from django.db import migrations

REMOVED_GROUPS = [
    'Grocery-Purchase-Manager',
    'Grocery-Store-Manager',
    'Grocery-Warehouse-Executive',
    'Grocery-Finance',
    'Grocery-Department-User',
    'Grocery-Auditor',
]


def delete_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=REMOVED_GROUPS).delete()


def recreate_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in REMOVED_GROUPS:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('grocery_stock', '0006_grocery_manager_group'),
        # The groups live in auth, so its tables must already exist.
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(delete_groups, recreate_groups),
    ]
