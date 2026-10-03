"""Parse structured invoice fields from the translated English text.

OCR.space + the translation webhook give us plain English text; this rules-based
parser recovers the fields the pipeline needs. It is intentionally conservative
(returns None when unsure) — pair it with a manual review step for production.
"""
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings


def parse(text):
    """Returns the same dict shape the pipeline expects from OCR structured data."""
    provider = getattr(settings, "VAT_EXTRACT_PROVIDER", "rules")
    if provider != "rules":
        # Hook point for a future extraction webhook/NER provider.
        pass
    return _rules(text)


def _rules(text):
    text = text or ""
    return {
        "invoice_number": _first(text, [
            r"invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9\-/]+)",
        ]),
        "invoice_date": _first(text, [
            r"(\d{4}-\d{2}-\d{2})",
            r"(\d{2}[./-]\d{2}[./-]\d{4})",
        ]),
        "currency": _currency(text),
        "taxable_amount": _amount_after(text, [r"taxable\s*(?:amount|value)", r"net\s*amount", r"sub\s*total"]),
        "charged_vat": _amount_after(text, [r"v\.?a\.?t\.?\s*amount", r"\bvat\b", r"tax\s*amount"]),
        "total_amount": _amount_after(text, [r"grand\s*total", r"total\s*amount", r"\btotal\b"]),
        "lines": _hsn_lines(text),
    }


def _first(text, patterns):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).strip()
    return None


_CCY = {"€": "EUR", "$": "USD", "₩": "KRW", "₫": "VND", "£": "GBP", "₹": "INR"}


def _currency(text):
    for sym, code in _CCY.items():
        if sym in text:
            return code
    m = re.search(r"\b(EUR|USD|KRW|VND|GBP|INR|PLN|HUF|SEK|RUB|JPY|CNY)\b", text)
    return m.group(1) if m else None


def _norm_decimal(s):
    """Handle both 1,234.56 (US) and 1.234,56 (EU) number formats."""
    s = re.sub(r"[^\d.,-]", "", s or "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):       # comma is the decimal separator
            s = s.replace(".", "").replace(",", ".")
        else:                                  # dot is the decimal separator
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _amount_after(text, labels):
    for lbl in labels:
        m = re.search(lbl + r"[^\d\-]{0,15}([-+]?[\d.,]+\d)", text, re.I)
        if m:
            val = _norm_decimal(m.group(1))
            if val is not None:
                return val
    return None


def _hsn_lines(text):
    """Lines that contain an 8–10 digit HSN/SAC code and a trailing amount."""
    lines = []
    for ln in text.splitlines():
        hsn = re.search(r"\b(\d{8,10})\b", ln)
        amt = re.search(r"([-+]?[\d.,]+\d)\s*$", ln.strip())
        if hsn and amt:
            val = _norm_decimal(amt.group(1))
            if val is not None:
                lines.append({"hsn": hsn.group(1), "taxable_amount": val})
    return lines
