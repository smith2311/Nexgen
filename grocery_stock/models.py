"""
Grocery Stock & Cost Management — master data
─────────────────────────────────────────────────────────────────────────────
Covers sections 2, 3, 4 and 9 of the functional specification, plus the
warehouse/bin masters that sections 6, 7 and 10 depend on.

Conventions followed from the rest of the codebase:

  * Every model inherits ``AuditSoftDeleteModel`` (logistics_performance),
    which supplies created_at / updated_at / created_by / updated_by and the
    is_deleted / deleted_by / deleted_at soft-delete trio. created_by and
    updated_by are stamped automatically by CurrentUserMiddleware — do not
    set them by hand, and never call obj.delete(); call obj.soft_delete().
  * Model names carry a ``Grocery`` prefix, matching Vat*/Visa*/Billing*.
  * ``db_table`` is set explicitly with a ``grocery_`` prefix, matching
    vat_* / billing_* / sub_* elsewhere. The names mirror the table list on
    the last page of the specification, namespaced so they cannot collide
    with another module's tables in the shared database.

NUMERIC PRECISION (decision D10) — fixed across every module of this app so
later phases stay consistent. Never use a float for a quantity or an amount:

    quantities  14 digits, 3 decimal places   e.g.   1234.500 KG
    unit costs  14 digits, 4 decimal places   e.g.     57.5000
    amounts     16 digits, 2 decimal places   e.g.  16750.00
"""
from django.core.validators import MinValueValidator
from django.db import models

from logistics_performance.models import AuditSoftDeleteModel

# ── Precision contract (D10) ────────────────────────────────────────────────
QTY_DIGITS, QTY_DP = 14, 3
COST_DIGITS, COST_DP = 14, 4
AMOUNT_DIGITS, AMOUNT_DP = 16, 2


def qty_field(**kwargs):
    kwargs.setdefault('default', 0)
    kwargs.setdefault('validators', [MinValueValidator(0)])
    return models.DecimalField(max_digits=QTY_DIGITS, decimal_places=QTY_DP, **kwargs)


def cost_field(**kwargs):
    kwargs.setdefault('default', 0)
    kwargs.setdefault('validators', [MinValueValidator(0)])
    return models.DecimalField(max_digits=COST_DIGITS, decimal_places=COST_DP, **kwargs)


def amount_field(**kwargs):
    """
    Money. Unlike qty_field/cost_field this does NOT default to non-negative:
    a ledger line for an issue carries a negative value, and a write-down can
    legitimately reduce inventory value.
    """
    kwargs.setdefault('default', 0)
    return models.DecimalField(max_digits=AMOUNT_DIGITS, decimal_places=AMOUNT_DP, **kwargs)


# ── Choices ─────────────────────────────────────────────────────────────────

class ValuationMethod(models.TextChoices):
    """
    Specification section 8. Weighted average is the default; FIFO is a
    per-item override (decision D2). LIFO is deliberately not offered — it is
    disallowed under Ind AS, and adding it later is additive, not breaking.
    """
    WEIGHTED_AVERAGE = 'WAC', 'Weighted Average Cost'
    FIFO = 'FIFO', 'First In, First Out'


# ── Category (specification section 3) ──────────────────────────────────────

