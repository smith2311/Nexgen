"""Invoice consolidation: many invoices in, one flat table out.

Finance receives a month of sales invoices as a folder of PDFs — or, when the
ERP cooperated, as spreadsheets — and needs them as a single line-item table.
This module flattens a mixed batch of both into uniform row dicts keyed to match
:class:`merilv.models.ConsolidationRow`, so the caller can bulk-create rows
without remapping anything.

Two things are worth being blunt about:

*   **PDF parsing is a heuristic and cannot be anything else.** A PDF carries
    glyphs at coordinates, not a table. Layouts differ per printer, per ERP and
    per financial year, and ``pypdf`` flattens them to lines of text whose column
    boundaries have already been lost. Every PDF row therefore carries a
    ``confidence`` score, and a page that yields nothing is reported in the
    warnings rather than quietly discarded — an invoice missing from the
    consolidated sheet is a reconciliation failure weeks later, so silence is the
    one unacceptable outcome.
*   **Spreadsheets go through :mod:`merilv.services.ingest`.** The same alias
    resolution that copes with SAP's unstable headers copes with accounting
    exports, so no second column-matching implementation exists here.

``pypdf`` is imported lazily so the module — and the spreadsheet path, and the
Django app that imports it at startup — keeps working when the dependency is not
installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .ingest import ColumnResolver, IngestError, cell, date_at, load_sheet, number_at, text_at

PDF_EXTENSIONS = (".pdf",)
SHEET_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".tsv")

#: Invoice date format written into the row dicts. ``ConsolidationRow.invoice_date``
#: is a CharField because unparseable dates are kept verbatim rather than dropped.
DATE_OUTPUT_FORMAT = "%d-%m-%Y"

_DATE_INPUT_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%y",
    "%d/%m/%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%m/%d/%Y",
)


class ConsolidationError(ValueError):
    """Raised when a file cannot be consolidated at all."""


class MissingDependencyError(ConsolidationError):
    """Raised when an optional parsing library is not installed.

    A distinct subclass so :func:`consolidate` can abort the whole batch instead
    of appending the same "pip install" instruction once per uploaded file.
    """


@dataclass
class ConsolidationResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    row_count: int = 0


# ─── Shared helpers ──────────────────────────────────────────────────────────
def _to_float(value: Any) -> float | None:
    """Parse a money/quantity token, or ``None`` when there is nothing to parse.

    ``None`` rather than ``0.0`` because the models allow nulls and a blank cell
    genuinely differs from a zero-value line.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalise_date(value: Any) -> str:
    """Render a date consistently, keeping the original text when unrecognised."""
    if isinstance(value, datetime):
        return value.strftime(DATE_OUTPUT_FORMAT)
    if isinstance(value, date):
        return value.strftime(DATE_OUTPUT_FORMAT)
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime(DATE_OUTPUT_FORMAT)
        except ValueError:
            continue
    return text


def _blank_row(source_file: str) -> dict[str, Any]:
    """The row shape both parsers emit, matching ``ConsolidationRow`` fields."""
    return {
        "source_file": source_file,
        "invoice_no": "",
        "invoice_date": "",
        "customer_name": "",
        "material": "",
        "description": "",
        "batch": "",
        "quantity": None,
        "rate": None,
        "amount": None,
        "confidence": 0.0,
        "data": {},
    }


# ─── PDF parsing ─────────────────────────────────────────────────────────────
#: Indian tax-invoice header labels. The separator between a label and its value
#: is a free-for-all — "Invoice No. : X", "Invoice No- X", or the label on one
#: line and the value on the next once the PDF has been flattened — so
#: punctuation and whitespace are matched as one interchangeable run.
_LABEL_GAP = r"[\s.:#\-]*"
INVOICE_NO_RE = re.compile(
    rf"(?:tax\s+)?invoice\s*(?:no|number|#)\b{_LABEL_GAP}([A-Za-z0-9][A-Za-z0-9/\-]{{2,30}})",
    re.IGNORECASE,
)
INVOICE_DATE_RE = re.compile(
    r"(?:invoice|bill|document|dated)\s*(?:date)?"
    + _LABEL_GAP
    + r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4})",
    re.IGNORECASE,
)
CUSTOMER_RE = re.compile(
    r"(?:bill(?:ed)?\s*to|buyer(?:\s*\(bill\s*to\))?|sold\s*to|customer\s*name)\s*[:\-]?\s*(.*)",
    re.IGNORECASE,
)
GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b")
HSN_RE = re.compile(r"\b(\d{4,8})\b")
BATCH_LABEL_RE = re.compile(
    r"(?:batch|b\.?\s*no|lot\s*no|lot)\s*[.:#\-]*\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,20})",
    re.IGNORECASE,
)

