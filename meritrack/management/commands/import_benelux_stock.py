"""
Import Benelux hospitals from the SAP consignment stock Excel export.

Usage:
    python manage.py import_benelux_stock
    python manage.py import_benelux_stock --file "media/Meritrack/Benelux Consignment stock.XLSX"
    python manage.py import_benelux_stock --dry-run

What it does:
  1. Creates/updates Hospitals from unique Customer rows (sap_code, name, country, plant)
  2. Creates a default 'General' Department per hospital if absent

Only hospital-level data is imported. Product catalog and consigned inventory
are managed separately via load_meritrack_data / fixtures.
"""

import os
import zipfile
# defusedxml, not xml.etree: the sheets below are parsed straight out of an
# operator-supplied .xlsx, and the stdlib parser expands entities.
import defusedxml.ElementTree as ET

from django.core.management.base import BaseCommand

from meritrack.models import Department, Hospital


DEFAULT_XLSX = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "media", "Meritrack", "Benelux Consignment stock.XLSX"
)


def _load_rows(xlsx_path):
    """Return list of dicts from Sheet1 of the XLSX file."""
    z = zipfile.ZipFile(xlsx_path)
    ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    with z.open("xl/sharedStrings.xml") as f:
        ss_tree = ET.parse(f)
    shared_strings = [t.text or "" for t in ss_tree.findall(".//ns:t", ns)]

    with z.open("xl/worksheets/sheet1.xml") as f:
        ws_tree = ET.parse(f)

    raw_rows = []
    for row in ws_tree.findall(".//ns:row", ns):
        row_data = []
        for cell in row.findall("ns:c", ns):
            v = cell.find("ns:v", ns)
            t = cell.get("t")
            if v is not None:
                row_data.append(shared_strings[int(v.text)] if t == "s" else v.text)
            else:
                row_data.append(None)
        raw_rows.append(row_data)

    if not raw_rows:
        return []

    headers = raw_rows[0]
    return [dict(zip(headers, row)) for row in raw_rows[1:]]


class Command(BaseCommand):
    help = "Import Benelux hospitals from SAP Excel export"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_XLSX,
            help="Path to the Benelux Consignment stock XLSX file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report what would be imported without writing to the database",
        )

    def handle(self, *args, **options):
        xlsx_path = os.path.normpath(options["file"])
        dry_run = options["dry_run"]

        if not os.path.exists(xlsx_path):
            self.stderr.write(self.style.ERROR(f"File not found: {xlsx_path}"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Importing Benelux hospitals from: {xlsx_path}"))
        if dry_run:
            self.stdout.write(self.style.WARNING("  DRY RUN — no database writes"))

        rows = _load_rows(xlsx_path)
        self.stdout.write(f"  Parsed {len(rows)} data rows")

        # Collect unique customers
        unique_customers = {}
        for row in rows:
            sap_code = (row.get("Customer") or "").strip()
            if sap_code and sap_code not in unique_customers:
                unique_customers[sap_code] = {
                    "name": (row.get("Customer Name") or "").strip(),
                    "country": (row.get("Country") or "Belgium").strip(),
                    "plant": (row.get("Plant") or "").strip(),
                }

        # ── Step 1: Hospitals ────────────────────────────────────────────────
        self.stdout.write("\nStep 1: Hospitals")
        hospital_map = {}

        hosp_created = hosp_updated = 0
        for sap_code, info in unique_customers.items():
            if dry_run:
                self.stdout.write(f"  [DRY] {sap_code} — {info['name']} ({info['country']})")
                continue
            hosp, created = Hospital.objects.update_or_create(
                sap_code=sap_code,
                defaults={
                    "name": info["name"],
                    "country": info["country"],
                    "plant": info["plant"] or None,
                    "is_active": True,
                },
            )
            hospital_map[sap_code] = hosp
            if created:
                hosp_created += 1
                self.stdout.write(f"  + Created: {hosp.name} ({sap_code})")
            else:
                hosp_updated += 1
                self.stdout.write(f"  ~ Updated: {hosp.name} ({sap_code})")

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {hosp_created} created, {hosp_updated} updated")
            )

        # ── Step 2: Default Departments ──────────────────────────────────────
        self.stdout.write("\nStep 2: Default Departments")

        dept_created = 0
        for sap_code, hosp in hospital_map.items():
            _, created = Department.objects.get_or_create(
                hospital=hosp,
                name="General",
                defaults={"code": "GEN", "is_active": True},
            )
            if created:
                dept_created += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  [DRY] Would import {len(unique_customers)} hospitals + departments"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {dept_created} departments created"))
            self.stdout.write(self.style.SUCCESS("\n✓ Benelux hospital import complete!"))
