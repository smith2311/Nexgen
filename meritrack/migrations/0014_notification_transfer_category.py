from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meritrack", "0013_alter_auditlog_action"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="category",
            field=models.CharField(
                choices=[
                    ("expiry_6m", "6 Month Warning"),
                    ("expiry_3m", "3 Month Warning"),
                    ("expiry_1m", "1 Month Urgent"),
                    ("expiry_1w", "1 Week Critical"),
                    ("expiry_now", "Product Expired"),
                    ("session_completed", "Session Completed"),
                    ("order_update", "Order Status Update"),
                    ("stock_low", "Low Stock Alert"),
                    ("recall", "Product Recall"),
                    ("transfer_closed", "Transfer Closed"),
                ],
                max_length=30,
            ),
        ),
    ]
