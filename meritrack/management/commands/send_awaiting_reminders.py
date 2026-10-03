"""
Management command: send_awaiting_reminders
===========================================
Sends daily email reminders to MT managers for every MeriTrack scanning
session that has been awaiting confirmation (completed) for more than 6 days
and is not on hold.

The first reminder fires on day 7 (i.e., >= 6 full days since completed_at).
Subsequent reminders fire daily until the session is closed or put on hold.
Resumed-from-hold sessions use notify_overdue_from instead, which is
back-dated so the reminder fires on the very next run.

Usage:
    python manage.py send_awaiting_reminders            # normal daily run
    python manage.py send_awaiting_reminders --dry-run  # list overdue, send nothing
    python manage.py send_awaiting_reminders --force    # resend even if already sent today

Schedule via cron (run daily at 09:00):
    0 9 * * * /path/to/venv/bin/python /path/to/manage.py send_awaiting_reminders
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from meritrack.models import AwaitingOverdueLog, ScanningSession
from meritrack.notification_service import notify_awaiting_overdue

OVERDUE_THRESHOLD_DAYS = 6


class Command(BaseCommand):
    help = "Send daily overdue reminders for MeriTrack sessions stuck in awaiting confirmation for more than 6 days."

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

        # Overdue if completed > 6 days ago, or (resumed-from-hold) the
        # back-dated notify_overdue_from is past the cutoff.
        overdue_sessions = (
            ScanningSession.objects.filter(status="completed")
            .filter(
                Q(notify_overdue_from__lte=cutoff)
                | Q(notify_overdue_from__isnull=True, completed_at__lte=cutoff)
            )
            .select_related("hospital", "to_hospital", "rep")
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

        sent, skipped = 0, 0
        for session in overdue_sessions:
            reference = session.notify_overdue_from or session.completed_at
            days_overdue = (timezone.now() - reference).days if reference else OVERDUE_THRESHOLD_DAYS

            already_sent = AwaitingOverdueLog.objects.filter(session=session, notified_on=today).exists()
            if already_sent and not force:
                skipped += 1
                self.stdout.write(f"  - {session.session_key}: already notified today, skipping")
                continue

            if dry_run:
                self.stdout.write(
                    f"  - [DRY RUN] {session.session_key} @ {session.hospital.name}: "
                    f"{days_overdue} day(s) overdue"
                )
                continue

            notify_awaiting_overdue(session, days_overdue)
            AwaitingOverdueLog.objects.get_or_create(session=session, notified_on=today)
            sent += 1
            self.stdout.write(
                f"  - {session.session_key} @ {session.hospital.name}: reminder sent ({days_overdue} days)"
            )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete — no emails sent."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. Sent {sent} reminder(s), skipped {skipped}."))
