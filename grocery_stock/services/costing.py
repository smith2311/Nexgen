"""
Grocery Stock — costing engine (specification section 8)
─────────────────────────────────────────────────────────────────────────────
Two jobs, kept apart from posting so each can be reasoned about and tested on
its own:

  1. LANDED COST on receipt
         Product Cost + GST + Freight + Loading + Insurance + Other Charges
     with one deviation the specification implies rather than states: GST is
     only part of inventory cost when it is NOT recoverable. Where
     ``GroceryItem.is_gst_creditable`` is true the tax is claimed back as input
     credit, so capitalising it would overstate stock value and understate the
     credit. This is decision D1 recorded on the item model.

  2. ISSUE COST when stock leaves, by the item's valuation method — weighted
     average (the default) or FIFO against actual cost layers.

DECIMAL DISCIPLINE
Every figure is a Decimal, quantised once at the end of a calculation rather
than at each step. Rounding intermediate values is what makes a ledger fail to
foot by a few paise a month later. ROUND_HALF_UP is used because that is what
an invoice does; Python's default is banker's rounding, which would disagree
with the supplier's own total.
"""
from decimal import ROUND_HALF_UP, Decimal

from ..models import COST_DP, AMOUNT_DP, QTY_DP

ZERO = Decimal('0')

_QTY_Q = Decimal(1).scaleb(-QTY_DP)        # 0.001
_COST_Q = Decimal(1).scaleb(-COST_DP)      # 0.0001
_AMOUNT_Q = Decimal(1).scaleb(-AMOUNT_DP)  # 0.01


def q_qty(value):
    return Decimal(value).quantize(_QTY_Q, rounding=ROUND_HALF_UP)


def q_cost(value):
    return Decimal(value).quantize(_COST_Q, rounding=ROUND_HALF_UP)


def q_amount(value):
    return Decimal(value).quantize(_AMOUNT_Q, rounding=ROUND_HALF_UP)


# ═══ 1. RECEIPT — landed cost ═══════════════════════════════════════════════

def compute_line_amounts(line):
    """
    Invoice arithmetic for one GRN line, before header charges are spread.

    Returns a dict of unrounded Decimals plus ``inventory_base`` — the part of
    the line that belongs in stock value.

    Priced on ACCEPTED quantity, not received: rejected goods go back to the
    supplier, so charging them to inventory would inflate both stock and the
    average cost of everything already on hand.
    """
    accepted = Decimal(line.accepted_quantity or 0)
    unit_price = Decimal(line.unit_price or 0)
    discount = Decimal(line.discount_amount or 0)
    tax_percent = Decimal(line.tax_percent or 0)

    gross = accepted * unit_price
    taxable = gross - discount
    if taxable < ZERO:
        taxable = ZERO

    tax = taxable * tax_percent / Decimal(100)
    line_total = taxable + tax

    # D1: creditable GST is reclaimed, so it never enters inventory cost.
    creditable = bool(line.item.is_gst_creditable)
    inventory_base = taxable if creditable else taxable + tax

    return {
        'accepted': accepted,
        'taxable': taxable,
        'tax': tax,
        'line_total': line_total,
        'inventory_base': inventory_base,
        'gst_creditable': creditable,
    }


def apportion_charges(total_charges, weights):
    """
    Spread header charges across lines in proportion to ``weights``.

    Returns a list of Decimals that sums EXACTLY to ``total_charges``. The last
    non-zero weight absorbs the rounding remainder, because three lines sharing
    ₹100 of freight would otherwise capitalise ₹99.99 and quietly lose a paisa
    from inventory value on every receipt.

    Falls back to an equal split when every weight is zero — a zero-value
    receipt (free samples, replacements) still has real freight to absorb.
    """
    count = len(weights)
    if count == 0 or total_charges == ZERO:
        return [ZERO] * count

    total_weight = sum(weights)

    if total_weight == ZERO:
        share = total_charges / Decimal(count)
        shares = [q_amount(share) for _ in range(count)]
    else:
        shares = [q_amount(total_charges * w / total_weight) for w in weights]

    # Push the residue onto the heaviest line so the split always foots.
    residue = q_amount(total_charges) - sum(shares)
    if residue != ZERO:
        target = max(range(count), key=lambda i: weights[i]) if total_weight else count - 1
        shares[target] = q_amount(shares[target] + residue)

    return shares


