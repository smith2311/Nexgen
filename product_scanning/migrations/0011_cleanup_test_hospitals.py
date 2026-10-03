"""
Data migration: clean up test/placeholder hospitals that were manually created
during development and have no SAP code or province.

Removes: City Medical Center, Apollo Hospital, St. Joseph Medical Institute,
         Sunrise Health Center, Royal Nursing Home

Also fixes: Mediclinic Durbanville Hospital province (Gauteng -> Western Cape)
"""
from django.db import migrations


TEST_HOSPITAL_NAMES = [
    'City Medical Center',
    'Apollo Hospital',
    'St. Joseph Medical Institute',
    'Sunrise Health Center',
    'Royal Nursing Home',
]


def remove_test_hospitals(apps, schema_editor):
    Hospital = apps.get_model('product_scanning', 'Hospital')
    # Deleting hospitals will CASCADE to linked Batches, ScanningSessions,
    # ScannedProducts, SessionAttachments, and AuditLogs
    deleted_count, _ = Hospital.objects.filter(
        name__in=TEST_HOSPITAL_NAMES,
        sap_code__isnull=True,
    ).delete()
    print(f"  Removed {deleted_count} test hospital records (including linked data).")


def fix_durbanville_province(apps, schema_editor):
    Hospital = apps.get_model('product_scanning', 'Hospital')
    updated = Hospital.objects.filter(
        sap_code='11200445',
        province='Gauteng',
    ).update(province='Western Cape')
    if updated:
        print("  Fixed Mediclinic Durbanville Hospital province: Gauteng -> Western Cape")


def reverse_migration(apps, schema_editor):
    # Cannot restore deleted data; this migration is irreversible
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0010_alter_scannedproduct_serial_number_and_more'),
    ]

    operations = [
        migrations.RunPython(remove_test_hospitals, reverse_migration),
        migrations.RunPython(fix_durbanville_province, reverse_migration),
    ]
