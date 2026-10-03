"""
GS1 DataMatrix / Barcode Parser — Hybrid Strategy
 1. GS1 standard-compliant parser (handles FNC1 and paren-AI formats)
 2. OpenAI fills any missing important fields
 3. Regex fallback when OpenAI not configured
"""

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI client — optional; parser works without it via GS1 + regex
# ---------------------------------------------------------------------------
try:
    from openai import APIConnectionError, APIError, OpenAI

    _OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    _client = OpenAI(api_key=_OPENAI_KEY) if _OPENAI_KEY else None
    if not _OPENAI_KEY:
        logger.warning("OPENAI_API_KEY not set — OpenAI enrichment disabled")
except ImportError:
    _client = None
    APIConnectionError = Exception
    APIError = Exception
    logger.warning("openai package not installed — OpenAI enrichment disabled")


# ---------------------------------------------------------------------------
# GS1 Application Identifier table
# length > 0  → fixed-length value (chars after AI)
# length == -1 → variable-length; terminated by FNC1 or end-of-string
# ---------------------------------------------------------------------------
GS1_AI = {
    # --- Identification ---
    "00": ("sscc", 18, False),
    "01": ("gtin_code", 14, False),
    "02": ("gtin_contained", 14, False),
    # --- Dates (YYMMDD) ---
    "11": ("production_date", 6, True),
    "12": ("due_date", 6, True),
    "13": ("packaging_date", 6, True),
    "15": ("best_before_date", 6, True),
    "16": ("sell_by_date", 6, True),
    "17": ("expiry_date", 6, True),
    # --- Batch / Serial ---
    "10": ("batch_number", -1, False),
    "20": ("variant", 2, False),
    "21": ("serial_number", -1, False),
    "22": ("secondary_data", -1, False),
    # --- Additional product IDs ---
    "240": ("additional_id", -1, False),
    "241": ("customer_part", -1, False),
    "242": ("mto_variant", -1, False),
    "243": ("packaging_component", -1, False),
    "250": ("secondary_serial", -1, False),
    "251": ("source_entity", -1, False),
    # --- Quantity / measure ---
    "30": ("quantity", -1, False),
    "3100": ("net_weight_kg", 6, False),
    "3101": ("net_weight_kg", 6, False),
    "3102": ("net_weight_kg", 6, False),
    "3103": ("net_weight_kg", 6, False),
    "3104": ("net_weight_kg", 6, False),
    "3105": ("net_weight_kg", 6, False),
    "310": ("net_weight_kg", 6, False),
    "311": ("length_m", 6, False),
    "314": ("area_m2", 6, False),
    "315": ("volume_l", 6, False),
    "316": ("volume_m3", 6, False),
    # --- Orders / shipment ---
    "400": ("purchase_order", -1, False),
    "401": ("consignment_number", -1, False),
    "402": ("shipment_id", 17, False),
    "403": ("routing_code", -1, False),
    # --- Locations ---
    "410": ("ship_to_location", 13, False),
    "411": ("bill_to_location", 13, False),
    "412": ("purchased_from", 13, False),
    "413": ("ship_for_location", 13, False),
    "414": ("physical_location", 13, False),
    "420": ("ship_to_postal", -1, False),
    "421": ("ship_to_postal_iso", -1, False),
    # --- Special ---
    "7001": ("nato_stock_number", 13, False),
    "7003": ("expiry_time", 10, False),
    "8001": ("roll_products", 14, False),
    "8003": ("grai", -1, False),
    "8004": ("giai", -1, False),
    "8005": ("price_per_unit", 6, False),
    "8006": ("itip", 18, False),
    "8007": ("iban", -1, False),
    "8018": ("gsrn", 18, False),
    "8020": ("payment_ref", -1, False),
    "8200": ("product_url", -1, False),
    # --- Internal company use (90-99) ---
    **{str(i): (f"internal_{i}", -1, False) for i in range(90, 100)},
}

# Sort keys longest-first so we always try the longest match first
_AI_KEYS_SORTED = sorted(GS1_AI.keys(), key=len, reverse=True)

FNC1 = chr(29)  # ASCII Group Separator
FNC1_ALIASES = ["\x1d", "\u001d", "<GS>", "[GS]", "{FNC1}", "<FNC1>"]

# Important fields — if any are missing after GS1 parse, we call OpenAI
_IMPORTANT_FIELDS = {"serial_number", "gtin_code", "expiry_date", "batch_number"}

