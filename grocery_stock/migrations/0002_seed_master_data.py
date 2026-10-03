"""
Seed the master data the specification enumerates by name.

Follows the pattern of visa_management/0004_seed_visa_data: seeding runs on
deploy rather than as a management command someone has to remember, every
write is get_or_create so re-running is harmless, and the reverse is a no-op
because rolling back must not delete rows that live documents point at
(category, unit and department are all on_delete=PROTECT from GroceryItem).

Seeded here:
    * the module's roles — ALL_GROUPS, which is Grocery-Admin and
      Grocery-Manager since 0007 dropped the six specialised roles
    * the eleven categories from section 3
    * the five consuming departments from section 9
    * a starter set of units — not enumerated in the specification, but an
      item cannot be created without one, so the masters would otherwise ship
      unusable
"""
from django.db import migrations

from grocery_stock.roles import ALL_GROUPS

# Section 3
CATEGORIES = [
    ('CEREALS', 'Cereals'),
    ('PULSES', 'Pulses'),
    ('FLOUR', 'Flour'),
    ('SPICES', 'Spices'),
    ('DAIRY', 'Dairy'),
    ('FROZEN', 'Frozen'),
    ('BAKERY', 'Bakery'),
    ('SNACKS', 'Snacks'),
    ('HOUSEHOLD', 'Household'),
    ('CLEANING', 'Cleaning'),
    ('PERSONAL', 'Personal Care'),
]

# Section 9
DEPARTMENTS = [
    ('KITCHEN', 'Kitchen'),
    ('HOUSEKEEPING', 'Housekeeping'),
    ('OFFICE', 'Office'),
    ('PRODUCTION', 'Production'),
    ('LAB', 'Laboratory'),
]

# Base units only — no conversion factors, so decision D8 stays open and can
# be answered later by adding derived units as data.
UNITS = [
    ('KG', 'Kilogram'),
    ('G', 'Gram'),
    ('L', 'Litre'),
    ('ML', 'Millilitre'),
    ('PCS', 'Pieces'),
    ('PKT', 'Packet'),
    ('BOX', 'Box'),
    ('BAG', 'Bag'),
    ('DOZ', 'Dozen'),
]


def seed(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    GroceryCategory = apps.get_model('grocery_stock', 'GroceryCategory')
    GroceryDepartment = apps.get_model('grocery_stock', 'GroceryDepartment')
    GroceryUnit = apps.get_model('grocery_stock', 'GroceryUnit')

    for name in ALL_GROUPS:
        Group.objects.get_or_create(name=name)

    for code, name in CATEGORIES:
        GroceryCategory.objects.get_or_create(code=code, defaults={'name': name})

    for code, name in DEPARTMENTS:
        GroceryDepartment.objects.get_or_create(code=code, defaults={'name': name})

    for code, name in UNITS:
        GroceryUnit.objects.get_or_create(code=code, defaults={'name': name})


class Migration(migrations.Migration):

    dependencies = [
        ('grocery_stock', '0001_initial'),
        # Groups are created here, so auth's tables must already exist.
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
