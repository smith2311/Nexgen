"""Coercion helpers for untrusted request parameters.

Django's ``IntegerField.get_prep_value`` calls ``int()`` and re-raises
``ValueError``, so passing a raw ``request.POST`` string straight into a pk
lookup turns a malformed form post into an HTTP 500 rather than a clean
"not found". ``get_object_or_404`` does not help — it only catches
``DoesNotExist``.

These helpers keep that coercion in one place so every callsite fails the same
way, instead of each view inventing its own ``try/except`` (or, as was the
case, mostly not having one).
"""

from __future__ import annotations


def int_or_none(raw) -> int | None:
    """An ``int`` from untrusted input, or ``None`` if it is not one.

    ``None`` is deliberately the failure value rather than a default: for a pk
    lookup there is no sensible default, and ``filter(pk=None)`` matches
    nothing, which is exactly the desired outcome for a malformed id.
    """
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


__all__ = ["int_or_none"]
