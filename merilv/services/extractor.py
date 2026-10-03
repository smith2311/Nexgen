"""Barcode extractor: bulk extraction, live scan and live validate.

Three related workflows that all sit on top of the same parser:

*   **Extract** — paste or upload a column of barcodes and get their fields
    broken out. Diagnostic tool, no state.
*   **Live scan** — scan items one by one against the material master and see
    found / not-found / duplicate counts build up in real time.
*   **Live validate** — scan against an uploaded stock dump, tracking matched,
    mismatched, not-in-dump, duplicate and remaining quantities.

Live validate is the physical-verification workflow: a warehouse operator walks
the racks with a scanner while the counts update, so every scan must give an
immediate, unambiguous verdict.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Mapping, Sequence

from .barcode import (
    NOT_IN_MASTER,
    EntityProfile,
    ParsedBarcode,
    PositionalRule,
    decode_gs1_date,
    is_non_serial,
    parse_barcode,
)
from .validation import (
    STOCK_GOOD,
    STOCK_NO_EXPIRY,
    ValidationSettings,
    classify_stock,
    clean,
    parse_loose_date,
    serial_matches,
)

# Live-scan verdicts.
FOUND = "found"
NOT_FOUND = "not_found"
DUPLICATE = "duplicate"

# Live-validate verdicts.
MATCH = "match"
MISMATCH = "mismatch"
NOT_IN_DUMP = "not_in_dump"
EXCESS = "excess"

# Why a mismatch happened. ``MISMATCH`` stays one verdict — the counters, the
# stored records and the audio cue all key on it, and splitting it would change
# data already on disk. But "the batch is wrong" and "the serial is wrong" call
# for different actions on the floor, and the operator was being shown one
# colour for both, so the distinction is carried alongside the verdict.
MISMATCH_BATCH = "batch"
MISMATCH_SERIAL = "serial"


@dataclass
class ExtractedRow:
    """One parsed barcode plus whatever the material master knows about it."""

    sequence: int
    raw: str
    material: str = ""
    scan_code: str = ""
    batch: str = ""
    serial: str = ""
    mfg: str = ""
    expiry: str = ""
    description: str = ""
    gtin: str = ""
    #: From the material master, not from the barcode — a GS1 string carries
    #: neither. Both are read here because the Stock Validator and this screen
    #: are the two places the client wants them (#17), and both went through
    #: `build_material_master`, which was already carrying Material Group in
    #: its payload with nothing reading it out.
    material_group: str = ""
    ph_level: str = ""
    strategy: str = ""
    is_valid: bool = False
    error: str = ""
    #: One of the validation module's STOCK_* bands, or "" when no settings
    #: were supplied. The extractor never knew whether a date it had just
    #: parsed was in the past — it printed the string and left the operator to
    #: read six digits and do the arithmetic. Banding is the Stock Validator's
    #: own ``classify_stock``, so a date reads the same in both screens and the
    #: per-entity thresholds (MHPL and MLSIPL invert them) are honoured here too.
    expiry_state: str = ""

    @classmethod
    def from_parsed(
        cls,
        sequence: int,
        parsed: ParsedBarcode,
        master: Mapping[str, Mapping[str, object]] | None = None,
        settings: ValidationSettings | None = None,
    ) -> "ExtractedRow":
        row = cls(
            sequence=sequence,
            raw=parsed.raw,
            material=parsed.material,
            scan_code=parsed.scan_code or parsed.material,
            batch=parsed.batch,
            serial=parsed.serial,
            mfg=parsed.mfg,
            expiry=parsed.expiry,
            strategy=parsed.strategy,
            is_valid=parsed.is_valid,
            error=parsed.error,
        )
        entry = _master_entry(master, row.material) or _master_entry(master, row.scan_code)
        if entry:
            row.description = str(entry.get("Description") or "")
            row.gtin = str(entry.get("GTIN") or "")
            row.material_group = str(entry.get("Material Group") or "")
            row.ph_level = str(entry.get("PH Level") or "")
        elif master:
            row.description = NOT_IN_MASTER
        if settings is not None:
            # Condition is empty: the extractor scans a label, not a stock
            # line, so there is no damage flag to weigh against the date.
            row.expiry_state = classify_stock("", expiry_to_date(row.expiry), settings)
        return row


def expiry_to_date(value: object) -> date | None:
    """Decode an expiry that may be a GS1 date or an SAP-shaped one.

    GS1 first, and this order is not cosmetic. ``parse_loose_date`` tries
    ``%Y%m%d``, and ``strptime`` will happily read the six digits of "300911"
    as the year 3009 — so a barcode expiry run through the SAP parser lands six
    centuries in the future and every expired item reads as good. Six-digit
    input is GS1 ``YYMMDD``; only longer, punctuated shapes are SAP's.
    """
    return decode_gs1_date(value) or parse_loose_date(value)


#: Bands worth colouring. "good" and "no_expiry" are the unremarkable cases and
#: get no highlight — highlighting everything highlights nothing.
EXPIRY_ALERT_STATES = frozenset({"expired", "near_expiry", "short_expiry", "damage"})


def expiry_is_alerting(state: str) -> bool:
    """True when an expiry band deserves the operator's attention."""
    return bool(state) and state not in (STOCK_GOOD, STOCK_NO_EXPIRY)


