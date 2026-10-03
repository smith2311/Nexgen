"""
Demo data for the Grocery Stock module — a small Indian supermarket.

    python manage.py grocery_demo_data            # create masters + post documents
    python manage.py grocery_demo_data --masters  # masters only, no stock movement
    python manage.py grocery_demo_data --flush    # remove everything it created

Written as a command rather than a migration so it never runs on a real
deployment by accident, and so it can be undone.

TWO THINGS THAT KEEP IT SAFE TO RE-RUN
  * Masters use get_or_create, so running twice does not duplicate them.
  * Documents are created ONCE. Posting is not idempotent — a second run would
    receive the same stock again — so the command refuses if demo documents
    already exist and tells you to --flush first.

Receipts are back-dated across five months so the dashboard trend chart has a
real shape instead of a single spike.
"""
import datetime as dt
from decimal import Decimal as D

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from grocery_stock.models import (
    AdjustmentReason,
    GroceryBrand,
    GroceryCategory,
    GroceryDepartment,
    GroceryGoodsReceipt,
    GroceryGoodsReceiptLine,
    GroceryItem,
    GroceryItemCostHistory,
    GroceryItemValuation,
    GroceryLocation,
    GroceryStock,
    GroceryStockAdjustment,
    GroceryStockAdjustmentLine,
    GroceryStockBatch,
    GroceryStockIssue,
    GroceryStockIssueLine,
    GroceryStockTransaction,
    GrocerySupplier,
    GroceryUnit,
    GroceryWarehouse,
    ValuationMethod,
)
from grocery_stock.services.posting import (
    post_goods_receipt,
    post_stock_adjustment,
    post_stock_issue,
)

# ── Reference data ──────────────────────────────────────────────────────────

# Categories beyond the eleven seeded by migration 0002. The specification
# lists "Oil" as an example item but has no matching category.
EXTRA_CATEGORIES = [
    ('OILS', 'Oils & Ghee', 'Cooking oils, vanaspati and ghee'),
    ('BEVERAGES', 'Beverages', 'Tea, coffee, juices and soft drinks'),
]

BRANDS = [
    ('TATA', 'Tata'), ('AASHIRVAAD', 'Aashirvaad'), ('FORTUNE', 'Fortune'),
    ('AMUL', 'Amul'), ('BRITANNIA', 'Britannia'), ('PARLE', 'Parle'),
    ('NESTLE', 'Nestlé'), ('HUL', 'Hindustan Unilever'), ('COLGATE', 'Colgate'),
    ('SAFFOLA', 'Saffola'), ('MDH', 'MDH'), ('DETTOL', 'Dettol'),
]

# key (internal to this command), name, phone, email, terms, credit days.
# The key never reaches the database — suppliers are identified by name.
SUPPLIERS = [
    ('SUP-METRO', 'Metro Cash & Carry India',
     '9820011223', 'orders@metro.example', 'Net 30', 30),
    ('SUP-ADANI', 'Adani Wilmar Limited',
     '9825044556', 'sales@adaniwilmar.example', 'Net 45', 45),
    ('SUP-GCMMF', 'Gujarat Co-operative Milk Marketing Federation',
     '9879012345', 'trade@amul.example', 'Net 7', 7),
    ('SUP-HUL', 'Hindustan Unilever Limited',
     '9930077889', 'b2b@hul.example', 'Net 30', 30),
    ('SUP-BRIT', 'Britannia Industries Limited',
     '9845066778', 'distributor@britannia.example', 'Net 21', 21),
    ('SUP-LOCAL', 'Shree Traders, Vapi',
     '9426011234', 'shree.traders@example.com', 'Cash on delivery', 0),
]

WAREHOUSES = [
    ('WH-MAIN', 'Main Distribution Centre', 'Plot 12, GIDC Phase II, Vapi, Gujarat'),
    ('WH-STORE', 'Store Backroom', 'Ground floor, retail block'),
    ('WH-COLD', 'Cold Storage', 'Chiller room, retail block'),
]

