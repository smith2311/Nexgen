"""
Management command: send_monthly_reminders
==========================================
Sends the 20th-of-month pending hospital visit reminder to each salesperson
(and warehouse managers) for hospitals that have not been visited yet.

Usage:
    python manage.py send_monthly_reminders            # auto: current month
    python manage.py send_monthly_reminders --force    # ignore already-sent log
    python manage.py send_monthly_reminders --year 2026 --month 4

Schedule via cron (run on the 20th of every month at 09:00):
    0 9 20 * * /path/to/venv/bin/python /path/to/manage.py send_monthly_reminders
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from product_scanning.models import Hospital, MonthlyReminderLog, ScanningSession
from product_scanning.notification_service import notify_pending_hospitals_reminder
from product_scanning.views import SalespersonPerformanceViewSet


class Command(BaseCommand):
    help = "Send monthly pending-hospital-visit reminder emails to salespersons and warehouse managers."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=None, help="Year to check (default: current year)")
        parser.add_argument("--month", type=int, default=None, help="Month to check (1-12, default: current month)")
        parser.add_argument(
            "--force", action="store_true", default=False, help="Send even if reminder was already sent this month"
        )

    def handle(self, *args, **options):
        now = timezone.now()
        year = options["year"] or now.year
        month = options["month"] or now.month
        force = options["force"]

        self.stdout.write(self.style.NOTICE(f"Running monthly reminders for {year}-{month:02d} (force={force}) …"))

        # Discover salespersons from Hospital master mapping (email-based)
        sp_list = SalespersonPerformanceViewSet._discover_salesperson_emails()

        if not sp_list:
            self.stdout.write(self.style.WARNING("No salespersons found in Hospital master. Nothing to do."))
            return

        sent = 0
        skipped = 0

        for sp in sp_list:
            sp_email = sp["email"]
            sp_name = sp["name"]

            # Skip if already sent this month (unless --force)
            if (
                not force
                and MonthlyReminderLog.objects.filter(salesperson_email=sp_email, year=year, month=month).exists()
            ):
                self.stdout.write(f"  SKIP  {sp_name} ({sp_email}) — reminder already sent for {year}-{month:02d}")
                skipped += 1
                continue

            # Find assigned hospitals for this salesperson
            assigned = list(Hospital.objects.filter(salesperson_email__iexact=sp_email))
            if not assigned:
                self.stdout.write(f"  SKIP  {sp_name} ({sp_email}) — no hospitals assigned")
                skipped += 1
                continue

            # Find visited hospitals (any session in this month at their hospitals)
            visited_ids = set(
                ScanningSession.objects.filter(
                    hospital__in=assigned,
                    started_at__year=year,
                    started_at__month=month,
                ).values_list("hospital_id", flat=True)
            )

            pending = [h for h in assigned if h.id not in visited_ids]

            if not pending:
                self.stdout.write(
                    f"  OK    {sp_name} ({sp_email}) — all {len(assigned)} hospitals visited, no reminder needed"
                )
                skipped += 1
                continue

            # Send the reminder
            recipients = notify_pending_hospitals_reminder(sp_email, sp_name, year, month, pending)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  SENT  {sp_name} ({sp_email}) — {len(pending)}/{len(assigned)} pending → {recipients}"
                )
            )
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"\nDone. Sent: {sent}  Skipped: {skipped}"))