def _master_entry(
    master: Mapping[str, Mapping[str, object]] | None, code: str
) -> Mapping[str, object] | None:
    if not master or not code:
        return None
    for variant in (code, code.upper(), code.lstrip("0"), code.lstrip("0").upper()):
        if variant and variant in master:
            return master[variant]
    return None


def extract_many(
    raw_scans: Iterable[str],
    profile: EntityProfile,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
    settings: ValidationSettings | None = None,
) -> list[ExtractedRow]:
    """Parse a batch of barcodes for the extractor tab."""
    rows: list[ExtractedRow] = []
    for index, raw in enumerate(raw_scans):
        text = (raw or "").strip()
        if not text:
            continue
        parsed = parse_barcode(text, profile, material_master, positional_rules)
        rows.append(ExtractedRow.from_parsed(len(rows), parsed, material_master, settings))
    return rows


#: Separators that can never be part of a barcode, so they always split.
_HARD_SEPARATOR_RE = re.compile(r"[;|]")

#: Whitespace that separates one code from the next. Spelled out rather than
#: written ``\s``, because Python counts the ASCII information separators
#: \x1c-\x1f as whitespace and one of them — \x1d, GS — is GS1's own FIELD
#: separator. A scanner reading an unbracketed DataMatrix puts GS *inside* a
#: single barcode, after each variable-length AI, so splitting on it shredded
#: one code into its AIs: ``10BB01<GS>215<GS>240MAT1`` arrived as three
#: "barcodes", two of which were then dropped for being too short.
_CODE_WHITESPACE = " \t\n\r\f\v"

#: Two or more spaces. One space can sit inside a code (MHPL does not strip
#: whitespace before parsing); a run of them is a column gap, or the padding in
#: front of the ``<barcode>    # <note>`` shape the fixtures use.
_GAP_RE = re.compile(rf"[{_CODE_WHITESPACE}]{{2,}}")
#: A line with no GS1 syntax at all: any run of commas or whitespace separates.
_PLAIN_SEPARATOR_RE = re.compile(rf"[,{_CODE_WHITESPACE}]+")


def _split_compound(chunk: str) -> list[str]:
    """Split a run of GS1 codes that arrived with no separator between them.

    A gun configured to send no terminator, or a cell holding a whole pick
    list, delivers ``(01)…(30)01(01)…(30)01`` as one string. Split before each
    repeated code-opening AI — ``(01)`` when the codes are GTIN-led, ``(240)``
    when they are scan-code-led — so each is processed as its own scan instead
    of the whole run failing to parse as one.

    Only *repeated* openers split. A single code contains exactly one, and
    ``(10)``/``(21)`` are deliberately not treated as openers: they occur
    inside every code and splitting on them would shred valid input.
    """
    for opener in ("(01)", "(240)"):
        if chunk.count(opener) > 1:
            pieces = [p.strip() for p in re.split(rf"(?={re.escape(opener)})", chunk)]
            return [p for p in pieces if p]
        if opener in chunk:
            # GTIN-led codes carry a (240) each; once (01) has been ruled out
            # as repeated, a repeated (240) is part of the same single code.
            break
    return [chunk]