# Variable-length AIs treated as heuristic split points when FNC1 is missing
_VARIABLE_AI_BOUNDARIES = frozenset({"21", "10", "22", "240", "241", "250", "251", "30"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(raw: str) -> str:
    """Normalise FNC1 variants and strip known scanner prefixes."""
    s = raw
    for alias in FNC1_ALIASES:
        s = s.replace(alias, FNC1)
    # Strip common scanner symbol IDs / AIM reader prefixes
    s = re.sub(r"^\](d2|C1|e0|Q3)", "", s.strip())
    return s


def _convert_date(date_str: str) -> str:
    """YYMMDD → YYYY-MM-DD. '00' day means last day of month per GS1 spec."""
    if not date_str or len(date_str) != 6 or not date_str.isdigit():
        return date_str
    yy, mm, dd = int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6])
    now = datetime.now()
    # GS1 century: yy ≤ (current_yy + 50) % 100 → 2000s, else 1900s
    current_yy = now.year % 100
    yyyy = 2000 + yy if yy <= (current_yy + 50) % 100 else 1900 + yy
    # GS1 'day 00' = last day of that month
    if dd == 0:
        import calendar

        dd = calendar.monthrange(yyyy, mm)[1]
    try:
        datetime(yyyy, mm, dd)
        return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
    except ValueError:
        return date_str


def _clean_token(value: str) -> str:
    """Remove control chars and surrounding spaces from extracted GS1 token values."""
    if value is None:
        return value
    return re.sub(r"[\x00-\x1F\x7F]", "", str(value)).strip()


def _find_ai_boundary(data: str, start_pos: int, current_ai: str = "") -> int | None:
    """
    Best-effort split point for variable-length AIs when scanner dropped FNC1.

    Strategy (two-pass, priority order):
    1. Variable-length AI boundary (e.g. AI 21 serial after AI 10 batch) — most
       semantically reliable; return the EARLIEST match found.
    2. Fixed-length AI boundary (all-digit value follows) — used only when no
       variable-length boundary was found.  Primary identifiers (00/01/02) are
       excluded from fixed-length detection because they always START a GS1
       message and never appear mid-string; including them causes false positives
       (e.g. the "00" inside "L25A006" matches AI-00/SSCC and truncates the batch).

    Typical no-FNC1 GS1 DataMatrix UDI sequence:
        01{14}17{6}10{batch}21{serial}
    Without FNC1, AI 10 (batch) would otherwise consume '21{serial}' as part of
    the batch value — this function detects the '21' boundary and stops it there.

    IMPORTANT: Serial number (AI 21) is always the last field in GS1 UDI DataMatrix
    barcodes. When no FNC1 is present, the serial must consume everything to
    end-of-string. Returning a boundary for AI 21 causes false splits when the
    sequence digits in a serial happen to match a GS1 AI (e.g. "10" in
    "CS32EFG69105" is mistaken for the Lot/Batch AI, "20" in "CS44EFG82021" for
    the Variant AI, "30" in "CM19EFG72306" for the Quantity AI).
    """
    # Serial number is always last — never split it further.
    if current_ai == "21":
        return None

    # Primary GS1 identifiers never appear as mid-string boundaries.
    _PRIMARY_EXCLUDES = frozenset({"00", "01", "02"})

    n = len(data)

    # Pass 1 — variable-length AI boundaries (semantically preferred)
    for i in range(start_pos + 1, n):
        for key in _VARIABLE_AI_BOUNDARIES:
            if key == current_ai:
                continue
            if not data.startswith(key, i):
                continue
            if i > start_pos and (i + len(key)) < n:
                logger.debug(f"GS1 heuristic boundary (variable) before AI({key}) at pos {i}")
                return i

    # Pass 2 — fixed-length AI boundaries (fallback when no variable boundary found)
    for i in range(start_pos + 1, n):
        for key in _AI_KEYS_SORTED:
            if key in _PRIMARY_EXCLUDES:
                continue  # never mid-string boundaries; cause false positives
            if not data.startswith(key, i):
                continue
            field, length, _ = GS1_AI[key]
            if length <= 0:
                continue  # variable-length: handled in pass 1
            val_start = i + len(key)
            val_end = val_start + length
            if val_end > n:
                continue
            if data[val_start:val_end].isdigit():
                logger.debug(f"GS1 heuristic boundary (fixed) before AI({key})/{field} at pos {i}")
                return i

    return None


# ---------------------------------------------------------------------------
# GS1 standard parser (no AI at all)
# ---------------------------------------------------------------------------