LOCATIONS = [
    ('WH-MAIN', 'A-01', 'Rack A, Shelf 1 — Cereals'),
    ('WH-MAIN', 'A-02', 'Rack A, Shelf 2 — Pulses & Flour'),
    ('WH-MAIN', 'B-01', 'Rack B, Shelf 1 — Oils'),
    ('WH-MAIN', 'B-02', 'Rack B, Shelf 2 — Spices'),
    ('WH-MAIN', 'C-01', 'Rack C — Cleaning & Household'),
    ('WH-STORE', 'S-01', 'Front aisle'),
    ('WH-STORE', 'S-02', 'Back aisle'),
    ('WH-COLD', 'CH-01', 'Chiller 1 — Dairy'),
    ('WH-COLD', 'CH-02', 'Freezer 1 — Frozen'),
]

# key (internal to this command — never stored; items are identified by name),
# name, category, brand, unit, gst, hsn (ignored since the field was removed),
# reorder, min, max, shelf_life, expiry_required, standard_cost, valuation, bin
ITEMS = [
    ('GR-1001', 'Basmati Rice 25 kg', 'CEREALS', 'TATA', 'BAG', 5, '1006', 20, 10, 200, None, False, 1850, 'WAC', 'A-01'),
    ('GR-1002', 'Sona Masoori Rice 25 kg', 'CEREALS', None, 'BAG', 5, '1006', 20, 10, 200, None, False, 1320, 'WAC', 'A-01'),
    ('GR-1003', 'Whole Wheat 10 kg', 'CEREALS', 'AASHIRVAAD', 'BAG', 5, '1001', 25, 10, 150, 180, False, 420, 'WAC', 'A-01'),
    ('GR-1004', 'Poha 1 kg', 'CEREALS', None, 'PKT', 5, '1904', 40, 20, 300, 120, False, 52, 'WAC', 'A-01'),

    ('GR-1101', 'Toor Dal 1 kg', 'PULSES', 'TATA', 'PKT', 5, '0713', 60, 30, 400, 240, False, 165, 'WAC', 'A-02'),
    ('GR-1102', 'Moong Dal 1 kg', 'PULSES', 'TATA', 'PKT', 5, '0713', 50, 25, 350, 240, False, 138, 'WAC', 'A-02'),
    ('GR-1103', 'Chana Dal 1 kg', 'PULSES', None, 'PKT', 5, '0713', 50, 25, 350, 240, False, 96, 'FIFO', 'A-02'),

    ('GR-1201', 'Atta 10 kg', 'FLOUR', 'AASHIRVAAD', 'BAG', 5, '1101', 30, 15, 200, 120, True, 465, 'WAC', 'A-02'),
    ('GR-1202', 'Maida 5 kg', 'FLOUR', None, 'BAG', 5, '1101', 20, 10, 120, 120, True, 190, 'WAC', 'A-02'),
    ('GR-1203', 'Besan 1 kg', 'FLOUR', None, 'PKT', 5, '1106', 40, 20, 250, 90, True, 88, 'WAC', 'A-02'),

    ('GR-1301', 'Garam Masala 100 g', 'SPICES', 'MDH', 'PKT', 5, '0910', 60, 30, 400, 365, True, 78, 'WAC', 'B-02'),
    ('GR-1302', 'Turmeric Powder 500 g', 'SPICES', 'MDH', 'PKT', 5, '0910', 50, 25, 300, 365, True, 132, 'WAC', 'B-02'),
    ('GR-1303', 'Red Chilli Powder 500 g', 'SPICES', 'MDH', 'PKT', 5, '0904', 50, 25, 300, 365, True, 168, 'WAC', 'B-02'),
    ('GR-1304', 'Iodised Salt 1 kg', 'SPICES', 'TATA', 'PKT', 5, '2501', 80, 40, 500, None, False, 24, 'WAC', 'B-02'),

    ('GR-1401', 'Sunflower Oil 15 L', 'OILS', 'FORTUNE', 'BOX', 5, '1512', 15, 8, 100, 270, True, 1740, 'WAC', 'B-01'),
    ('GR-1402', 'Groundnut Oil 5 L', 'OILS', 'FORTUNE', 'BOX', 5, '1508', 15, 8, 90, 270, True, 980, 'WAC', 'B-01'),
    ('GR-1403', 'Saffola Gold 5 L', 'OILS', 'SAFFOLA', 'BOX', 5, '1512', 12, 6, 70, 270, True, 1120, 'FIFO', 'B-01'),

    ('GR-1501', 'Amul Toned Milk 1 L', 'DAIRY', 'AMUL', 'PKT', 0, '0401', 120, 60, 800, 3, True, 27, 'FIFO', 'CH-01'),
    ('GR-1502', 'Amul Butter 500 g', 'DAIRY', 'AMUL', 'PKT', 12, '0405', 40, 20, 250, 180, True, 265, 'FIFO', 'CH-01'),
    ('GR-1503', 'Amul Cheese Slices 200 g', 'DAIRY', 'AMUL', 'PKT', 12, '0406', 30, 15, 180, 150, True, 135, 'FIFO', 'CH-01'),
    ('GR-1504', 'Fresh Curd 1 kg', 'DAIRY', 'AMUL', 'PKT', 5, '0403', 50, 25, 300, 7, True, 68, 'FIFO', 'CH-01'),

    ('GR-1601', 'Frozen Green Peas 1 kg', 'FROZEN', None, 'PKT', 5, '0710', 40, 20, 250, 365, True, 118, 'FIFO', 'CH-02'),
    ('GR-1602', 'Frozen Paratha 400 g', 'FROZEN', None, 'PKT', 12, '1905', 40, 20, 250, 270, True, 92, 'FIFO', 'CH-02'),

    ('GR-1701', 'Sandwich Bread 400 g', 'BAKERY', 'BRITANNIA', 'PKT', 5, '1905', 60, 30, 300, 4, True, 45, 'FIFO', 'S-01'),
    ('GR-1702', 'Milk Rusk 300 g', 'BAKERY', 'BRITANNIA', 'PKT', 18, '1905', 40, 20, 200, 120, True, 58, 'WAC', 'S-01'),

    ('GR-1801', 'Parle-G Biscuits 800 g', 'SNACKS', 'PARLE', 'PKT', 18, '1905', 80, 40, 500, 180, True, 88, 'WAC', 'S-01'),
    ('GR-1802', 'Potato Chips 150 g', 'SNACKS', None, 'PKT', 12, '2005', 60, 30, 400, 120, True, 42, 'WAC', 'S-01'),
    ('GR-1803', 'Instant Noodles (12 pack)', 'SNACKS', 'NESTLE', 'BOX', 18, '1902', 30, 15, 200, 240, True, 168, 'WAC', 'S-02'),

    ('GR-1901', 'Garbage Bags (Large, 30 pc)', 'HOUSEHOLD', None, 'PKT', 18, '3923', 40, 20, 250, None, False, 96, 'WAC', 'C-01'),
    ('GR-1902', 'Aluminium Foil 72 m', 'HOUSEHOLD', None, 'PCS', 18, '7607', 25, 12, 150, None, False, 210, 'WAC', 'C-01'),

    ('GR-2001', 'Surf Excel Easy Wash 5 kg', 'CLEANING', 'HUL', 'PKT', 18, '3402', 20, 10, 120, None, False, 620, 'WAC', 'C-01'),
    ('GR-2002', 'Vim Dishwash Bar 300 g', 'CLEANING', 'HUL', 'PCS', 18, '3401', 90, 45, 500, None, False, 28, 'WAC', 'C-01'),
    ('GR-2003', 'Dettol Floor Cleaner 2 L', 'CLEANING', 'DETTOL', 'PCS', 18, '3808', 25, 12, 150, None, False, 285, 'WAC', 'C-01'),

    ('GR-2101', 'Colgate Strong Teeth 200 g', 'PERSONAL', 'COLGATE', 'PCS', 18, '3306', 50, 25, 300, 730, True, 105, 'WAC', 'S-02'),
    ('GR-2102', 'Lifebuoy Soap (4 pack)', 'PERSONAL', 'HUL', 'PKT', 18, '3401', 60, 30, 350, 730, True, 132, 'WAC', 'S-02'),

    ('GR-2201', 'Tata Tea Gold 1 kg', 'BEVERAGES', 'TATA', 'PKT', 5, '0902', 30, 15, 200, 365, True, 520, 'WAC', 'S-02'),
    ('GR-2202', 'Nescafé Classic 200 g', 'BEVERAGES', 'NESTLE', 'PCS', 18, '2101', 20, 10, 120, 540, True, 610, 'WAC', 'S-02'),
]