_NUMBER_TOKEN = r"[0-9][0-9,]*(?:\.\d+)?"
#: A line item is recognised by its numeric tail: quantity, rate, amount. Tax and
#: discount columns sit between the description and these three often enough that
#: anchoring on the tail beats trying to model the whole row.
LINE_TAIL_RE = re.compile(
    rf"^(?P<head>.*?)\s+(?P<qty>{_NUMBER_TOKEN})\s+(?P<rate>{_NUMBER_TOKEN})\s+(?P<amount>{_NUMBER_TOKEN})"
    r"\s*(?:[A-Za-z%₹]{0,3})\s*$"
)
_LEADING_SERIAL_RE = re.compile(r"^\d{1,3}\s*[.)\-]?\s+")
#: Hyphenated codes are matched as one token before bare words, so ``MI-KNEE-042``
#: does not get chopped into a two-letter stub plus ``KNEE-042``.
_CODE_TOKEN_RE = re.compile(r"\b([A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+|[A-Za-z0-9]{3,})\b")

#: Total lines, matched precisely rather than by prefix. "Total 95,101.50" and
#: "Total Amount:" are invoice furniture, but "Total Knee System Femoral" is a
#: product Meril actually sells — so a bare "total" prefix cannot be the test.
#: A total is a total only when the label is followed by a figure or nothing.
_NOISE_TOTAL_RE = re.compile(
    r"^(?:grand\s+|sub\s*)?total\b\s*(?:amount|value|qty|quantity|invoice\s+value)?\s*[:\-]?\s*(?:[\d(₹]|$)",
    re.IGNORECASE,
)

#: Lines *starting* with these are invoice furniture, not products.
_NOISE_PREFIXES = (
    "cgst",
    "sgst",
    "igst",
    "utgst",
    "gst",
    "tax ",
    "taxable",
    "round",
    "freight",
    "insurance",
    "discount",
    "less ",
    "add ",
    "hsn/sac",
    "page ",
    "continued",
    "declaration",
    "terms",
    "bank",
    "gstin",
    "pan ",
    "state code",
    "invoice no",
    "invoice date",
    "bill to",
    "ship to",
    "sr.",
    "sr ",
    "s.no",
    "sl.no",
)
#: Unambiguous phrases that make a line furniture wherever they appear.
_NOISE_PHRASES = (
    "amount in words",
    "authorised signatory",
    "authorized signatory",
    "subject to",
    "e.&o.e",
    "e. & o.e",
    "computer generated",
    "for meril",
)


def _is_noise(line: str) -> bool:
    lowered = line.strip().lower()
    if not lowered:
        return True
    if any(phrase in lowered for phrase in _NOISE_PHRASES):
        return True
    if _NOISE_TOTAL_RE.match(lowered):
        return True
    return lowered.startswith(_NOISE_PREFIXES)


