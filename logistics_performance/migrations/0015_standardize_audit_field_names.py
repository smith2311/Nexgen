from django.db import migrations


class Migration(migrations.Migration):
    """Standardize audit field names so DomesticSalesCard and DivisionMaster
    match the shared AuditSoftDeleteModel. RenameField preserves existing data
    (unlike a drop + add)."""

    dependencies = [
        ("logistics_performance", "0014_remove_logisticsperformance_sales_type"),
    ]

    operations = [
        # DomesticSalesCard: created_date -> created_at
        migrations.RenameField(
            model_name="domesticsalescard",
            old_name="created_date",
            new_name="created_at",
        ),
        # DivisionMaster: created_date -> created_at, modified_date -> updated_at,
        #                 deleted_date -> deleted_at
        migrations.RenameField(
            model_name="divisionmaster",
            old_name="created_date",
            new_name="created_at",
        ),
        migrations.RenameField(
            model_name="divisionmaster",
            old_name="modified_date",
            new_name="updated_at",
        ),
        migrations.RenameField(
            model_name="divisionmaster",
            old_name="deleted_date",
            new_name="deleted_at",
        ),
    ]
