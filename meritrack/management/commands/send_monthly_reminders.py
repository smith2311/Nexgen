"""
Management command: send_monthly_reminders
==========================================
Sends the 20th-of-month pending hospital visit reminder to each MT rep
(and managers) for assigned hospitals that have not been visited yet
this month.

Usage:
    python manage.py send_monthly_reminders            # auto: current month
    python manage.py send_monthly_reminders --force    # ignore already-sent log
    python manage.py send_monthly_reminders --year 2026 --month 4

Schedule via cron (run on the 20th of every month at 09:00):
    0 9 20 * * /path/to/venv/bin/python /path/to/manage.py send_monthly_reminders
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from meritrack.models import Hospital, MonthlyReminderLog, ScanningSession
from meritrack.notification_service import notify_pending_hospitals_reminder


def _discover_reps():
    """Return [(email, display_name)] for every rep referenced in the hospital master."""
    emails = set()
    for e in (
        Hospital.objects.filter(is_active=True)
        .exclude(salesperson_email="")
        .exclude(salesperson_email__isnull=True)
        .values_list("salesperson_email", flat=True)
    ):
        emails.add(e.lower())
    for e in (
        Hospital.objects.filter(is_active=True, salespersons__isnull=False)
        .values_list("salespersons__email", flat=True)
        .distinct()
    ):
        if e:
            emails.add(e.lower())

    reps = []
    for email in sorted(emails):
        user = User.objects.filter(email__iexact=email).first()
        name = (f"{user.first_name} {user.last_name}".strip() or user.username) if user else email
        reps.append((email, name))
    return reps


class Command(BaseCommand):
    help = "Send monthly pending-hospital-visit reminder emails to MT reps and managers."

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

        reps = _discover_reps()
        if not reps:
            self.stdout.write(self.style.WARNING("No reps found in Hospital master. Nothing to do."))
            return

        sent, skipped = 0, 0
        for sp_email, sp_name in reps:
            if (
                not force
                and MonthlyReminderLog.objects.filter(salesperson_email=sp_email, year=year, month=month).exists()
            ):
                self.stdout.write(f"  SKIP  {sp_name} ({sp_email}) — reminder already sent for {year}-{month:02d}")
                skipped += 1
                continue

            assigned = list(
                Hospital.objects.filter(
                    Q(salesperson_email__iexact=sp_email) | Q(salespersons__email__iexact=sp_email),
                    is_active=True,
                ).distinct()
            )
            if not assigned:
                self.stdout.write(f"  SKIP  {sp_name} ({sp_email}) — no hospitals assigned")
                skipped += 1
                continue

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

            recipients = notify_pending_hospitals_reminder(sp_email, sp_name, year, month, pending)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  SENT  {sp_name} ({sp_email}) — {len(pending)}/{len(assigned)} pending → {recipients}"
                )
            )
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"\nDone. Sent: {sent}  Skipped: {skipped}"))
