"""Configurable OCR provider for VAT invoices.

Default provider is OCR.space (REST API). Switch via settings.VAT_OCR_PROVIDER.
Adding a provider = write one function and register it in _PROVIDERS.

OCR.space returns plain text only (no structured fields) — the structured
invoice fields are recovered later by services/extract.py after translation.
"""
import logging

import requests
from django.conf import settings

from . import ai_extractor

logger = logging.getLogger(__name__)

# Human language / Country-Master language → OCR.space language code.
OCR_LANG_MAP = {
    "english": "eng", "german": "ger", "korean": "kor", "vietnamese": "vie",
    "russian": "rus", "spanish": "spa", "hungarian": "hun", "polish": "pol",
    "swedish": "swe", "italian": "ita", "french": "fre", "portuguese": "por",
}
# OCR.space Engine 2 handles Latin scripts only; non-Latin must use Engine 1.
NON_LATIN = {"kor", "rus", "jpn", "chs", "cht", "ara", "gre", "bul"}


def extract(file_bytes, mime_type, filename="", language=None):
    """Run OCR on a single invoice file.

    Returns: {'raw_text': str, 'structured': dict, 'provider': str}
    Raises RuntimeError/ValueError with a user-facing message on failure.
    """
    provider = getattr(settings, "VAT_OCR_PROVIDER", "ocrspace")
    fn = _PROVIDERS.get(provider)
    if not fn:
        raise ValueError(f"Unknown OCR provider '{provider}'")
    return fn(file_bytes, mime_type, filename, language)


def _lang_code(language):
    if not language:
        return getattr(settings, "VAT_OCR_LANGUAGE", "eng")
    key = str(language).strip().lower()
    return OCR_LANG_MAP.get(key, key if len(key) == 3 else "eng")


def _filetype(mime_type, filename):
    name = (filename or "").lower()
    if name.endswith(".pdf") or (mime_type or "").endswith("pdf"):
        return "PDF"
    if name.endswith((".jpg", ".jpeg")):
        return "JPG"
    if name.endswith(".png"):
        return "PNG"
    return None


def _ocrspace(file_bytes, mime_type, filename, language):
    key = getattr(settings, "VAT_OCR_SPACE_API_KEY", "")
    if not key:
        raise RuntimeError("VAT_OCR_SPACE_API_KEY not configured — OCR unavailable.")

    lang = _lang_code(language)
    engine = 1 if lang in NON_LATIN else 2
    data = {
        "language": lang,
        "isOverlayRequired": "false",
        "scale": "true",
        "detectOrientation": "true",
        "OCREngine": str(engine),
    }
    ftype = _filetype(mime_type, filename)
    if ftype:
        data["filetype"] = ftype

    resp = requests.post(
        getattr(settings, "VAT_OCR_SPACE_URL", "https://api.ocr.space/parse/image"),
        headers={"apikey": key},
        data=data,
        files={"file": (filename or "invoice", file_bytes, mime_type or "application/octet-stream")},
        timeout=90,
    )
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("IsErroredOnProcessing"):
        msg = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "OCR failed"
        if isinstance(msg, list):
            msg = "; ".join(msg)
        raise RuntimeError(f"OCR.space error: {msg}")

    results = payload.get("ParsedResults") or []
    raw_text = "\n".join((r.get("ParsedText") or "") for r in results).strip()
    return {"raw_text": raw_text, "structured": {}, "provider": "ocrspace"}


def _ai_extractor(file_bytes, mime_type, filename, language):
    """AI Data Extractor V3 — OCR + translation + structured extraction in one call.

    A non-empty 'structured' result signals the pipeline to skip the separate
    translation webhook and regex extractor (V3 already returns English fields).

    The live V3 response is page-wrapped (the PDF reference's flat shape is wrong):
        {"data": [{"FileName": ..., "Page": 1, "data": {<fields>}}], "message": ...}
    so we unwrap each page's inner 'data' and merge across pages.
    """
    resp = ai_extractor.extract_v3(file_bytes, mime_type, filename)

    pages = resp.get("data") if isinstance(resp, dict) else resp
    if not isinstance(pages, list):
        pages = [pages] if pages else []
    page_data = []
    for p in pages:
        if isinstance(p, dict):
            inner = p.get("data") if isinstance(p.get("data"), dict) else p
            page_data.append(inner or {})

    def pick(key):
        """First non-empty value for `key` across all pages."""
        for pd in page_data:
            v = pd.get(key)
            if v not in (None, ""):
                return v
        return None

    raw_lines = []
    for pd in page_data:
        for ln in (pd.get("lines") or []):
            if isinstance(ln, dict):
                raw_lines.append(ln)

    structured = {
        "invoice_number": pick("invoice_number"),
        "invoice_date":   pick("invoice_date"),
        "country":        pick("country"),
        "currency":       pick("currency"),
        "taxable_amount": pick("taxable_amount"),
        "charged_vat":    pick("charged_vat"),
        "total_amount":   pick("total_amount"),
        # Optional country-specific extras consumed by the per-country output
        # layouts (Australia customs duty, Korea/Hungary currency split). Absent
        # for most documents — the output screen falls back to 0/blank.
        "transport_insurance": pick("transport_insurance") or pick("transport_and_insurance")
                               or pick("freight_insurance") or pick("freight"),
        "quarantine_charge":   pick("quarantine_charge") or pick("quarantine_processing_charge"),
        "declaration_charge":  pick("declaration_charge") or pick("declaration_processing_charge"),
        "vat_percent":         pick("vat_percent") or pick("gst_percent") or pick("standard_gst"),
        "local_currency":      pick("local_currency"),
        "exchange_rate":       pick("exchange_rate"),
        "local_amount":        pick("local_amount") or pick("amount_local"),
        # V3 line items carry hsn_code + taxable_amount (+ optional local-currency amount).
        "lines": [
            {"hsn": ln.get("hsn_code") or ln.get("hsn") or "",
             "taxable_amount": ln.get("taxable_amount") or ln.get("amount"),
             "local_amount": (ln.get("local_amount") or ln.get("amount_local")
                              or ln.get("amount_huf") or ln.get("amount_krw")),
             "description": ln.get("description")}
            for ln in raw_lines
        ],
    }
    raw_text = "\n".join((pd.get("raw_text") or "") for pd in page_data).strip()
    return {
        "raw_text": raw_text,
        "structured": structured,
        "language": pick("language"),
        "provider": "ai_extractor",
    }


_PROVIDERS = {
    "ocrspace": _ocrspace,
    "ai_extractor": _ai_extractor,
}