class GroceryCategory(AuditSoftDeleteModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_category'
        ordering = ['name']
        verbose_name = 'Grocery Category'
        verbose_name_plural = 'Grocery Categories'

    def __str__(self):
        return self.name


# ── Brand ───────────────────────────────────────────────────────────────────

class GroceryBrand(AuditSoftDeleteModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_brand'
        ordering = ['name']
        verbose_name = 'Grocery Brand'
        verbose_name_plural = 'Grocery Brands'

    def __str__(self):
        return self.name


# ── Unit of measure ─────────────────────────────────────────────────────────

class GroceryUnit(AuditSoftDeleteModel):
    """
    Unit of measure — KG, L, PCS, BAG and so on.

    ``base_unit`` and ``conversion_factor`` are present but nullable so that
    buy-in-bags / consume-in-kilograms (decision D8) can be switched on later
    as a data change rather than a schema change. Leaving them null means the
    unit stands alone, which is how every unit behaves until D8 is answered.
    Nothing in the costing services reads them yet.
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    base_unit = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='derived_units',
        help_text='Leave blank unless this unit converts into another one.',
    )
    conversion_factor = cost_field(
        null=True, blank=True, default=None,
        help_text='How many base units one of this unit contains. 1 BAG = 25 KG → 25.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_unit'
        ordering = ['code']
        verbose_name = 'Grocery Unit'
        verbose_name_plural = 'Grocery Units'

    def __str__(self):
        return self.code


# ── Supplier (specification section 4) ──────────────────────────────────────

class GrocerySupplier(AuditSoftDeleteModel):
    # The name is the identity — the module keeps no supplier code, so it has
    # to be unique for pickers and documents to distinguish vendors.
    name = models.CharField(max_length=200, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=150, blank=True)
    credit_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_supplier'
        ordering = ['name']
        verbose_name = 'Grocery Supplier'
        verbose_name_plural = 'Grocery Suppliers'

    def __str__(self):
        return self.name


# ── Warehouse and bin location ──────────────────────────────────────────────

class GroceryWarehouse(AuditSoftDeleteModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_warehouse'
        ordering = ['name']
        verbose_name = 'Grocery Warehouse'
        verbose_name_plural = 'Grocery Warehouses'

    def __str__(self):
        return self.name


class GroceryLocation(AuditSoftDeleteModel):
    """Bin location inside a warehouse (specification section 6)."""
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='locations',
    )
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_location'
        ordering = ['warehouse__name', 'code']
        verbose_name = 'Grocery Bin Location'
        verbose_name_plural = 'Grocery Bin Locations'
        constraints = [
            models.UniqueConstraint(
                fields=['warehouse', 'code'],
                name='grocery_location_unique_code_per_warehouse',
            ),
        ]

    def __str__(self):
        return f'{self.warehouse.code}/{self.code}'


# ── Consuming department (specification section 9) ───────────────────────────

class GroceryDepartment(AuditSoftDeleteModel):
    """
    A department that consumes stock — Kitchen, Housekeeping, Office,
    Production, Laboratory. This is the dimension every cost-per-department
    report groups by, so it is a master rather than a free-text field.
    """
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_department'
        ordering = ['name']
        verbose_name = 'Grocery Department'
        verbose_name_plural = 'Grocery Departments'

    def __str__(self):
        return self.name


# ── Item master (specification section 2) ───────────────────────────────────

class GroceryItem(AuditSoftDeleteModel):
    """
    Every field named in specification section 2, plus three that the costing
    module in section 8 needs to be able to read per item.
    """
    # Identity — the name is the identity; the module keeps no item code or
    # barcode, so the name must be unique for pickers and documents to
    # distinguish items.
    name = models.CharField(max_length=200, unique=True)
    local_name = models.CharField(max_length=200, blank=True)

    # Classification
    category = models.ForeignKey(
        GroceryCategory, on_delete=models.PROTECT, related_name='items',
    )
    brand = models.ForeignKey(
        GroceryBrand, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='items',
    )
    unit = models.ForeignKey(
        GroceryUnit, on_delete=models.PROTECT, related_name='items',
    )

    # Tax
    gst_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )

    # Stock control levels
    reorder_level = qty_field()
    minimum_stock = qty_field()
    maximum_stock = qty_field()

    # Shelf life and expiry (specification section 13)
    shelf_life_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Blank when the item does not perish.',
    )
    expiry_required = models.BooleanField(
        default=False,
        help_text='When set, a goods receipt cannot be posted without an expiry date.',
    )

    # Default storage
    storage_location = models.ForeignKey(
        GroceryLocation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='default_items',
    )

    # Costing (specification section 8)
    standard_cost = cost_field(
        help_text='Benchmark cost. The cost-variance report compares actual against this.',
    )
    valuation_method = models.CharField(
        max_length=8, choices=ValuationMethod.choices,
        default=ValuationMethod.WEIGHTED_AVERAGE,
    )
    is_gst_creditable = models.BooleanField(
        default=True,
        help_text=(
            'When true, GST is claimed as input credit and is EXCLUDED from '
            'inventory cost. When false, GST forms part of landed cost. '
            'This is the flag for decision D1 — confirm it before the first '
            'goods receipt is posted, because it moves the whole valuation.'
        ),
    )

    # Status (section 2 "Status")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'grocery_item'
        ordering = ['name']
        verbose_name = 'Grocery Item'
        verbose_name_plural = 'Grocery Items'
        indexes = [
            models.Index(fields=['category'], name='grocery_item_category_idx'),
            models.Index(fields=['is_active'], name='grocery_item_active_idx'),
        ]

    def __str__(self):
        return self.name


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — TRANSACTIONS
# Specification sections 6 (GRN), 7 (Inventory), 8 (Cost) and 9 (Consumption).
#
# Three rules hold everywhere below and are enforced in services/posting.py:
#
#   1. A document is DRAFT until posted. Only posting moves stock, and a
#      document can be posted exactly once.
#   2. GroceryStockTransaction is append-only. It is the audit trail, so
#      nothing edits or deletes a row once written — a mistake is corrected by
#      posting a reversing document, never by rewriting history.
#   3. Stock rows are locked with select_for_update during posting, so two
#      concurrent issues cannot both pass the same "enough stock?" check.
# ═══════════════════════════════════════════════════════════════════════════


class DocumentStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    CANCELLED = 'CANCELLED', 'Cancelled'


class StockMovementType(models.TextChoices):
    """
    Every way stock can move (specification section 7 "Features").

    Phase 2 posts RECEIPT and ISSUE. The rest are declared now so the ledger
    schema does not change when transfers and adjustments arrive — adding a
    movement type later would otherwise mean migrating a live audit table.
    """
    RECEIPT = 'RECEIPT', 'Goods Receipt'
    ISSUE = 'ISSUE', 'Department Issue'
    ADJUSTMENT = 'ADJUSTMENT', 'Stock Adjustment'
    TRANSFER_IN = 'TRANSFER_IN', 'Transfer In'
    TRANSFER_OUT = 'TRANSFER_OUT', 'Transfer Out'
    DAMAGE = 'DAMAGE', 'Damage Write-off'
    EXPIRY = 'EXPIRY', 'Expiry Write-off'


# ── Document numbering ──────────────────────────────────────────────────────

class GroceryDocumentSequence(models.Model):
    """
    Per-prefix, per-year counter behind GRN-2026-0001 style numbers.

    A dedicated row locked with select_for_update is used rather than
    ``MAX(number) + 1``: under two concurrent posts the MAX approach hands the
    same number to both, and the unique constraint then fails the second one
    after its work is done.

    Not audited or soft-deletable — it is plumbing, not business data.
    """
    prefix = models.CharField(max_length=10)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'grocery_document_sequence'
        constraints = [
            models.UniqueConstraint(fields=['prefix', 'year'],
                                    name='grocery_docseq_unique_prefix_year'),
        ]

    def __str__(self):
        return f'{self.prefix}-{self.year}: {self.last_number}'


# ── Valuation (specification section 8) ─────────────────────────────────────

class GroceryItemValuation(AuditSoftDeleteModel):
    """
    Running inventory position and cost for one item, across all warehouses.

    Average cost is deliberately held company-wide rather than per warehouse.
    Weighted average is an accounting figure and the same item must not be
    valued at two different costs in one balance sheet; a per-warehouse average
    would do exactly that as soon as stock moved between sites.

    ``total_value`` is the source of truth and ``average_cost`` is derived from
    it on every write, so rounding never compounds: recomputing the average
    from a rounded average is what makes long-running WAC drift.
    """
    item = models.OneToOneField(
        GroceryItem, on_delete=models.PROTECT, related_name='valuation',
    )
    quantity_on_hand = qty_field()
    total_value = amount_field()
    average_cost = cost_field()

    last_purchase_cost = cost_field(
        help_text='Landed unit cost of the most recent receipt.',
    )
    last_purchase_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'grocery_item_valuation'
        ordering = ['item__name']
        verbose_name = 'Grocery Item Valuation'
        verbose_name_plural = 'Grocery Item Valuations'

    def __str__(self):
        return f'{self.item.name}: {self.quantity_on_hand} @ {self.average_cost}'


class GroceryItemCostHistory(AuditSoftDeleteModel):
    """
    Append-only log of every change to an item's average cost.

    Feeds the Cost Variance and Price History reports in section 15, and makes
    "why did this item's value jump?" answerable without replaying the ledger.
    """
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='cost_history',
    )
    changed_on = models.DateTimeField(auto_now_add=True)

    previous_average_cost = cost_field()
    new_average_cost = cost_field()
    previous_quantity = qty_field()
    new_quantity = qty_field()

    movement_type = models.CharField(max_length=16, choices=StockMovementType.choices)
    reference_number = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = 'grocery_item_cost_history'
        ordering = ['-changed_on', '-id']
        verbose_name = 'Grocery Item Cost History'
        verbose_name_plural = 'Grocery Item Cost History'
        indexes = [
            models.Index(fields=['item', '-changed_on'], name='grocery_costhist_item_idx'),
        ]

    def __str__(self):
        return f'{self.item.name}: {self.previous_average_cost} → {self.new_average_cost}'


# ── Batches / cost layers (sections 6 and 13) ───────────────────────────────

class GroceryStockBatch(AuditSoftDeleteModel):
    """
    One receipt of one item — a batch for expiry tracking and a cost layer
    for FIFO, which are the same physical thing.

    ``batch_number`` is intentionally NOT unique. Receiving the same supplier
    batch twice at different prices has to produce two layers, otherwise FIFO
    cannot tell the two costs apart. Identity here is the receipt, not the
    printed batch code.
    """
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='batches',
    )
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='batches',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='batches',
    )
    supplier = models.ForeignKey(
        GrocerySupplier, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='batches',
    )

    batch_number = models.CharField(max_length=60, blank=True)
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    received_quantity = qty_field()
    remaining_quantity = qty_field()
    unit_cost = cost_field(
        help_text='Landed cost per unit for this layer — see services/costing.py.',
    )

    received_on = models.DateField()
    reference_number = models.CharField(
        max_length=40, blank=True,
        help_text='GRN number this layer came from.',
    )

    class Meta:
        db_table = 'grocery_stock_batch'
        # FEFO: consume the earliest expiry first, falling back to the earliest
        # receipt when an item is not expiry-tracked. Postgres sorts NULLs last
        # by default on ASC, which is what we want — undated stock is used only
        # after dated stock.
        ordering = ['expiry_date', 'received_on', 'id']
        verbose_name = 'Grocery Stock Batch'
        verbose_name_plural = 'Grocery Stock Batches'
        indexes = [
            models.Index(fields=['item', 'warehouse'], name='grocery_batch_item_wh_idx'),
            models.Index(fields=['expiry_date'], name='grocery_batch_expiry_idx'),
            models.Index(fields=['remaining_quantity'], name='grocery_batch_remaining_idx'),
        ]

    def __str__(self):
        label = self.batch_number or f'#{self.pk}'
        return f'{self.item.name} / {label}'

    @property
    def is_exhausted(self):
        return self.remaining_quantity <= 0


# ── Current stock (specification section 7) ─────────────────────────────────

class GroceryStock(AuditSoftDeleteModel):
    """
    Live quantity for one item in one place.

    Kept as a maintained balance rather than summed from the ledger on every
    read: "do we have enough?" runs on every issue line, and summing a
    ledger that grows forever would get slower every month. The ledger stays
    the audit trail; this is the index into it.
    """
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='stock_rows',
    )
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='stock_rows',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='stock_rows',
    )

    quantity_available = qty_field()
    quantity_reserved = qty_field(
        help_text='Committed to an approved requisition but not yet issued.',
    )
    quantity_blocked = qty_field(
        help_text='Quarantined — damaged, expired or awaiting inspection.',
    )
    quantity_in_transit = qty_field(
        help_text='Dispatched on a transfer but not yet received at the destination.',
    )

    class Meta:
        db_table = 'grocery_stock'
        ordering = ['item__name', 'warehouse__name']
        verbose_name = 'Grocery Stock'
        verbose_name_plural = 'Grocery Stock'
        constraints = [
            # nulls_distinct=False so a warehouse-level row (location NULL)
            # cannot be created twice. Without it Postgres treats every NULL as
            # unique and the same item silently gets two balances in one
            # warehouse.
            models.UniqueConstraint(
                fields=['item', 'warehouse', 'location'],
                name='grocery_stock_unique_item_wh_loc',
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=['item'], name='grocery_stock_item_idx'),
            models.Index(fields=['warehouse'], name='grocery_stock_wh_idx'),
        ]

    def __str__(self):
        return f'{self.item.name} @ {self.warehouse.code}: {self.quantity_available}'


# ── Stock ledger (specification section 15 "Stock Ledger") ──────────────────

class GroceryStockTransaction(AuditSoftDeleteModel):
    """
    Append-only ledger. One row per item, per place, per movement.

    ``quantity`` and ``value`` are signed — positive in, negative out — so a
    balance is a SUM with no CASE on movement type, and a reversal is simply
    the same row with the sign flipped.

    An issue that draws on three batches writes three rows, one per batch, so
    this table doubles as the batch-allocation record and no separate
    allocation table is needed.
    """
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='transactions',
    )
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='transactions',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='transactions',
    )
    batch = models.ForeignKey(
        GroceryStockBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name='transactions',
    )
    department = models.ForeignKey(
        GroceryDepartment, on_delete=models.PROTECT, null=True, blank=True,
        related_name='transactions',
        help_text='Set on an issue — this is what section 9 groups cost by.',
    )

    movement_type = models.CharField(max_length=16, choices=StockMovementType.choices)
    moved_on = models.DateField()

    quantity = models.DecimalField(
        max_digits=QTY_DIGITS, decimal_places=QTY_DP,
        help_text='Signed: positive increases stock, negative decreases it.',
    )
    unit_cost = cost_field()
    value = amount_field(help_text='Signed, and always quantity × unit_cost.')

    balance_quantity = qty_field(
        help_text='Quantity in this item/warehouse/bin immediately after the move.',
    )

    # Deliberately a loose reference rather than a FK per document type: the
    # ledger must outlive any one document table, and a GenericForeignKey would
    # make the stock-ledger report un-joinable in plain SQL.
    reference_type = models.CharField(max_length=16, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    reference_number = models.CharField(max_length=40, blank=True)

    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'grocery_stock_transaction'
        ordering = ['-moved_on', '-id']
        verbose_name = 'Grocery Stock Transaction'
        verbose_name_plural = 'Grocery Stock Transactions'
        indexes = [
            models.Index(fields=['item', '-moved_on'], name='grocery_txn_item_date_idx'),
            models.Index(fields=['warehouse', '-moved_on'], name='grocery_txn_wh_date_idx'),
            models.Index(fields=['movement_type'], name='grocery_txn_type_idx'),
            models.Index(fields=['reference_type', 'reference_id'],
                         name='grocery_txn_reference_idx'),
            models.Index(fields=['department'], name='grocery_txn_dept_idx'),
        ]

    def __str__(self):
        return f'{self.movement_type} {self.item.name} {self.quantity}'


# ── Goods Receipt Note (specification section 6) ────────────────────────────

class GroceryGoodsReceipt(AuditSoftDeleteModel):
    """
    GRN header. Charges here are apportioned across the lines at posting time
    to build the landed cost required by section 8.
    """
    # NULL until posted, never ''. A unique column holding '' collides on the
    # second draft, because Postgres treats the empty string as a real value —
    # only NULLs are exempt from a unique index. The number is assigned at
    # posting so abandoned drafts do not burn numbers and the posted series has
    # no gaps for an auditor to ask about.
    grn_number = models.CharField(
        max_length=40, unique=True, null=True, blank=True, default=None,
    )
    status = models.CharField(
        max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT,
    )

    supplier = models.ForeignKey(
        GrocerySupplier, on_delete=models.PROTECT, related_name='goods_receipts',
    )
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='goods_receipts',
    )

    receipt_date = models.DateField()
    invoice_number = models.CharField(max_length=60, blank=True)
    invoice_date = models.DateField(null=True, blank=True)

    # Section 8: Product Cost + GST + Freight + Loading + Insurance + Other
    freight_amount = amount_field()
    loading_amount = amount_field()
    insurance_amount = amount_field()
    other_charges = amount_field()

    # Filled in at posting so a posted GRN never needs recomputing to display.
    subtotal_amount = amount_field()
    tax_amount = amount_field()
    total_amount = amount_field()
    inventory_value = amount_field(
        help_text='What actually went into stock — excludes creditable GST.',
    )

    remarks = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = 'grocery_goods_receipt'
        ordering = ['-receipt_date', '-id']
        verbose_name = 'Grocery Goods Receipt'
        verbose_name_plural = 'Grocery Goods Receipts'
        indexes = [
            models.Index(fields=['status'], name='grocery_grn_status_idx'),
            models.Index(fields=['-receipt_date'], name='grocery_grn_date_idx'),
            models.Index(fields=['supplier'], name='grocery_grn_supplier_idx'),
        ]

    def __str__(self):
        return self.grn_number or f'GRN draft #{self.pk}'

    @property
    def is_editable(self):
        return self.status == DocumentStatus.DRAFT

    @property
    def total_charges(self):
        return (self.freight_amount + self.loading_amount
                + self.insurance_amount + self.other_charges)


class GroceryGoodsReceiptLine(AuditSoftDeleteModel):
    """
    One item on a GRN.

    Received / accepted / rejected are captured separately because only the
    accepted quantity becomes stock, while the rejected quantity is what a
    supplier-quality report needs.
    """
    receipt = models.ForeignKey(
        GroceryGoodsReceipt, on_delete=models.CASCADE, related_name='lines',
    )
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='receipt_lines',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='receipt_lines',
    )

    batch_number = models.CharField(max_length=60, blank=True)
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    received_quantity = qty_field()
    accepted_quantity = qty_field()
    rejected_quantity = qty_field()

    unit_price = cost_field()
    discount_amount = amount_field()
    tax_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )

    # Computed by services/costing.py — stored so a posted line is a record of
    # what was actually charged, not a formula re-evaluated against today's
    # master data.
    taxable_amount = amount_field()
    tax_amount = amount_field()
    line_total = amount_field()
    apportioned_charges = amount_field()
    landed_unit_cost = cost_field()

    batch = models.ForeignKey(
        GroceryStockBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='receipt_lines',
        help_text='The cost layer this line created, set at posting.',
    )

    class Meta:
        db_table = 'grocery_goods_receipt_line'
        ordering = ['id']
        verbose_name = 'Grocery Goods Receipt Line'
        verbose_name_plural = 'Grocery Goods Receipt Lines'
        indexes = [
            models.Index(fields=['receipt'], name='grocery_grnline_receipt_idx'),
            models.Index(fields=['item'], name='grocery_grnline_item_idx'),
        ]

    def __str__(self):
        return f'{self.item.name} × {self.accepted_quantity}'


# ── Stock issue / consumption (specification section 9) ─────────────────────

class GroceryStockIssue(AuditSoftDeleteModel):
    """Issue header — stock leaving a warehouse for a consuming department."""
    # NULL until posted — see the note on GroceryGoodsReceipt.grn_number.
    issue_number = models.CharField(
        max_length=40, unique=True, null=True, blank=True, default=None,
    )
    status = models.CharField(
        max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT,
    )

    department = models.ForeignKey(
        GroceryDepartment, on_delete=models.PROTECT, related_name='stock_issues',
    )
    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='stock_issues',
    )

    issue_date = models.DateField()
    issued_to = models.CharField(max_length=150, blank=True)
    purpose = models.CharField(max_length=255, blank=True)

    total_value = amount_field(
        help_text='Cost of everything issued — the figure section 9 reports per department.',
    )

    remarks = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = 'grocery_stock_issue'
        ordering = ['-issue_date', '-id']
        verbose_name = 'Grocery Stock Issue'
        verbose_name_plural = 'Grocery Stock Issues'
        indexes = [
            models.Index(fields=['status'], name='grocery_issue_status_idx'),
            models.Index(fields=['-issue_date'], name='grocery_issue_date_idx'),
            models.Index(fields=['department'], name='grocery_issue_dept_idx'),
        ]

    def __str__(self):
        return self.issue_number or f'Issue draft #{self.pk}'

    @property
    def is_editable(self):
        return self.status == DocumentStatus.DRAFT


class GroceryStockIssueLine(AuditSoftDeleteModel):
    """
    One item on an issue.

    No batch field: a single line can draw on several batches, and which ones
    is decided at posting by the FEFO picker. Those allocations are recorded as
    ledger rows, each carrying its batch.
    """
    issue = models.ForeignKey(
        GroceryStockIssue, on_delete=models.CASCADE, related_name='lines',
    )
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='issue_lines',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='issue_lines',
    )

    quantity = qty_field()

    # Set at posting from the valuation method on the item.
    unit_cost = cost_field()
    line_value = amount_field()

    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'grocery_stock_issue_line'
        ordering = ['id']
        verbose_name = 'Grocery Stock Issue Line'
        verbose_name_plural = 'Grocery Stock Issue Lines'
        indexes = [
            models.Index(fields=['issue'], name='grocery_issueline_issue_idx'),
            models.Index(fields=['item'], name='grocery_issueline_item_idx'),
        ]

    def __str__(self):
        return f'{self.item.name} × {self.quantity}'


# ── Stock adjustment (specification section 11) ─────────────────────────────

class AdjustmentReason(models.TextChoices):
    """The five reasons named in section 11."""
    PHYSICAL_COUNT = 'PHYSICAL_COUNT', 'Physical Count'
    DAMAGE = 'DAMAGE', 'Damage'
    THEFT = 'THEFT', 'Theft'
    EXPIRY = 'EXPIRY', 'Expiry'
    BREAKAGE = 'BREAKAGE', 'Breakage'


# Which ledger movement each reason writes. Damage and breakage are the same
# event to the ledger; theft and a recount are both plain corrections. Keeping
# the business reason separate from the ledger type means a report can group by
# either without one dictating the other.
REASON_MOVEMENT = {
    AdjustmentReason.PHYSICAL_COUNT: StockMovementType.ADJUSTMENT,
    AdjustmentReason.THEFT: StockMovementType.ADJUSTMENT,
    AdjustmentReason.DAMAGE: StockMovementType.DAMAGE,
    AdjustmentReason.BREAKAGE: StockMovementType.DAMAGE,
    AdjustmentReason.EXPIRY: StockMovementType.EXPIRY,
}


class GroceryStockAdjustment(AuditSoftDeleteModel):
    """
    Correct the book quantity to match reality (specification section 11).

    The reason lives on the header because one document is one event — a
    stocktake, or a pallet that was dropped. Mixing a recount and a theft in
    one document would make the audit trail ambiguous about what happened.
    """
    # NULL until posted — see the note on GroceryGoodsReceipt.grn_number.
    adjustment_number = models.CharField(
        max_length=40, unique=True, null=True, blank=True, default=None,
    )
    status = models.CharField(
        max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT,
    )

    warehouse = models.ForeignKey(
        GroceryWarehouse, on_delete=models.PROTECT, related_name='adjustments',
    )
    reason = models.CharField(
        max_length=16, choices=AdjustmentReason.choices,
        default=AdjustmentReason.PHYSICAL_COUNT,
    )
    adjustment_date = models.DateField()
    reference = models.CharField(
        max_length=100, blank=True,
        help_text='Stocktake sheet number, incident report, and so on.',
    )

    total_value = amount_field(
        help_text='Signed: negative when the correction writes stock down.',
    )

    remarks = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = 'grocery_stock_adjustment'
        ordering = ['-adjustment_date', '-id']
        verbose_name = 'Grocery Stock Adjustment'
        verbose_name_plural = 'Grocery Stock Adjustments'
        indexes = [
            models.Index(fields=['status'], name='grocery_adj_status_idx'),
            models.Index(fields=['-adjustment_date'], name='grocery_adj_date_idx'),
            models.Index(fields=['reason'], name='grocery_adj_reason_idx'),
        ]

    def __str__(self):
        return self.adjustment_number or f'Adjustment draft #{self.pk}'

    @property
    def is_editable(self):
        return self.status == DocumentStatus.DRAFT

    @property
    def movement_type(self):
        return REASON_MOVEMENT.get(self.reason, StockMovementType.ADJUSTMENT)


class GroceryStockAdjustmentLine(AuditSoftDeleteModel):
    """
    One item being corrected.

    The user enters ``counted_quantity`` — what is physically there — rather
    than a delta. It reads the same whether the cause is a stocktake or five
    broken jars, and it is the number a person can actually verify on the
    shelf.

    ``system_quantity`` and ``difference`` are filled at posting from the
    locked stock row, not from whatever the screen showed when the draft was
    typed. A draft saved this morning must not write this morning's balance
    over an afternoon of receipts — the whole point of an adjustment is to land
    on the counted figure.
    """
    adjustment = models.ForeignKey(
        GroceryStockAdjustment, on_delete=models.CASCADE, related_name='lines',
    )
    item = models.ForeignKey(
        GroceryItem, on_delete=models.PROTECT, related_name='adjustment_lines',
    )
    location = models.ForeignKey(
        GroceryLocation, on_delete=models.PROTECT, null=True, blank=True,
        related_name='adjustment_lines',
    )
    batch = models.ForeignKey(
        GroceryStockBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name='adjustment_lines',
        help_text='Target one layer — e.g. writing off a specific expired batch. '
                  'Leave blank to let the write-down follow FEFO.',
    )

    counted_quantity = qty_field(help_text='The quantity physically present.')

    # Written at posting.
    system_quantity = qty_field()
    difference = models.DecimalField(
        max_digits=QTY_DIGITS, decimal_places=QTY_DP, default=0,
        help_text='counted − system. Negative writes stock down.',
    )
    unit_cost = cost_field()
    line_value = amount_field()

    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'grocery_stock_adjustment_line'
        ordering = ['id']
        verbose_name = 'Grocery Stock Adjustment Line'
        verbose_name_plural = 'Grocery Stock Adjustment Lines'
        indexes = [
            models.Index(fields=['adjustment'], name='grocery_adjline_adj_idx'),
            models.Index(fields=['item'], name='grocery_adjline_item_idx'),
        ]

    def __str__(self):
        return f'{self.item.name}: {self.system_quantity} → {self.counted_quantity}'
