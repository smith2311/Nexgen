"""Tolerance check + reconciliation.

variance = |charged_vat - expected_vat|
status   = MATCHED      if variance <= tolerance
           DISCREPANCY  otherwise
"""
from decimal import Decimal, ROUND_HALF_UP

from ..models import VatCountryMaster, VatInvoice

TWO = Decimal("0.01")


def _r2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def tolerance_for(country):
    """Per-country tolerance from Country Master; default 0.05 if not set."""
    cm = VatCountryMaster.objects.filter(
        country__iexact=(country or "").strip(), is_active=True
    ).first()
    return cm.tolerance_limit if cm and cm.tolerance_limit is not None else Decimal("0.05")


def reconcile(expected_vat, charged_vat, country):
    """Pure comparison. Returns dict with variance, tolerance and status."""
    expected = _r2(expected_vat or 0)
    charged = _r2(charged_vat or 0)
    tolerance = Decimal(str(tolerance_for(country)))
    variance = _r2(abs(charged - expected))
    status = (VatInvoice.ReconStatus.MATCHED if variance <= tolerance
              else VatInvoice.ReconStatus.DISCREPANCY)
    return {
        "expected_vat": expected,
        "charged_vat": charged,
        "variance": variance,
        "tolerance": tolerance,
        "status": status,
    }
