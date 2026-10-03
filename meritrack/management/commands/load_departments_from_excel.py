"""
Management command: load_departments_from_excel
===============================================
Reads the Germany consignment serial data Excel file and creates
Department records for each hospital found in the database.

Storage-location → Department name mapping:
  TR01  →  Interventional Cardiology / Cath Lab
  EP03  →  Electrophysiology
  (blank/unknown) → General Consignment  (fallback, only if no other dept exists)

Usage:
  python manage.py load_departments_from_excel
  python manage.py load_departments_from_excel --excel media/Meritrack/200_germany_consignment_serial_data.XLSX
  python manage.py load_departments_from_excel --dry-run
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from meritrack.models import Department, Hospital

# SAP storage-location code → human-readable department name + optional code
STORAGE_LOCATION_MAP = {
    "TR01": {"name": "Interventional Cardiology / Cath Lab", "code": "TR01"},
    "EP03": {"name": "Electrophysiology", "code": "EP03"},
}

DEFAULT_DEPT = {"name": "General Consignment", "code": "GEN"}


class Command(BaseCommand):
    help = "Populate Department records from Germany consignment serial Excel data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--excel",
            default="media/Meritrack/200_germany_consignment_serial_data.XLSX",
            help="Path to the serial data Excel file (relative to project root or absolute)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without saving to the database",
        )
        parser.add_argument(
            "--force-update",
            action="store_true",
            help="Update the name/code of existing departments if they differ",
        )

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError("openpyxl is required: pip install openpyxl")

        excel_path = options["excel"]
        if not os.path.isabs(excel_path):
            excel_path = os.path.join(os.getcwd(), excel_path)

        if not os.path.exists(excel_path):
            raise CommandError(f"Excel file not found: {excel_path}")

        dry_run = options["dry_run"]
        force_update = options["force_update"]

        self.stdout.write(f"Reading: {excel_path}")

        # ── 1. Parse Excel ────────────────────────────────────────────────────
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(max_row=1))]

        try:
            cust_idx = headers.index("Customer")
            name_idx = headers.index("Customer Name")
            stloc_idx = headers.index("St.Location")
        except ValueError as e:
            raise CommandError(f"Expected column not found in Excel: {e}")

        # Collect unique (sap_code, st_location) pairs
        pairs: dict[str, set] = {}  # sap_code → set of st_location codes
        hosp_names: dict[str, str] = {}  # sap_code → customer name (last seen)

        for row in ws.iter_rows(min_row=2, values_only=True):
            sap_code = str(row[cust_idx]).strip() if row[cust_idx] else None
            st_loc = str(row[stloc_idx]).strip() if row[stloc_idx] else ""
            cust_name = row[name_idx] or ""

            if not sap_code:
                continue
            pairs.setdefault(sap_code, set()).add(st_loc)
            hosp_names[sap_code] = cust_name

        wb.close()
        self.stdout.write(f"Found {len(pairs)} unique SAP customer codes in Excel")

        # ── 2. Build wanted departments per hospital ──────────────────────────
        # For each SAP code → resolve to a Hospital in DB
        created = updated = skipped = missing_hospital = 0

        hospitals_by_sap: dict[str, Hospital] = {
            h.sap_code: h for h in Hospital.objects.exclude(sap_code__isnull=True).exclude(sap_code="")
        }

        with transaction.atomic():
            for sap_code, st_locs in sorted(pairs.items()):
                hospital = hospitals_by_sap.get(sap_code)
                if hospital is None:
                    self.stdout.write(
                        self.style.WARNING(f'  SKIP (no DB match): {sap_code} — {hosp_names.get(sap_code, "?")}')
                    )
                    missing_hospital += 1
                    continue

                # Build desired departments from the st_locs present for this hospital
                wanted = []
                named_locs = [loc for loc in st_locs if loc in STORAGE_LOCATION_MAP]
                if named_locs:
                    for loc in sorted(named_locs):
                        wanted.append(STORAGE_LOCATION_MAP[loc])
                # Always include General Consignment if no TR01 (generic consignment hospitals)
                if not named_locs or not any(d["code"] == "TR01" for d in wanted):
                    wanted.append(DEFAULT_DEPT)

                for dept_def in wanted:
                    existing = Department.objects.filter(
                        hospital=hospital,
                        code=dept_def["code"],
                    ).first()

                    if existing:
                        if force_update and existing.name != dept_def["name"]:
                            if not dry_run:
                                existing.name = dept_def["name"]
                                existing.save(update_fields=["name", "updated_at"])
                            self.stdout.write(
                                f'  UPDATE  {hospital.name} → {dept_def["name"]} (code={dept_def["code"]})'
                            )
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        if not dry_run:
                            Department.objects.create(
                                hospital=hospital,
                                name=dept_def["name"],
                                code=dept_def["code"],
                                is_active=True,
                            )
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  CREATE  {hospital.name} → {dept_def["name"]} (code={dept_def["code"]})'
                            )
                        )
                        created += 1

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("\n[DRY RUN] No changes saved."))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done — created: {created}, updated: {updated}, "
                f"skipped (exists): {skipped}, missing hospital: {missing_hospital}"
            )
        )