def split_pasted(text: str) -> list[str]:
    """Split a pasted block into individual barcodes.

    Scanners terminate with a newline, but operators also paste tab- or
    comma-separated columns out of Excel, and some guns send a space or a
    semicolon between codes instead — all of which arrived as one enormous
    "barcode" that parsed as nothing, which is what "bulk scan does not accept
    codes" turned out to be. Parentheses are never split on: they are GS1
    syntax, and a comma inside a GS1 line stays part of the code.
    """
    if not text:
        return []
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "\n")
    parts: list[str] = []
    for line in normalised.split("\n"):
        for piece in _HARD_SEPARATOR_RE.split(line):
            piece = piece.strip()
            if not piece:
                continue
            if "(" not in piece:
                parts.extend(p for p in _PLAIN_SEPARATOR_RE.split(piece) if p)
                continue
            for chunk in _GAP_RE.split(piece):
                chunk = chunk.strip()
                if chunk:
                    parts.extend(_split_compound(chunk))
    return parts


# ─── Live scan ───────────────────────────────────────────────────────────────
@dataclass
class LiveScanState:
    """Running counters and seen-set for a live-scan session."""

    seen: set[str] = field(default_factory=set)
    found: int = 0
    not_found: int = 0
    duplicates: int = 0
    total: int = 0

    def counters(self) -> dict[str, int]:
        return {
            "total": self.total,
            "found": self.found,
            "not_found": self.not_found,
            "duplicates": self.duplicates,
        }


@dataclass
class LiveScanResult:
    result: str
    parsed: ParsedBarcode
    row: ExtractedRow
    message: str = ""

    @property
    def audio_cue(self) -> str:
        if self.result == FOUND:
            return "match"
        return "duplicate" if self.result == DUPLICATE else "mismatch"


def live_scan(
    raw: str,
    profile: EntityProfile,
    state: LiveScanState,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
    settings: ValidationSettings | None = None,
) -> LiveScanResult:
    """Record one live scan and classify it against the material master."""
    parsed = parse_barcode(raw, profile, material_master, positional_rules)
    row = ExtractedRow.from_parsed(state.total, parsed, material_master, settings)
    state.total += 1

    # Duplicate **batch** is legal — a warehouse can hold many identical units
    # of one batch, and this is the primary dump-truck-load workflow for
    # non-serialised entities. Only a duplicate **serial** is ever wrong, so a
    # non-serialised scan (parsed.serial is "" or the "Unknown" placeholder)
    # must never be compared against the seen-set at all — otherwise every
    # second scan of the same material+batch collides on "MAT|BATCH|UNKNOWN"
    # and is wrongly rejected as a duplicate (Row 3.2).
    if not is_non_serial(parsed.serial):
        identity = f"{clean(parsed.material)}|{clean(parsed.batch)}|{clean(parsed.serial)}"
        if identity in state.seen:
            state.duplicates += 1
            return LiveScanResult(DUPLICATE, parsed, row, "Already scanned in this session.")
        state.seen.add(identity)

    if not parsed.is_valid or not parsed.material:
        state.not_found += 1
        return LiveScanResult(NOT_FOUND, parsed, row, parsed.error or "Barcode could not be read.")

    if material_master and not _master_entry(material_master, parsed.material):
        state.not_found += 1
        return LiveScanResult(NOT_FOUND, parsed, row, f"{parsed.material} is not in the material master.")

    state.found += 1
    return LiveScanResult(FOUND, parsed, row, row.description or "Found.")


