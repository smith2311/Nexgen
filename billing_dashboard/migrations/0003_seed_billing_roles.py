"""
Seed the Export Billing Dashboard RBAC groups and enrichment masters as part of
the normal ``migrate`` flow, so the roles exist on every deploy without anyone
having to run ``setup_billing_roles`` by hand.

Mirrors billing_dashboard/management/commands/setup_billing_roles.py (which is
retained for manual re-seeding). Idempotent — safe to re-run.
"""
from django.db import migrations

GROUPS = [
    "Billing Dashboard",   # super role — full access
    "Billing-Admin",       # can upload + manage masters
    "Billing-Members",     # can view dashboards + export
]

# Country → (name, code, region, is_subsidiary, subsidiary_name)
COUNTRIES = [
    # Subsidiaries called out in the meeting
    ("Bangladesh", "BD", "South East Asia", True, "Meril Bangladesh"),
    ("Brazil", "BR", "Latin America", True, "Meril Brazil"),
    ("China", "CN", "Asia", True, "Meril China"),
    ("Germany", "DE", "West Europe", True, "Meril Germany"),
    ("Hungary", "HU", "East Europe", True, "Meril Hungary"),
    ("Italy", "IT", "West Europe", True, "Meril Italy"),
    ("Poland", "PL", "East Europe", True, "Meril Poland"),
    ("Russia", "RU", "CIS", True, "Meril Russia"),
    # Non-subsidiary regional buckets called out in the meeting
    ("India", "IN", "India (Domestic)", False, ""),
    ("Turkey", "TR", "Middle East", False, ""),
    ("USA", "US", "North America", False, ""),
    ("South Africa", "ZA", "Africa", False, ""),
    ("Nigeria", "NG", "Africa", False, ""),
    ("Kenya", "KE", "Africa", False, ""),
    ("Singapore", "SG", "South East Asia", False, ""),
    ("Malaysia", "MY", "South East Asia", False, ""),
    ("Thailand", "TH", "South East Asia", False, ""),
    ("Mexico", "MX", "Latin America", False, ""),
    ("Argentina", "AR", "Latin America", False, ""),
    ("United Kingdom", "GB", "West Europe", False, ""),
    ("France", "FR", "West Europe", False, ""),
    ("Spain", "ES", "West Europe", False, ""),
    ("UAE", "AE", "Middle East", False, ""),
    ("Saudi Arabia", "SA", "Middle East", False, ""),
]

# LOB seeds (best-effort; extend from the LOB master as real rules are confirmed)
LOBS = [
    ("MATERIAL_PREFIX", "MC", "Mechanical"),
    ("MATERIAL_PREFIX", "MECR", "Mechanical"),
    ("MATERIAL_PREFIX", "ECR", "Mechanical"),
    ("MATERIAL_PREFIX", "BS", "Biosurgical"),
    ("MATERIAL_PREFIX", "IUD", "Mechanical IUD"),
]


def seed_billing_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in GROUPS:
        Group.objects.get_or_create(name=name)

    # apps.get_model returns a historical model whose default manager is a plain
    # Manager (the ActiveManager soft-delete filter is not applied in migrations),
    # so .objects here sees all rows — equivalent to the command's all_objects.
    CountryRegionMap = apps.get_model("billing_dashboard", "CountryRegionMap")
    for name, code, region, is_sub, sub_name in COUNTRIES:
        CountryRegionMap.objects.get_or_create(
            country_name=name,
            defaults=dict(
                country_code=code, region=region,
                is_subsidiary=is_sub, subsidiary_name=sub_name,
            ),
        )

    LobMap = apps.get_model("billing_dashboard", "LobMap")
    for key_type, key_value, lob in LOBS:
        LobMap.objects.get_or_create(
            key_type=key_type, key_value=key_value, defaults=dict(lob=lob),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing_dashboard", "0002_billingupload_column_map_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_billing_roles, migrations.RunPython.noop),
    ]
