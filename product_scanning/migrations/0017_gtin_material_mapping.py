from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("product_scanning", "0016_add_updated_by_to_hospital"),
    ]

    # Already represented by the current 0001_initial migration.
    operations = []