def _parse_gs1(raw: str) -> dict:
    """
    Strict GS1 AI parser.
    Handles both:
      • Concatenated format:  011234567890123417250630\x1d10BATCH\x1d21SERIAL
      • Parenthesised format: (01)12345678901234(17)250630(10)BATCH(21)SERIAL
    """
    result = {}
    data = _normalise(raw)

    # ---- parenthesised format detection ----
    if re.search(r"\(\d{2,4}\)", data):
        # Replace (AI) with FNC1 + AI so the sequential parser handles it uniformly
        data = re.sub(r"\((\d{2,4})\)", lambda m: FNC1 + m.group(1), data)
        # Ensure it starts with FNC1 if the first token was a parens AI
        if not data.startswith(FNC1):
            data = FNC1 + data

    # ---- sequential parser ----
    pos = 0
    n = len(data)

    while pos < n:
        # skip separator
        if data[pos] == FNC1:
            pos += 1
            continue

        # find matching AI (longest-first)
        ai = None
        for key in _AI_KEYS_SORTED:
            end = pos + len(key)
            if end <= n and data[pos:end] == key:
                ai = key
                pos = end
                break

        if ai is None:
            # Not a known AI — could be a plain barcode
            remaining = data[pos:]
            if remaining.isdigit() and len(remaining) in {8, 12, 13, 14}:
                result.setdefault("gtin_code", remaining)
                logger.info(f"GS1 plain barcode: {remaining}")
            elif remaining:
                logger.debug(f"GS1 parser: unrecognised segment at {pos}: {data[pos:pos+10]!r}")
                pos += 1
            break

        field, length, is_date = GS1_AI[ai]

        if length > 0:
            value = data[pos : pos + length]
            pos += length
        else:
            end_pos = data.find(FNC1, pos)
            if end_pos == -1:
                # Scanner may drop FNC1 separators in CODE128 streams.
                # Try to split before a plausible AI boundary (fixed or variable-length).
                boundary = _find_ai_boundary(data, pos, current_ai=ai)
                if boundary is not None:
                    value = data[pos:boundary]
                    pos = boundary
                else:
                    value = data[pos:]
                    pos = n
            else:
                value = data[pos:end_pos]
                pos = end_pos  # will skip FNC1 on next iteration

        value = _clean_token(value)

        if is_date:
            value = _convert_date(value)

        result[field] = value
        logger.debug(f"GS1 AI({ai}) → {field}: {value!r}")

    logger.info(f"GS1 parsed: {result}")
    return result


# ---------------------------------------------------------------------------
# OpenAI enrichment  — fills fields missing from GS1 parse
# ---------------------------------------------------------------------------

_OPENAI_SYSTEM = """\
You are an expert at decoding GS1 barcodes, QR codes, and DataMatrix codes used in medical devices, pharmaceuticals, and supply chain.
Always return valid JSON and nothing else.
Never invent data — only extract what is present in the raw scan string.
"""

_OPENAI_PROMPT_TEMPLATE = """\
Below is the raw string captured from a barcode / QR code scanner.
Special characters may be present:
  • \\x1d or <GS> or [GS] = FNC1 separator (separates variable-length GS1 fields)
  • Parenthesised numbers like (01), (10), (17), (21) are GS1 Application Identifiers (AIs)

Raw scan:
```
{raw}
```

Already extracted by rule-based GS1 parser (do NOT override these unless clearly wrong):
{already_extracted}

Extract every possible field from the raw scan.
Use these GS1 AI definitions to decode numeric sequences:
  (00) SSCC-18               fixed 18 digits
  (01) GTIN                  fixed 14 digits → field: gtin_code
  (10) Batch/Lot Number      variable        → field: batch_number
  (11) Production Date       YYMMDD          → field: production_date   (convert to YYYY-MM-DD)
  (13) Packaging Date        YYMMDD          → field: packaging_date    (convert to YYYY-MM-DD)
  (15) Best Before Date      YYMMDD          → field: best_before_date  (convert to YYYY-MM-DD)
  (17) Expiry Date           YYMMDD          → field: expiry_date       (convert to YYYY-MM-DD)
  (20) Product Variant       fixed 2         → field: variant
  (21) Serial Number         variable        → field: serial_number
  (22) Secondary Data        variable        → field: secondary_data
  (240) Additional Product ID variable       → field: additional_id
  (30) Count / Qty           variable        → field: quantity
  (37) Item count            variable        → field: item_count
  (91)–(99) Internal codes   variable        → field: internal_XX

If the string looks like a plain EAN-13 / UPC / EAN-8 number (8–14 digits, no AIs), store it as gtin_code.

For serial_number: if AI(21) is absent, check if any standalone alphanumeric identifier is present that looks like a serial.

Date format rule: YYMMDD → YYYY-MM-DD. Assume 00–49 = 2000s, 50–99 = 1900s. Day "00" = last day of month.

Return ONLY a flat JSON object with field names as keys. Include ONLY fields you found.
Example:
{{
  "gtin_code": "07302409310191",
  "batch_number": "L25A006",
  "expiry_date": "2027-06-30",
  "serial_number": "1234567",
  "production_date": "2025-01-10"
}}
"""


