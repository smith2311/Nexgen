from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meritrack", "0002_remove_gtinmaterialmapping_updated_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scanlog",
            name="quantity",
            field=models.PositiveIntegerField(blank=True, default=1, null=True),
        ),
    ]
