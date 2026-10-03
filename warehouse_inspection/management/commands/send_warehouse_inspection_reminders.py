"""
warehouse_inspection/management/commands/send_warehouse_inspection_reminders.py
================================================================================
Daily cron entry point for Warehouse Inspection: open-issue reminder emails
+ the daily activity report to the Report Recipients master.

Cron (run once a day, after the day's inspections are expected to be done):
    0 9 * * * podman exec django_app python manage.py send_warehouse_inspection_reminders

On this project's Windows/Podman dev box there is no cron daemon, so this is
installed as a Windows Scheduled Task ("NexGen-WarehouseInspectionReminders",
daily at 09:00) that runs the same podman exec command directly.

Usage:
    python manage.py send_warehouse_inspection_reminders
    python manage.py send_warehouse_inspection_reminders --reminders-only
    python manage.py send_warehouse_inspection_reminders --report-only
    python manage.py send_warehouse_inspection_reminders --report-only --date 2026-08-11
    python manage.py send_warehouse_inspection_reminders --report-only --today

The report covers YESTERDAY by default: it is sent at 09:00, when the current
day's inspections have not happened yet. Use --date/--today to re-send for a
specific day.

Flow:
  1. (unless --report-only) tickets.send_ticket_reminder_emails() - for every
     open WarehouseInspectionTicket not opened today and not already
     reminded today, resend a "still open" email (only-No items) to that
     checkpoint/floor's responsible_email + internal_department_email.
  2. (unless --reminders-only) services.send_daily_warehouse_inspection_report()
     - builds and emails the end-to-end daily activity report to whoever is
     active in the Report Recipients master (manage at
     /warehouse-inspection/admin/report-recipients/).
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warehouse_inspection.services import (
    default_report_date,
    send_daily_warehouse_inspection_report,
)
from warehouse_inspection.tickets import send_ticket_reminder_emails


class Command(BaseCommand):
    help = 'Send daily Warehouse Inspection reminders for open tickets and the daily activity report to the Report Recipients master.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reminders-only',
            action='store_true',
            help='Only send the still-open reminder emails, skip the daily report.',
        )
        parser.add_argument(
            '--report-only',
            action='store_true',
            help='Only send the daily activity report, skip the reminder emails.',
        )
        parser.add_argument(
            '--date',
            dest='report_date',
            help='Report on this day (YYYY-MM-DD) instead of yesterday.',
        )
        parser.add_argument(
            '--today',
            action='store_true',
            help="Report on today instead of yesterday (partial day - the 09:00 cron should not use this).",
        )

    def handle(self, *args, **options):
        if options['report_date'] and options['today']:
            raise CommandError('Use either --date or --today, not both.')

        if options['report_date']:
            try:
                report_date = datetime.strptime(options['report_date'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('--date must be in YYYY-MM-DD format.') from exc
        elif options['today']:
            report_date = timezone.localdate()
        else:
            report_date = default_report_date()

        if not options['report_only']:
            result = send_ticket_reminder_emails()
            self.stdout.write(
                f"Reminders: sent {result['sent_count']}, failed {result['failed_count']}"
                + (f" - {'; '.join(result['errors'])}" if result['errors'] else '')
            )

        if not options['reminders_only']:
            result = send_daily_warehouse_inspection_report(report_date)
            self.stdout.write(
                f"Daily report for {report_date}: sent {result['sent_count']}, failed {result['failed_count']}"
                + (f" - {'; '.join(result['errors'])}" if result['errors'] else '')
            )
