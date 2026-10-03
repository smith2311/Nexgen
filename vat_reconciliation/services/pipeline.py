"""End-to-end invoice processing pipeline.

Upload -> OCR -> Translation -> Country ID -> Currency -> VAT calc -> Reconcile.

Each stage updates VatInvoice.stage so progress is visible and the whole thing
can be re-run. Any failure is captured on invoice.process_error and re-raised
to the caller (the view turns it into a JSON error).
"""
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..models import VatCountryMaster, VatInvoice, VatVatCalculation
from . import currency, extract, ocr, reconciliation, translation, vat_engine

logger = logging.getLogger(__name__)


def _console(title, body):
    """Print a clearly delimited block to the runserver console (and logs)."""
    bar = "═" * 70
    if not isinstance(body, str):
        import json
        body = json.dumps(body, default=str, ensure_ascii=False, indent=2)
    block = "\n%s\n[VAT] %s\n%s\n%s\n%s\n" % (bar, title, bar, body, bar)
    try:
        print(block, flush=True)
    except Exception:  # noqa: BLE001 — console encoding can choke on Unicode
        print(block.encode("ascii", "replace").decode("ascii"), flush=True)
    logger.info("[VAT] %s\n%s", title, body)


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def process_invoice(invoice):
    """Run the full pipeline on a VatInvoice that already has document(s)."""
    try:
        doc = invoice.documents.first()
        if not doc:
            raise ValueError("No document attached to invoice.")

        # ── Step 1: OCR (raw text only) ──────────────────────────────────
        doc.file.open("rb")
        file_bytes = doc.file.read()
        doc.file.close()
        # invoice.language (if pre-set) selects the OCR language pack.
        ocr_result = ocr.extract(file_bytes, doc.mime_type, doc.file_name,
                                 language=invoice.language)
        invoice.raw_ocr_text = ocr_result.get("raw_text", "")
        invoice.ocr_provider = ocr_result.get("provider")
        invoice.stage = VatInvoice.Stage.OCR
        invoice.save()
        _console("STEP 1 · OCR EXTRACTED (%s, lang=%s)" % (
            invoice.ocr_provider, invoice.language or "eng"), invoice.raw_ocr_text)

        # A provider may return structured fields directly (e.g. the AI Data
        # Extractor V3, which does OCR + translation + extraction in one call).
        combined = ocr_result.get("structured")

        if combined:
            # ── Combined path: V3 already extracted the English fields ───
            # V3 returns structured English fields but NOT a translated body,
            # so translate the original-language raw_text for the English panel.
            invoice.source_language = ocr_result.get("language") or invoice.language
            trans = translation.to_english(invoice.raw_ocr_text, invoice.source_language)
            invoice.translated_text = trans.get("translated") or invoice.raw_ocr_text
            invoice.stage = VatInvoice.Stage.TRANSLATED
            invoice.save()
            structured = combined
            _console("STEP 2 · AI EXTRACTOR fields + translation (%s)" % trans.get("provider"),
                     structured)
        else:
            # ── Step 2: Translation (raw text → English via webhook) ─────
            trans = translation.to_english(invoice.raw_ocr_text, invoice.language)
            invoice.translated_text = trans.get("translated", "")
            invoice.source_language = trans.get("source_language") or invoice.language
            invoice.stage = VatInvoice.Stage.TRANSLATED
            invoice.save()
            _console("STEP 2 · TRANSLATED TO ENGLISH (%s)" % trans.get("provider"),
                     invoice.translated_text)
            # ── Step 2.5: Extract structured fields from the English text ─
            structured = extract.parse(invoice.translated_text)

        invoice.ocr_json = structured
        if structured.get("invoice_number"):
            invoice.invoice_number = str(structured["invoice_number"])[:100]
        invoice.save()
        _console("STEP 2.5 · EXTRACTED FIELDS", structured)

        # ── Step 3: Country identification ───────────────────────────────
        country_name = structured.get("country") or invoice.country
        cm = None
        if country_name:
            cm = VatCountryMaster.objects.filter(
                country__iexact=str(country_name).strip(), is_active=True
            ).first()
        if cm:
            invoice.country = cm.country
            invoice.local_entity = cm.local_entity
            invoice.cha_partner = cm.cha_partner_name
            invoice.local_currency = cm.local_currency
        else:
            invoice.country = country_name

        # Pull header amounts from OCR if not already set
        invoice.invoice_currency = structured.get("currency") or invoice.invoice_currency
        if not invoice.invoice_date:
            invoice.invoice_date = _parse_date(structured.get("invoice_date"))
        invoice.taxable_amount = _to_decimal(structured.get("taxable_amount")) or invoice.taxable_amount
        invoice.charged_vat = _to_decimal(structured.get("charged_vat")) or invoice.charged_vat
        invoice.total_amount = _to_decimal(structured.get("total_amount")) or invoice.total_amount
        invoice.save()

        # ── Step 4: Currency normalization ───────────────────────────────
        inv_ccy = invoice.invoice_currency
        loc_ccy = invoice.local_currency or (cm.local_currency if cm else None)
        if inv_ccy and loc_ccy and invoice.taxable_amount is not None:
            conv = currency.convert(invoice.taxable_amount, inv_ccy, loc_ccy)
            invoice.exchange_rate = conv["rate"]
            invoice.converted_taxable = conv["converted"]
            invoice.rate_source = conv["source"]
            invoice.rate_date = timezone.now().date()
        invoice.stage = VatInvoice.Stage.CONVERTED
        invoice.save()

        # ── Step 5: VAT calculation ──────────────────────────────────────
        lines = _extract_lines(structured, invoice)
        result = vat_engine.compute(invoice.country, lines)

        # Persist working-summary lines (replace any previous run)
        invoice.calc_lines.all().delete()
        for ln in result["lines"]:
            VatVatCalculation.objects.create(
                invoice=invoice,
                hsn_sac_code=ln.get("hsn") or "",
                taxable_amount=ln.get("taxable_amount") or Decimal("0"),
                vat_percent=ln.get("vat_percent") or Decimal("0"),
                expected_vat=ln.get("expected_vat") or Decimal("0"),
            )
        invoice.expected_vat = result["grand_expected"]
        if invoice.taxable_amount is None:
            invoice.taxable_amount = result["grand_taxable"]
        invoice.stage = VatInvoice.Stage.CALCULATED
        invoice.save()

        # ── Step 6: Reconciliation ───────────────────────────────────────
        recon = reconciliation.reconcile(
            invoice.expected_vat, invoice.charged_vat, invoice.country
        )
        invoice.variance = recon["variance"]
        invoice.tolerance_used = recon["tolerance"]
        invoice.recon_status = recon["status"]
        invoice.stage = VatInvoice.Stage.RECONCILED
        invoice.process_error = None
        invoice.save()

        return invoice

    except Exception as exc:  # noqa: BLE001 — surface to the UI
        logger.exception("VAT pipeline failed for invoice %s", invoice.pk)
        invoice.process_error = str(exc)
        invoice.save(update_fields=["process_error", "updated_at"])
        raise


def _extract_lines(structured, invoice):
    """Build the list of {hsn, taxable_amount} lines for the VAT engine."""
    raw_lines = structured.get("lines") or structured.get("hsn_codes") or []
    lines = []
    for ln in raw_lines:
        if isinstance(ln, dict):
            amt = _to_decimal(ln.get("taxable_amount") or ln.get("amount"))
            lines.append({"hsn": ln.get("hsn") or ln.get("hsn_code"),
                          "taxable_amount": amt or Decimal("0")})
        elif isinstance(ln, str):
            lines.append({"hsn": ln, "taxable_amount": Decimal("0")})

    # No structured lines -> single flat line from the header taxable amount.
    if not lines and invoice.taxable_amount is not None:
        lines.append({"hsn": "", "taxable_amount": invoice.taxable_amount})
    return lines
