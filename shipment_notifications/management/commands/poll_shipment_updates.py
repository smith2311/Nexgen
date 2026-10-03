"""
shipment_notifications/management/commands/poll_shipment_updates.py
==================================================================
Django management command — the CRON ENTRY POINT for the notification system.

This is what the daily cron job runs:
  0 11 * * * podman exec django_app python manage.py poll_shipment_updates

Usage:
  python manage.py poll_shipment_updates              # Full: sync + notify
  python manage.py poll_shipment_updates --sync-only   # Only fetch data, no notifications
  python manage.py poll_shipment_updates --notify-only  # Only send notifications for existing changes
  python manage.py poll_shipment_updates --days-back=30 # Custom lookback window

Flow:
  1. (unless --notify-only) Call ShipmentSyncService.sync(days_back)
     → Fetches data from merigate.e-meril.in → upserts ExportShipment
  2. (unless --sync-only) Call WhatsAppNotificationService.send_grouped_notifications()
     → Detects changed shipments → sends WhatsApp → updates notification hashes
"""

import logging

from django.core.management.base import BaseCommand

from shipment_notifications.services import EUROPEAN_COUNTRIES, ShipmentSyncService, WhatsAppNotificationService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync shipments from merigate.e-meril.in and send grouped WhatsApp notifications for changes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days-back",
            type=int,
            default=60,
            help="Number of days of invoice data to sync (default: 60)",
        )
        parser.add_argument(
            "--sync-only",
            action="store_true",
            help="Only sync data from merigate, do not send notifications",
        )
        parser.add_argument(
            "--notify-only",
            action="store_true",
            help="Only send notifications for already-changed shipments, skip sync",
        )
        parser.add_argument(
            "--countries",
            type=str,
            default="",
            help='Comma-separated list of countries to restrict notifications to (e.g. "Germany,France,UK")',
        )
        parser.add_argument(
            "--europe",
            action="store_true",
            help="Restrict notifications to European countries only (uses built-in EUROPEAN_COUNTRIES list)",
        )

    def handle(self, *args, **options):
        sync_only = options.get("sync_only", False)
        notify_only = options.get("notify_only", False)
        days_back = options.get("days_back", 60)
        countries_raw = options.get("countries", "")
        europe_only = options.get("europe", False)

        # Build the countries filter list (takes union of --europe and --countries)
        countries_filter: list[str] | None = None
        if europe_only:
            countries_filter = list(EUROPEAN_COUNTRIES)
            self.stdout.write(f"Country filter: European countries ({len(countries_filter)} entries)")
        elif countries_raw:
            countries_filter = [c.strip() for c in countries_raw.split(",") if c.strip()]
            self.stdout.write(f"Country filter: {countries_filter}")

        if not notify_only:
            self.stdout.write(f"Syncing shipments from merigate.e-meril.in (last {days_back} days)...")
            sync_result = ShipmentSyncService.sync(days_back=days_back)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sync complete: created={sync_result['created']} updated={sync_result['updated']} "
                    f"deleted={sync_result['deleted']} skipped={sync_result['skipped']} errors={sync_result['errors']}"
                )
            )

        if not sync_only:
            self.stdout.write("Sending grouped WhatsApp notifications...")
            notify_result = WhatsAppNotificationService.send_grouped_notifications(
                countries=countries_filter,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Notifications: sent={notify_result['sent']} skipped={notify_result['skipped']} "
                    f"errors={notify_result['errors']} changed={notify_result.get('changed_shipments', 0)}"
                )
            )
