"""Cost + capability comparison matrix for one RFI's bids.

This is the data behind the Bid Comparison Heatmap (Tier 1.3): one column per
vendor bid, one row per cost line and per capability, with each cost row tinted
cheapest-to-dearest.

Two decisions worth stating, because both are easy to get subtly wrong:

**Banding is per row, never per table.** A cell's tint says "this vendor is
cheap or dear *for this line item*", so storage at 40,000 and pick at 12 are
each scored against their own row. Band across the whole table instead and the
colour just maps which rows happen to hold big numbers — which is a fact about
units, not about vendors.

**Currency is never mixed silently.** Bids on one RFI can arrive in different
currencies, and a raw comparison of 900 USD against 74,000 INR is meaningless.
Rows are banded only within a currency group; when a request has more than one,
the caller is told so it can say as much on screen rather than quietly ranking
across them.

No Django imports — this takes plain values and returns plain dicts, so it can
be unit-tested without a database.
"""

from __future__ import annotations

#: The SOW cost lines, in sheet order, as (attribute, label).
COST_LINES: tuple[tuple[str, str], ...] = (
    ("storage_cost", "Storage"),
    ("inbound_cost", "Inbound"),
    ("outbound_cost", "Outbound"),
    ("pick_cost", "Pick"),
    ("packing_cost", "Packing"),
    ("last_mile_charges", "Last Mile"),
    ("manpower_charges", "Manpower"),
)

#: Everything on this screen is reported in one currency.
TARGET_CURRENCY = "INR"

#: Retained because band_for is still imported by tests and the older heatmap
#: path; the comparison matrix no longer bands its cells. See _row.
BAND_COUNT = 5


def _to_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def band_for(value, low, high):
    """Which of the five tints a value earns, given its row's range.

    A row where every vendor quoted the same number has no spread to show, so
    it stays unbanded rather than painting an arbitrary winner — colour has to
    mean something or it is noise.
    """
    if value is None or low is None or high is None or high <= low:
        return None
    position = (value - low) / (high - low)
    band = int(position * BAND_COUNT) + 1
    return min(band, BAND_COUNT)


def _declares(vendor, code):
    """Whether ``vendor`` declares capability ``code``. False for no vendor.

    A free function rather than a method call at the call site so this module
    keeps taking plain objects — it has no Django imports and is unit-tested
    against stubs, which is worth preserving.
    """
    checker = getattr(vendor, "has_capability", None)
    return bool(checker(code)) if callable(checker) else False


def build(bids, capability_fields=()):
    """Build the comparison matrix.

    ``bids`` is a sequence of objects exposing the COST_LINES attributes plus
    ``currency``; each is wrapped by the caller into a column dict via
    :func:`column_for`. ``capability_fields`` is a sequence of
    ``(attribute, label)`` read off each column's ``vendor`` (which may be
    None when a bid has no linked vendor record).

    Returns a dict with ``columns``, ``cost_rows``, ``capability_rows``,
    ``totals`` and ``currencies``.
    """
    columns = list(bids)

    # How many columns each vendor occupies. The "chosen revision" badge is
    # only meaningful for a vendor who submitted more than one: with a single
    # bid there was never a choice to make, and a badge there reads as "this
    # vendor won" — which is what made a request with one award look like it
    # had two winners. Counted here because the per-bid header row cannot see
    # its own vendor group.
    occupancy: dict = {}
    for column in columns:
        key = column.get("vendor_key") or (column.get("vendor_name") or "").casefold()
        occupancy[key] = occupancy.get(key, 0) + 1
    for column in columns:
        key = column.get("vendor_key") or (column.get("vendor_name") or "").casefold()
        column["sibling_count"] = occupancy[key]

    currencies = sorted({(c.get("currency") or "").upper() for c in columns if c.get("currency")})
    mixed = len(currencies) > 1

    # Comparison runs on INR where rates are available.
    #
    # When they are not, fall back to the quoted figures -- but only if every
    # column shares one currency, where raw values are directly comparable and
    # this is exactly the pre-conversion behaviour. With currencies mixed AND
    # no rates, there is nothing valid to compare, so values are dropped and no
    # cell is crowned: ranking 900 USD against 74,000 INR ranks vendors by
    # currency. Showing an empty table because a rates service was down would
    # be its own failure.
    converted = all(c.get("total_inr") is not None for c in columns)
    fall_back_to_quoted = not converted and not mixed

    def column_values(attr):
        if converted:
            return [_to_number(c.get("costs_inr", {}).get(attr)) for c in columns]
        if fall_back_to_quoted:
            return [_to_number(c["costs"].get(attr)) for c in columns]
        return [None for _ in columns]

    cost_rows = []
    for attr, label in COST_LINES:
        cost_rows.append(_row(label, attr, column_values(attr), columns, mixed))

    if converted:
        totals = [_to_number(c.get("total_inr")) for c in columns]
    elif fall_back_to_quoted:
        totals = [_to_number(c.get("total")) for c in columns]
    else:
        totals = [None for _ in columns]
    total_row = _row("Total", "total", totals, columns, mixed)
    total_row["is_total"] = True

    capability_rows = []
    for attr, label in capability_fields:
        capability_rows.append({
            "label": label,
            "key": attr,
            "cells": [
                # has_capability, not getattr: the capability booleans are gone
                # from Vendor, so getattr would answer False for every column
                # and the whole matrix would read "no vendor can do anything" —
                # with nothing raising to say so. Kept defensive about a column
                # with no vendor record, which is a real case (a bid from a
                # portal login whose registration was never linked).
                {"has_vendor": c.get("vendor") is not None,
                 "yes": _declares(c.get("vendor"), attr)}
                for c in columns
            ],
        })

    return {
        "columns": columns,
        "vendor_groups": _vendor_header_groups(columns),
        "cost_rows": cost_rows,
        "total_row": total_row,
        "capability_rows": capability_rows,
        "currencies": currencies,
        "mixed_currency": mixed,
        "has_data": bool(columns),
        "target_currency": TARGET_CURRENCY,
        # False when no rate was available and the figures below are the
        # vendors' own, so the screen does not label them as INR.
        "in_target_currency": all(c.get("total_inr") is not None for c in columns),
        # The rates actually applied, so the screen can disclose them. A
        # converted figure with no stated rate is not a defensible number on a
        # procurement record.
        "fx_used": [
            {"base": base, "rate": rate}
            for base, rate in sorted({
                (c.get("currency") or "").upper(): c.get("inr_rate")
                for c in columns
                if c.get("inr_rate") and (c.get("currency") or "").upper() != TARGET_CURRENCY
            }.items())
        ],
        # Named so the screen can say which vendors are shown un-converted
        # instead of leaving the reader to notice the different currency.
        "unconverted": [
            c["vendor_name"] for c in columns if c.get("total_inr") is None
        ],
    }


