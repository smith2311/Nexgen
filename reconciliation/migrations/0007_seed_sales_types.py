"""
Fill the sales-type master with the six codes the real exports actually carry.

Seeded to reproduce the behaviour the classifier already had, so turning a
hardcoded mapping into data does not move a single number on anybody's filing
summary. What changes is that the mapping is now visible and editable.

Idempotent, and it never overwrites an existing row: the master is meant to be
edited from the Sales Type Master screen, so a migration that re-applied the
seed would quietly discard whoever corrected one.
"""
from django.db import migrations

from reconciliation.sales_types import SEED_ROWS


def seed(apps, schema_editor):
    ReconSalesType = apps.get_model("reconciliation", "ReconSalesType")
    existing = set(ReconSalesType.objects.values_list("code", flat=True))

    ReconSalesType.objects.bulk_create([
        ReconSalesType(
            code=code,
            description=description,
            gstr1_section=section,
            is_export=is_export,
            is_b2c=is_b2c,
            notes=notes,
            is_active=True,
        )
        for code, description, section, is_export, is_b2c, notes in SEED_ROWS
        if code not in existing
    ])


def unseed(apps, schema_editor):
    ReconSalesType = apps.get_model("reconciliation", "ReconSalesType")
    ReconSalesType.objects.filter(code__in=[row[0] for row in SEED_ROWS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0006_reconsalestype"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
