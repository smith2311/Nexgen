"""Per-operator focus, for every Meril-V console.

Focus is the picking lock: nothing may be scanned until the operator has said
which delivery, order or receiving plant they are working on, and every scan
resolves against that target.

It used to live on the record — ``OutboundSession.focus_delivery_no``, and
``focus_key`` on the ZVA05 dump, the BT scan session and the inbound receipt.
One value per record meant one value for everyone looking at it. Since the
consoles poll state every two seconds, one picker clicking a delivery silently
took over every other screen open on that record, and the other picker's next
scan landed on a delivery they never chose.

The record is meant to be shared; the position in it is not. Everything here
is keyed on (record, user).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import OperatorFocus

#: Model class name → the short name used in the stored scope string. Explicit
#: rather than derived from ``__name__`` so renaming a model cannot silently
#: orphan every operator's focus.
_SCOPES = {
    "OutboundSession": "outbound",
    "Zva05Dump": "zva05",
    "BtScanSession": "btscan",
    "BranchTransferReceipt": "inbound",
}


@dataclass(frozen=True)
class Focus:
    """One operator's position. Both fields blank when nothing is focused."""

    value: str = ""
    customer_code: str = ""

    def __bool__(self) -> bool:
        return bool(self.value or self.customer_code)


NONE = Focus()


def scope_for(record) -> str:
    """``"outbound:41"`` — which console and which record."""
    name = type(record).__name__
    try:
        return f"{_SCOPES[name]}:{record.pk}"
    except KeyError:  # pragma: no cover - a new console must register here
        raise ValueError(
            f"{name} has no focus scope — add it to merilv.focus._SCOPES"
        ) from None


def get(record, user) -> Focus:
    """What this operator is focused on, or :data:`NONE`.

    An anonymous or unsaved user focuses nothing, rather than sharing a blank
    row with every other anonymous caller.
    """
    if user is None or not getattr(user, "pk", None):
        return NONE
    row = OperatorFocus.objects.filter(user=user, scope=scope_for(record)).first()
    return Focus(row.value, row.customer_code) if row else NONE


def set_to(record, user, value: str = "", customer_code: str = "") -> Focus:
    """Put this operator on ``value``. Blank clears them."""
    if user is None or not getattr(user, "pk", None):
        return NONE
    value = (value or "").strip()
    customer_code = (customer_code or "").strip()
    scope = scope_for(record)
    if not value and not customer_code:
        OperatorFocus.objects.filter(user=user, scope=scope).delete()
        return NONE
    OperatorFocus.objects.update_or_create(
        user=user, scope=scope,
        defaults={"value": value, "customer_code": customer_code},
    )
    return Focus(value, customer_code)


def toggle(record, user, value: str = "", customer_code: str = "") -> Focus:
    """Clicking the focused thing again clears it — the console's own gesture.

    Kept here rather than in each view so all four consoles agree on what a
    second click means.
    """
    current = get(record, user)
    value = (value or "").strip()
    customer_code = (customer_code or "").strip()
    if value:
        return set_to(record, user, "" if current.value == value else value, "")
    if customer_code:
        return set_to(
            record, user, "",
            "" if current.customer_code == customer_code else customer_code,
        )
    return set_to(record, user)


def clear_for_record(record) -> int:
    """Drop everyone's focus on this record. For when the record itself goes."""
    deleted, _ = OperatorFocus.objects.filter(scope=scope_for(record)).delete()
    return deleted


def by_user(record) -> dict[int, Focus]:
    """Every operator's focus on this record, keyed by user id.

    For the live-operators board, which says what each person is working on.
    """
    return {
        row.user_id: Focus(row.value, row.customer_code)
        for row in OperatorFocus.objects.filter(scope=scope_for(record))
    }


__all__ = [
    "Focus", "NONE", "by_user", "clear_for_record", "get", "scope_for",
    "set_to", "toggle",
]