def _load_pdf_reader():
    """Import ``pypdf`` on demand.

    Deferred so that a deployment without the dependency can still consolidate
    spreadsheets, and so importing this module never fails at Django startup.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise MissingDependencyError(
            "Reading PDF invoices requires the 'pypdf' package. "
            "Install it with 'pip install pypdf', or upload the invoices as Excel/CSV instead."
        ) from exc
    return PdfReader


def _pdf_page_texts(file_obj: Any, filename: str) -> list[str]:
    """Extract each page's text, substituting an empty string for failures.

    One unreadable page must not lose the rest of the document, so extraction
    errors become blank pages that the caller reports as warnings.
    """
    reader_class = _load_pdf_reader()
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    try:
        reader = reader_class(file_obj)
        if getattr(reader, "is_encrypted", False):
            # Many ERPs apply an owner password with an empty user password;
            # that case decrypts silently and is worth trying before giving up.
            reader.decrypt("")
        pages = list(reader.pages)
    except MissingDependencyError:
        raise
    except Exception as exc:
        raise ConsolidationError(f"Could not read {filename or 'the PDF'}: {exc}") from exc

    texts: list[str] = []
    for page in pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return texts


def _extract_customer(lines: Sequence[str]) -> str:
    """Find the bill-to party.

    The name usually sits on the line *after* the "Bill To" label once the PDF
    has been flattened, so the following lines are searched when the label line
    itself has no useful tail.
    """
    for index, line in enumerate(lines):
        match = CUSTOMER_RE.search(line)
        if not match:
            continue
        tail = match.group(1).strip(" :-\t")
        tail = GSTIN_RE.sub("", tail).strip(" ,:-")
        if len(tail) >= 3:
            return tail[:300]
        for follow in lines[index + 1 : index + 4]:
            candidate = GSTIN_RE.sub("", follow).strip(" ,:-")
            if len(candidate) >= 3 and not _is_noise(candidate):
                return candidate[:300]
    return ""


def _extract_invoice_no(text: str) -> str:
    """Find the invoice number, rejecting captures that are only words.

    The label/value separator has to tolerate a line break, which means the
    pattern can otherwise run on into the next column heading. Every real
    invoice number contains a digit, and that is enough to tell them apart.
    """
    for match in INVOICE_NO_RE.finditer(text):
        candidate = match.group(1).strip(" .:-")
        if any(char.isdigit() for char in candidate):
            return candidate
    return ""


def _extract_header(text: str) -> dict[str, str]:
    """Pull the invoice-level fields shared by every line on the page."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]

    date_match = INVOICE_DATE_RE.search(text)
    gstin_match = GSTIN_RE.search(text)

    return {
        "invoice_no": _extract_invoice_no(text),
        "invoice_date": _normalise_date(date_match.group(1)) if date_match else "",
        "customer_name": _extract_customer(lines),
        "gstin": gstin_match.group(1) if gstin_match else "",
    }


def _looks_like_code(token: str) -> bool:
    """True for tokens shaped like a material code rather than prose.

    SAP material numbers are long digit strings; Meril's own codes mix letters
    and digits. A plain word matches neither and is left in the description.
    """
    if len(token) < 3:
        return False
    has_digit = any(char.isdigit() for char in token)
    has_alpha = any(char.isalpha() for char in token)
    if has_digit and has_alpha:
        return True
    return token.isdigit() and len(token) >= 6


def _split_head(head: str) -> tuple[str, str, str]:
    """Break a line item's leading text into material, description and HSN.

    HSN is claimed before the material, but only from a digit run that is *not*
    the row's leading token: Indian layouts print HSN after the description,
    whereas a numeric SAP material number leads the row. Without that guard the
    two swap places on rows that have one but not the other.

    Tokens are removed by position rather than by value — the same code can
    legitimately appear again inside the description.
    """
    text = _LEADING_SERIAL_RE.sub("", " ".join(head.split())).strip()
    spans = [(match.start(), match.end(), match.group(1)) for match in _CODE_TOKEN_RE.finditer(text)]
    consumed: list[tuple[int, int]] = []

    hsn = ""
    for start, end, token in reversed(spans):
        # HSN codes are 4, 6 or 8 digits by statute.
        if start > 0 and token.isdigit() and len(token) in (4, 6, 8):
            hsn = token
            consumed.append((start, end))
            break

    material = ""
    for start, end, token in spans:
        if (start, end) in consumed:
            continue
        if _looks_like_code(token):
            material = token
            consumed.append((start, end))
            break

    for start, end in sorted(consumed, reverse=True):
        text = text[:start] + " " + text[end:]

    description = " ".join(text.split()).strip(" -,:")
    return material.upper(), description[:400], hsn


