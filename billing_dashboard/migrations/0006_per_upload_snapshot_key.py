# Per-upload snapshots: scope the SAP business key to the upload so every
# upload keeps its own independent rows (and therefore its own dashboard),
# instead of later uploads upsert-overwriting earlier ones.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing_dashboard", "0005_billingupload_processed_rows_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="exportbillingdetails",
            name="upload",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rows",
                to="billing_dashboard.billingupload",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="exportbillingdetails",
            name="uniq_billing_doc_item_material",
        ),
        migrations.AddConstraint(
            model_name="exportbillingdetails",
            constraint=models.UniqueConstraint(
                fields=("upload", "billing_doc", "item", "material"),
                name="uniq_upload_billing_doc_item_material",
            ),
        ),
    ]