def price_goods_receipt(receipt, lines):
    """
    Price a whole GRN: line amounts, apportioned charges, landed unit costs.

    Mutates the line objects in memory and returns the header totals. The
    caller decides when to save, so this stays callable for a live preview on
    an unsaved draft.
    """
    computed = [compute_line_amounts(line) for line in lines]

    total_charges = Decimal(receipt.total_charges or 0)
    weights = [c['inventory_base'] for c in computed]
    shares = apportion_charges(total_charges, weights)

    subtotal = tax_total = grand_total = inventory_value = ZERO

    for line, calc, share in zip(lines, computed, shares):
        line.taxable_amount = q_amount(calc['taxable'])
        line.tax_amount = q_amount(calc['tax'])
        line.line_total = q_amount(calc['line_total'])
        line.apportioned_charges = share

        landed_total = calc['inventory_base'] + share
        accepted = calc['accepted']
        # A line can be fully rejected; it still carries its share of charges
        # in the header totals but has no unit cost to state.
        line.landed_unit_cost = q_cost(landed_total / accepted) if accepted > ZERO else ZERO

        subtotal += calc['taxable']
        tax_total += calc['tax']
        grand_total += calc['line_total']
        inventory_value += landed_total

    return {
        'subtotal_amount': q_amount(subtotal),
        'tax_amount': q_amount(tax_total),
        'total_amount': q_amount(grand_total + total_charges),
        'inventory_value': q_amount(inventory_value),
    }


# ═══ 2. VALUATION — weighted average ════════════════════════════════════════

def apply_receipt_to_valuation(valuation, quantity, unit_cost):
    """
    Fold a receipt into an item's running weighted average.

        new_average = (old_value + received_value) / (old_quantity + received_quantity)

    Derived from ``total_value``, never from the previous average, so the
    stored average stays the quotient of two exact running totals instead of an
    average of averages.

    Returns (previous_average, new_average).
    """
    previous_average = Decimal(valuation.average_cost or 0)

    old_qty = Decimal(valuation.quantity_on_hand or 0)
    old_value = Decimal(valuation.total_value or 0)

    new_qty = old_qty + Decimal(quantity)
    new_value = old_value + (Decimal(quantity) * Decimal(unit_cost))

    valuation.quantity_on_hand = q_qty(new_qty)
    valuation.total_value = q_amount(new_value)
    valuation.average_cost = q_cost(new_value / new_qty) if new_qty > ZERO else ZERO

    return previous_average, Decimal(valuation.average_cost)


def apply_issue_to_valuation(valuation, quantity, unit_cost):
    """
    Remove issued stock from the running totals.

    The average is deliberately left untouched on the way out: under weighted
    average, issuing at the current average removes value in exact proportion
    to quantity, so the average cannot move. Recomputing it here would only
    introduce rounding noise.

    The one exception is the last unit leaving. Accumulated rounding can strand
    a few paise against zero quantity, so the position is snapped clean rather
    than left holding value for stock that does not exist.
    """
    previous_average = Decimal(valuation.average_cost or 0)

    new_qty = Decimal(valuation.quantity_on_hand or 0) - Decimal(quantity)
    new_value = Decimal(valuation.total_value or 0) - (Decimal(quantity) * Decimal(unit_cost))

    if new_qty <= ZERO:
        valuation.quantity_on_hand = ZERO
        valuation.total_value = ZERO
        # average_cost is kept as the last known cost — reports still want a
        # figure for an item that has just gone to zero.
    else:
        valuation.quantity_on_hand = q_qty(new_qty)
        valuation.total_value = q_amount(new_value)
        valuation.average_cost = q_cost(new_value / new_qty)

    return previous_average, Decimal(valuation.average_cost)


# ═══ 3. ISSUE — batch picking and cost ══════════════════════════════════════

class InsufficientStock(Exception):
    """Raised when an issue asks for more than the batches actually hold."""

    def __init__(self, item, requested, available):
        self.item = item
        self.requested = requested
        self.available = available
        super().__init__(
            f'{item.name}: asked for {requested}, only {available} available.'
        )


def allocate_batches(batches, quantity):
    """
    Draw ``quantity`` from ``batches`` in the order given, which the caller
    locks and orders FEFO (earliest expiry, then earliest receipt).

    Returns [(batch, quantity_taken)]. Raises InsufficientStock rather than
    allowing a negative balance — going short is a counting error, and letting
    it through corrupts every valuation that follows.

    Picking is FEFO regardless of the costing method. Which physical carton
    leaves the shelf and how it is valued are separate questions: perishables
    must always ship earliest-expiry-first, while FIFO vs weighted average only
    decides the number attached to the movement.
    """
    remaining = Decimal(quantity)
    allocations = []

    for batch in batches:
        if remaining <= ZERO:
            break
        available = Decimal(batch.remaining_quantity or 0)
        if available <= ZERO:
            continue

        take = available if available < remaining else remaining
        allocations.append((batch, q_qty(take)))
        remaining -= take

    if remaining > ZERO:
        taken = sum(a[1] for a in allocations)
        raise InsufficientStock(None, q_qty(quantity), q_qty(taken))

    return allocations


def issue_unit_cost(item, valuation, batch):
    """
    What one unit leaving is worth.

    FIFO reads the actual cost of the layer being consumed, so a line spanning
    three layers produces three ledger rows at three different costs. Weighted
    average uses the item's single running average, which is why a WAC issue
    ignores the batch entirely.
    """
    from ..models import ValuationMethod

    if item.valuation_method == ValuationMethod.FIFO and batch is not None:
        return Decimal(batch.unit_cost or 0)
    return Decimal(valuation.average_cost or 0)
