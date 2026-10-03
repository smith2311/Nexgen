"""
Data migration: sync hospital list to the authoritative master list.

Changes:
  ADD  6 new hospitals missing from previous migrations
  FIX  Mediclinic Durbanville Hospital province (Western Cape -> Gauteng)
  DEL  2 hospitals not in master list with no linked data
       - 1 Military hospital (des)     [11200002]
       - Netcare union                 [11200118]
"""
from django.db import migrations


NEW_HOSPITALS = [
    # (name, sap_code, province)
    ('Lenmed Wilmed Park Hospital and Heart Centre', '11200329', 'North West'),
    ('Mediclinic Vereeniging Hospital',              '11200389', 'Gauteng'),
    ('Mediclinic Nelspruit Hospital & Day Clinic',   '11200156', 'Gauteng'),
    ('Netcare Pholōšo Hospital',                    '11200100', 'Limpopo'),
    ('Raslouw Private Hospital',                     '11200364', 'Gauteng'),
    ('Inkosi Albert Luthuli Central Hospital',       '11200056', 'KwaZulu-Natal'),
]

REMOVE_SAP_CODES = ['11200002', '11200118']

PROVINCE_FIXES = [
    # (sap_code, correct_province)
    ('11200445', 'Gauteng'),   # Mediclinic Durbanville Hospital
]


def apply_changes(apps, schema_editor):
    Hospital = apps.get_model('product_scanning', 'Hospital')

    # Add new hospitals
    for name, sap_code, province in NEW_HOSPITALS:
        Hospital.objects.get_or_create(
            sap_code=sap_code,
            defaults={'name': name, 'province': province},
        )
        print(f"  Added: {name} ({sap_code})")

    # Fix province mismatches
    for sap_code, correct_province in PROVINCE_FIXES:
        updated = Hospital.objects.filter(sap_code=sap_code).update(province=correct_province)
        if updated:
            h = Hospital.objects.get(sap_code=sap_code)
            print(f"  Fixed province: {h.name} ({sap_code}) -> {correct_province}")

    # Remove hospitals not in master list (no linked data)
    deleted, detail = Hospital.objects.filter(sap_code__in=REMOVE_SAP_CODES).delete()
    if deleted:
        print(f"  Removed {deleted} hospital(s) not in master list: {REMOVE_SAP_CODES}")


def reverse_migration(apps, schema_editor):
    # Partial reverse: remove the newly added hospitals; cannot restore deleted ones
    Hospital = apps.get_model('product_scanning', 'Hospital')
    Hospital.objects.filter(sap_code__in=[code for _, code, _ in NEW_HOSPITALS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0011_cleanup_test_hospitals'),
    ]

    operations = [
        migrations.RunPython(apply_changes, reverse_migration),
    ]
