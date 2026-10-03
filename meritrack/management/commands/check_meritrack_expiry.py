"""
Management command to check consigned product expiry dates and generate alerts.
Run daily via cron: python manage.py check_meritrack_expiry
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from meritrack.models import ConsignedProduct, ExpiryAlert
from meritrack.notification_service import notify_expiry_alert

TIER_THRESHOLDS = [
    ("expired", 0),
    ("1w", 7),
    ("1m", 30),
    ("3m", 90),
    ("6m", 180),
]


class Command(BaseCommand):
    help = "Check consigned product expiry dates and create tiered alerts"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview alerts without creating them",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Send email notifications for new alerts",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        send_notifications = options["notify"]
        today = timezone.now().date()
        created_count = 0
        skipped_count = 0

        products = ConsignedProduct.objects.filter(
            status="on_shelf",
            expiry_date__isnull=False,
        ).select_related("department__hospital", "catalog_product")

        for product in products:
            days_until = (product.expiry_date - today).days

            for tier, threshold in TIER_THRESHOLDS:
                if tier == "expired" and days_until > 0:
                    continue
                if tier != "expired" and days_until > threshold:
                    continue

                exists = ExpiryAlert.objects.filter(product=product, tier=tier).exists()

                if exists:
                    skipped_count += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN] Would create {tier} alert for "
                        f"{product.serial_number} (expires {product.expiry_date})"
                    )
                    created_count += 1
                    continue

                ExpiryAlert.objects.create(
                    product=product,
                    tier=tier,
                    status="pending",
                    days_until_expiry=days_until,
                )
                created_count += 1

                if send_notifications:
                    try:
                        notify_expiry_alert(product, tier)
                    except Exception as e:
                        self.stderr.write(f"Notification error for {product.serial_number}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Done. Created: {created_count}, Skipped (existing): {skipped_count}"))
