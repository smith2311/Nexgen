"""
Load all MeriTrack fixture data on a fresh or existing production instance.

Usage:
    python manage.py load_meritrack_data                     # insert (fresh DB)
    python manage.py load_meritrack_data --skip-postings     # skip large postings table
    python manage.py load_meritrack_data --clear-meritrack   # wipe MeriTrack data first (re-push)
    python manage.py load_meritrack_data --skip-expiry       # skip expiry recalculation

This loads fixtures in dependency order:
  1. salesperson users      (auth.user subset — upsert by username)
  2. hospitals + departments (with salesperson M2M mapping)
  3. product catalog
  4. consigned products
  5. consignment postings   (large — ~58K rows)

Then runs generate_expiry_data to compute expiry dates from posting data.
"""

import json
import os

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand

from meritrack.models import (
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    Hospital,
    ProductCatalog,
)

FIXTURE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "fixtures"))

# Loaded after salesperson users; salespersons.json handled separately via upsert
FIXTURES = [
    ("meritrack_hospitals.json", "Hospitals + Departments"),
    ("meritrack_catalog.json.gz", "Product Catalog"),
    ("meritrack_consigned.json.gz", "Consigned Products"),
    ("meritrack_postings.json.gz", "Consignment Postings"),
]


class Command(BaseCommand):
    help = "Load all MeriTrack fixture data in correct dependency order"

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-postings",
            action="store_true",
            help="Skip loading the large consignment postings fixture",
        )
        parser.add_argument(
            "--skip-expiry",
            action="store_true",
            help="Skip running generate_expiry_data after loading",
        )
        parser.add_argument(
            "--clear-meritrack",
            action="store_true",
            help=(
                "Delete all existing MeriTrack data before loading (safe re-push). "
                "Cascades: hospitals → departments, consigned products, postings, etc. "
                "Does NOT affect auth.user accounts."
            ),
        )

    def _upsert_salesperson_users(self, fixture_path):
        """
        Load salesperson users from fixture with update-or-create semantics.
        Matches on username so existing production accounts are updated, not duplicated.
        Salesperson users authenticate via Zoho OAuth; the stored password hash is
        never used for login but is preserved for Django admin compatibility.
        """
        if not os.path.exists(fixture_path):
            self.stdout.write(self.style.WARNING(f"  Skipped salesperson users: {fixture_path} not found"))
            return 0

        with open(fixture_path, "r", encoding="utf-8") as fh:
            records = json.load(fh)

        if not records:
            self.stdout.write(self.style.WARNING("  No salesperson users in fixture"))
            return 0

        created = updated = 0
        for record in records:
            fields = record["fields"]
            username = fields["username"]
            defaults = {
                "email": fields.get("email", ""),
                "first_name": fields.get("first_name", ""),
                "last_name": fields.get("last_name", ""),
                "is_staff": fields.get("is_staff", False),
                "is_active": fields.get("is_active", True),
                "is_superuser": fields.get("is_superuser", False),
            }
            obj, was_created = User.objects.update_or_create(username=username, defaults=defaults)
            if was_created:
                # Set unusable password for OAuth-only accounts
                obj.set_unusable_password()
                obj.save(update_fields=["password"])
                created += 1
            else:
                updated += 1

        return created, updated

    def _clear_meritrack_data(self):
        """
        Delete all MeriTrack domain data in reverse-dependency order.
        Safe: does not touch auth.user or any non-meritrack tables.
        """
        self.stdout.write("  Clearing existing MeriTrack data...")
        # Postings and consigned products depend on hospitals/catalog
        count = ConsignmentPosting.objects.all().delete()[0]
        self.stdout.write(f"    Deleted {count} consignment postings")
        count = ConsignedProduct.objects.all().delete()[0]
        self.stdout.write(f"    Deleted {count} consigned products")
        count = Department.objects.all().delete()[0]
        self.stdout.write(f"    Deleted {count} departments")
        count = Hospital.objects.all().delete()[0]
        self.stdout.write(f"    Deleted {count} hospitals")
        count = ProductCatalog.objects.all().delete()[0]
        self.stdout.write(f"    Deleted {count} catalog entries")
        self.stdout.write(self.style.SUCCESS("  ✓ MeriTrack data cleared"))

    def handle(self, *args, **options):
        skip_postings = options["skip_postings"]
        skip_expiry = options["skip_expiry"]
        clear_first = options["clear_meritrack"]

        self.stdout.write(self.style.MIGRATE_HEADING("Loading MeriTrack data..."))

        # ── Optional: wipe existing data before re-push ──────────────────────
        if clear_first:
            self._clear_meritrack_data()

        # ── Step 1: Salesperson users (upsert, must come before hospitals) ───
        salespersons_path = os.path.join(FIXTURE_DIR, "meritrack_salespersons.json")
        self.stdout.write("Loading Salesperson Users...")
        result = self._upsert_salesperson_users(salespersons_path)
        if isinstance(result, tuple):
            created, updated = result
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Salesperson Users ({created} created, {updated} updated)")
            )

        # ── Steps 2–5: Remaining fixtures via loaddata ───────────────────────
        for filename, label in FIXTURES:
            if skip_postings and "postings" in filename:
                self.stdout.write(self.style.WARNING(f"  Skipped: {label}"))
                continue

            fixture_path = os.path.join(FIXTURE_DIR, filename)
            if not os.path.exists(fixture_path):
                self.stdout.write(self.style.ERROR(f"  Missing fixture: {fixture_path}"))
                continue

            self.stdout.write(f"Loading {label}...")
            call_command("loaddata", fixture_path, verbosity=0)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {label}"))

        # ── Step 6: Generate expiry data ─────────────────────────────────────
        if not skip_expiry:
            self.stdout.write("Running generate_expiry_data...")
            call_command("generate_expiry_data")
            self.stdout.write(self.style.SUCCESS("  ✓ Expiry data generated"))

        self.stdout.write(self.style.SUCCESS("\n✓ All MeriTrack data loaded successfully!"))