def _openai_enrich(raw: str, already: dict) -> dict:
    """Ask OpenAI to extract all fields from raw scan string."""
    if not _client:
        return {}

    already_str = json.dumps(already, ensure_ascii=False, indent=2) if already else "{}"
    prompt = _OPENAI_PROMPT_TEMPLATE.format(raw=raw, already_extracted=already_str)

    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",  # fast, accurate, cheap
            messages=[
                {"role": "system", "content": _OPENAI_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content.strip()
        logger.info(f"OpenAI raw response: {text}")
        data = json.loads(text)
        # Convert any YYMMDD dates that slipped through
        for k, v in data.items():
            if "date" in k and isinstance(v, str) and len(v) == 6 and v.isdigit():
                data[k] = _convert_date(v)
        return data
    except Exception as e:
        logger.error(f"OpenAI enrichment error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Regex fallback  — last resort when OpenAI not available
# ---------------------------------------------------------------------------

_REGEX_AI = [
    ("gtin_code", r"(?:01|GTIN[:\s]*)(\d{8,14})"),
    ("serial_number", r"(?:21|S/N[:\s]*|Serial[:\s]*)([A-Z0-9\-]{4,30})"),
    ("batch_number", r"(?:10|Batch[:\s]*|Lot[:\s]*)([A-Z0-9\-]{2,20})"),
    ("expiry_date", r"(?:17|Exp[:\s]*)(\d{6})"),
    ("production_date", r"(?:11|Prod[:\s]*)(\d{6})"),
    ("best_before_date", r"(?:15|BB[:\s]*)(\d{6})"),
    ("quantity", r"(?:30|Qty[:\s]*)(\d{1,6})"),
]


def _regex_fallback(raw: str) -> dict:
    result = {}
    for field, pattern in _REGEX_AI:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            val = m.group(1)
            if "date" in field:
                val = _convert_date(val)
            result[field] = val
    # plain barcode?
    if not result and re.match(r"^\d{8,14}$", raw.strip()):
        result["gtin_code"] = raw.strip()
    if not result:
        result["raw_data"] = raw
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class GS1Parser:
    """
    Hybrid GS1 + OpenAI barcode / QR / DataMatrix parser.

    Strategy:
      1. GS1 rule-based parser (always runs, very fast, 100% deterministic)
      2. If important fields (serial, GTIN, expiry, batch) are missing,
         call OpenAI to fill gaps — merge into result
      3. Regex fallback if OpenAI unavailable
    """

    # expose helpers for external use
    convert_date = staticmethod(_convert_date)
    normalise = staticmethod(_normalise)

    @classmethod
    def parse(cls, raw_data: str, use_openai: bool = True) -> dict:
        """
        Parse raw barcode / QR scan string and return structured dict.

        Args:
            raw_data:    Raw string from scanner (any encoding).
            use_openai:  If True (default) and OpenAI is configured, enrich
                         missing fields via OpenAI after GS1 parse.

        Returns:
            dict with keys such as: gtin_code, serial_number, batch_number,
            expiry_date, production_date, quantity, variant, raw_data, …
        """
        if not raw_data:
            return {"error": "No data provided"}

        result = {}

        # Step 1 — GS1 rule-based parse
        try:
            gs1_result = _parse_gs1(raw_data)
            result.update(gs1_result)
        except Exception as e:
            logger.warning(f"GS1 parser exception: {e}")

        # Step 2 — OpenAI enrichment where important fields are missing
        missing = _IMPORTANT_FIELDS - set(result.keys())
        if missing and use_openai:
            logger.info(f"Important fields missing after GS1 parse: {missing} — calling OpenAI")
            try:
                openai_result = _openai_enrich(raw_data, result)
                # Merge: GS1 values take priority; OpenAI fills gaps
                for k, v in openai_result.items():
                    if k not in result and v:
                        result[k] = v
                logger.info(f"After OpenAI merge: {result}")
            except Exception as e:
                logger.warning(f"OpenAI enrichment failed: {e}")

        # Step 3 — Regex fallback if result is still empty
        if not result:
            logger.info("Trying regex fallback")
            result = _regex_fallback(raw_data)

        # Always store the original raw scan
        result["raw_data"] = raw_data

        logger.info(f"Final parsed result: {result}")
        return result

    # ------------------------------------------------------------------
    # Convenience shims (backward-compat with old call sites)
    # ------------------------------------------------------------------

    @classmethod
    def parse_gs1_standard(cls, raw_data: str) -> dict:
        return _parse_gs1(raw_data)

    @classmethod
    def parse_with_openai(cls, raw_data: str) -> dict:
        return _openai_enrich(raw_data, {})

    @staticmethod
    def format_for_storage(parsed_data: dict) -> dict:
        """Return only non-empty fields that the DB model expects."""
        fields = [
            "serial_number",
            "gtin_code",
            "batch_number",
            "expiry_date",
            "production_date",
            "best_before_date",
            "packaging_date",
            "due_date",
            "quantity",
            "variant",
            "product_name",
            "raw_data",
            "material_code",
        ]
        return {k: parsed_data[k] for k in fields if parsed_data.get(k)}
