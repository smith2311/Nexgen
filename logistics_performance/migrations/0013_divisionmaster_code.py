from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds the `code` column to DivisionMaster.

    The field already existed on the model but no migration had been generated
    for it, so the database column was missing. The table is empty, so adding a
    NOT NULL column with a one-off default is safe.
    """

    dependencies = [
        ("logistics_performance", "0012_statemaster"),
    ]

    operations = [
        migrations.AddField(
            model_name="divisionmaster",
            name="code",
            field=models.IntegerField(default=0, max_length=5),
            preserve_default=False,
        ),
    ]
