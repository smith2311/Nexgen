"""
Data migration: create RBAC groups for product scanning.

  PS Salesperson  → scanner-only permissions (start/complete sessions, scan products)
  PS Warehouse    → dashboard-only permissions (view sessions/products, upload attachments)
  Product Scanner → all permissions (both scanner + dashboard)  [already exists from 0005]
"""
from django.db import migrations


# Permissions granted to Salesperson (scanner operations only)
SCANNER_PERMS = [
    ('product_scanning', 'scanningsession', 'add_scanningsession'),
    ('product_scanning', 'scanningsession', 'change_scanningsession'),
    ('product_scanning', 'scanningsession', 'view_scanningsession'),
    ('product_scanning', 'scannedproduct',  'add_scannedproduct'),
    ('product_scanning', 'scannedproduct',  'change_scannedproduct'),
    ('product_scanning', 'scannedproduct',  'view_scannedproduct'),
    ('product_scanning', 'batch',           'add_batch'),
    ('product_scanning', 'batch',           'change_batch'),
    ('product_scanning', 'batch',           'view_batch'),
    ('product_scanning', 'hospital',        'view_hospital'),
    ('product_scanning', 'qrtemplate',      'view_qrtemplate'),
]

# Permissions granted to Warehouse (read/manage sessions + upload attachments)
WAREHOUSE_PERMS = [
    ('product_scanning', 'scanningsession',   'view_scanningsession'),
    ('product_scanning', 'scanningsession',   'change_scanningsession'),
    ('product_scanning', 'scannedproduct',    'view_scannedproduct'),
    ('product_scanning', 'batch',             'view_batch'),
    ('product_scanning', 'hospital',          'view_hospital'),
    ('product_scanning', 'sessionattachment', 'add_sessionattachment'),
    ('product_scanning', 'sessionattachment', 'change_sessionattachment'),
    ('product_scanning', 'sessionattachment', 'view_sessionattachment'),
    ('product_scanning', 'sessionattachment', 'delete_sessionattachment'),
]


def _get_perm(Permission, ContentType, app_label, model_name, codename):
    try:
        ct = ContentType.objects.get(app_label=app_label, model=model_name)
        return Permission.objects.get(content_type=ct, codename=codename)
    except Exception:
        return None


def create_rbac_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    # --- PS Salesperson ---
    salesperson_group, _ = Group.objects.get_or_create(name='PS Salesperson')
    scanner_perms = [
        p for app, model, codename in SCANNER_PERMS
        for p in [_get_perm(Permission, ContentType, app, model, codename)]
        if p
    ]
    salesperson_group.permissions.set(scanner_perms)

    # --- PS Warehouse ---
    warehouse_group, _ = Group.objects.get_or_create(name='PS Warehouse')
    warehouse_perms = [
        p for app, model, codename in WAREHOUSE_PERMS
        for p in [_get_perm(Permission, ContentType, app, model, codename)]
        if p
    ]
    warehouse_group.permissions.set(warehouse_perms)


def remove_rbac_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['PS Salesperson', 'PS Warehouse']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0006_add_product_date_fields'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(create_rbac_groups, remove_rbac_groups),
    ]
