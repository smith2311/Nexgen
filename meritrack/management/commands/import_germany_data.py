"""
Import Germany consignment data from SAP Excel exports.

Usage:
  python manage.py import_germany_data                       # import all 3 files
  python manage.py import_germany_data --stock-only          # stock file only
  python manage.py import_germany_data --serial-only         # serial file only
  python manage.py import_germany_data --posting-only        # posting file only
  python manage.py import_germany_data --dry-run             # preview without writing
"""

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from meritrack.models import (
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    Hospital,
    ProductCatalog,
)

MEDIA_DIR = os.path.join("media", "Meritrack")
STOCK_FILE = "200_germany_consignment_stock_07.03.XLSX"
SERIAL_FILE = "200_germany_consignment_serial_data.XLSX"
POSTING_FILE = "200_germany_consignment_posting_data.XLSX"

# SAP movement type → MeriTrack consigned product status
MVT_TO_STATUS = {
    "631": "on_shelf",  # consignment fill up / lending
    "632": "returned",  # consignment pickup / return
    "633": "used",  # issued to customer (used on patient)
    "634": "returned",  # customer return receipt
    "561": "on_shelf",  # initial stock balance
    "551": "expired_removed",  # scrap
    "309": "transferred",  # transfer posting
    "310": "transferred",  # transfer posting (reversal)
    "301": "transferred",  # plant-to-plant transfer
}

# SAP delivery type name → MeriTrack consigned product status
DELIVERY_NAME_TO_STATUS = {
    "Consignment Fill Up": "on_shelf",
    "EXP-Consigmt Fill Up": "on_shelf",
    "Consignment issue": "used",
    "EXP-Consignmt issue": "used",
    "Consignment pickup": "returned",
    "EXP-Consignmt pickup": "returned",
    "Consignment Returns": "returned",
}


def _safe_str(val):
    """Convert cell value to stripped string, or empty string."""
    if val is None:
        return ""
    return str(val).strip()


def _safe_date(val):
    """Convert cell value to date or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, "date"):
        return val.date()
    return None


def _safe_time(val):
    if val is None:
        return None
    if hasattr(val, "hour"):
        return val
    return None


def _safe_decimal(val, default=0):
    if val is None:
        return Decimal(str(default))
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return Decimal(str(default))


def _safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _load_xlsx(path):
    """Load an Excel file; returns list of dicts keyed by header row."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
    return [dict(zip(headers, row)) for row in rows[1:]]


