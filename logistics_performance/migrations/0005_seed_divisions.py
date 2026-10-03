"""
Seed the Division master with the values that were previously hard-coded in
code (DIVISION_CHOICES + the _BUDGET_LC dict + the MILSPL->MICRO rename), so the
application behaves identically after the switch to a dynamic master.

It also auto-creates any extra division codes that already exist in real data
(LogisticsPerformance / DomesticSalesCard) but weren't in the hard-coded list,
so the dropdowns show every division actually in use.
"""
from django.db import migrations


# (code, display_name, budget_lc_percent, badge_class, sort_order)
SEED = [
    ("MEPL",   "",      0.93, "badge-mepl",   1),
    ("MDPL",   "",      3.10, "badge-mdpl",   2),
    ("MHPL",   "",      0.56, "badge-mhpl",   3),
    ("MLSPL",  "",      0.86, "badge-mlspl",  4),
    ("MLSIPL", "",      0.60, "badge-mlsipl", 5),
    ("MILSPL", "MICRO", 0.93, "badge-milspl", 6),
    ("ROBO",   "",      0.56, "badge-muted",  7),
]

DEFAULT_BUDGET = 0.86


def seed_divisions(apps, schema_editor):
    Division = apps.get_model("logistics_performance", "Division")
    LogisticsPerformance = apps.get_model("logistics_performance", "LogisticsPerformance")
    DomesticSalesCard = apps.get_model("logistics_performance", "DomesticSalesCard")

    # 1) Known divisions with their previous budget % and display names
    for code, display, budget, badge, order in SEED:
        Division.objects.update_or_create(
            code=code,
            defaults={
                "display_name": display,
                "budget_lc_percent": budget,
                "badge_class": badge,
                "sort_order": order,
                "is_active": True,
            },
        )

    # 2) Any extra division codes already present in real data
    seen = {c for c, *_ in SEED}
    extra = set()
    extra.update(LogisticsPerformance.objects.exclude(division__isnull=True)
                 .exclude(division="").values_list("division", flat=True).distinct())
    extra.update(DomesticSalesCard.objects.exclude(division__isnull=True)
                 .exclude(division="").values_list("division", flat=True).distinct())

    order = len(SEED)
    for code in sorted(c for c in extra if c and c not in seen):
        order += 1
        Division.objects.update_or_create(
            code=code,
            defaults={
                "display_name": "",
                "budget_lc_percent": DEFAULT_BUDGET,
                "badge_class": "badge-muted",
                "sort_order": order,
                "is_active": True,
            },
        )


def unseed_divisions(apps, schema_editor):
    # Only remove the explicitly seeded codes; leave user-added ones alone.
    Division = apps.get_model("logistics_performance", "Division")
    Division.objects.filter(code__in=[c for c, *_ in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("logistics_performance", "0004_division_alter_domesticsalescard_division"),
    ]

    operations = [
        migrations.RunPython(seed_divisions, unseed_divisions),
    ]
