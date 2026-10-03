"""Upload-date filter + master-driven dispatch-pending wording.

Two independent fields in one migration because they ship together as one
business ask and run against the same tables, so splitting them would mean a
second deploy with no payoff.

* ``ReconRecord.UploadedAt`` records when the staged row landed on the record,
  so the records grid can be filtered by *the date the file was uploaded*
  rather than the GST date on the invoice. The business team uploads one file
  per month and the invoice dates spill across months — July invoices arrive
  in August — so filtering by invoice date mixes batches. Nullable so historic
  rows stay valid; the importer backfills it as each row is updated, and a
  data migration below seeds every existing row to the latest import job's
  timestamp as a reasonable starting point (nothing better is recorded).

* ``ReconBillingType.pending_remark`` lets an admin override the "Dispatched
  Pending" wording per billing-type code — the Gujarat section's "Dispatch
  Pending" tag was hard-coded, and the business team wanted the status driven
  by master data instead. Blank means "use the shipped wording".
"""
from django.db import migrations, models


def _seed_uploaded_at(apps, schema_editor):
    """Backfill UploadedAt on every existing row.

    Nothing was stored at import time, so the best we can do is use the most
    recent import job's finished_at as a floor. For rows older than any job we
    fall back to UpdatedAt, which is at least a date that was once right.
    """
    ReconRecord = apps.get_model("reconciliation", "ReconRecord")
    ReconImportJob = apps.get_model("reconciliation", "ReconImportJob")

    latest_import = (
        ReconImportJob.objects.filter(kind="import", finished_at__isnull=False)
        .order_by("-finished_at").values_list("finished_at", flat=True).first()
    )

    to_update = []
    for record in ReconRecord.objects.filter(UploadedAt__isnull=True).only(
            "id", "UpdatedAt"):
        record.UploadedAt = latest_import or record.UpdatedAt
        to_update.append(record)
        if len(to_update) >= 2000:
            ReconRecord.objects.bulk_update(to_update, ["UploadedAt"])
            to_update.clear()
    if to_update:
        ReconRecord.objects.bulk_update(to_update, ["UploadedAt"])


def _unseed_uploaded_at(apps, schema_editor):
    """Reverse is a no-op: the field is dropped by the schema-rollback step
    above this one, and un-populating a dropped column is nothing."""


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0007_seed_sales_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="reconrecord",
            name="UploadedAt",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddIndex(
            model_name="reconrecord",
            index=models.Index(fields=["UploadedAt"], name="recon_uploaded_idx"),
        ),
        migrations.AddField(
            model_name="reconbillingtype",
            name="pending_remark",
            field=models.CharField(
                max_length=200, blank=True,
                help_text=(
                    "Overrides the 'Dispatched Pending' wording for rows of "
                    "this billing type. Leave blank to use the shipped wording."
                ),
            ),
        ),
        migrations.RunPython(_seed_uploaded_at, _unseed_uploaded_at),
    ]