class Command(BaseCommand):
    help = "Import Germany consignment data from SAP Excel exports"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
        parser.add_argument("--stock-only", action="store_true")
        parser.add_argument("--serial-only", action="store_true")
        parser.add_argument("--posting-only", action="store_true")

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        any_flag = options["stock_only"] or options["serial_only"] or options["posting_only"]

        if not any_flag or options["stock_only"]:
            self._import_stock()
        if not any_flag or options["serial_only"]:
            self._import_serial()
        if not any_flag or options["posting_only"]:
            self._import_posting()

        self.stdout.write(self.style.SUCCESS("Import complete."))

    # ─── helpers ────────────────────────────────────────────────────────────
    def _get_or_create_hospital(self, sap_code, name, plant=None):
        """Get or create Hospital by SAP customer code."""
        sap_code = _safe_str(sap_code)
        name = _safe_str(name)
        if not sap_code:
            return None

        hospital, created = Hospital.objects.get_or_create(
            sap_code=sap_code,
            defaults={
                "name": name or f"Customer {sap_code}",
                "country": "Germany",
                "plant": plant or "",
            },
        )
        if not created and plant and not hospital.plant:
            hospital.plant = plant
            hospital.save(update_fields=["plant"])
        # Update name if it was truncated / placeholder
        if not created and name and hospital.name.startswith("Customer "):
            hospital.name = name
            hospital.save(update_fields=["name"])
        return hospital

    def _get_or_create_default_dept(self, hospital):
        """Create or get the default 'Consignment Stock' department."""
        dept, _ = Department.objects.get_or_create(
            hospital=hospital,
            name="Consignment Stock",
            defaults={"code": "CS", "is_active": True},
        )
        return dept

    def _get_or_create_catalog(self, material_code, name=None, **extra):
        """Get or create ProductCatalog by material_code."""
        material_code = _safe_str(material_code)
        if not material_code:
            return None

        catalog, created = ProductCatalog.objects.get_or_create(
            material_code=material_code,
            defaults={
                "name": name or material_code,
                "category": self._guess_category(name or material_code),
                **{k: v for k, v in extra.items() if v},
            },
        )
        # Backfill name if it was a placeholder
        if not created and name and catalog.name == catalog.material_code:
            catalog.name = name
            catalog.save(update_fields=["name"])
        # Backfill SAP classification fields
        updated_fields = []
        for field in (
            "material_type",
            "material_type_description",
            "material_group",
            "material_group_description",
            "base_uom",
        ):
            val = extra.get(field)
            if val and not getattr(catalog, field, None):
                setattr(catalog, field, val)
                updated_fields.append(field)
        if updated_fields:
            catalog.save(update_fields=updated_fields)
        return catalog

    def _guess_category(self, name):
        """Heuristic category from product name."""
        n = name.lower()
        if any(k in n for k in ("stent", "biomime", "coronary")):
            return "stent"
        if any(k in n for k in ("balloon", "catheter", "inflation")):
            return "balloon"
        if any(k in n for k in ("guidewire", "guide wire")):
            return "guidewire"
        if any(k in n for k in ("valve", "myval", "dafodil", "aortic", "octacor")):
            return "valve"
        if any(k in n for k in ("graft", "endograft")):
            return "graft"
        if any(k in n for k in ("closure", "occluder")):
            return "closure"
        if any(k in n for k in ("accessory", "device", "adapter", "introducer", "sheath", "dilator", "inflation")):
            return "accessory"
        return "other"

    # ─── 1. STOCK FILE ─────────────────────────────────────────────────────
    def _import_stock(self):
        path = os.path.join(MEDIA_DIR, STOCK_FILE)
        self.stdout.write(f"\n>>> Importing STOCK from {path}")
        rows = _load_xlsx(path)

        created = updated = skipped = 0
        # Headers: Customer, Name 1, Material, Batch, Unrestricted,
        #          In Quality Insp., Restricted-Use Stock, In Transfer, Base Unit of Measure, Plant
        for row in rows:
            sap_code = _safe_str(row.get("Customer"))
            hospital_name = _safe_str(row.get("Name 1"))
            material_code = _safe_str(row.get("Material"))
            batch = _safe_str(row.get("Batch"))
            unrestricted = _safe_int(row.get("Unrestricted"))
            in_quality = _safe_int(row.get("In Quality Insp."))
            restricted = _safe_int(row.get("Restricted-Use Stock"))
            in_transfer = _safe_int(row.get("In Transfer (Plant)"))
            uom = _safe_str(row.get("Base Unit of Measure")) or "EA"
            plant = _safe_str(row.get("Plant"))

            if not sap_code or not material_code:
                skipped += 1
                continue

            if self.dry_run:
                created += 1
                continue

            hospital = self._get_or_create_hospital(sap_code, hospital_name, plant)
            if not hospital:
                skipped += 1
                continue
            dept = self._get_or_create_default_dept(hospital)

            catalog = self._get_or_create_catalog(
                material_code,
                name=material_code,
                base_uom=uom,
            )

            # For batch-level stock, use material_code:batch as identifier
            identifier = f"{material_code}:{batch}" if batch else material_code

            product, was_created = ConsignedProduct.objects.update_or_create(
                department=dept,
                serial_number=identifier,
                batch_number=batch,
                defaults={
                    "material_code": material_code,
                    "catalog_product": catalog,
                    "product_name": catalog.name if catalog else material_code,
                    "quantity": max(unrestricted, 0),
                    "status": "on_shelf" if unrestricted > 0 else "returned",
                    "notes": (
                        f"Stock import | Unrestricted: {unrestricted}, "
                        f"In Quality: {in_quality}, Restricted: {restricted}, "
                        f"In Transfer: {in_transfer}"
                    ),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"  Stock: created={created}, updated={updated}, skipped={skipped}"))

    # ─── 2. SERIAL DATA ────────────────────────────────────────────────────
    def _import_serial(self):
        path = os.path.join(MEDIA_DIR, SERIAL_FILE)
        self.stdout.write(f"\n>>> Importing SERIAL DATA from {path}")
        rows = _load_xlsx(path)

        # Group by serial number to find LAST movement (determines current status)
        serial_movements = {}
        for row in rows:
            serial = _safe_str(row.get("Serial Number"))
            if not serial:
                continue
            date = _safe_date(row.get("Date"))
            # Keep the latest movement per serial
            if serial not in serial_movements or (
                date and (serial_movements[serial].get("_date") is None or date > serial_movements[serial]["_date"])
            ):
                serial_movements[serial] = {**row, "_date": date}

        created = updated = skipped = 0
        # Headers: Serial Number, Material, Material Description, Equipment,
        #          Delv.NO, Delv.Itm, Date, Customer, Customer Name,
        #          Movement Type, Delivery Type, Delivery Name, Plant, St.Location, Batch No
        for serial, row in serial_movements.items():
            material = _safe_str(row.get("Material"))
            mat_desc = _safe_str(row.get("Material Description"))
            equipment = _safe_str(row.get("Equipment"))
            sap_code = _safe_str(row.get("Customer"))
            cust_name = _safe_str(row.get("Customer Name"))
            mvt_type = _safe_str(row.get("Movement Type"))
            delv_name = _safe_str(row.get("Delivery Name"))
            plant = _safe_str(row.get("Plant"))
            stor_loc = _safe_str(row.get("St.Location"))
            batch = _safe_str(row.get("Batch No"))
            date = row.get("_date")

            if not sap_code or not material:
                skipped += 1
                continue

            # Determine status from last movement
            status = DELIVERY_NAME_TO_STATUS.get(delv_name)
            if not status:
                status = MVT_TO_STATUS.get(mvt_type, "on_shelf")

            if self.dry_run:
                self.stdout.write(f"  [DRY] {serial} @ {cust_name} -> {status} ({delv_name})")
                created += 1
                continue

            hospital = self._get_or_create_hospital(sap_code, cust_name, plant)
            if not hospital:
                skipped += 1
                continue
            dept = self._get_or_create_default_dept(hospital)

            catalog = self._get_or_create_catalog(material, name=mat_desc)

            product, was_created = ConsignedProduct.objects.update_or_create(
                serial_number=serial,
                defaults={
                    "department": dept,
                    "material_code": material,
                    "catalog_product": catalog,
                    "product_name": mat_desc or material,
                    "batch_number": batch,
                    "status": status,
                    "quantity": 1,
                    "shelf_location": stor_loc or None,
                    "notes": (
                        f"Serial import | Equipment: {equipment}, " f"MVT: {mvt_type} ({delv_name}), " f"Date: {date}"
                    ),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  Serial: created={created}, updated={updated}, skipped={skipped} "
                f"(from {len(serial_movements)} unique serials, {len(rows)} total movements)"
            )
        )

    # ─── 3. POSTING DATA ───────────────────────────────────────────────────
    def _import_posting(self):
        path = os.path.join(MEDIA_DIR, POSTING_FILE)
        self.stdout.write(f"\n>>> Importing POSTING DATA from {path}")
        self.stdout.write("  Loading Excel (this may take a moment for 58K rows)...")
        rows = _load_xlsx(path)

        # Pre-build hospital and catalog lookup caches
        hospital_cache = {}
        catalog_cache = {}

        created = skipped = 0
        batch_size = 2000
        batch = []

        for row in rows:
            plant = _safe_str(row.get("Plant"))
            posting_date = _safe_date(row.get("Posting Date"))
            entry_date = _safe_date(row.get("Entry Date"))
            entry_time = _safe_time(row.get("Time of Entry"))
            mvt_type = _safe_str(row.get("Movement Type"))
            mvt_text = _safe_str(row.get("Movement Type Text"))
            stor_loc = _safe_str(row.get("Storage Location"))
            mat_doc = _safe_str(row.get("Material Document"))
            mat_doc_item = _safe_str(row.get("Material Doc.Item"))
            mat_doc_year = _safe_str(row.get("Material Doc. Year"))
            material = _safe_str(row.get("Material"))
            mat_desc = _safe_str(row.get("Material Description"))
            mat_batch = _safe_str(row.get("Batch"))
            qty = _safe_decimal(row.get("Qty in Un. of Entry"))
            uom = _safe_str(row.get("Unit of Entry")) or "EA"
            doc_header = _safe_str(row.get("Document Header Text"))
            mat_type = _safe_str(row.get("Material Type"))
            mat_type_desc = _safe_str(row.get("Material type description"))
            mat_group = _safe_str(row.get("Material Group"))
            mat_group_desc = _safe_str(row.get("Material Group Desc."))
            customer_name = _safe_str(row.get("Name 1"))
            customer = _safe_str(row.get("Customer"))
            delivery = _safe_str(row.get("Delivery"))
            user_name = _safe_str(row.get("User name"))
            recv_plant = _safe_str(row.get("Receiving Plant"))
            send_plant = _safe_str(row.get("Sending Plant"))
            amount_lc = _safe_decimal(row.get("Amount in LC"), 0)
            currency = _safe_str(row.get("Currency"))

            if not posting_date or not material:
                skipped += 1
                continue

            if self.dry_run:
                created += 1
                continue

            # Resolve FK links (cached)
            hospital = None
            if customer:
                if customer not in hospital_cache:
                    hospital_cache[customer] = self._get_or_create_hospital(customer, customer_name, plant)
                hospital = hospital_cache[customer]

            catalog = None
            if material:
                if material not in catalog_cache:
                    catalog_cache[material] = self._get_or_create_catalog(
                        material,
                        name=mat_desc or material,
                        material_type=mat_type,
                        material_type_description=mat_type_desc,
                        material_group=mat_group,
                        material_group_description=mat_group_desc,
                        base_uom=uom,
                    )
                catalog = catalog_cache[material]

            posting = ConsignmentPosting(
                plant=plant,
                storage_location=stor_loc or None,
                posting_date=posting_date,
                entry_date=entry_date,
                entry_time=entry_time,
                movement_type=mvt_type,
                movement_type_text=mvt_text or None,
                material_document=mat_doc or None,
                material_doc_item=mat_doc_item or None,
                material_doc_year=mat_doc_year or None,
                material=material,
                material_description=mat_desc or None,
                batch=mat_batch or None,
                quantity=qty,
                base_uom=uom,
                customer=customer or None,
                customer_name=customer_name or None,
                material_type=mat_type or None,
                material_type_description=mat_type_desc or None,
                material_group=mat_group or None,
                material_group_description=mat_group_desc or None,
                delivery=delivery or None,
                user_name=user_name or None,
                document_header_text=doc_header or None,
                receiving_plant=recv_plant or None,
                sending_plant=send_plant or None,
                amount_lc=amount_lc,
                currency=currency or None,
                hospital=hospital,
                catalog_product=catalog,
            )
            batch.append(posting)

            if len(batch) >= batch_size:
                ConsignmentPosting.objects.bulk_create(batch)
                created += len(batch)
                self.stdout.write(f"  ... {created} postings imported")
                batch = []

        if batch:
            ConsignmentPosting.objects.bulk_create(batch)
            created += len(batch)

        self.stdout.write(self.style.SUCCESS(f"  Postings: created={created}, skipped={skipped}"))
