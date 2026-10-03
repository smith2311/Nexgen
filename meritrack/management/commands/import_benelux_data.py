"""
Import Benelux consignment stock data from:
  media/Meritrack/Benelux Consignment stock.XLSX

Columns: Customer | Customer Name | Country | Material | Description |
         Batch | Plant | Serial Number | Unrestricted | SLED/BBD

Usage:
  python manage.py import_benelux_data              # wipe & re-import
  python manage.py import_benelux_data --dry-run    # preview, no DB changes
"""

import os
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from meritrack.models import (
    AuditLog,
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    ExpiryAlert,
    Hospital,
    Notification,
    ProductCatalog,
    ScanLog,
    ScanningSession,
    SessionAttachment,
    StockOrder,
    StockOrderItem,
    TransferredProduct,
    TransferSession,
    UsageRecord,
)

XLSX_PATH = os.path.join("media", "Meritrack", "Benelux Consignment stock.XLSX")

# Column indices (0-based)
COL_CUSTOMER = 0
COL_CUSTOMER_NAME = 1
COL_COUNTRY = 2
COL_MATERIAL = 3
COL_DESCRIPTION = 4
COL_BATCH = 5
COL_PLANT = 6
COL_SERIAL = 7
COL_UNRESTRICTED = 8
COL_EXPIRY = 9

DEFAULT_DEPT_NAME = "Consignment Stock"


def _safe_str(val):
    if val is None:
        return ""
    return str(val).strip()


def _safe_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, "date"):
        return val.date()
    return None


