"""Schema migration retained for historical dependency ordering."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("product_scanning", "0012_sync_hospitals_master_list"),
    ]

    # Already represented by the current 0001_initial migration.
    operations = []
