"""
Create the RBAC groups for the Export Billing Dashboard and seed the enrichment
masters (country→region/subsidiary and product→LOB) with the examples from the
requirement meeting. Safe to re-run (idempotent).

    python manage.py setup_billing_roles
"""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from billing_dashboard.models import CountryRegionMap, LobMap

GROUPS = [
    "Billing Dashboard",   # super role — full access
    "Billing-Admin",       # can upload + manage masters
    "Billing-Members",     # can view dashboards + export
]

# Country → (code, region, is_subsidiary, subsidiary_name)
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


class Command(BaseCommand):
    help = "Create Billing Dashboard RBAC groups and seed enrichment masters."

    def handle(self, *args, **options):
        for name in GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            self.stdout.write(f"  {'Created' if created else 'Exists '}: {name}")

        c_new = 0
        for name, code, region, is_sub, sub_name in COUNTRIES:
            _, created = CountryRegionMap.all_objects.get_or_create(
                country_name=name,
                defaults=dict(country_code=code, region=region,
                              is_subsidiary=is_sub, subsidiary_name=sub_name),
            )
            c_new += int(created)
        self.stdout.write(f"  Country/Region master: {c_new} seeded, {len(COUNTRIES)} total defaults.")

        l_new = 0
        for key_type, key_value, lob in LOBS:
            _, created = LobMap.all_objects.get_or_create(
                key_type=key_type, key_value=key_value, defaults=dict(lob=lob),
            )
            l_new += int(created)
        self.stdout.write(f"  LOB master: {l_new} seeded, {len(LOBS)} total defaults.")

        self.stdout.write(self.style.SUCCESS("Billing Dashboard roles & masters ready."))