class Command(BaseCommand):
    help = 'Create (or remove) demo data for the Grocery Stock module.'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true',
                            help='Delete all demo data instead of creating it.')
        parser.add_argument('--masters', action='store_true',
                            help='Create master data only — no receipts or issues.')

    def handle(self, *args, **options):
        if options['flush']:
            return self._flush()

        with transaction.atomic():
            self.stdout.write(self.style.MIGRATE_HEADING('Masters'))
            categories = self._categories()
            brands = self._brands()
            units = self._units()
            suppliers = self._suppliers()
            warehouses, locations = self._warehouses()
            items = self._items(categories, brands, units, locations)

            if options['masters']:
                self.stdout.write(self.style.SUCCESS(
                    '\nMasters created. Re-run without --masters to post documents.'))
                return

            if GroceryGoodsReceipt.objects.exists():
                raise CommandError(
                    'Goods receipts already exist. Posting more demo receipts would '
                    'add stock on top of what is already there.\n'
                    'Run "manage.py grocery_demo_data --flush" first if you want a '
                    'clean demo dataset.'
                )

            self.stdout.write(self.style.MIGRATE_HEADING('\nDocuments'))
            self._documents(items, suppliers, warehouses, locations)

        self._summary()

    # ── masters ─────────────────────────────────────────────────────────────

    def _categories(self):
        for code, name, description in EXTRA_CATEGORIES:
            GroceryCategory.objects.get_or_create(
                code=code, defaults={'name': name, 'description': description})
        found = {c.code: c for c in GroceryCategory.objects.all()}
        self.stdout.write(f'  categories  {len(found)}')
        return found

    def _brands(self):
        for code, name in BRANDS:
            GroceryBrand.objects.get_or_create(code=code, defaults={'name': name})
        found = {b.code: b for b in GroceryBrand.objects.all()}
        self.stdout.write(f'  brands      {len(found)}')
        return found

    def _units(self):
        found = {u.code: u for u in GroceryUnit.objects.all()}
        self.stdout.write(f'  units       {len(found)} (seeded by migration 0002)')
        return found

    def _suppliers(self):
        found = {}
        for key, name, phone, email, terms, days in SUPPLIERS:
            supplier, _ = GrocerySupplier.objects.get_or_create(name=name, defaults={
                'phone': phone, 'email': email,
                'payment_terms': terms, 'credit_days': days,
                'address': 'India',
            })
            found[key] = supplier
        self.stdout.write(f'  suppliers   {len(found)}')
        return found

    def _warehouses(self):
        for code, name, address in WAREHOUSES:
            GroceryWarehouse.objects.get_or_create(
                code=code, defaults={'name': name, 'address': address})
        warehouses = {w.code: w for w in GroceryWarehouse.objects.all()}

        for wh_code, code, name in LOCATIONS:
            GroceryLocation.objects.get_or_create(
                warehouse=warehouses[wh_code], code=code, defaults={'name': name})
        locations = {f'{loc.warehouse.code}/{loc.code}': loc
                     for loc in GroceryLocation.objects.select_related('warehouse')}

        self.stdout.write(f'  warehouses  {len(warehouses)}')
        self.stdout.write(f'  bins        {len(locations)}')
        return warehouses, locations

    def _items(self, categories, brands, units, locations):
        bin_by_code = {key.split('/')[1]: loc for key, loc in locations.items()}

        found = {}
        for row in ITEMS:
            (key, name, cat, brand, unit, gst, _hsn, reorder, minimum, maximum,
             shelf_life, expiry, standard, method, bin_code) = row

            item, _ = GroceryItem.objects.get_or_create(name=name, defaults={
                'category': categories[cat],
                'brand': brands.get(brand) if brand else None,
                'unit': units[unit],
                'gst_percent': D(str(gst)),
                'reorder_level': D(str(reorder)),
                'minimum_stock': D(str(minimum)),
                'maximum_stock': D(str(maximum)),
                'shelf_life_days': shelf_life,
                'expiry_required': expiry,
                'standard_cost': D(str(standard)),
                'valuation_method': (ValuationMethod.FIFO if method == 'FIFO'
                                     else ValuationMethod.WEIGHTED_AVERAGE),
                'storage_location': bin_by_code.get(bin_code),
            })
            found[key] = item

        self.stdout.write(f'  items       {len(found)}')
        return found

    # ── documents ───────────────────────────────────────────────────────────

    def _receipt(self, supplier, warehouse, day, lines, freight=0, invoice=''):
        """Create and post one GRN. ``lines`` is (item, qty, rate, bin, expiry_days)."""
        receipt = GroceryGoodsReceipt.objects.create(
            supplier=supplier, warehouse=warehouse, receipt_date=day,
            invoice_number=invoice, invoice_date=day,
            freight_amount=D(str(freight)),
        )
        for item, qty, rate, bin_code, expiry_days in lines:
            default_bin = item.storage_location
            GroceryGoodsReceiptLine.objects.create(
                receipt=receipt, item=item,
                location=(default_bin
                          if default_bin and default_bin.warehouse_id == warehouse.id
                          else None),
                received_quantity=D(str(qty)), accepted_quantity=D(str(qty)),
                unit_price=D(str(rate)), tax_percent=item.gst_percent,
                batch_number=f'B{day.strftime("%y%m")}-{item.pk:04d}',
                manufacturing_date=day - dt.timedelta(days=5),
                expiry_date=(day + dt.timedelta(days=expiry_days)
                             if expiry_days else None),
            )
        post_goods_receipt(receipt, 'demo-data')
        return receipt

    def _documents(self, items, suppliers, warehouses, locations):
        today = dt.date.today()
        main = warehouses['WH-MAIN']
        cold = warehouses['WH-COLD']
        store = warehouses['WH-STORE']

        def days_ago(n):
            return today - dt.timedelta(days=n)

        def shelf(code):
            """Expiry offset for an item, or None when it does not perish."""
            return items[code].shelf_life_days

        def bin_for(item, warehouse):
            """The item's default bin, but only when it lives in this warehouse.
            Receiving into WH-MAIN must not file stock in a WH-STORE bin."""
            loc = item.storage_location
            return loc if loc and loc.warehouse_id == warehouse.id else None

        def line(code, qty, rate):
            return (items[code], qty, rate, None, shelf(code))

        receipts = []

        # Spread across five months so the dashboard trend has a shape.
        receipts.append(self._receipt(
            suppliers['SUP-METRO'], main, days_ago(140), invoice='MC/2026/1181',
            freight=1200, lines=[
                line('GR-1001', 40, 1795), line('GR-1002', 60, 1280),
                line('GR-1101', 150, 158), line('GR-1102', 120, 132),
            ]))

        receipts.append(self._receipt(
            suppliers['SUP-ADANI'], main, days_ago(112), invoice='AWL/26/4402',
            freight=850, lines=[
                line('GR-1401', 40, 1710), line('GR-1402', 35, 962),
                line('GR-1403', 25, 1105),
            ]))

        receipts.append(self._receipt(
            suppliers['SUP-METRO'], main, days_ago(82), invoice='MC/2026/1298',
            freight=1450, lines=[
                line('GR-1001', 35, 1860), line('GR-1201', 70, 452),
                line('GR-1202', 45, 186), line('GR-1203', 90, 84),
                line('GR-1301', 120, 74), line('GR-1302', 90, 128),
            ]))

        receipts.append(self._receipt(
            suppliers['SUP-HUL'], main, days_ago(55), invoice='HUL/26/77120',
            freight=600, lines=[
                line('GR-2001', 45, 605), line('GR-2002', 200, 26),
                line('GR-2102', 130, 128), line('GR-2003', 50, 278),
            ]))

        receipts.append(self._receipt(
            suppliers['SUP-BRIT'], store, days_ago(38), invoice='BIL/26/9021',
            freight=280, lines=[
                line('GR-1701', 140, 43), line('GR-1702', 90, 56),
                line('GR-1801', 180, 85),
            ]))

        # Recent, and deliberately close to expiry so the alerts light up.
        receipts.append(self._receipt(
            suppliers['SUP-GCMMF'], cold, days_ago(4), invoice='AMUL/26/33418',
            freight=180, lines=[
                (items['GR-1501'], 260, D('26.20'), None, 5),
                (items['GR-1502'], 70, D('258'), None, 45),
                (items['GR-1503'], 55, D('131'), None, 25),
                (items['GR-1504'], 90, D('65'), None, 6),
            ]))

        receipts.append(self._receipt(
            suppliers['SUP-LOCAL'], main, days_ago(11), invoice='ST/1042',
            freight=350, lines=[
                line('GR-1003', 60, 408), line('GR-1004', 110, 49),
                line('GR-1304', 200, 22), line('GR-2201', 55, 512),
            ]))

        self.stdout.write(f'  receipts    {len(receipts)} posted')

        # ── Issues to departments, also spread over time ────────────────────
        kitchen = GroceryDepartment.objects.get(code='KITCHEN')
        housekeeping = GroceryDepartment.objects.get(code='HOUSEKEEPING')
        office = GroceryDepartment.objects.get(code='OFFICE')

        def issue(department, warehouse, day, rows, to='', purpose=''):
            doc = GroceryStockIssue.objects.create(
                department=department, warehouse=warehouse, issue_date=day,
                issued_to=to, purpose=purpose)
            for code, qty in rows:
                GroceryStockIssueLine.objects.create(
                    issue=doc, item=items[code], quantity=D(str(qty)),
                    location=bin_for(items[code], warehouse))
            post_stock_issue(doc, 'demo-data')
            return doc

        issues = [
            issue(kitchen, main, days_ago(95), [('GR-1001', 8), ('GR-1101', 30)],
                  'Head Chef', 'Monthly kitchen indent'),
            issue(kitchen, main, days_ago(70), [('GR-1401', 6), ('GR-1102', 24)],
                  'Head Chef', 'Monthly kitchen indent'),
            issue(kitchen, main, days_ago(45), [('GR-1001', 10), ('GR-1201', 18),
                                                ('GR-1301', 25)],
                  'Head Chef', 'Monthly kitchen indent'),
            issue(housekeeping, main, days_ago(40), [('GR-2001', 8), ('GR-2002', 45)],
                  'Housekeeping Supervisor', 'Cleaning supplies'),
            issue(kitchen, main, days_ago(20), [('GR-1002', 12), ('GR-1402', 5),
                                                ('GR-1302', 15)],
                  'Head Chef', 'Monthly kitchen indent'),
            issue(office, store, days_ago(15), [('GR-1801', 24)],
                  'Admin', 'Pantry restock — biscuits'),
            issue(office, main, days_ago(14), [('GR-2201', 6)],
                  'Admin', 'Pantry restock — tea'),
            issue(kitchen, cold, days_ago(2), [('GR-1501', 90), ('GR-1504', 30)],
                  'Head Chef', 'Daily dairy indent'),
            issue(housekeeping, main, days_ago(1), [('GR-2003', 6), ('GR-2002', 30)],
                  'Housekeeping Supervisor', 'Weekly cleaning'),
        ]
        self.stdout.write(f'  issues      {len(issues)} posted')

        # ── A stocktake, so the adjustment screens have content ─────────────
        adjustment = GroceryStockAdjustment.objects.create(
            warehouse=main, adjustment_date=days_ago(7),
            reason=AdjustmentReason.PHYSICAL_COUNT,
            reference='STOCKTAKE/2026/07',
            remarks='Quarterly cycle count, Rack A and B.')

        # Two shorts and one over — a realistic count sheet.
        for code, delta in (('GR-1101', -3), ('GR-1301', -5), ('GR-1304', 4)):
            stock = (GroceryStock.objects
                     .filter(item=items[code], warehouse=main).first())
            if stock:
                GroceryStockAdjustmentLine.objects.create(
                    adjustment=adjustment, item=items[code],
                    location=stock.location,
                    counted_quantity=max(D('0'), stock.quantity_available + D(str(delta))),
                    remarks='Counted on shelf')
        post_stock_adjustment(adjustment, 'demo-data')
        self.stdout.write('  adjustment  1 posted (quarterly stocktake)')

        # ── Drive a few items below reorder, and one to zero ────────────────
        # Without this every row is healthy, so the low-stock KPI, the LOW/
        # OUT_OF_STOCK filters and the reorder alerts all read zero and look
        # broken rather than simply quiet. Quantities are computed from live
        # stock so the result is exact whatever the earlier documents did.
        def drain_to(code, target, day, department, purpose):
            item = items[code]
            stock = (GroceryStock.objects
                     .filter(item=item, warehouse=main).first())
            if not stock:
                return None
            take = stock.quantity_available - D(str(target))
            if take <= 0:
                return None
            doc = GroceryStockIssue.objects.create(
                department=department, warehouse=main, issue_date=day,
                issued_to='Head Chef', purpose=purpose)
            GroceryStockIssueLine.objects.create(
                issue=doc, item=item, quantity=take,
                location=bin_for(item, main))
            post_stock_issue(doc, 'demo-data')
            return doc

        low = [
            # Just under the reorder level → LOW.
            drain_to('GR-1101', 40, days_ago(6), kitchen, 'Bulk cooking — dal'),
            drain_to('GR-1302', 30, days_ago(5), kitchen, 'Bulk cooking — masala'),
            # Nothing left → OUT_OF_STOCK.
            drain_to('GR-1004', 0, days_ago(3), kitchen, 'Cleared remaining poha'),
        ]
        self.stdout.write(
            f'  low stock   {len([d for d in low if d])} issues to trigger reorder alerts')

        # A couple of drafts, so the list screens show both states.
        draft = GroceryGoodsReceipt.objects.create(
            supplier=suppliers['SUP-METRO'], warehouse=main, receipt_date=today,
            invoice_number='MC/2026/1402', invoice_date=today,
            freight_amount=D('900'), remarks='Awaiting quality check.')
        for code, qty, rate in (('GR-1001', 25, 1880), ('GR-1201', 40, 470)):
            GroceryGoodsReceiptLine.objects.create(
                receipt=draft, item=items[code], location=bin_for(items[code], main),
                received_quantity=D(str(qty)), accepted_quantity=D(str(qty)),
                unit_price=D(str(rate)), tax_percent=items[code].gst_percent,
                batch_number=f'B{today.strftime("%y%m")}-{code[-4:]}',
                expiry_date=(today + dt.timedelta(days=items[code].shelf_life_days)
                             if items[code].shelf_life_days else None))

        draft_issue = GroceryStockIssue.objects.create(
            department=kitchen, warehouse=main, issue_date=today,
            issued_to='Head Chef', purpose='Tomorrow’s indent — not yet released')
        GroceryStockIssueLine.objects.create(
            issue=draft_issue, item=items['GR-1002'], quantity=D('6'),
            location=bin_for(items['GR-1002'], main))

        self.stdout.write('  drafts      1 receipt + 1 issue left unposted')

    # ── teardown ────────────────────────────────────────────────────────────

    def _flush(self):
        """
        Remove everything the command creates, children first.

        Hard deletes via all_objects — the audit base soft-deletes by default,
        and a soft-deleted ledger would still block a clean re-run.
        """
        self.stdout.write(self.style.WARNING('Removing grocery demo data…'))

        with transaction.atomic():
            steps = [
                ('stock transactions', GroceryStockTransaction),
                ('cost history', GroceryItemCostHistory),
                ('receipt lines', GroceryGoodsReceiptLine),
                ('issue lines', GroceryStockIssueLine),
                ('adjustment lines', GroceryStockAdjustmentLine),
                ('receipts', GroceryGoodsReceipt),
                ('issues', GroceryStockIssue),
                ('adjustments', GroceryStockAdjustment),
                ('batches', GroceryStockBatch),
                ('stock rows', GroceryStock),
                ('valuations', GroceryItemValuation),
                ('items', GroceryItem),
                ('bin locations', GroceryLocation),
                ('warehouses', GroceryWarehouse),
                ('suppliers', GrocerySupplier),
                ('brands', GroceryBrand),
            ]
            for label, model in steps:
                count = model.all_objects.count()
                model.all_objects.all().delete()
                self.stdout.write(f'  {label:<20} {count} removed')

            # Categories and units are seeded by migration 0002 and may be
            # referenced by data that is not ours, so only the two this command
            # adds are removed.
            removed, _ = GroceryCategory.all_objects.filter(
                code__in=[c[0] for c in EXTRA_CATEGORIES]).delete()
            self.stdout.write(f'  {"demo categories":<20} {removed} removed')

        self.stdout.write(self.style.SUCCESS(
            '\nDemo data removed. Seeded categories, units, departments and the '
            'seven role groups were left in place.'))

    def _summary(self):
        stock_lines = GroceryStock.objects.filter(quantity_available__gt=0).count()
        value = sum((v.total_value for v in GroceryItemValuation.objects.all()), D('0'))
        movements = GroceryStockTransaction.objects.count()

        self.stdout.write(self.style.SUCCESS('\nDemo data ready.'))
        self.stdout.write(f'  stock lines with quantity : {stock_lines}')
        self.stdout.write(f'  inventory value           : ₹{value:,.2f}')
        self.stdout.write(f'  ledger movements          : {movements}')
        self.stdout.write('\nOpen /grocery/dashboard/ to see it.')
        self.stdout.write(self.style.WARNING(
            'To remove: python manage.py grocery_demo_data --flush'))
