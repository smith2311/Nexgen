from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def dedupe_scan_logs(apps, schema_editor):
    """Remove newer duplicate (session, serial) scan logs so the unique
    constraint can be added. Blank serials are exempt from the constraint."""
    ScanLog = apps.get_model("meritrack", "ScanLog")
    seen = set()
    dupe_ids = []
    for pk, session_id, serial in (
        ScanLog.objects.exclude(serial_number="")
        .exclude(serial_number__isnull=True)
        .order_by("scanned_at", "pk")
        .values_list("pk", "session_id", "serial_number")
    ):
        key = (session_id, serial)
        if key in seen:
            dupe_ids.append(pk)
        else:
            seen.add(key)
    if dupe_ids:
        ScanLog.objects.filter(pk__in=dupe_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("meritrack", "0010_serial_number_optional"),
    ]

    operations = [
        # ── ExpiredProductReport: acknowledgment fields (fixes crashing endpoint) ──
        migrations.AddField(
            model_name="expiredproductreport",
            name="days_expired",
            field=models.IntegerField(default=0, help_text="Days past expiry at acknowledgment time"),
        ),
        migrations.AddField(
            model_name="expiredproductreport",
            name="notes",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expiredproductreport",
            name="acknowledged_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mt_acknowledged_expired",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="expiredproductreport",
            name="acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # ── ScanningSession: hold/overdue-reminder tracking ──
        migrations.AddField(
            model_name="scanningsession",
            name="on_hold_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scanningsession",
            name="notify_overdue_from",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Overdue reminders count from this time instead of completed_at (set on resume)",
            ),
        ),
        # ── AwaitingOverdueLog: daily reminder dedup ──
        migrations.CreateModel(
            name="AwaitingOverdueLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notified_on", models.DateField()),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="overdue_logs",
                        to="meritrack.scanningsession",
                    ),
                ),
            ],
            options={
                "verbose_name": "Awaiting Overdue Log",
                "verbose_name_plural": "Awaiting Overdue Logs",
                "unique_together": {("session", "notified_on")},
            },
        ),
        # ── SAPIntegrationLog: referenced by sap_integration_service but missing ──
        migrations.CreateModel(
            name="SAPIntegrationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "operation",
                    models.CharField(
                        choices=[
                            ("import_stock", "Import Stock Balances"),
                            ("import_postings", "Import Consignment Postings"),
                            ("import_serial", "Import Serial Numbers"),
                            ("export_usage", "Export Usage to SAP"),
                            ("sync_master", "Sync Master Data"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("started", "Started"),
                            ("completed", "Completed"),
                            ("partial", "Partially Completed"),
                            ("failed", "Failed"),
                        ],
                        default="started",
                        max_length=20,
                    ),
                ),
                ("plant", models.CharField(blank=True, default="", max_length=20)),
                ("source_file", models.CharField(blank=True, default="", max_length=255)),
                ("sap_system", models.CharField(blank=True, default="", max_length=100)),
                ("records_processed", models.PositiveIntegerField(default=0)),
                ("records_created", models.PositiveIntegerField(default=0)),
                ("records_updated", models.PositiveIntegerField(default=0)),
                ("records_failed", models.PositiveIntegerField(default=0)),
                ("error_details", models.TextField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "initiated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mt_sap_integrations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "SAP Integration Log",
                "verbose_name_plural": "SAP Integration Logs",
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddIndex(
            model_name="sapintegrationlog",
            index=models.Index(fields=["operation", "-started_at"], name="meritrack_s_operati_b8e2f1_idx"),
        ),
        # ── MonthlyReminderLog: detail fields for pending-visit reminders ──
        migrations.AddField(
            model_name="monthlyreminderlog",
            name="salesperson_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="monthlyreminderlog",
            name="pending_hospitals_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="monthlyreminderlog",
            name="pending_hospital_names",
            field=models.TextField(blank=True, default="", help_text="JSON list of hospital names"),
        ),
        # ── ScanLog: dedupe then enforce uniqueness per session+serial ──
        migrations.RunPython(dedupe_scan_logs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="scanlog",
            constraint=models.UniqueConstraint(
                condition=models.Q(("serial_number", ""), _negated=True),
                fields=("session", "serial_number"),
                name="uniq_scanlog_session_serial",
            ),
        ),
    ]
