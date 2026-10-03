"""
Generate/update expiry dates for ConsignedProducts using (priority order):
  0. Actual scanned GS1 batch expiry from ScanLog (highest priority — real barcode data)
  1. Earliest 631 (consignment out) posting date + shelf_life_months from ProductCatalog
  2. placed_on_shelf_at - 6 months as placement proxy + shelf_life_months (last resort)

Then generate ExpiryAlert records for products approaching expiry.

Usage:
    python manage.py generate_expiry_data
    python manage.py generate_expiry_data --dry-run
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max, Min
from django.utils import timezone

from dateutil.relativedelta import relativedelta

from meritrack.models import (
    ConsignedProduct,
    ConsignmentPosting,
    ExpiryAlert,
    ProductCatalog,
    ScanLog,
)


class Command(BaseCommand):
    help = "Generate expiry dates from scanned batch data + shelf life, then create expiry alerts"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
        parser.add_argument(
            "--all", action="store_true", help="Re-process all products, even those with existing expiry_date"
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force_all = options["all"]
        today = timezone.now().date()

        # ── Step 0: Backfill from ScanLog actual GS1 expiry dates (highest priority) ──
        self.stdout.write("Step 0: Backfilling expiry dates from scanned GS1 barcodes...")
        # Build: serial_number → latest scanned expiry_date from ScanLog
        scan_expiry_by_serial = {}
        for row in (
            ScanLog.objects.filter(expiry_date__isnull=False)
            .values("serial_number")
            .annotate(latest_expiry=Max("expiry_date"))
        ):
            if row["serial_number"]:
                scan_expiry_by_serial[row["serial_number"]] = row["latest_expiry"]

        # Also build: (material_code, batch_number) → latest scanned expiry_date
        scan_expiry_by_batch = {}
        for row in (
            ScanLog.objects.filter(
                expiry_date__isnull=False,
                material_code__isnull=False,
                batch_number__isnull=False,
            )
            .values("material_code", "batch_number")
            .annotate(latest_expiry=Max("expiry_date"))
        ):
            key = (row["material_code"], row["batch_number"])
            scan_expiry_by_batch[key] = row["latest_expiry"]

        self.stdout.write(f"  Found {len(scan_expiry_by_serial)} serial-level scan expiry records")
        self.stdout.write(f"  Found {len(scan_expiry_by_batch)} batch-level scan expiry records")

        scan_updated = 0
        qs_for_scan = (
            ConsignedProduct.objects.all() if force_all else ConsignedProduct.objects.filter(expiry_date__isnull=True)
        )

        for cp in qs_for_scan.iterator(chunk_size=1000):
            # Priority 1: exact serial match
            exp = scan_expiry_by_serial.get(cp.serial_number)
            # Priority 2: material + batch match
            if not exp and cp.material_code and cp.batch_number:
                exp = scan_expiry_by_batch.get((cp.material_code, cp.batch_number))
            if exp and (force_all or not cp.expiry_date):
                if not dry_run:
                    cp.expiry_date = exp
                    cp.save(update_fields=["expiry_date"])
                scan_updated += 1

        self.stdout.write(self.style.SUCCESS(f"  Updated from scan data: {scan_updated}"))

        # ── Step 1: Build a map of (material, batch) → earliest 631 posting date ──
        self.stdout.write("Step 1: Computing earliest consignment-out dates per material+batch...")
        date_lookup = {}
        for row in (
            ConsignmentPosting.objects.filter(movement_type="631")
            .values("material", "batch")
            .annotate(earliest=Min("posting_date"))
        ):
            date_lookup[(row["material"], row["batch"] or "")] = row["earliest"]

        self.stdout.write(f"  Found {len(date_lookup)} material+batch combinations with 631 postings")

        # ── Step 2: Build material_code → shelf_life_months map ──
        shelf_life = dict(
            ProductCatalog.objects.filter(shelf_life_months__gt=0).values_list("material_code", "shelf_life_months")
        )
        self.stdout.write(f"  Found {len(shelf_life)} products with shelf life data")

        # ── Step 3: Fill remaining products from posting date + shelf life ──
        self.stdout.write("Step 2: Updating remaining ConsignedProduct expiry dates via shelf life...")
        products = ConsignedProduct.objects.filter(expiry_date__isnull=True).select_related("catalog_product")
        updated = 0
        skipped = 0

        for cp in products.iterator(chunk_size=1000):
            mat = cp.material_code
            batch = cp.batch_number or ""
            sl = shelf_life.get(mat)
            if not sl:
                if cp.catalog_product and cp.catalog_product.shelf_life_months:
                    sl = cp.catalog_product.shelf_life_months
            if not sl:
                skipped += 1
                continue

            placement_date = date_lookup.get((mat, batch))
            if not placement_date:
                for key, dt in date_lookup.items():
                    if key[0] == mat:
                        placement_date = dt
                        break
            if not placement_date:
                if cp.placed_on_shelf_at:
                    placement_date = (cp.placed_on_shelf_at - relativedelta(months=6)).date()
                else:
                    skipped += 1
                    continue

            expiry = placement_date + relativedelta(months=sl)
            if not dry_run:
                cp.expiry_date = expiry
                if not cp.production_date:
                    cp.production_date = placement_date
                cp.save(update_fields=["expiry_date", "production_date"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"  Updated: {updated} | Skipped: {skipped}"))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes saved"))
            return

        # Step 4: Generate ExpiryAlert records for scanned products only
        # Source: ScanLog records (per serial, use latest expiry date)
        self.stdout.write("Step 3: Generating expiry alerts from scanned batches...")
        from django.db.models import Max as _Max

        scanned_expiries = (
            ScanLog.objects.filter(expiry_date__isnull=False, product__isnull=False)
            .values("product")
            .annotate(latest_expiry=_Max("expiry_date"))
        )
        product_ids = {row["product"]: row["latest_expiry"] for row in scanned_expiries}
        on_shelf = ConsignedProduct.objects.filter(
            pk__in=product_ids.keys(),
            status="on_shelf",
        )

        tiers = [
            ("expired", lambda d: d < today),
            ("1w", lambda d: today <= d <= today + timedelta(days=7)),
            ("1m", lambda d: today + timedelta(days=8) <= d <= today + timedelta(days=30)),
            ("3m", lambda d: today + timedelta(days=31) <= d <= today + timedelta(days=90)),
            ("6m", lambda d: today + timedelta(days=91) <= d <= today + timedelta(days=180)),
        ]

        created_alerts = 0
        for product in on_shelf.iterator(chunk_size=1000):
            days_until = (product.expiry_date - today).days
            tier = None
            for tier_name, check_fn in tiers:
                if check_fn(product.expiry_date):
                    tier = tier_name
                    break
            if not tier:
                continue

            _, was_created = ExpiryAlert.objects.get_or_create(
                product=product,
                tier=tier,
                defaults={
                    "status": "pending",
                    "days_until_expiry": days_until,
                    "notified_at": timezone.now(),
                },
            )
            if was_created:
                created_alerts += 1

        self.stdout.write(self.style.SUCCESS(f"  Created {created_alerts} new expiry alerts"))

        # Summary
        alert_counts = {}
        for tier_name, _ in tiers:
            alert_counts[tier_name] = ExpiryAlert.objects.filter(tier=tier_name, status="pending").count()
        self.stdout.write(f"\nExpiry Alert Summary: {alert_counts}")
        self.stdout.write(self.style.SUCCESS("Done!"))
