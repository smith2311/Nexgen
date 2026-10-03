"""Widen CustomerMaster.plant to hold comma-separated multi-plant values.

A single customer can ship from more than one plant. The SAP extract carries
one row per customer-per-plant, and the old max_length=20 field could hold
only one plant code. Widening to 200 lets the import merge all of a
customer's plants into a comma-separated list instead of discarding them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0018_courier_name_not_unique"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customermaster",
            name="plant",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