# ─── Live validate ───────────────────────────────────────────────────────────
@dataclass
class ValidateStock:
    """Expected quantity for one material/batch, and how much has been seen."""

    material: str
    batch: str
    expected: float = 0.0
    scanned: float = 0.0
    description: str = ""
    serials: Counter[str] = field(default_factory=Counter)
    scanned_serials: Counter[str] = field(default_factory=Counter)

    @property
    def remaining(self) -> float:
        return max(self.expected - self.scanned, 0)

    def serial_remaining(self, serial: str) -> float:
        key = clean(serial)
        return max(self.serials.get(key, 0.0) - self.scanned_serials.get(key, 0.0), 0)

    def expected_serials(self) -> list[str]:
        return sorted(self.serials)

    def resolve_serial(
        self,
        serial: str,
        profile: EntityProfile | None,
        delimiter: str = "",
    ) -> str:
        """The dump serial this scan is, or ``""`` when the dump holds no such serial.

        A scanner reads batch and serial as separate fields while SAP often
        stores the two concatenated, so the scanned ``029`` and the dump's
        ``L2512001029`` are one serial under two spellings. Resolving to the
        dump's spelling is what makes the rest of this function safe: the
        remaining-quantity counters are keyed on the dump serial, so crediting a
        scan under ``029`` would leave ``L2512001029`` looking untouched forever
        and the excess check would never fire.

        Correspondence itself is :func:`serial_matches`' decision, never a
        substring test of our own.
        """
        # The parser's "Unknown" placeholder is truthy but is not a serial, so it
        # must never resolve to one — not even to a dump that literally lists
        # "Unknown" — or a serial-less scan would consume a real unit.
        if is_non_serial(serial):
            return ""
        key = clean(serial)
        if key in self.serials:
            return key
        for candidate in self.expected_serials():
            if serial_matches(self.batch, key, candidate, profile, delimiter):
                return candidate
        return ""


