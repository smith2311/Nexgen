from django.db import migrations, models
from django.db.models import F


def backfill_submission_notified_at(apps, schema_editor):
    """
    Sessions that already left 'active' were notified at submission time, so seed the
    marker from completed_at. Without this every historical session looks un-notified and
    a stray notify_batch_submitted() call could re-blast its managers.
    """
    ScanningSession = apps.get_model("meritrack", "ScanningSession")
    ScanningSession.objects.filter(
        submission_notified_at__isnull=True,
        completed_at__isnull=False,
    ).exclude(status="active").update(submission_notified_at=F("completed_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("meritrack", "0015_remove_scanlog_uniq_scanlog_session_serial_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scanningsession",
            name="submission_notified_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Set once when the 'batch submitted' notification fires; blocks duplicate sends.",
                null=True,
            ),
        ),
        migrations.RunPython(backfill_submission_notified_at, migrations.RunPython.noop),
    ]
