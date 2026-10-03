"""
Data migration: Import Benelux Consignment Stock data.

Reads from:  meritrack/fixtures/benelux_consignment_stock.xlsx

Idempotent — skips entirely if any Hospital records already exist.
Run automatically with:  python manage.py migrate
"""

import os
from datetime import datetime

from django.db import migrations, transaction


# ── helpers ────────────────────────────────────────────────────────────────────


def _safe_str(val):
    return "" if val is None else str(val).strip()


def _safe_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, "date"):
        return val.date()
    return None


def _safe_int(val, default=1):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ── column indices (0-based) ──────────────────────────────────────────────────

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


# ── forward migration ─────────────────────────────────────────────────────────


def import_benelux_data(apps, schema_editor):
    Hospital = apps.get_model("meritrack", "Hospital")
    Department = apps.get_model("meritrack", "Department")
    ProductCatalog = apps.get_model("meritrack", "ProductCatalog")
    ConsignedProduct = apps.get_model("meritrack", "ConsignedProduct")

    # Idempotent: skip if data already exists
    if Hospital.objects.exists():
        print("\n[0005_benelux_data] Hospitals already present — skipping import.")
        return

    xlsx_path = os.path.join(
        os.path.dirname(__file__),  # meritrack/migrations/
        "..",  # meritrack/
        "fixtures",
        "benelux_consignment_stock.xlsx",
    )
    xlsx_path = os.path.normpath(xlsx_path)

    if not os.path.exists(xlsx_path):
        print(f"\n[0005_benelux_data] WARNING: XLSX not found at {xlsx_path} — skipping.")
        return

    try:
        import openpyxl
    except ImportError:
        print("\n[0005_benelux_data] WARNING: openpyxl not installed — skipping import.")
        return

    print(f"\n[0005_benelux_data] Reading {xlsx_path} …")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:  # skip header
            continue
        if not row[COL_CUSTOMER]:  # skip empty rows
            continue
        rows.append((i, row))

    print(f"[0005_benelux_data]   {len(rows)} data rows found")

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
            hospitals_map[sap_code] = {"name": cust_name, "country": country, "plant": plant}
        if mat_code and mat_code not in materials_map:
            materials_map[mat_code] = mat_desc

    with transaction.atomic():
        # 1. Hospitals
        hospital_objs = {}
        for sap_code, info in hospitals_map.items():
            h = Hospital.objects.create(
                name=info["name"],
                sap_code=sap_code,
                country=info["country"],
                plant=info["plant"] or None,
                is_active=True,
            )
            hospital_objs[sap_code] = h

        print(f"[0005_benelux_data]   {len(hospital_objs)} hospitals created")

        # 2. Departments (one per hospital)
        dept_objs = {}
        for sap_code, hospital in hospital_objs.items():
            d = Department.objects.create(
                hospital=hospital,
                name=DEFAULT_DEPT_NAME,
                is_active=True,
            )
            dept_objs[sap_code] = d

        print(f"[0005_benelux_data]   {len(dept_objs)} departments created")

        # 3. ProductCatalog
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

        catalog_objs = {
            pc.material_code: pc for pc in ProductCatalog.objects.filter(material_code__in=materials_map.keys())
        }
        print(f"[0005_benelux_data]   {len(catalog_objs)} catalog entries created")

        # 4. ConsignedProducts
        to_create = []
        for idx, row in rows:
            sap_code = _safe_str(row[COL_CUSTOMER])
            mat_code = _safe_str(row[COL_MATERIAL])
            mat_desc = _safe_str(row[COL_DESCRIPTION])
            batch = _safe_str(row[COL_BATCH])
            serial_raw = _safe_str(row[COL_SERIAL])
            qty = _safe_int(row[COL_UNRESTRICTED], default=1)
            expiry = _safe_date(row[COL_EXPIRY])

            serial = serial_raw if serial_raw else f"BATCH-{sap_code}-{mat_code}-{batch}-{idx}"
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
        print(
            f"[0005_benelux_data]   {len(to_create)} consigned products created\n"
            f"[0005_benelux_data] ✓ Import complete"
        )


def noop(apps, schema_editor):
    """Reverse migration: do nothing (data deletion handled separately)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("meritrack", "0004_stockorder_source_session"),
    ]

    operations = [
        migrations.RunPython(import_benelux_data, noop),
    ]