def _row(label, key, values, columns, mixed):
    """One cost row, with the cheapest cell marked.

    Values arrive already converted to INR, so there is a single scale and one
    cheapest per row. The previous five-band ramp is gone: it shaded every
    cell against the row's range, which answers "where does this quote sit on
    a scale" — a question the screen was not asked. What a buyer wants from a
    row is who is cheapest on it.

    A column with no INR value (no rate for its currency) is skipped rather
    than compared, and a row where every quote matches has no winner to mark.
    """
    present = [value for value in values if value is not None]
    cheapest = min(present) if present else None
    dearest = max(present) if present else None
    # One quote has nothing to have beaten; an all-equal row has no winner.
    has_spread = len(present) > 1 and cheapest < dearest

    cells = []
    for column, value in zip(columns, values):
        cells.append({
            "value": value,
            "currency": TARGET_CURRENCY,
            # Kept so a cell can still show what the vendor actually quoted.
            "source_value": column["costs"].get(key) if key != "total" else column.get("total"),
            "source_currency": (column.get("currency") or "").upper(),
            "converted": value is not None,
            # No ramp any more — see the docstring.
            "band": None,
            "is_best": has_spread and value is not None and value == cheapest,
            "delta_pct": (
                round((value - cheapest) / cheapest * 100, 1)
                if value is not None and cheapest else None
            ),
        })

    return {
        "label": label,
        "key": key,
        "cells": cells,
        "mixed_currency": mixed,
        "tied": len(present) > 1 and not has_spread,
    }


def vendor_key_for(bid):
    """Stable company identity for grouping a request's repeat bids."""
    registration = getattr(bid, "vendor_registration", None)
    if registration is not None:
        vendor_id = getattr(registration, "vendor_id", None)
        if vendor_id:
            return f"v{vendor_id}"
        email = (getattr(registration, "email", "") or "").strip().lower()
        if email:
            return f"e{email}"
        name = (getattr(registration, "vendor_name", "") or "").strip().casefold()
        if name:
            return f"n{name}"
        registration_id = getattr(registration, "pk", None)
        if registration_id:
            return f"r{registration_id}"

    submitted_by_id = getattr(bid, "submitted_by_id", None)
    if submitted_by_id:
        return f"u{submitted_by_id}"

    pk = getattr(bid, "pk", None)
    return f"b{pk}" if pk else f"obj{id(bid)}"


def _vendor_header_groups(columns):
    groups = []
    for column in columns:
        key = column.get("vendor_key") or (column.get("vendor_name") or "").casefold()
        if groups and groups[-1]["key"] == key:
            groups[-1]["colspan"] += 1
            groups[-1]["bid_count"] += 1
            continue
        groups.append({
            "key": key,
            "vendor_name": column.get("vendor_name") or "Unknown vendor",
            "colspan": 1,
            "bid_count": 1,
        })
    return groups


def column_for(bid, vendor_name, vendor=None, total=None, inr_rate=None):
    """Wrap one bid into the column shape :func:`build` expects.

    ``inr_rate`` is INR per unit of this bid's currency. Supplied by the
    caller, which fetches all the rates it needs in one pass rather than
    letting each column look one up. When it is None the column carries no
    converted figures and :func:`build` leaves it out of the comparison —
    never converted at an assumed rate.
    """
    costs = {attr: getattr(bid, attr, None) for attr, _label in COST_LINES}
    if total is None:
        total = sum(float(v) for v in (_to_number(x) for x in costs.values()) if v is not None)

    rate = _to_number(inr_rate)
    costs_inr, total_inr = {}, None
    if rate:
        costs_inr = {
            attr: (None if _to_number(value) is None else _to_number(value) * rate)
            for attr, value in costs.items()
        }
        total_inr = total * rate

    return {
        "bid": bid,
        "pk": getattr(bid, "pk", None),
        "vendor_name": vendor_name,
        "vendor_key": vendor_key_for(bid),
        "vendor": vendor,
        "currency": getattr(bid, "currency", "") or "",
        "status": getattr(bid, "status", "") or "",
        "is_selected": bool(getattr(bid, "is_selected", False)),
        "bid_number": getattr(bid, "bid_number", None),
        "costs": costs,
        "total": total,
        # Converted figures, and the rate that produced them, so the screen can
        # say what it applied.
        "costs_inr": costs_inr,
        "total_inr": total_inr,
        "inr_rate": rate,
    }
