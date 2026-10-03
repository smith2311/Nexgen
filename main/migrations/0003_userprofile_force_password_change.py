from django.db import migrations, models


class Migration(migrations.Migration):
    """Add UserProfile.force_password_change for VendorLink vendor onboarding.

    Written as a forward migration rather than taken from the smith branch,
    which added the field by editing main/0001_initial in place. 0001 is already
    recorded as applied on every existing database, so Django would never re-run
    it and the column would never be created — VendorLink would then fail on any
    profile query. This adds the column on top of the current graph instead.
    """

    dependencies = [
        ("main", "0002_alter_userprofile_timezone"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE main_userprofile "
                        "ADD COLUMN IF NOT EXISTS force_password_change boolean DEFAULT false; "
                        "UPDATE main_userprofile "
                        "SET force_password_change = false "
                        "WHERE force_password_change IS NULL; "
                        "ALTER TABLE main_userprofile "
                        "ALTER COLUMN force_password_change SET DEFAULT false; "
                        "ALTER TABLE main_userprofile "
                        "ALTER COLUMN force_password_change SET NOT NULL"
                    ),
                    reverse_sql=(
                        "ALTER TABLE main_userprofile "
                        "DROP COLUMN IF EXISTS force_password_change"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="userprofile",
                    name="force_password_change",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
