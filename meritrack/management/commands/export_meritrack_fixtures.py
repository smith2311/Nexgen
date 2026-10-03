"""
Export fresh MeriTrack fixtures from the current database for production deployment.

Exports (in load order):
  1. meritrack_salespersons.json  — Salesperson User accounts (required before hospitals)
  2. meritrack_hospitals.json     — Hospitals + departments with salesperson M2M mapping
  3. meritrack_catalog.json.gz    — Product catalog
  4. meritrack_consigned.json.gz  — Consigned inventory (on-shelf stock)
  5. meritrack_postings.json.gz   — Consignment postings (large, optional)

Usage:
    python manage.py export_meritrack_fixtures
    python manage.py export_meritrack_fixtures --skip-postings
    python manage.py export_meritrack_fixtures --output-dir /custom/path
"""

import json
import os

from django.contrib.auth.models import User
from django.core import serializers as django_serializers
from django.core.management import call_command
from django.core.management.base import BaseCommand

from meritrack.models import (
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    Hospital,
    ProductCatalog,
)

DEFAULT_FIXTURE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")
)


class Command(BaseCommand):
    help = "Export fresh MeriTrack fixtures (salesperson mapping, hospitals, catalog, inventory)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-postings",
            action="store_true",
            help="Skip exporting the large consignment postings table",
        )
        parser.add_argument(
            "--output-dir",
            default=DEFAULT_FIXTURE_DIR,
            help=f"Directory to write fixture files (default: {DEFAULT_FIXTURE_DIR})",
        )

    def handle(self, *args, **options):
        output_dir = os.path.abspath(options["output_dir"])
        os.makedirs(output_dir, exist_ok=True)
        skip_postings = options["skip_postings"]

        self.stdout.write(self.style.MIGRATE_HEADING("Exporting MeriTrack fixtures..."))
        self.stdout.write(f"  Output directory: {output_dir}\n")

        # ── 1. Salesperson Users ─────────────────────────────────────────────
        # Export only users who are assigned to hospitals via M2M.
        # Uses natural keys (username) so PK differences across environments
        # do not cause FK mismatch errors when loading.
        salespersons_path = os.path.join(output_dir, "meritrack_salespersons.json")
        self.stdout.write("  [1/5] Salesperson users...")

        sp_users = User.objects.filter(mt_hospitals__isnull=False).distinct()
        sp_count = sp_users.count()

        if sp_count:
            data = django_serializers.serialize(
                "json",
                sp_users,
                indent=2,
                use_natural_foreign_keys=True,
                use_natural_primary_keys=True,
            )
            with open(salespersons_path, "w", encoding="utf-8") as fh:
                fh.write(data)
            self.stdout.write(self.style.SUCCESS(f"       ✓ {sp_count} users → meritrack_salespersons.json"))
            for u in sp_users:
                self.stdout.write(f"         · {u.email} ({u.username})")
        else:
            with open(salespersons_path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            self.stdout.write(self.style.WARNING("       ⚠ No salesperson users found — empty fixture written"))

        # ── 2. Hospitals + Departments (with salesperson M2M) ────────────────
        # natural_foreign=True → salespersons M2M stored as [["username"]] tuples
        # instead of raw PK integers, enabling cross-environment loading.
        hospitals_path = os.path.join(output_dir, "meritrack_hospitals.json")
        self.stdout.write("  [2/5] Hospitals & departments...")

        hosp_count = Hospital.objects.count()
        dept_count = Department.objects.count()

        call_command(
            "dumpdata",
            "meritrack.hospital",
            "meritrack.department",
            indent=2,
            output=hospitals_path,
            natural_foreign=True,
            verbosity=0,
        )
        size_kb = os.path.getsize(hospitals_path) // 1024
        self.stdout.write(
            self.style.SUCCESS(
                f"       ✓ {hosp_count} hospitals, {dept_count} departments "
                f"→ meritrack_hospitals.json ({size_kb} KB)"
            )
        )

        # ── 3. Product Catalog ───────────────────────────────────────────────
        catalog_path = os.path.join(output_dir, "meritrack_catalog.json.gz")
        self.stdout.write("  [3/5] Product catalog...")

        catalog_count = ProductCatalog.objects.count()
        call_command(
            "dumpdata",
            "meritrack.productcatalog",
            indent=2,
            output=catalog_path,
            verbosity=0,
        )
        size_kb = os.path.getsize(catalog_path) // 1024
        self.stdout.write(
            self.style.SUCCESS(
                f"       ✓ {catalog_count} products → meritrack_catalog.json.gz ({size_kb} KB)"
            )
        )

        # ── 4. Consigned Inventory ───────────────────────────────────────────
        consigned_path = os.path.join(output_dir, "meritrack_consigned.json.gz")
        self.stdout.write("  [4/5] Consigned inventory...")

        consigned_count = ConsignedProduct.objects.count()
        call_command(
            "dumpdata",
            "meritrack.consignedproduct",
            indent=2,
            output=consigned_path,
            natural_foreign=True,
            verbosity=0,
        )
        size_kb = os.path.getsize(consigned_path) // 1024
        self.stdout.write(
            self.style.SUCCESS(
                f"       ✓ {consigned_count} items → meritrack_consigned.json.gz ({size_kb} KB)"
            )
        )

        # ── 5. Consignment Postings ──────────────────────────────────────────
        if not skip_postings:
            postings_path = os.path.join(output_dir, "meritrack_postings.json.gz")
            self.stdout.write("  [5/5] Consignment postings (may take a moment)...")

            postings_count = ConsignmentPosting.objects.count()
            call_command(
                "dumpdata",
                "meritrack.consignmentposting",
                indent=2,
                output=postings_path,
                natural_foreign=True,
                verbosity=0,
            )
            size_kb = os.path.getsize(postings_path) // 1024
            self.stdout.write(
                self.style.SUCCESS(
                    f"       ✓ {postings_count} postings → meritrack_postings.json.gz ({size_kb} KB)"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("  [5/5] Consignment postings — SKIPPED"))

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✓ Export complete!"))
        self.stdout.write("")
        self.stdout.write("  Next steps:")
        self.stdout.write("    1. Review the fixtures in meritrack/fixtures/")
        self.stdout.write("    2. rsync meritrack/fixtures/ to the production server")
        self.stdout.write("    3. On production: python manage.py load_meritrack_data")
