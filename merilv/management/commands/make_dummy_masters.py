"""Regenerate the master-data dummy workbooks, one per importable master.

Every sheet's header row is taken from ``masters.MASTER_SHEETS`` rather than
written out here, so the dummy files cannot drift from the real templates the
importer expects — which is exactly what happened to the previous hand-made pair
when the console grew from two importable masters to ten.

The values are real, not synthetic: material codes, customer codes/names and
courier partners are lifted from the existing dummy files, and plants, storage
locations and the billing/sales type tables come from the SAP-derived
"Master Data.xlsx". That matters for two reasons — the barcode dumps in the other
dummy folders resolve against these material codes, and the org chain has to be
referentially intact or the importer skips it (a storage location whose plant
does not exist is dropped by design).

Files are emitted in registry order, which is also dependency order: company →
sales org → plant → storage → types → materials → customers → couriers → cartons.

Usage (from inside the running container):
    python manage.py make_dummy_masters
    python manage.py make_dummy_masters --rows 50
    python manage.py make_dummy_masters --out /app/dummy_tempelate/meril_v_dummy_data/6_master_data_import
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from merilv.masters import MASTER_SHEETS
from merilv.services import export

DEFAULT_OUT = "dummy_tempelate/meril_v_dummy_data/6_master_data_import"
REAL_WORKBOOK = "Master Data.xlsx"

#: The two real business units these files are built for. Materials are stored
#: per entity, so every material row has to name one; the bulk stays MHPL so the
#: barcode dumps in folders 1-5 keep resolving, with a slice of MLSIPL so
#: multi-entity behaviour (and the entity-scoped Clear) is exercised too.
PRIMARY_ENTITY = "MHPL"
SECONDARY_ENTITY = "MLSIPL"

#: Real company / sales-org hierarchy, as the workbook's own sheet names imply
#: ("Plant-900" hangs off company 900, "Plant - 300" off 300).
COMPANIES = [
    ("900", "Meril Healthcare Pvt. Ltd."),
    ("300", "Meril Life Sciences Pvt. Ltd."),
]
SALES_ORGS = [
    ("9100", "MHPL Domestic Sales", "900"),
    ("3100", "MLSIPL Domestic Sales", "300"),
]

#: The two plant/storage sheet pairs, and the sales org each side reports to.
#: "Plant-900"/"Storage-900" are the MHPL (company 900) side, "Plant - 300"/
#: "Storage - 300" the MLSIPL (300) side.
PLANT_SOURCES = (
    ("Plant-900", "Storage-900", "9100"),
    ("Plant - 300", "Storage - 300", "3100"),
)

#: The warehouse codes the other dummy folders scan against. They appear in
#: Storage-900 but have no row in Plant-900 (the plant sheets list SAP production
#: plants, not warehouses), so they are named here and filed under 9100 —
#: otherwise every storage row for them would be skipped as parentless.
WAREHOUSE_NAMES = {
    "WGJ1": "Vapi Warehouse 1",
    "WGJ2": "Vapi Warehouse 2",
    "WGJ3": "Vapi Warehouse 3",
    "WGJ4": "Vapi Warehouse 4",
    "WGJ5": "Vapi Warehouse 5",
    "WGJ6": "Vapi Warehouse 6",
    "WGJ7": "Ahmedabad Warehouse",
    "WDL1": "Delhi Warehouse",
    "CTN1": "Chennai Warehouse",
    "WAP1": "Pune Warehouse",
}

#: The carriers the original customer file named, plus enough well-known Indian
#: ones to make the list worth searching and paging through. Held here rather than
#: re-read from the customer file, so regenerating never shrinks the list.
COURIERS = (
    "Safexpress", "Gati", "DTDC", "Blue Dart", "Delhivery",
    "TCI Express", "VRL Logistics", "Spoton",
)

#: Which customer sheet belongs to which unit, and the company code that goes with
#: it. Sampling both means the customer file spans both business units.
CUSTOMER_SOURCES = (
    ("9100 - Customer Master", PRIMARY_ENTITY, "900"),
    ("3100 - Customer Master", SECONDARY_ENTITY, "300"),
)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


class Command(BaseCommand):
    help = "Regenerate the per-master dummy import workbooks from real master data."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=DEFAULT_OUT, help="Output directory.")
        parser.add_argument(
            "--rows",
            type=int,
            default=300,
            help="Cap on generated rows per master (materials/customers/storage).",
        )

    def handle(self, *args, **options):
        out = Path(options["out"])
        if not out.is_absolute():
            out = Path.cwd() / out
        out.mkdir(parents=True, exist_ok=True)
        cap = max(1, options["rows"])

        self.old_dir = out
        self.real = Path(REAL_WORKBOOK)
        if not self.real.exists():
            raise CommandError(
                f"{REAL_WORKBOOK} not found in {Path.cwd()} — it is the source for "
                "plants, storage locations and the type tables."
            )

        builders = {
            "company": self._company,
            "organization": self._organization,
            "plant": self._plant,
            "storage": self._storage,
            "billing_type": lambda cap: self._types("billing_type", cap),
            "sales_type": lambda cap: self._types("sales_type", cap),
            "materials": self._materials,
            "customers": self._customers,
            "couriers": self._couriers,
            "boxes": self._boxes,
        }
        missing = set(MASTER_SHEETS) - set(builders)
        if missing:
            raise CommandError(
                "No dummy-row builder for: " + ", ".join(sorted(missing))
                + ". Add one here when a master joins MASTER_SHEETS."
            )

        # Storage rows decide which plants have to exist, so they are read first
        # and the plant sheet is then widened to cover every plant they name — a
        # storage location whose plant is missing gets skipped by the importer.
        self._storage_rows = self._read_storage(cap)
        self._plants = self._read_plants()
        for code in sorted({row["Plant Code"] for row in self._storage_rows}):
            if code not in self._plants:
                self._plants[code] = {
                    "Plant Code": code,
                    "Plant Name": WAREHOUSE_NAMES.get(code, f"Plant {code}"),
                    "Sales Org Code": "9100",
                }

        written = []
        for key in MASTER_SHEETS:
            headers = list(MASTER_SHEETS[key]["headers"])
            records = builders[key](cap)
            rows = [[record.get(header, "") for header in headers] for record in records]
            buffer = export.export_generic(MASTER_SHEETS[key]["title"], headers, rows)
            path = out / f"{key}_dummy.xlsx"
            path.write_bytes(buffer.getvalue())
            written.append((key, len(rows), path.name))
            self.stdout.write(f"  {key:<14} {len(rows):>5} rows  ->  {path.name}")

        # The pre-console pair used the old header set (no Entity column) and no
        # longer imports cleanly. Removed only after every builder has read it.
        for stale in ("material_master_dummy.xlsx", "customer_master_dummy.xlsx"):
            victim = out / stale
            if victim.exists():
                victim.unlink()
                self.stdout.write(self.style.WARNING(f"  removed stale {stale}"))

        total = sum(n for _, n, _ in written)
        self.stdout.write(self.style.SUCCESS(
            f"\n{len(written)} workbooks, {total:,} rows total, in {out}"
        ))

    # ── Sheet readers ────────────────────────────────────────────────────────
    def _real_sheet(self, title: str):
        wb = load_workbook(self.real, read_only=True, data_only=True)
        try:
            if title not in wb.sheetnames:
                raise CommandError(f'"{title}" sheet missing from {REAL_WORKBOOK}.')
            yield from ([_text(c) for c in row] for row in wb[title].iter_rows(values_only=True))
        finally:
            wb.close()

    def _old_dummy(self, *names: str) -> tuple[list[str], list[list[str]]]:
        """Header row + data rows of the first of ``names`` that exists.

        Several names are accepted so the command stays runnable after it has
        replaced its own inputs: the pre-console files are tried first, then the
        ones this command writes, which is what makes a second run work.
        """
        for name in names:
            path = self.old_dir / name
            if not path.exists():
                continue
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                rows = [[_text(c) for c in row] for row in wb.active.iter_rows(values_only=True)]
            finally:
                wb.close()
            if rows:
                return rows[0], rows[1:]
        return [], []

    def _sheet_column(self, path: Path, *names: str) -> list[str]:
        """Every value of the first matching column in a dummy workbook."""
        if not path.exists():
            return []
        wanted = {n.lower() for n in names}
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            rows = wb.active.iter_rows(values_only=True)
            headers = [_text(h).lower() for h in next(rows, ())]
            index = next((i for i, h in enumerate(headers) if h in wanted), None)
            if index is None:
                return []
            return [_text(r[index]) for r in rows if index < len(r) and _text(r[index])]
        finally:
            wb.close()

    def _referenced_materials(self) -> dict[str, dict]:
        """Every material code the other dummy folders scan against.

        The point of these files is that an upload resolves instead of reporting
        "Master Missing" on every row, so the material master is built from the
        union of what folders 1-5 actually reference — not from an unrelated
        sample. Descriptions come from the stock dump, GTINs from the barcodes
        that carry them, and material groups from the real workbook.
        """
        import re

        root = self.old_dir.parent
        found: dict[str, dict] = {}

        def note(code: str, **fields):
            code = code.strip()
            if not code:
                return
            entry = found.setdefault(code, {"description": "", "gtin": "", "group": ""})
            for field, value in fields.items():
                if value and not entry[field]:
                    entry[field] = value

        # Stock dump: the one source that pairs a code with a description.
        stock = root / "1_stock_validator/ZMB58_MHPL_STOCK_dummy.xlsx"
        if stock.exists():
            wb = load_workbook(stock, read_only=True, data_only=True)
            try:
                rows = wb.active.iter_rows(values_only=True)
                headers = [_text(h).lower() for h in next(rows, ())]
                mat = headers.index("material") if "material" in headers else None
                desc = headers.index("description") if "description" in headers else None
                for cells in rows:
                    if mat is None or mat >= len(cells):
                        continue
                    note(
                        _text(cells[mat]),
                        description=_text(cells[desc]) if desc is not None and desc < len(cells) else "",
                    )
            finally:
                wb.close()

        # Barcodes: (01) GTIN and (240) scan code travel together in one string.
        pair = re.compile(r"\(01\)(\d+)(?:.*?)\(240\)([A-Za-z0-9\-/.]+)")
        bare = re.compile(r"\(240\)([A-Za-z0-9\-/.]+)")
        for txt in root.rglob("*.txt"):
            text = txt.read_text(errors="replace")
            for gtin, code in pair.findall(text):
                note(code, gtin=gtin)
            for code in bare.findall(text):
                note(code)

        # The remaining dumps name materials in a plain column.
        for relative, columns in (
            ("2_outbound_scanner/YVL06_delivery_dump_dummy.xlsx", ("material",)),
            ("3_branch_transfer/MB52_stock_dummy.xlsx", ("material",)),
            ("3_branch_transfer/BT_plan_dummy.xlsx", ("sku code",)),
            ("4_zva05/ZVA05_open_orders_dummy.xlsx", ("material",)),
            ("5_barcode_extractor/barcodes_bulk_dummy.xlsx", ("scan code", "material")),
        ):
            for code in self._sheet_column(root / relative, *columns):
                note(code)

        # Material groups from the real workbook, for the codes it knows.
        groups: dict[str, str] = {}
        for sheet in ("MHPL Material Master", "MLSIPL Material Master"):
            for index, cells in enumerate(self._real_sheet(sheet)):
                if index == 0:
                    continue
                if len(cells) > 8 and cells[2]:
                    groups.setdefault(cells[2], cells[8])
        for code, entry in found.items():
            if not entry["group"]:
                entry["group"] = groups.get(code, "")

        return found

    def _read_plants(self) -> dict[str, dict]:
        """Real plant codes and names from both plant sheets, keyed by code.

        The descriptive name lives in "Search Term 1" ("HEALTH CARE", "STENT");
        the "Name" column is the legal entity and repeats for every row, so it is
        only a fallback.
        """
        plants: dict[str, dict] = {}
        for plant_sheet, _, sales_org in PLANT_SOURCES:
            for index, cells in enumerate(self._real_sheet(plant_sheet)):
                if index == 0 or not cells or not cells[0]:
                    continue
                code = cells[0].upper()
                if code.startswith("PL") or code in plants:
                    continue
                name = (cells[1] if len(cells) > 1 else "") or (cells[5] if len(cells) > 5 else "")
                plants[code] = {
                    "Plant Code": code,
                    "Plant Name": name.title() if name.isupper() else (name or code),
                    "Sales Org Code": sales_org,
                }
        return plants

    def _read_storage(self, cap: int) -> list[dict]:
        """Real (plant, storage, description) triples from both storage sheets.

        Sampled round-robin across plants rather than taking the first N rows: the
        sheets are sorted by plant, so a straight slice returned a single plant's
        locations and left the rest of the hierarchy untested.
        """
        by_plant: dict[str, list[dict]] = {}
        # (plant, code) is the real identity, and WGJ2 appears in both sheets — so
        # collecting without this produced rows the importer then had to reject as
        # duplicates.
        seen: set[tuple[str, str]] = set()
        for _, storage_sheet, _ in PLANT_SOURCES:
            for index, cells in enumerate(self._real_sheet(storage_sheet)):
                if index == 0 or len(cells) < 2 or not cells[0] or not cells[1]:
                    continue
                plant, code = cells[0].upper(), cells[1].upper()
                if plant.startswith("PL") or (plant, code) in seen:
                    continue
                seen.add((plant, code))
                by_plant.setdefault(plant, []).append({
                    "Plant Code": plant,
                    "Storage Code": code,
                    "Storage Name": (cells[2] if len(cells) > 2 else "") or cells[1],
                })

        rows, depth = [], 0
        while len(rows) < cap and any(len(v) > depth for v in by_plant.values()):
            for plant in sorted(by_plant):
                if depth < len(by_plant[plant]):
                    rows.append(by_plant[plant][depth])
                    if len(rows) >= cap:
                        break
            depth += 1

        if not rows:
            raise CommandError("No usable rows found in either storage sheet.")
        return rows

    # ── Row builders, one per master ─────────────────────────────────────────
    def _company(self, cap):
        return [{"Company Code": code, "Company Name": name} for code, name in COMPANIES]

    def _organization(self, cap):
        return [
            {"Sales Org Code": code, "Sales Org Name": name, "Company Code": company}
            for code, name, company in SALES_ORGS
        ]

    def _plant(self, cap):
        return [self._plants[code] for code in sorted(self._plants)]

    def _storage(self, cap):
        return self._storage_rows

    def _types(self, key: str, cap: int):
        """Billing and sales types share one sheet with a two-row header.

        Row 1 groups the columns per entity ("MHPL Sales type", …) and row 2 holds
        the real "Sales Type"/"Description" pair under each group, so the codes are
        read by locating that pair rather than by a fixed column index.
        """
        grid = list(self._real_sheet("Billing and Sales Type"))
        if len(grid) < 3:
            raise CommandError('"Billing and Sales Type" sheet has no data rows.')

        wanted = "billing type" if key == "billing_type" else "sales type"
        code_col = next(
            (i for i, cell in enumerate(grid[1]) if cell.strip().lower() == wanted),
            None,
        )
        if code_col is None:
            raise CommandError(f'No "{wanted}" column found in the type sheet.')

        code_header, desc_header = MASTER_SHEETS[key]["headers"]
        records, seen = [], set()
        for cells in grid[2:]:
            code = _text(cells[code_col]) if code_col < len(cells) else ""
            if not code or code.upper() in seen:
                continue
            seen.add(code.upper())
            desc = _text(cells[code_col + 1]) if code_col + 1 < len(cells) else ""
            records.append({code_header: code, desc_header: desc})
            if len(records) >= cap:
                break
        return records

    def _materials(self, cap):
        """Every material the other dummy folders reference, plus the Entity column.

        Not capped by ``--rows``: a partial material master is the one thing that
        makes the other five folders useless, since anything absent here comes back
        as "Master Missing" from the validator and as a rejected scan from the
        outbound console.
        """
        referenced = self._referenced_materials()
        if not referenced:
            raise CommandError(
                "No material codes found in the sibling dummy folders — expected the "
                "stock dump, barcode files and delivery dumps beside this one."
            )

        # Whatever the previous file said about a code wins, so hand-tuned
        # descriptions and GTINs survive a regeneration.
        headers, rows = self._old_dummy("material_master_dummy.xlsx", "materials_dummy.xlsx")
        index = {name.strip().lower(): i for i, name in enumerate(headers) if name}

        def col(cells, *names):
            for name in names:
                i = index.get(name)
                if i is not None and i < len(cells):
                    return cells[i]
            return ""

        previous = {}
        for cells in rows:
            code = col(cells, "scan code", "material")
            if code:
                previous[code] = {
                    "sap": col(cells, "sap code"),
                    "description": col(cells, "description", "material description"),
                    "gtin": col(cells, "gtin", "ean"),
                    "group": col(cells, "material group", "group"),
                }

        records = []
        for n, code in enumerate(sorted(referenced)):
            entry = referenced[code]
            old = previous.get(code, {})
            records.append({
                # A slice goes to the second unit so entity scoping — and the
                # entity-scoped Clear — is testable, while everything the MHPL
                # barcodes need stays under MHPL.
                "Entity": SECONDARY_ENTITY if n % 10 == 9 else PRIMARY_ENTITY,
                "Scan Code": code,
                "SAP Code": old.get("sap") or code,
                "Description": old.get("description") or entry["description"],
                "GTIN": old.get("gtin") or entry["gtin"],
                "Material Group": old.get("group") or entry["group"],
            })
        return records

    def _customers(self, cap):
        """Real customer codes and names, sampled from both units' sheets.

        Sourced from the real workbook rather than from the previous dummy file:
        reading its own output meant a run with a smaller ``--rows`` permanently
        shrank the set, with no way back once the original file was replaced.

        The SAP sheets carry code/name/city/country/street but no dispatch detail,
        so courier, plant and contact fields are dealt round-robin from the courier
        and warehouse lists — which keeps them consistent with couriers_dummy.xlsx
        instead of naming carriers that do not exist.

        Columns are resolved by header name, not position: the two sheets do not
        share a layout (9100 has "Customer" third, 3100 second), so fixed indices
        read a customer *name* into the code column on one of them.
        """
        records = []
        per_sheet = max(1, cap // len(CUSTOMER_SOURCES))
        # Global, not per sheet: customer_code is unique across the whole table, and
        # a few accounts are set up under both sales orgs — so a per-sheet set let a
        # repeat through, which the importer then folded into an update.
        seen: set[str] = set()

        for sheet, entity, company in CUSTOMER_SOURCES:
            taken = 0
            columns: dict[str, int] = {}
            for index, cells in enumerate(self._real_sheet(sheet)):
                if index == 0:
                    for position, header in enumerate(cells):
                        columns.setdefault(header.strip().lower(), position)
                    if "customer" not in columns:
                        raise CommandError(f'No "Customer" column in the "{sheet}" sheet.')
                    continue

                def field(*names: str) -> str:
                    for name in names:
                        position = columns.get(name)
                        if position is not None and position < len(cells):
                            return _text(cells[position])
                    return ""

                code, name = field("customer"), field("customer name")
                # The sheets repeat a customer once per distribution channel.
                if not code or not name or code.upper() in seen:
                    continue
                seen.add(code.upper())

                n = len(records)
                records.append({
                    "Customer Code": code,
                    "Customer Name": name,
                    "Entity": entity,
                    "Company Code": company,
                    "Plant": sorted(WAREHOUSE_NAMES)[n % len(WAREHOUSE_NAMES)],
                    "Courier Partner": COURIERS[n % len(COURIERS)],
                    "Courier Number": f"DKT{200000 + n * 731}",
                    "City": field("city"),
                    "State": "",
                    "Country": field("country") or "IN",
                    "Pincode": field("postal code", "pincode"),
                    "GSTIN": f"24AAACM{1000 + n}Q1Z{n % 10}",
                    "Email": field("email"),
                    "Mobile": field("mobile#", "mobile") or f"9{800000000 + n * 4211}",
                    "Contact Person": "",
                    "Address": field("street", "address"),
                    # A couple of blocked rows so the Blocked badge has something
                    # to show and the yes/no parsing is exercised.
                    "Blocked": "Yes" if n % 40 == 39 else "No",
                })
                taken += 1
                if taken >= per_sheet:
                    break

        if not records:
            raise CommandError("No customer rows found in the real workbook's customer sheets.")
        return records

    def _couriers(self, cap):
        """The fixed carrier list, spread across warehouses."""
        warehouses = sorted(WAREHOUSE_NAMES)
        return [
            {
                "Courier Name": name,
                "Plant": warehouses[n % len(warehouses)],
                "Courier Number": f"DKT{300000 + n * 137}",
                "Vehicle Number": f"GJ-01-{1000 + n}",
                # One in seven inactive, so the Active/Inactive badge and the
                # is_active parsing both have something to show.
                "Active": "No" if n % 7 == 6 else "Yes",
            }
            for n, name in enumerate(COURIERS)
        ]

    def _boxes(self, cap):
        """The real seeded carton profiles, so the file matches seed_merilv."""
        from merilv.services.entities import SEED_BOXES

        return [
            {
                "Box Code": spec["code"],
                "Length (cm)": spec.get("length_cm", 0),
                "Width (cm)": spec.get("width_cm", 0),
                "Height (cm)": spec.get("height_cm", 0),
                "Volumetric Divisor": spec.get("divisor", 5000),
                "Max Units": spec.get("max_qty", 0),
                "Active": "Yes",
            }
            for spec in SEED_BOXES
        ]
