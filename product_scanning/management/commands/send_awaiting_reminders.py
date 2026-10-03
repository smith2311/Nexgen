"""
Management command: send_awaiting_reminders
===========================================
Sends daily email reminders to warehouse managers and PS admins for every
scanning session that has been in "awaiting confirmation" (completed) status
for more than 6 days and is not on hold.

The first reminder fires on day 7 (i.e., >= 6 full days since completed_at).
Subsequent reminders fire daily until the session is closed or put on hold.

Usage:
    python manage.py send_awaiting_reminders          # normal daily run
    python manage.py send_awaiting_reminders --dry-run # list overdue sessions, send nothing
    python manage.py send_awaiting_reminders --force  # resend even if already notified today

Schedule via cron (run daily at 09:00):
    0 9 * * * /path/to/venv/bin/python /path/to/manage.py send_awaiting_reminders
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from product_scanning.models import AwaitingOverdueLog, ScanningSession
from product_scanning.notification_service import notify_awaiting_overdue

OVERDUE_THRESHOLD_DAYS = 6


class Command(BaseCommand):
    help = "Send daily overdue-awaiting reminders for sessions stuck in awaiting confirmation for more than 6 days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="List overdue sessions without sending any emails.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Send even if a reminder was already sent today.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        today = timezone.localdate()
        cutoff = timezone.now() - timedelta(days=OVERDUE_THRESHOLD_DAYS)

        # Sessions are overdue if:
        # 1. completed_at > 6 days ago (normal path), OR
        # 2. notify_overdue_from is set and > 6 days ago (resumed-from-hold path — fires next day)
        from django.db.models import Q
        overdue_sessions = (
            ScanningSession.objects.filter(
                status="completed",
            )
            .filter(
                Q(notify_overdue_from__lte=cutoff) |
                Q(notify_overdue_from__isnull=True, completed_at__lte=cutoff)
            )
            .select_related("batch", "hospital", "to_hospital", "scanned_by")
        )

        if not overdue_sessions.exists():
            self.stdout.write(self.style.SUCCESS("No overdue sessions found. Nothing to do."))
            return

        self.stdout.write(
            self.style.NOTICE(
                f"Found {overdue_sessions.count()} session(s) awaiting confirmation for more than "
                f"{OVERDUE_THRESHOLD_DAYS} days."
            )
        )

        sent = 0
        skipped = 0

        for session in overdue_sessions:
            days_overdue = max(1, (timezone.now() - session.completed_at).days)

            if dry_run:
                batch_id = session.batch.batch_id if session.batch else "N/A"
                hospital = session.hospital.name if session.hospital else "N/A"
                self.stdout.write(
                    f"  [DRY RUN] {session.session_key} | Batch {batch_id} | {hospital} | {days_overdue} days overdue"
                )
                continue

            if not force and AwaitingOverdueLog.objects.filter(session=session, notified_on=today).exists():
                skipped += 1
                continue

            try:
                notify_awaiting_overdue(session, days_overdue)
                AwaitingOverdueLog.objects.get_or_create(session=session, notified_on=today)
                sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Sent reminder for session {session.session_key} ({days_overdue} days overdue)"
                    )
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  Failed to send reminder for {session.session_key}: {exc}")
                )

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Done — {sent} reminder(s) sent, {skipped} already notified today.")
            )
