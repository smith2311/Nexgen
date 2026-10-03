"""
Fill the billing-type master with the 53 codes it ships with.

Idempotent on purpose: it creates what is missing and leaves what is already
there alone. The master is *meant* to be edited from the Billing Type Master
screen, so a migration that overwrote every row would quietly discard whoever
corrected one — and the correction would be invisible until the next re-run of
the rules moved several thousand records.
"""
from django.db import migrations

from reconciliation.billing_types import SEED_ROWS


def seed(apps, schema_editor):
    ReconBillingType = apps.get_model("reconciliation", "ReconBillingType")
    existing = set(ReconBillingType.objects.values_list("code", flat=True))

    ReconBillingType.objects.bulk_create([
        ReconBillingType(
            code=code,
            description=description,
            doc_type=doc_type,
            gst_applicable=gst,
            einv_applicable=einv,
            ewb_applicable=ewb,
            final_remark=final_remark,
            health=health,
            other_conditions=conditions,
            is_active=True,
        )
        for (code, description, doc_type, gst, einv, ewb,
             final_remark, health, conditions) in SEED_ROWS
        if code not in existing
    ])


def unseed(apps, schema_editor):
    ReconBillingType = apps.get_model("reconciliation", "ReconBillingType")
    ReconBillingType.objects.filter(code__in=[row[0] for row in SEED_ROWS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