@dataclass
class LiveValidateState:
    """Expected stock plus the running verdict counters."""

    stock: dict[str, ValidateStock] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    matched: int = 0
    mismatched: int = 0
    not_in_dump: int = 0
    duplicates: int = 0
    excess: int = 0
    total: int = 0

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> "LiveValidateState":
        state = cls()
        for record in records:
            material = clean(record.get("material"))
            batch = clean(record.get("batch"))
            if not material:
                continue
            key = f"{material}|{batch}"
            entry = state.stock.get(key)
            if entry is None:
                entry = ValidateStock(
                    material=material,
                    batch=batch,
                    description=str(record.get("description") or ""),
                )
                state.stock[key] = entry
            entry.expected += float(record.get("qty") or 0) or 1.0
            serial = clean(record.get("serial"))
            if serial:
                entry.serials[serial] += float(record.get("qty") or 0) or 1.0
        return state

    @property
    def remaining_total(self) -> float:
        return sum(entry.remaining for entry in self.stock.values())

    def counters(self) -> dict[str, float]:
        return {
            "total": self.total,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "not_in_dump": self.not_in_dump,
            "duplicates": self.duplicates,
            "excess": self.excess,
            "remaining": self.remaining_total,
        }

    def material_totals(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for entry in self.stock.values():
            totals[entry.material] += entry.expected
        return dict(totals)


@dataclass
class LiveValidateResult:
    result: str
    parsed: ParsedBarcode
    row: ExtractedRow
    message: str = ""
    stock: ValidateStock | None = None
    #: MISMATCH_BATCH or MISMATCH_SERIAL when ``result`` is MISMATCH, else "".
    mismatch_kind: str = ""

    @property
    def audio_cue(self) -> str:
        if self.result == MATCH:
            return "match"
        if self.result == DUPLICATE:
            return "duplicate"
        return "mismatch" if self.result == MISMATCH else "error"


def live_validate(
    raw: str,
    profile: EntityProfile,
    state: LiveValidateState,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
    settings: ValidationSettings | None = None,
) -> LiveValidateResult:
    """Validate one scan against the loaded dump and update the counters.

    Distinguishes a genuinely unexpected item (``not_in_dump``) from one whose
    material is expected but in a different batch (``mismatch``) and from one
    that is expected but already fully counted (``excess``) — three situations
    that need three different responses on the warehouse floor.
    """
    parsed = parse_barcode(raw, profile, material_master, positional_rules)
    row = ExtractedRow.from_parsed(state.total, parsed, material_master, settings)
    state.total += 1

    if not parsed.is_valid or not parsed.material:
        state.not_in_dump += 1
        return LiveValidateResult(
            NOT_IN_DUMP, parsed, row, parsed.error or "Barcode could not be read."
        )

    # Same rule as live_scan(): duplicate batch is legal, duplicate serial is
    # not. ``parsed.serial`` being the "Unknown" placeholder is truthy but not
    # a real serial, so the check must go through is_non_serial() rather than
    # a bare truthiness test — the previous `if parsed.serial:` let "Unknown"
    # through and flagged every second non-serialised scan as a duplicate.
    identity = f"{clean(parsed.material)}|{clean(parsed.batch)}|{clean(parsed.serial)}"
    if not is_non_serial(parsed.serial):
        if identity in state.seen:
            state.duplicates += 1
            return LiveValidateResult(DUPLICATE, parsed, row, "This serial has already been counted.")
        state.seen.add(identity)

    material = clean(parsed.material)
    batch = clean(parsed.batch)
    entry = state.stock.get(f"{material}|{batch}")

    if entry is not None:
        serial = clean(parsed.serial)
        # The dump's own spelling of the scanned serial, so that "is this in the
        # dump", "is it already counted" and the credit below all key on one
        # value. A scanner splits batch and serial where SAP concatenates them,
        # so the raw scanned text is not the key the dump is indexed under.
        resolved_serial = entry.resolve_serial(
            serial, profile, settings.concat_delimiter if settings else "",
        )
        if entry.serials:
            expected = entry.expected_serials()
            listed = ", ".join(expected[:8])
            if len(expected) > 8:
                listed += f", +{len(expected) - 8} more"
            if is_non_serial(serial):
                state.mismatched += 1
                return LiveValidateResult(
                    MISMATCH, parsed, row, f"Serial missing — dump expects {listed}.", entry,
                    mismatch_kind=MISMATCH_SERIAL,
                )
            if not resolved_serial:
                state.mismatched += 1
                return LiveValidateResult(
                    MISMATCH, parsed, row, f"Serial mismatch — dump expects {listed}, scanned {serial}.", entry,
                    mismatch_kind=MISMATCH_SERIAL,
                )
            if entry.serial_remaining(resolved_serial) <= 0:
                state.excess += 1
                return LiveValidateResult(
                    EXCESS, parsed, row, f"Excess — serial {serial} is already counted.", entry
                )

        if entry.remaining <= 0:
            state.excess += 1
            return LiveValidateResult(
                EXCESS, parsed, row,
                f"Excess — all {entry.expected:g} units of this batch are already counted.",
                entry,
            )
        entry.scanned += 1
        if entry.serials and resolved_serial:
            entry.scanned_serials[resolved_serial] += 1
        state.matched += 1
        return LiveValidateResult(
            MATCH, parsed, row, f"{entry.remaining:g} remaining in this batch.", entry
        )

    if material in state.material_totals():
        state.mismatched += 1
        expected = sorted({e.batch for e in state.stock.values() if e.material == material and e.batch})
        listed = ", ".join(expected) if expected else "another batch"
        return LiveValidateResult(
            MISMATCH, parsed, row, f"Batch mismatch — dump expects {listed}, scanned {batch or '(none)'}.",
            mismatch_kind=MISMATCH_BATCH,
        )

    state.not_in_dump += 1
    return LiveValidateResult(NOT_IN_DUMP, parsed, row, f"{material} is not in the loaded dump.")


__all__ = [
    "DUPLICATE",
    "EXCESS",
    "FOUND",
    "MATCH",
    "MISMATCH",
    "NOT_FOUND",
    "NOT_IN_DUMP",
    "ExtractedRow",
    "LiveScanResult",
    "LiveScanState",
    "LiveValidateResult",
    "LiveValidateState",
    "ValidateStock",
    "extract_many",
    "live_scan",
    "live_validate",
    "split_pasted",
]
