"""
Grocery Stock — posting engine
─────────────────────────────────────────────────────────────────────────────
Posting is the only thing in this module that moves stock. Everything else
reads. Each function here does the whole job or none of it.

Three invariants, enforced rather than assumed:

  ATOMIC     Every post runs inside one transaction. A GRN that updates six
             stock rows and fails on the seventh must leave nothing behind.

  ONCE       A document is posted exactly once. The status guard runs inside
             the transaction against a locked row, so two clicks on Post
             cannot both get past it.

  ORDERED    Rows are locked in a deterministic order — item id, then
             warehouse, then location. Two documents touching the same two
             items in opposite orders would otherwise deadlock under load.

Posting never edits history. A mistake is corrected by posting a reversal, so
GroceryStockTransaction stays append-only and the ledger always reconstructs.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from ..models import (
    DocumentStatus,
    GroceryDocumentSequence,
    GroceryItemCostHistory,
    GroceryItemValuation,
    GroceryStock,
    GroceryStockBatch,
    GroceryStockTransaction,
    StockMovementType,
)
from .costing import (
    InsufficientStock,
    ZERO,
    allocate_batches,
    apply_issue_to_valuation,
    apply_receipt_to_valuation,
    issue_unit_cost,
    price_goods_receipt,
    q_amount,
    q_cost,
    q_qty,
)

logger = logging.getLogger(__name__)


class PostingError(Exception):
    """A document cannot be posted as it stands."""


# ── Document numbering ──────────────────────────────────────────────────────

def next_document_number(prefix, year=None):
    """
    Reserve the next number for a prefix and year, e.g. ``GRN-2026-0001``.

    Locks the counter row, so concurrent posts queue instead of colliding.
    Must be called inside a transaction — the lock is meaningless otherwise.
    """
    year = year or timezone.localdate().year

    sequence, _ = GroceryDocumentSequence.objects.get_or_create(
        prefix=prefix, year=year,
    )
    sequence = (GroceryDocumentSequence.objects
                .select_for_update()
                .get(pk=sequence.pk))

    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])

    return f'{prefix}-{year}-{sequence.last_number:04d}'


# ── Shared helpers ──────────────────────────────────────────────────────────

def _lock_valuation(item):
    """Locked valuation row for an item, created on first use."""
    GroceryItemValuation.objects.get_or_create(item=item)
    return GroceryItemValuation.objects.select_for_update().get(item=item)


def _lock_stock(item, warehouse, location):
    """
    Locked stock row for one item in one place, created on first use.

    get_or_create then re-select rather than select_for_update directly:
    SELECT ... FOR UPDATE cannot lock a row that does not exist yet, and the
    first receipt into a new bin is exactly that case.
    """
    GroceryStock.objects.get_or_create(
        item=item, warehouse=warehouse, location=location,
    )
    return (GroceryStock.objects
            .select_for_update()
            .get(item=item, warehouse=warehouse, location=location))


def _record_cost_change(item, previous_average, new_average,
                        previous_quantity, new_quantity,
                        movement_type, reference_number):
    """Log an average-cost change; skip no-op writes to keep the history readable."""
    if previous_average == new_average and previous_quantity == new_quantity:
        return
    GroceryItemCostHistory.objects.create(
        item=item,
        previous_average_cost=previous_average,
        new_average_cost=new_average,
        previous_quantity=previous_quantity,
        new_quantity=new_quantity,
        movement_type=movement_type,
        reference_number=reference_number or '',
    )


def _line_sort_key(line):
    """Deterministic lock order — see the ORDERED invariant above."""
    return (
        line.item_id,
        getattr(line, 'warehouse_id', 0) or 0,
        line.location_id or 0,
        line.id or 0,
    )


def _check_bins(lines, warehouse):
    """
    Every line's bin must live in the document's warehouse.

    A bin belongs to exactly one warehouse, so filing stock into another
    warehouse's bin produces a row that no bin-level report can explain and
    that FEFO would then hand out from the wrong place. Nothing in the schema
    prevents it — the two foreign keys are independent — so it is checked here,
    where every posting path goes through.
    """
    for line in lines:
        location = line.location
        if location and location.warehouse_id != warehouse.id:
            raise PostingError(
                f'{line.item.name}: bin {location.code} belongs to '
                f'{location.warehouse.code}, not {warehouse.code}.'
            )


# ═══ GOODS RECEIPT ══════════════════════════════════════════════════════════

@transaction.atomic
def post_goods_receipt(receipt, username=''):
    """
    Post a GRN: price it, create cost layers, raise stock, write the ledger and
    move the weighted average.

    Returns the receipt. Raises PostingError if it cannot be posted.
    """
    # Re-read under a lock so a second concurrent post sees POSTED here.
    receipt = (type(receipt).objects.select_for_update().get(pk=receipt.pk))

    if receipt.status != DocumentStatus.DRAFT:
        raise PostingError(
            f'This receipt is already {receipt.get_status_display().lower()}.'
        )

    lines = list(
        receipt.lines.select_related('item', 'location').all()
    )
    if not lines:
        raise PostingError('Add at least one line before posting.')

    for line in lines:
        if line.accepted_quantity < 0 or line.received_quantity < 0:
            raise PostingError(f'{line.item.name}: quantities cannot be negative.')
        if line.accepted_quantity > line.received_quantity:
            raise PostingError(
                f'{line.item.name}: accepted quantity cannot exceed received.'
            )
        # Section 13 — an expiry-tracked item must arrive with a date, or the
        # expiry alerts have nothing to count down and FEFO cannot order it.
        if line.item.expiry_required and not line.expiry_date:
            raise PostingError(
                f'{line.item.name} is expiry-tracked, so an expiry date is required.'
            )

    _check_bins(lines, receipt.warehouse)

    if not any(line.accepted_quantity > 0 for line in lines):
        raise PostingError('Nothing was accepted, so there is nothing to post.')

    # 1. Price the document. Header charges are spread here.
    totals = price_goods_receipt(receipt, lines)

    # 2. Move stock, in a deterministic lock order.
    for line in sorted(lines, key=_line_sort_key):
        if line.accepted_quantity <= 0:
            line.save(update_fields=[
                'taxable_amount', 'tax_amount', 'line_total',
                'apportioned_charges', 'landed_unit_cost',
            ])
            continue

        quantity = q_qty(line.accepted_quantity)
        unit_cost = line.landed_unit_cost

        batch = GroceryStockBatch.objects.create(
            item=line.item,
            warehouse=receipt.warehouse,
            location=line.location,
            supplier=receipt.supplier,
            batch_number=line.batch_number or '',
            manufacturing_date=line.manufacturing_date,
            expiry_date=line.expiry_date,
            received_quantity=quantity,
            remaining_quantity=quantity,
            unit_cost=unit_cost,
            received_on=receipt.receipt_date,
            reference_number=receipt.grn_number or '',
        )

        stock = _lock_stock(line.item, receipt.warehouse, line.location)
        stock.quantity_available = q_qty(Decimal(stock.quantity_available) + quantity)
        stock.save(update_fields=['quantity_available', 'updated_at'])

        valuation = _lock_valuation(line.item)
        previous_quantity = Decimal(valuation.quantity_on_hand)
        previous_average, new_average = apply_receipt_to_valuation(
            valuation, quantity, unit_cost,
        )
        valuation.last_purchase_cost = unit_cost
        valuation.last_purchase_date = receipt.receipt_date
        valuation.save()

        _record_cost_change(
            line.item, previous_average, new_average,
            previous_quantity, valuation.quantity_on_hand,
            StockMovementType.RECEIPT, receipt.grn_number,
        )

        GroceryStockTransaction.objects.create(
            item=line.item,
            warehouse=receipt.warehouse,
            location=line.location,
            batch=batch,
            movement_type=StockMovementType.RECEIPT,
            moved_on=receipt.receipt_date,
            quantity=quantity,
            unit_cost=unit_cost,
            value=q_amount(quantity * Decimal(unit_cost)),
            balance_quantity=stock.quantity_available,
            reference_type='GRN',
            reference_id=receipt.pk,
            reference_number=receipt.grn_number or '',
        )

        line.batch = batch
        line.save(update_fields=[
            'taxable_amount', 'tax_amount', 'line_total',
            'apportioned_charges', 'landed_unit_cost', 'batch',
        ])

    # 3. Stamp the header last, so a failure anywhere above leaves it DRAFT.
    if not receipt.grn_number:
        receipt.grn_number = next_document_number('GRN', receipt.receipt_date.year)
        # Backfill the number onto rows written before it existed.
        GroceryStockTransaction.objects.filter(
            reference_type='GRN', reference_id=receipt.pk,
        ).update(reference_number=receipt.grn_number)
        GroceryStockBatch.objects.filter(
            receipt_lines__receipt=receipt,
        ).update(reference_number=receipt.grn_number)

    receipt.subtotal_amount = totals['subtotal_amount']
    receipt.tax_amount = totals['tax_amount']
    receipt.total_amount = totals['total_amount']
    receipt.inventory_value = totals['inventory_value']
    receipt.status = DocumentStatus.POSTED
    receipt.posted_at = timezone.now()
    receipt.posted_by = username or ''
    receipt.save()

    logger.info('Posted %s: %s lines, inventory value %s',
                receipt.grn_number, len(lines), receipt.inventory_value)
    return receipt


# ═══ STOCK ISSUE ════════════════════════════════════════════════════════════

@transaction.atomic
def post_stock_issue(issue, username=''):
    """
    Post an issue: pick batches FEFO, lower stock, write the ledger and charge
    the consuming department.

    A line spanning several batches writes one ledger row per batch, so the
    allocation is recorded without a separate table.
    """
    issue = type(issue).objects.select_for_update().get(pk=issue.pk)

    if issue.status != DocumentStatus.DRAFT:
        raise PostingError(
            f'This issue is already {issue.get_status_display().lower()}.'
        )

    lines = list(issue.lines.select_related('item', 'location').all())
    if not lines:
        raise PostingError('Add at least one line before posting.')

    for line in lines:
        if line.quantity <= 0:
            raise PostingError(f'{line.item.name}: quantity must be above zero.')

    _check_bins(lines, issue.warehouse)

    total_value = ZERO

    for line in sorted(lines, key=_line_sort_key):
        quantity = q_qty(line.quantity)

        stock = _lock_stock(line.item, issue.warehouse, line.location)
        if Decimal(stock.quantity_available) < quantity:
            raise PostingError(
                f'{line.item.name}: only {stock.quantity_available} available '
                f'in {issue.warehouse.code}, but {quantity} was requested.'
            )

        # Lock the candidate layers in FEFO order before allocating, so a
        # concurrent issue cannot consume the same batch between the read and
        # the write.
        batch_filter = Q(item=line.item, warehouse=issue.warehouse,
                         remaining_quantity__gt=0)
        if line.location_id:
            batch_filter &= Q(location=line.location)

        batches = list(
            GroceryStockBatch.objects
            .select_for_update()
            .filter(batch_filter)
            .order_by(F('expiry_date').asc(nulls_last=True), 'received_on', 'id')
        )

        try:
            allocations = allocate_batches(batches, quantity)
        except InsufficientStock as exc:
            # The stock row and the batches disagree, which means something
            # wrote stock without a batch. Refuse rather than guess.
            raise PostingError(
                f'{line.item.name}: batch quantities are short of the stock '
                f'balance ({exc.available} in batches vs {stock.quantity_available} '
                f'on hand). This needs a stock adjustment before issuing.'
            )

        valuation = _lock_valuation(line.item)
        line_value = ZERO

        for batch, taken in allocations:
            unit_cost = issue_unit_cost(line.item, valuation, batch)
            value = q_amount(taken * unit_cost)

            batch.remaining_quantity = q_qty(Decimal(batch.remaining_quantity) - taken)
            batch.save(update_fields=['remaining_quantity', 'updated_at'])

            stock.quantity_available = q_qty(Decimal(stock.quantity_available) - taken)
            stock.save(update_fields=['quantity_available', 'updated_at'])

            previous_quantity = Decimal(valuation.quantity_on_hand)
            previous_average, new_average = apply_issue_to_valuation(
                valuation, taken, unit_cost,
            )
            valuation.save()
            _record_cost_change(
                line.item, previous_average, new_average,
                previous_quantity, valuation.quantity_on_hand,
                StockMovementType.ISSUE, issue.issue_number,
            )

            GroceryStockTransaction.objects.create(
                item=line.item,
                warehouse=issue.warehouse,
                location=line.location,
                batch=batch,
                department=issue.department,
                movement_type=StockMovementType.ISSUE,
                moved_on=issue.issue_date,
                quantity=-taken,
                unit_cost=unit_cost,
                value=-value,
                balance_quantity=stock.quantity_available,
                reference_type='ISSUE',
                reference_id=issue.pk,
                reference_number=issue.issue_number or '',
            )

            line_value += value

        line.line_value = q_amount(line_value)
        # q_cost, not q_amount: unit_cost carries 4 decimals, and a line drawn
        # from batches at different costs needs that precision to reconcile
        # back to line_value.
        line.unit_cost = (q_cost(line_value / quantity) if quantity > ZERO else ZERO)
        line.save(update_fields=['unit_cost', 'line_value'])

        total_value += line_value

    if not issue.issue_number:
        issue.issue_number = next_document_number('ISS', issue.issue_date.year)
        GroceryStockTransaction.objects.filter(
            reference_type='ISSUE', reference_id=issue.pk,
        ).update(reference_number=issue.issue_number)

    issue.total_value = q_amount(total_value)
    issue.status = DocumentStatus.POSTED
    issue.posted_at = timezone.now()
    issue.posted_by = username or ''
    issue.save()

    logger.info('Posted %s: %s lines, value %s',
                issue.issue_number, len(lines), issue.total_value)
    return issue


# ═══ STOCK ADJUSTMENT ═══════════════════════════════════════════════════════

@transaction.atomic
def post_stock_adjustment(adjustment, username=''):
    """
    Reconcile book stock to a physical count (specification section 11).

    Unlike a receipt or an issue, a line can move stock either way, so each
    direction is handled separately:

      SHORTFALL (counted < system)
        Consume the difference from batches — the named batch when the line
        targets one (writing off a specific expired layer), otherwise FEFO.
        Valued like an issue, so the write-off costs what the stock cost.

      SURPLUS (counted > system)
        Create a layer at the item's current average cost. There is no invoice
        to price found stock against, and the average is the only defensible
        figure; using standard cost would book a gain or loss that never
        happened. When the item has no average yet, its standard cost is the
        fallback.

    ``system_quantity`` is read from the locked row at posting, never from the
    draft, so an adjustment always lands exactly on the counted figure even if
    stock moved after the draft was typed.
    """
    from ..models import GroceryStockAdjustment  # avoids a circular import

    adjustment = (GroceryStockAdjustment.objects
                  .select_for_update().get(pk=adjustment.pk))

    if adjustment.status != DocumentStatus.DRAFT:
        raise PostingError(
            f'This adjustment is already {adjustment.get_status_display().lower()}.'
        )

    lines = list(adjustment.lines.select_related('item', 'location', 'batch').all())
    if not lines:
        raise PostingError('Add at least one line before posting.')

    for line in lines:
        if line.counted_quantity < 0:
            raise PostingError(
                f'{line.item.name}: a counted quantity cannot be negative.'
            )

    _check_bins(lines, adjustment.warehouse)

    movement_type = adjustment.movement_type
    total_value = ZERO
    changed = 0

    for line in sorted(lines, key=_line_sort_key):
        stock = _lock_stock(line.item, adjustment.warehouse, line.location)
        system = q_qty(stock.quantity_available)
        counted = q_qty(line.counted_quantity)
        difference = q_qty(counted - system)

        line.system_quantity = system
        line.difference = difference

        if difference == ZERO:
            # Counted matches the books. Keep the line as evidence the item was
            # checked, but write no ledger row — nothing moved.
            line.unit_cost = ZERO
            line.line_value = ZERO
            line.save(update_fields=['system_quantity', 'difference',
                                     'unit_cost', 'line_value'])
            continue

        changed += 1
        valuation = _lock_valuation(line.item)
        line_value = ZERO

        if difference < ZERO:
            shortfall = -difference

            batch_filter = Q(item=line.item, warehouse=adjustment.warehouse,
                             remaining_quantity__gt=0)
            if line.batch_id:
                batch_filter &= Q(pk=line.batch_id)
            elif line.location_id:
                batch_filter &= Q(location=line.location)

            batches = list(
                GroceryStockBatch.objects
                .select_for_update()
                .filter(batch_filter)
                .order_by(F('expiry_date').asc(nulls_last=True), 'received_on', 'id')
            )

            try:
                allocations = allocate_batches(batches, shortfall)
            except InsufficientStock as exc:
                where = (f'batch {line.batch.batch_number or line.batch_id}'
                         if line.batch_id else 'the available batches')
                raise PostingError(
                    f'{line.item.name}: {where} hold only {exc.available}, '
                    f'but the count is short by {shortfall}.'
                )

            for batch, taken in allocations:
                unit_cost = issue_unit_cost(line.item, valuation, batch)
                value = q_amount(taken * unit_cost)

                batch.remaining_quantity = q_qty(
                    Decimal(batch.remaining_quantity) - taken)
                batch.save(update_fields=['remaining_quantity', 'updated_at'])

                stock.quantity_available = q_qty(
                    Decimal(stock.quantity_available) - taken)
                stock.save(update_fields=['quantity_available', 'updated_at'])

                previous_quantity = Decimal(valuation.quantity_on_hand)
                previous_average, new_average = apply_issue_to_valuation(
                    valuation, taken, unit_cost)
                valuation.save()
                _record_cost_change(
                    line.item, previous_average, new_average,
                    previous_quantity, valuation.quantity_on_hand,
                    movement_type, adjustment.adjustment_number)

                GroceryStockTransaction.objects.create(
                    item=line.item, warehouse=adjustment.warehouse,
                    location=line.location, batch=batch,
                    movement_type=movement_type,
                    moved_on=adjustment.adjustment_date,
                    quantity=-taken, unit_cost=unit_cost, value=-value,
                    balance_quantity=stock.quantity_available,
                    reference_type='ADJ', reference_id=adjustment.pk,
                    reference_number=adjustment.adjustment_number or '',
                    remarks=adjustment.get_reason_display(),
                )
                line_value -= value

        else:
            surplus = difference
            unit_cost = Decimal(valuation.average_cost or 0)
            if unit_cost <= ZERO:
                unit_cost = Decimal(line.item.standard_cost or 0)

            batch = GroceryStockBatch.objects.create(
                item=line.item, warehouse=adjustment.warehouse,
                location=line.location,
                batch_number=(line.batch.batch_number if line.batch_id else ''),
                received_quantity=surplus, remaining_quantity=surplus,
                unit_cost=unit_cost, received_on=adjustment.adjustment_date,
                reference_number=adjustment.adjustment_number or '',
            )

            stock.quantity_available = q_qty(
                Decimal(stock.quantity_available) + surplus)
            stock.save(update_fields=['quantity_available', 'updated_at'])

            previous_quantity = Decimal(valuation.quantity_on_hand)
            previous_average, new_average = apply_receipt_to_valuation(
                valuation, surplus, unit_cost)
            valuation.save()
            _record_cost_change(
                line.item, previous_average, new_average,
                previous_quantity, valuation.quantity_on_hand,
                movement_type, adjustment.adjustment_number)

            value = q_amount(surplus * unit_cost)
            GroceryStockTransaction.objects.create(
                item=line.item, warehouse=adjustment.warehouse,
                location=line.location, batch=batch,
                movement_type=movement_type,
                moved_on=adjustment.adjustment_date,
                quantity=surplus, unit_cost=unit_cost, value=value,
                balance_quantity=stock.quantity_available,
                reference_type='ADJ', reference_id=adjustment.pk,
                reference_number=adjustment.adjustment_number or '',
                remarks=adjustment.get_reason_display(),
            )
            line_value += value

        magnitude = abs(difference)
        line.unit_cost = q_cost(abs(line_value) / magnitude) if magnitude > ZERO else ZERO
        line.line_value = q_amount(line_value)
        line.save(update_fields=['system_quantity', 'difference',
                                 'unit_cost', 'line_value'])
        total_value += line_value

    if not changed:
        raise PostingError(
            'Every counted quantity already matches the system, so there is '
            'nothing to adjust.'
        )

    if not adjustment.adjustment_number:
        adjustment.adjustment_number = next_document_number(
            'ADJ', adjustment.adjustment_date.year)
        GroceryStockTransaction.objects.filter(
            reference_type='ADJ', reference_id=adjustment.pk,
        ).update(reference_number=adjustment.adjustment_number)
        GroceryStockBatch.objects.filter(
            reference_number='', transactions__reference_type='ADJ',
            transactions__reference_id=adjustment.pk,
        ).update(reference_number=adjustment.adjustment_number)

    adjustment.total_value = q_amount(total_value)
    adjustment.status = DocumentStatus.POSTED
    adjustment.posted_at = timezone.now()
    adjustment.posted_by = username or ''
    adjustment.save()

    logger.info('Posted %s: %s of %s lines changed, net value %s',
                adjustment.adjustment_number, changed, len(lines),
                adjustment.total_value)
    return adjustment


# ═══ CANCELLATION ═══════════════════════════════════════════════════════════

@transaction.atomic
def cancel_document(document, username=''):
    """
    Cancel a DRAFT document.

    Posted documents are deliberately not cancellable here. Undoing a posted
    movement means writing a reversal — recalculating a weighted average
    backwards through later receipts is not reliably possible, so that needs
    its own reversal document rather than a status flip.
    """
    document = type(document).objects.select_for_update().get(pk=document.pk)

    if document.status == DocumentStatus.POSTED:
        raise PostingError(
            'A posted document cannot be cancelled. Post a reversing '
            'document instead so the ledger stays complete.'
        )
    if document.status == DocumentStatus.CANCELLED:
        raise PostingError('This document is already cancelled.')

    document.status = DocumentStatus.CANCELLED
    document.save(update_fields=['status', 'updated_at'])
    return document