def _safe_int(val, default=1):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class Command(BaseCommand):
    help = "Wipe all MeriTrack data and re-import from Benelux Consignment stock.XLSX"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse the XLSX and show counts without writing to DB",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not os.path.exists(XLSX_PATH):
            self.stderr.write(self.style.ERROR(f"File not found: {XLSX_PATH}"))
            return

        try:
            import openpyxl
        except ImportError:
            self.stderr.write(self.style.ERROR("openpyxl is required: pip install openpyxl"))
            return

        self.stdout.write(f"Reading {XLSX_PATH} ...")
        wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
        ws = wb.active

        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:  # skip header
                continue
            # Skip completely empty rows (no customer code)
            if not row[COL_CUSTOMER]:
                continue
            rows.append((i, row))

        self.stdout.write(f"  Found {len(rows)} data rows")

        # Collect unique hospitals and materials
        hospitals_map = {}  # sap_code → {name, country, plant}
        materials_map = {}  # material_code → description

        for idx, row in rows:
            sap_code = _safe_str(row[COL_CUSTOMER])
            cust_name = _safe_str(row[COL_CUSTOMER_NAME])
            country = _safe_str(row[COL_COUNTRY]) or "Belgium"
            plant = _safe_str(row[COL_PLANT])
            mat_code = _safe_str(row[COL_MATERIAL])
            mat_desc = _safe_str(row[COL_DESCRIPTION])

            if sap_code and sap_code not in hospitals_map:
                hospitals_map[sap_code] = {
                    "name": cust_name,
                    "country": country,
                    "plant": plant,
                }
            if mat_code and mat_code not in materials_map:
                materials_map[mat_code] = mat_desc

        self.stdout.write(f"  Unique hospitals  : {len(hospitals_map)}")
        self.stdout.write(f"  Unique materials  : {len(materials_map)}")
        self.stdout.write(f"  Consigned products: {len(rows)}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN — no changes written."))
            return

        with transaction.atomic():
            # ── 1. Wipe all existing meritrack data (dependency order) ──────
            self.stdout.write("\nClearing existing data...")

            AuditLog.objects.all().delete()
            self.stdout.write("  ✓ AuditLog cleared")

            Notification.objects.all().delete()
            self.stdout.write("  ✓ Notifications cleared")

            ExpiryAlert.objects.all().delete()
            self.stdout.write("  ✓ ExpiryAlerts cleared")

            ScanLog.objects.all().delete()
            self.stdout.write("  ✓ ScanLogs cleared")

            SessionAttachment.objects.all().delete()
            self.stdout.write("  ✓ SessionAttachments cleared")

            ScanningSession.objects.all().delete()
            self.stdout.write("  ✓ ScanningSessions cleared")

            StockOrderItem.objects.all().delete()
            StockOrder.objects.all().delete()
            self.stdout.write("  ✓ StockOrders cleared")

            UsageRecord.objects.all().delete()
            self.stdout.write("  ✓ UsageRecords cleared")

            try:
                TransferredProduct.objects.all().delete()
                TransferSession.objects.all().delete()
                self.stdout.write("  ✓ TransferSessions cleared")
            except Exception:
                pass

            try:
                ConsignmentPosting.objects.all().delete()
                self.stdout.write("  ✓ ConsignmentPostings cleared")
            except Exception:
                pass

            ConsignedProduct.objects.all().delete()
            self.stdout.write("  ✓ ConsignedProducts cleared")

            Department.objects.all().delete()
            self.stdout.write("  ✓ Departments cleared")

            Hospital.objects.all().delete()
            self.stdout.write("  ✓ Hospitals cleared")

            ProductCatalog.objects.all().delete()
            self.stdout.write("  ✓ ProductCatalog cleared")

            # ── 2. Create Hospitals ─────────────────────────────────────────
            self.stdout.write("\nCreating hospitals...")
            hospital_objs = {}  # sap_code → Hospital instance

            for sap_code, info in hospitals_map.items():
                h = Hospital.objects.create(
                    name=info["name"],
                    sap_code=sap_code,
                    country=info["country"],
                    plant=info["plant"] or None,
                    is_active=True,
                )
                hospital_objs[sap_code] = h
                self.stdout.write(f'  + {h.name} [{sap_code}] ({info["country"]})')

            # ── 3. Create one default department per hospital ───────────────
            self.stdout.write("\nCreating departments...")
            dept_objs = {}  # sap_code → Department instance

            for sap_code, hospital in hospital_objs.items():
                d = Department.objects.create(
                    hospital=hospital,
                    name=DEFAULT_DEPT_NAME,
                    is_active=True,
                )
                dept_objs[sap_code] = d

            self.stdout.write(f"  ✓ {len(dept_objs)} departments created")

            # ── 4. Create ProductCatalog entries ───────────────────────────
            self.stdout.write("\nCreating product catalog...")
            catalog_objs = {}  # material_code → ProductCatalog instance

            catalog_to_create = [
                ProductCatalog(
                    material_code=mat_code,
                    name=desc or mat_code,
                    description=desc or None,
                    is_active=True,
                )
                for mat_code, desc in materials_map.items()
            ]
            ProductCatalog.objects.bulk_create(catalog_to_create, ignore_conflicts=True)

            # Reload from DB to get PKs
            for pc in ProductCatalog.objects.filter(material_code__in=materials_map.keys()):
                catalog_objs[pc.material_code] = pc

            self.stdout.write(f"  ✓ {len(catalog_objs)} catalog entries created")

            # ── 5. Create ConsignedProducts ────────────────────────────────
            self.stdout.write("\nCreating consigned products...")
            to_create = []

            for idx, row in rows:
                sap_code = _safe_str(row[COL_CUSTOMER])
                mat_code = _safe_str(row[COL_MATERIAL])
                mat_desc = _safe_str(row[COL_DESCRIPTION])
                batch = _safe_str(row[COL_BATCH])
                serial_raw = _safe_str(row[COL_SERIAL])
                qty = _safe_int(row[COL_UNRESTRICTED], default=1)
                expiry = _safe_date(row[COL_EXPIRY])

                # Use actual serial if available; otherwise generate a reference
                if serial_raw:
                    serial = serial_raw
                else:
                    serial = f"BATCH-{sap_code}-{mat_code}-{batch}-{idx}"

                dept = dept_objs.get(sap_code)
                catalog = catalog_objs.get(mat_code)

                if not dept:
                    continue

                to_create.append(
                    ConsignedProduct(
                        department=dept,
                        serial_number=serial,
                        material_code=mat_code or None,
                        catalog_product=catalog,
                        product_name=mat_desc or None,
                        batch_number=batch or None,
                        quantity=max(qty, 1),
                        expiry_date=expiry,
                        status="on_shelf",
                    )
                )

            ConsignedProduct.objects.bulk_create(to_create, batch_size=500)
            self.stdout.write(f"  ✓ {len(to_create)} consigned products created")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Import complete — "
                f"{len(hospital_objs)} hospitals, "
                f"{len(dept_objs)} departments, "
                f"{len(catalog_objs)} catalog entries, "
                f"{len(to_create)} consigned products"
            )
        )
