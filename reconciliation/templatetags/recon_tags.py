"""
reconciliation/templatetags/recon_tags.py — formatting the pivot renders with.

Indian digit grouping (12,34,56,789 rather than 123,456,789) is done here
rather than with ``humanize.intcomma``: humanize is not installed in this
project, and its grouping is the Western one anyway, which would put a
reconciliation report's figures in a shape the people reading them do not use.
"""
from decimal import Decimal

from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()

# The character, not the HTML entity. Returning "&mdash;" would need mark_safe
# to survive autoescaping, and reaching for mark_safe to print a dash is how a
# codebase ends up with mark_safe everywhere.
EM_DASH = "—"


def _group_indian(digits):
    """``123456789`` → ``12,34,56,789``: last three, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


@register.filter
def rupees(value):
    """Whole rupees with Indian grouping; an em dash for nothing.

    Zero prints as a dash on purpose — the source report does the same, and a
    pivot full of "0" is much harder to read across than one where only the
    rows carrying money draw the eye.
    """
    if value is None:
        return EM_DASH
    try:
        number = Decimal(value)
    except Exception:
        return value
    if number == 0:
        return EM_DASH
    rounded = int(number.to_integral_value(rounding="ROUND_HALF_UP"))
    sign = "-" if rounded < 0 else ""
    return f"{sign}{_group_indian(str(abs(rounded)))}"


@register.filter
def rupees_compact(value):
    """``64,77,61,259`` as ``64.78 Cr`` — a headline figure that fits a tile.

    Six KPI tiles across a row leaves roughly 150px for the number, and a full
    nine-digit rupee figure simply does not fit: it ran out past the tile's
    edge. Indian finance reads crores and lakhs at a glance anyway, so the tile
    carries the short form and the exact figure goes on its tooltip and into
    the pivot below, where precision is what people are there for.
    """
    if value is None:
        return EM_DASH
    try:
        number = Decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return value
    if number == 0:
        return EM_DASH

    sign = "-" if number < 0 else ""
    amount = abs(number)
    for limit, suffix in ((Decimal("10000000"), "Cr"),
                          (Decimal("100000"), "L"),
                          (Decimal("1000"), "K")):
        if amount >= limit:
            scaled = (amount / limit).quantize(Decimal("0.01"))
            # 5.00 Cr reads worse than 5 Cr, and the decimals carry no
            # information once they are zero.
            trimmed = str(scaled).rstrip("0").rstrip(".")
            return f"{sign}{trimmed} {suffix}"
    return f"{sign}{_group_indian(str(int(amount)))}"


@register.filter
def count_in(value):
    """A plain count with Indian grouping. Zero shows as 0, not a dash — a
    subtotal band covering no documents is a fact worth seeing."""
    try:
        return _group_indian(str(int(value)))
    except (TypeError, ValueError):
        return value


@register.filter
def presence_glyph(presence):
    """The G / E / W mask as three lozenges."""
    mask = presence or "---"
    parts = [
        ("G", "on-g" if "G" in mask else ""),
        ("E", "on-e" if "E" in mask else ""),
        ("W", "on-w" if "W" in mask else ""),
    ]
    # format_html rather than mark_safe: every value here is one of our own
    # constants, but building trusted HTML by hand is a habit that only has to
    # be wrong once, and a scanner cannot tell the safe case from the unsafe.
    inner = format_html_join("", '<i class="{}">{}</i>',
                             ((cls, letter) for letter, cls in parts))
    return format_html('<span class="rc-glyph">{}</span>', inner)