def _confidence(invoice_no: str, material: str, batch: str, quantity: Any, rate: Any, amount: Any) -> float:
    """Score how much of a PDF row was actually recognised, not guessed.

    Exposed per row so an operator can sort the consolidated sheet worst-first
    and check only the lines that need checking.
    """
    score = 0.25
    if material:
        score += 0.25
    if invoice_no:
        score += 0.15
    if batch:
        score += 0.10
    if quantity:
        score += 0.10
    if rate:
        score += 0.05
    if amount:
        score += 0.10
    return round(min(score, 1.0), 2)


def _parse_page_items(text: str, header: dict[str, str], source_file: str, page_no: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 8 or _is_noise(line):
            continue
        match = LINE_TAIL_RE.match(line)
        if not match:
            continue

        quantity = _to_float(match.group("qty"))
        rate = _to_float(match.group("rate"))
        amount = _to_float(match.group("amount"))
        if not quantity or quantity <= 0 or amount is None:
            continue

        head = match.group("head")
        if not any(char.isalpha() for char in head):
            # No words at all — a totals or tax-summary row that slipped the filter.
            continue

        batch_match = BATCH_LABEL_RE.search(line)
        # Unlabelled batches are left blank on purpose: guessing which token is a
        # batch produces confidently wrong data, which is worse than an empty cell.
        batch = batch_match.group(1).strip(" .:-").upper() if batch_match else ""
        if batch_match:
            head = head.replace(batch_match.group(0), " ")

        material, description, hsn = _split_head(head)

        row = _blank_row(source_file)
        row.update(
            {
                "invoice_no": header.get("invoice_no", ""),
                "invoice_date": header.get("invoice_date", ""),
                "customer_name": header.get("customer_name", ""),
                "material": material,
                "description": description,
                "batch": batch,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "confidence": _confidence(header.get("invoice_no", ""), material, batch, quantity, rate, amount),
                "data": {
                    "source": "pdf",
                    "page": page_no,
                    "hsn": hsn,
                    "gstin": header.get("gstin", ""),
                    "raw_line": line[:500],
                },
            }
        )
        rows.append(row)
    return rows


def parse_invoice_pdf(file_obj: Any, filename: str = "", warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """Extract invoice line items from a PDF, best-effort.

    Text is pulled per page, invoice-level fields are matched by label, and line
    items are recognised by their trailing quantity/rate/amount triple. Because
    layouts vary by ERP, printer and year, every returned row carries a
    ``confidence`` between 0 and 1 describing how much of it was recognised
    rather than defaulted — treat anything below roughly 0.6 as needing a human.

    Header fields carry forward across pages: a multi-page invoice usually prints
    them on page one only, and a continuation page must not lose its identity.

    Pages that yield no line items are appended to ``warnings`` when a list is
    supplied. That is the *only* channel for them — nothing is dropped silently,
    because a missing invoice surfaces as an unexplained reconciliation gap weeks
    later. A page with no text at all is almost always a scanned image, and is
    reported as such since no amount of regex work will help it.
    """
    sink = warnings if warnings is not None else []
    label = filename or getattr(file_obj, "name", "") or "the PDF"
    pages = _pdf_page_texts(file_obj, label)

    rows: list[dict[str, Any]] = []
    carried: dict[str, str] = {"invoice_no": "", "invoice_date": "", "customer_name": "", "gstin": ""}
    #: Pages that yielded nothing, held back rather than warned about
    #: immediately. Every real MLSIPL export invoice carries a second page of
    #: packing and weight boilerplate with no line items on it, so warning per
    #: empty page told the operator four times that a perfectly good batch of
    #: invoices needed checking by hand. Only worth reporting if the *file*
    #: produced nothing at all.
    empty_pages: list[int] = []

    for index, text in enumerate(pages, start=1):
        if not text.strip():
            sink.append(f"{label} page {index} — no text layer (scanned image?); nothing could be extracted.")
            continue

        header = _extract_header(text)
        # Carry forward, don't overwrite: page 2 of an invoice keeps page 1's identity.
        for key, value in header.items():
            if value:
                carried[key] = value
            else:
                header[key] = carried.get(key, "")

        page_rows = _parse_page_items(text, header, label, index)
        if not page_rows:
            # The domestic GST layout above anchors on a trailing qty/rate/amount
            # triple, which an export invoice never has (USD unit price only, no
            # GST/HSN). Rather than report a page as empty just because it is the
            # OTHER layout, try that one before giving up on it.
            page_rows = _parse_export_page_items(text, re.sub(r"\.[^./]+$", "", label), label, index)
        if not page_rows:
            empty_pages.append(index)
            continue
        rows.extend(page_rows)

    if not rows:
        sink.append(f"{label} — no line items could be extracted from any page.")
    elif empty_pages:
        # Informational, and only once per file: the invoice did parse.
        pages_text = ", ".join(str(page) for page in empty_pages)
        sink.append(
            f"{label} — {len(rows)} line item(s) read; page(s) {pages_text} carried no line items "
            "(usually packing or terms boilerplate)."
        )
    return rows


# ─── MLSIPL export-invoice parsing ───────────────────────────────────────────
# A second, unrelated layout: USD unit pricing rather than GST/HSN, a lot
# number shaped like V/U/M followed by 7-10 digits, a trailing FDA 510(k)
# clearance number, and two D-Mon-YY dates per line (manufacture, expiry).
# Ported from the legacy tool's own MLSIPL export-invoice screen
# (``Export-Consolidator.html:extractInvoiceDataPDF``) — tried only when the
# domestic strategy above finds nothing on a page, since a real invoice is
# never both at once.
_EXPORT_INVOICE_LABEL_RE = re.compile(r"invoice\s*(?:#|no\.?|number)?", re.IGNORECASE)
_EXPORT_INVOICE_DATE_RE = re.compile(r"date\s*[:\-]?\s*(\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4})", re.IGNORECASE)
_EXPORT_NOISE_LINE_RE = re.compile(r"customer|date|ship", re.IGNORECASE)
_EXPORT_ITEM_RE = re.compile(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*-?")
_EXPORT_LONG_CODE_RE = re.compile(r"\b[A-Z0-9]{8,20}\b")
_EXPORT_PRICE_RE = re.compile(r"\$?\d{1,3}(?:,\d{3})*\.\d{2}")
_EXPORT_LOT_RE = re.compile(r"[VUM]\d{7,10}", re.IGNORECASE)
_EXPORT_DATE_RE = re.compile(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}")
_EXPORT_510K_RE = re.compile(r"K\d{6}", re.IGNORECASE)
_EXPORT_LEADING_QTY_RE = re.compile(r"^\s*(\d+)")

#: Boilerplate that reads like a line item but is not one. Verified against the
#: four real MLSIPL shipping invoices in ``module_info/``: the tariff line
#: "Harmonized System Code: 9021.31.000000 / Artificial Joints" was being parsed
#: as material "000000" at $21.31, because "000000" is code-shaped and "21.31"
#: is price-shaped. The legacy HTML reader had the same false positive.
_EXPORT_SKIP_LINE_RE = re.compile(
    r"harmonized\s+system|hs\s*code|hsn|country\s+of\s+origin|total\s+quantity"
    r"|sub\s*total|subtotal|sales\s+tax|gross\s+weight|reviewed\s+by|authorized\s+by"
    r"|all\s+prices\s+are|entry\s+number|re-?exported|telephone|^total\b",
    re.IGNORECASE,
)


def _export_header(text: str, fallback_name: str) -> dict[str, str]:
    """Invoice number + date for the export layout — no GSTIN, no HSN."""
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    invoice_no = fallback_name
    invoice_date = ""
    for index, line in enumerate(lines):
        if _EXPORT_INVOICE_LABEL_RE.search(line):
            tail = _EXPORT_INVOICE_LABEL_RE.split(line, maxsplit=1)[-1]
            tail = re.split(r"date", tail, maxsplit=1, flags=re.IGNORECASE)[0]
            tail = re.split(r"customer", tail, maxsplit=1, flags=re.IGNORECASE)[0].strip(" :-\t")
            if tail:
                invoice_no = tail
            elif index + 1 < len(lines):
                candidate = lines[index + 1].strip()
                if candidate and not _EXPORT_NOISE_LINE_RE.search(candidate):
                    invoice_no = candidate
        if not invoice_date:
            date_match = _EXPORT_INVOICE_DATE_RE.search(line)
            if date_match:
                invoice_date = date_match.group(1).strip()
    return {"invoice_no": invoice_no, "invoice_date": invoice_date, "customer_name": ""}


def _parse_export_page_items(
    text: str, fallback_name: str, source_file: str, page_no: int
) -> list[dict[str, Any]]:
    header = _export_header(text, fallback_name)
    rows: list[dict[str, Any]] = []

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        # Tariff/total/footer boilerplate first, before any of the shape tests —
        # some of it genuinely looks like a line item (see _EXPORT_SKIP_LINE_RE).
        if _EXPORT_SKIP_LINE_RE.search(line):
            continue

        # The legacy tool took the *first* regex match unconditionally, which
        # on a line like "2 EA V1234-01 Widget …" matches the bare leading
        # quantity "2" — `[A-Z0-9]+` matches a lone digit just as happily as a
        # real code. Scanning every candidate and keeping the first one that
        # is actually code-shaped (via the same ``_looks_like_code`` the
        # domestic strategy uses) fixes that without abandoning the pattern.
        candidates = [m.group(0) for m in _EXPORT_ITEM_RE.finditer(line)]
        candidates += [m.group(0) for m in _EXPORT_LONG_CODE_RE.finditer(line)]
        item_code = next((c.upper() for c in candidates if _looks_like_code(c)), "")
        price_matches = _EXPORT_PRICE_RE.findall(line)
        lot_match = _EXPORT_LOT_RE.search(line)
        # No code-shaped token, or neither a price nor a lot number, means
        # this is prose, not a line item — the same "needs at least one
        # strong signal" rule the domestic strategy applies via its numeric tail.
        if not item_code or not (price_matches or lot_match):
            continue
        qty_re = re.compile(rf"(?:^|\s)(\d+)\s+(?:EA\s+)?{re.escape(item_code)}", re.IGNORECASE)
        qty_match = qty_re.search(line) or _EXPORT_LEADING_QTY_RE.search(line)
        quantity = _to_float(qty_match.group(1)) if qty_match else None

        dates = _EXPORT_DATE_RE.findall(line)
        mfg_date = dates[0] if dates else ""
        exp_date = dates[1] if len(dates) > 1 else mfg_date
        lot = lot_match.group(0).upper() if lot_match else ""

        stripped = _EXPORT_510K_RE.sub("", _EXPORT_PRICE_RE.sub("", line))
        if lot:
            parts = stripped.split(lot, 1)
            description = parts[1] if len(parts) > 1 else ""
        else:
            parts = stripped.split(item_code, 1)
            description = parts[1] if len(parts) > 1 else ""
            for token in (mfg_date, exp_date):
                if token:
                    description = description.replace(token, "")
        description = re.sub(r"^[-\s]+", "", description.strip())[:400]

        # Two prices on an export line: unit price then line total ("$125.00
        # $11,625.00"). The domestic strategy calls these rate/amount, so the
        # same columns carry them here rather than inventing parallel fields.
        rate = _to_float(price_matches[0]) if price_matches else None
        amount = _to_float(price_matches[-1]) if len(price_matches) > 1 else rate
        k_match = _EXPORT_510K_RE.search(line)

        row = _blank_row(source_file)
        row.update(
            {
                "invoice_no": header["invoice_no"],
                "invoice_date": _normalise_date(header["invoice_date"]),
                "customer_name": header["customer_name"],
                "material": item_code,
                "description": description or "Details in PDF",
                "batch": lot,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "confidence": _confidence(header["invoice_no"], item_code, lot, quantity, rate, amount),
                "data": {
                    "source": "pdf_export",
                    "page": page_no,
                    # Numbered per invoice, not per page: the client's sheet has
                    # one "Line #" column running through the whole invoice.
                    "line_no": len(rows) + 1,
                    "mfg_date": mfg_date,
                    "expiry_date": exp_date,
                    "k_number": k_match.group(0).upper() if k_match else "",
                    "raw_line": line[:500],
                },
            }
        )
        rows.append(row)
    return rows


# ─── Spreadsheet parsing ─────────────────────────────────────────────────────
#: Header aliases, most specific first so "Invoice Date" is never mistaken for
#: the invoice number. Resolution runs exact-match before substring-match.
SHEET_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_no": (
        "invoice no",
        "invoice no.",
        "invoice number",
        "invoice #",
        "bill no",
        "bill no.",
        "billing document",
        "document no",
        "voucher no",
        "invoice",
    ),
    "invoice_date": ("invoice date", "bill date", "billing date", "document date", "posting date", "date"),
    "customer_name": (
        "customer name",
        "bill to",
        "billed to",
        "buyer name",
        "sold to",
        "party name",
        "name 1",
        "customer",
        "buyer",
    ),
    "material": ("material code", "material", "item code", "product code", "sku code", "sku", "article", "item no"),
    "description": (
        "material description",
        "item description",
        "product description",
        "description",
        "particulars",
        "product name",
        "item name",
    ),
    "batch": ("batch no", "batch number", "batch", "lot no", "lot number", "lot"),
    "quantity": ("invoice qty", "billed qty", "quantity", "qty", "qnty"),
    "rate": ("unit rate", "unit price", "rate", "price", "mrp"),
    "amount": ("taxable value", "net amount", "line total", "total amount", "amount", "value"),
}

#: Terms that must not be swallowed by a looser alias when matched as substrings.
_SHEET_EXCLUDES: dict[str, tuple[str, ...]] = {
    "invoice_no": ("date",),
    "customer_name": ("code", "no.", "gstin"),
    "material": ("description", "desc", "name"),
    "quantity": ("uom", "unit of"),
    "amount": ("tax amount", "gst"),
}


def _resolve_sheet_columns(resolver: ColumnResolver) -> dict[str, int]:
    columns: dict[str, int] = {}
    for name, aliases in SHEET_ALIASES.items():
        exclude = _SHEET_EXCLUDES.get(name, ())
        index = resolver.exact(*aliases)
        if index < 0:
            index = resolver.contains(*aliases, exclude=exclude)
        columns[name] = index
    return columns


def _optional_number(row: Sequence[Any], index: int) -> float | None:
    """Read a numeric cell, keeping "absent" distinct from "zero"."""
    if index < 0:
        return None
    raw = cell(row, index, None)
    if raw is None or str(raw).strip() == "":
        return None
    return number_at(row, index, default=0.0)


def parse_invoice_sheet(file_obj: Any, filename: str = "", warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """Extract invoice line items from an Excel/CSV export.

    Column resolution is delegated to :class:`merilv.services.ingest.ColumnResolver`
    so accounting exports get the same alias tolerance as SAP dumps, and the
    header row is located by content rather than assumed to be row one.

    Invoice-level fields are forward-filled down the sheet: exports commonly
    print the invoice number, date and customer once per invoice and leave the
    continuation lines blank, and a blank there means "same as above", not
    "unknown".
    """
    sink = warnings if warnings is not None else []
    label = filename or getattr(file_obj, "name", "") or "the spreadsheet"

    sheet = load_sheet(
        file_obj,
        required=[("material", "item", "description", "particulars", "product")],
        fallbacks=[[("invoice", "bill")], [("qty", "quantity")]],
        filename=filename,
    )
    resolver = ColumnResolver(sheet.headers)
    columns = _resolve_sheet_columns(resolver)

    if columns["material"] < 0 and columns["description"] < 0:
        raise ConsolidationError(f"{label} has neither a material nor a description column.")

    # A resolved material column means the values were read, not inferred; without
    # one the rows are still usable but deserve a visible haircut.
    base_confidence = 1.0 if columns["material"] >= 0 else 0.7

    rows: list[dict[str, Any]] = []
    carried = {"invoice_no": "", "invoice_date": "", "customer_name": ""}
    skipped = 0

    for source_row in sheet.rows:
        material = text_at(source_row, columns["material"])
        description = text_at(source_row, columns["description"])
        quantity = _optional_number(source_row, columns["quantity"])
        amount = _optional_number(source_row, columns["amount"])
        if not material and not description and quantity is None and amount is None:
            skipped += 1
            continue

        invoice_no = text_at(source_row, columns["invoice_no"]) or carried["invoice_no"]
        parsed_date = date_at(source_row, columns["invoice_date"]) if columns["invoice_date"] >= 0 else None
        invoice_date = _normalise_date(parsed_date or text_at(source_row, columns["invoice_date"]))
        invoice_date = invoice_date or carried["invoice_date"]
        customer_name = text_at(source_row, columns["customer_name"]) or carried["customer_name"]
        carried.update({"invoice_no": invoice_no, "invoice_date": invoice_date, "customer_name": customer_name})

        row = _blank_row(label)
        row.update(
            {
                "invoice_no": invoice_no,
                "invoice_date": invoice_date,
                "customer_name": customer_name[:300],
                "material": material.upper(),
                "description": description[:400],
                "batch": text_at(source_row, columns["batch"]).upper(),
                "quantity": quantity,
                "rate": _optional_number(source_row, columns["rate"]),
                "amount": amount,
                "confidence": base_confidence,
                "data": {"source": "sheet"},
            }
        )
        rows.append(row)

    if skipped:
        sink.append(f"{label} — {skipped} empty row(s) skipped.")
    if not rows:
        sink.append(f"{label} — no invoice lines were found.")
    return rows


# ─── Batch entry point ───────────────────────────────────────────────────────
def _unpack(entry: Any) -> tuple[Any, str]:
    """Accept either a file-like object or an explicit ``(file, name)`` pair."""
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        file_obj, filename = entry
        return file_obj, str(filename or "")
    return entry, str(getattr(entry, "name", "") or "")


def consolidate(files: Iterable[Any]) -> ConsolidationResult:
    """Flatten a mixed batch of invoice PDFs and spreadsheets into one table.

    Per-file failures become warnings instead of exceptions: a consolidation run
    is usually dozens of files, and one corrupt PDF should not cost the operator
    the other forty. A missing parsing library is the exception — it is an
    environment fault rather than a data fault, so it aborts immediately instead
    of repeating the same install instruction once per file.
    """
    result = ConsolidationResult()

    for entry in files:
        file_obj, filename = _unpack(entry)
        result.file_count += 1
        lowered = filename.lower()

        try:
            if lowered.endswith(PDF_EXTENSIONS):
                rows = parse_invoice_pdf(file_obj, filename, result.warnings)
            elif lowered.endswith(SHEET_EXTENSIONS):
                rows = parse_invoice_sheet(file_obj, filename, result.warnings)
            else:
                result.warnings.append(f"{filename or 'A file'} — unsupported type; expected a PDF, Excel or CSV file.")
                continue
        except MissingDependencyError:
            raise
        except (ConsolidationError, IngestError) as exc:
            result.warnings.append(f"{filename or 'A file'} — {exc}")
            continue

        for row in rows:
            row["sequence"] = len(result.rows)
            result.rows.append(row)

    result.row_count = len(result.rows)
    return result


__all__ = [
    "DATE_OUTPUT_FORMAT",
    "PDF_EXTENSIONS",
    "SHEET_ALIASES",
    "SHEET_EXTENSIONS",
    "ConsolidationError",
    "ConsolidationResult",
    "MissingDependencyError",
    "consolidate",
    "parse_invoice_pdf",
    "parse_invoice_sheet",
]
