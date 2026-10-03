from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meritrack", "0009_add_hospital_salespersons_m2m"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consignedproduct",
            name="serial_number",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="scanlog",
            name="serial_number",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="transferredproduct",
            name="serial_number",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="expiredproductreport",
            name="serial_number",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
