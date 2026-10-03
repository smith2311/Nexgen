"""VAT determination engine.

Supports both calculation methods from the spec:
  Method 1 — HSN Based: per-line VAT % looked up by HSN/SAC code.
  Method 2 — Flat:      single VAT % for the whole invoice.

All math is Decimal, rounded HALF_UP to 2 dp, producing the exact row-wise and
grand-total figures shown in the Output Screen.
"""
from decimal import Decimal, ROUND_HALF_UP

from ..models import VatRateMaster

TWO = Decimal("0.01")


def _r2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def compute(country, lines):
    """Compute expected VAT for an invoice.

    Args:
        country: country name (matched against VatRateMaster, case-insensitive).
        lines:   list of dicts. HSN mode -> {'hsn': str, 'taxable_amount': num}.
                 Flat mode -> {'taxable_amount': num} (hsn omitted/blank).

    Returns:
        {
          'lines': [{'hsn', 'taxable_amount', 'vat_percent', 'expected_vat', 'error'?}],
          'grand_taxable': Decimal,
          'grand_expected': Decimal,
          'errors': [str, ...],   # rows with no matching rate
        }
    """
    rates = VatRateMaster.objects.filter(country__iexact=(country or "").strip(), is_active=True)
    flat = rates.filter(calc_type=VatRateMaster.CalcType.FLAT).first()
    hsn_map = {
        (r.hsn_sac_code or "").strip(): r.vat_percent
        for r in rates.filter(calc_type=VatRateMaster.CalcType.HSN)
    }

    out_lines = []
    errors = []
    grand_taxable = Decimal("0")
    grand_expected = Decimal("0")

    for ln in lines:
        amount = Decimal(str(ln.get("taxable_amount") or 0))
        hsn = (str(ln.get("hsn") or "")).strip()

        if hsn and hsn in hsn_map:
            pct = hsn_map[hsn]
        elif not hsn and flat:
            pct = flat.vat_percent
        elif flat and hsn not in hsn_map:
            # HSN given but unknown — fall back to flat if the country has one.
            pct = flat.vat_percent
        else:
            msg = (f"No VAT rate for HSN '{hsn}' in {country}" if hsn
                   else f"No flat VAT rate configured for {country}")
            errors.append(msg)
            out_lines.append({
                "hsn": hsn, "taxable_amount": _r2(amount),
                "vat_percent": None, "expected_vat": None, "error": msg,
            })
            continue

        expected = _r2(amount * pct / Decimal("100"))
        grand_taxable += amount
        grand_expected += expected
        out_lines.append({
            "hsn": hsn, "taxable_amount": _r2(amount),
            "vat_percent": pct, "expected_vat": expected,
        })

    return {
        "lines": out_lines,
        "grand_taxable": _r2(grand_taxable),
        "grand_expected": _r2(grand_expected),
        "errors": errors,
    }
