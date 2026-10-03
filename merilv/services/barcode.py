"""GS1 / legacy barcode parsing for Meril-V.

The legacy JavaScript system carried four independently-evolved parsers (one per
outbound scanner, plus a fifth inside the validators). They diverged in bug-fixes
rather than in intent, so this module keeps a *single* parser whose behaviour is
driven by an :class:`EntityProfile`. Each legacy quirk becomes an explicit,
documented flag instead of a forked file.

Reference for the original behaviour:
    public/src/core/outbound-scanner.js          (MHPL, canonical)
    public/src/core/outbound-scanner-mlsipl.js   (compact GS1 + positional rules)
    public/src/core/outbound-scanner-micro.js    (bracket pass + AI walker)
    public/script.js                             (validator, ``parseProBarcode``)
    public/Barcode-Extractor.html                (canonical AI table)
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

# ─── GS1 Application Identifier table ────────────────────────────────────────
# value = (fixed_length, max_variable_length); fixed_length 0 => variable length.
AI_DEF: dict[str, tuple[int, int]] = {
    "00": (18, 0),  # SSCC
    "01": (14, 0),  # GTIN-14
    "02": (14, 0),  # GTIN of contained items
    "10": (0, 20),  # Batch / lot
    "11": (6, 0),  # Production (MFG) date, YYMMDD
    "15": (6, 0),  # Best-before date, YYMMDD
    "17": (6, 0),  # Expiry date, YYMMDD
    "21": (0, 20),  # Serial
    "30": (0, 8),  # Variable count
    "37": (0, 8),  # Count of trade items
    "240": (0, 30),  # Additional item identification — Meril scan code
    "241": (0, 30),  # Customer part number
    "250": (0, 30),  # Secondary serial
    "310": (6, 0),  # Net weight, kg
    "311": (6, 0),  # Length, m
    "320": (6, 0),  # Net weight, lb
}

#: AIs the bracket pass will extract. Deliberately narrower than :data:`AI_DEF`
#: so that stray digit groups are not mistaken for identifiers.
BRACKET_AIS = ("01", "02", "10", "11", "15", "17", "21", "30", "37", "240", "241", "250")

#: Sentinels the legacy code compares against literally. Preserved verbatim —
#: several downstream rules test for these exact strings.
UNKNOWN = "UNKNOWN"
UNKNOWN_SERIAL = "Unknown"
NOT_IN_MASTER = "Not in Master"

#: Width of the serial component of an assigned serial. The dispatch record is
#: ``BATCH || SERIAL`` and the warehouse writes the serial as three digits —
#: batch ``ABC123`` plus serial ``7`` is ``ABC123007``, never ``ABC1237``. A
#: serial longer than this is left alone: some labels carry a seven-digit
#: running number (``(21)3265203``) and truncating it would destroy identity.
SERIAL_DIGITS = 3

#: ``MT-XX0000000L`` + ``L0000000`` + ``yyyy-mm-dd`` + ``yyyy-mm-dd`` (41–43 chars).
TRAUMA_RE = re.compile(
    r"^(MT-[A-Za-z]{2}\d{7}[A-Za-z]{1,2})([A-Za-z]\d{7})(\d{4}-\d{2}-\d{2})(\d{4}-\d{2}-\d{2})$"
)

#: Compact GS1 with no brackets and a locked field order.
COMPACT_GS1_RE = re.compile(r"^01(\d{14})10(.+?)11(\d{6})17(\d{6})240(.+)$")

_SYMBOLOGY_RE = re.compile(r"^\][A-Za-z]\d")
_OPEN_BRACKETS = re.compile(r"[\[\{【（]")
_CLOSE_BRACKETS = re.compile(r"[\]\}】）]")
_NOISE_RE = re.compile(r"[%\x1d\x1c\x1e\s]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: GS1's field separator. A scanner reading a DataMatrix emits it after every
#: variable-length AI, which is the only thing that says where such a field
#: ends when the string carries no parentheses. It is a control character, so
#: the blanket strip above used to destroy it before anything could read it —
#: leaving the unbracketed parser to guess field ends, and guess wrong.
GS = "\x1d"
#: Every control character except :data:`GS`, for the unbracketed pass.
_CONTROL_EXCEPT_GS_RE = re.compile(r"[\x00-\x1c\x1e\x1f\x7f]")
_BATCH_SANITISE_RE = re.compile(r"[^A-Za-z0-9\-]")
_CARET_AI_RE = re.compile(r"[\^~](240|241|250|310|311|320|10|11|15|17|21|30|37|00|01|02)")


# ─── Entity configuration ────────────────────────────────────────────────────
@dataclass(frozen=True)
class EntityProfile:
    """Per-business-unit parsing behaviour.

    Replaces the four forked scanner files. Values mirror the legacy defaults
    documented in ``docs/MERIL_V_SYSTEM_DOCUMENTATION.md`` §6.
    """

    code: str
    name: str = ""

    #: MLSIPL/Cardio/Micro strip whitespace before parsing; base MHPL does not.
    strip_whitespace: bool = True
    #: Whether SAP tracks this unit's stock at serial level. Off for MLSIPL,
    #: which is batch-managed — it gates the Stock Validator's requirement for
    #: a serial column in the SAP dump. It does NOT mean the label never
    #: prints a unit number: see ``ai21_is_material`` and
    #: :func:`_ai21_holds_running_number`.
    supports_serial: bool = True
    #: MLSIPL overloads AI (21) — it holds a *material* code on some labels and
    #: a running unit number on others, so the field is resolved per value
    #: rather than per entity by :func:`_ai21_holds_running_number`.
    ai21_is_material: bool = False
    #: MLSIPL strips punctuation from the batch and uppercases it.
    sanitise_batch: bool = False
    #: Micro accepts an ``A…A`` envelope and ``^240`` / ``~10`` AI prefixes.
    allow_envelope: bool = False
    #: Micro/Extractor walk unbracketed AIs positionally after the bracket pass.
    positional_walker: bool = False
    #: MLSIPL/Cardio/MDPL accept the locked-order compact GS1 form.
    compact_gs1: bool = False
    #: MLSIPL falls back to length-keyed positional slicing.
    positional_rules: bool = False
    #: Validators additionally accept the Meril Trauma legacy layout.
    trauma_legacy: bool = False
    #: Digits in the synthetic id given to non-serialised items (MHPL 4, others 5).
    pseudo_serial_digits: int = 5

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("EntityProfile.code is required")


#: Built-in profiles. Persisted overrides live in ``merilv.models.Entity`` and are
#: layered on top of these by :func:`merilv.services.entities.get_profile`.
BUILTIN_PROFILES: dict[str, EntityProfile] = {
    "MHPL": EntityProfile(
        code="MHPL",
        name="Meril Healthcare Pvt. Ltd.",
        strip_whitespace=False,
        pseudo_serial_digits=4,
    ),
    "MLSIPL": EntityProfile(
        code="MLSIPL",
        name="Meril Life Sciences India Pvt. Ltd.",
        supports_serial=False,
        ai21_is_material=True,
        sanitise_batch=True,
        compact_gs1=True,
        positional_rules=True,
    ),
    "CARDIO": EntityProfile(
        code="CARDIO",
        name="Meril Life Sciences — Cardio",
        compact_gs1=True,
    ),
    "MICRO": EntityProfile(
        code="MICRO",
        name="Meril Life Sciences — Micro",
        allow_envelope=True,
        positional_walker=True,
    ),
    "MDPL": EntityProfile(
        code="MDPL",
        name="Meril Diagnostics Pvt. Ltd.",
        compact_gs1=True,
    ),
}


@dataclass
class ParsedBarcode:
    """Outcome of parsing a single scan string."""

    raw: str
    material: str = ""
    batch: str = ""
    serial: str = ""
    mfg: str = ""
    expiry: str = ""
    qty: float | None = None
    #: Material as read from the barcode, *before* master-data resolution. MLSIPL's
    #: matcher needs both the scan code and the resolved SAP code.
    scan_code: str = ""
    #: Which strategy produced the result — useful in the UI and for diagnostics.
    strategy: str = ""
    is_valid: bool = False
    error: str = ""
    #: True when no AI (21) was present and the serial was synthesised/defaulted.
    is_default_serial: bool = False
    #: True when the serial was read off the label rather than defaulted. Set
    #: only by :func:`_apply_ai_map`, and only for an ``ai21_is_material``
    #: entity whose AI (21) turned out to hold a running number: such an
    #: entity is ``supports_serial=False`` (SAP does not track it at serial
    #: level) yet its label still carries the unit's number, and
    #: :func:`_finalise` must not blank what the label actually said.
    serial_from_label: bool = False

    @property
    def expiry_date(self) -> date | None:
        return decode_gs1_date(self.expiry)

    @property
    def mfg_date(self) -> date | None:
        return decode_gs1_date(self.mfg)

    def as_dict(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "material": self.material,
            "batch": self.batch,
            "serial": self.serial,
            "mfg": self.mfg,
            "expiry": self.expiry,
            "qty": self.qty,
            "scan_code": self.scan_code,
            "strategy": self.strategy,
            "is_valid": self.is_valid,
            "error": self.error,
            "is_default_serial": self.is_default_serial,
        }


@dataclass
class PositionalRule:
    """One length-keyed slicing rule (legacy ``MLSIPL_PARSE_RULES``)."""

    length: int
    mat_start: int
    mat_len: int
    batch_start: int
    batch_len: int
    mfg_start: int = 0
    mfg_len: int = 0
    exp_start: int = 0
    exp_len: int = 0

    @classmethod
    def from_csv_row(cls, row: Sequence[str]) -> "PositionalRule":
        """Parse ``Len, MatS, MatL, BatS, BatL, MfgS, MfgL, ExpS, ExpL``."""
        nums = [int(str(v).strip() or 0) for v in row[:9]]
        nums += [0] * (9 - len(nums))
        return cls(*nums)


def parse_positional_rules(csv_text: str) -> dict[int, list[PositionalRule]]:
    """Build a length→rules index from the admin-editable CSV block."""
    rules: dict[int, list[PositionalRule]] = {}
    for line in (csv_text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("len"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            rule = PositionalRule.from_csv_row(parts)
        except ValueError:
            continue
        rules.setdefault(rule.length, []).append(rule)
    return rules


# ─── Primitives ──────────────────────────────────────────────────────────────
def clean_val(value: object) -> str:
    """Strip GS1 noise: ``%``, GS/FS/RS control chars and all whitespace."""
    return _NOISE_RE.sub("", str(value or "")).strip()


def normalize(raw: object) -> str:
    """Strip the symbology identifier and fold alternate bracket glyphs to ASCII."""
    text = str(raw or "").strip()
    text = _SYMBOLOGY_RE.sub("", text)  # ]C1, ]e0, ]d2 …
    text = _OPEN_BRACKETS.sub("(", text)
    text = _CLOSE_BRACKETS.sub(")", text)
    return text


#: Characters a scanner gun or a spreadsheet paste bolts onto an otherwise
#: valid code: wrapping quotes, and the stray control bytes a keyboard-wedge
#: gun emits around the payload.
_WRAPPING_QUOTES = "\"'\u2018\u2019\u201c\u201d`"

#: Real whitespace, for trimming. Not ``str.strip()``'s default set, which in
#: Python includes the information separators \x1c-\x1f and so would remove a
#: trailing :data:`GS`.
_FRAME_WHITESPACE = " \t\n\r\f\v"


def normalise_scan_input(raw: object) -> str:
    """Clean one scanned/pasted string before it is parsed.

    A gun sends a terminator, a spreadsheet cell brings its own quotes, and a
    code pasted out of a chat window arrives with the line wrapped. All three
    reached :func:`parse_barcode` verbatim and were rejected as unparseable,
    which is indistinguishable from a genuinely bad label from the operator's
    side. Normalised in one place so the single-scan and bulk paths cannot
    disagree about what counts as the same code.

    Deliberately conservative: it removes only framing (control characters,
    embedded newlines, wrapping quotes and outer whitespace). Everything the
    parser treats as payload is left untouched — and that now includes
    :data:`GS`, the separator a scanner puts after each variable-length AI of
    an unbracketed code. This function used to strip it along with every other
    control character while its own docstring claimed otherwise, so the one
    thing that says where an unbracketed field ends never survived to
    :func:`_parse_ai_stream`. ``clean_val`` still strips it out of individual
    values, so nothing downstream sees it.

    Whitespace is trimmed by an explicit set for the same reason: Python counts
    the ASCII information separators \x1c-\x1f as whitespace, so a bare
    ``.strip()`` would eat a trailing GS.
    """
    text = str(raw or "")
    # Embedded line breaks: a code that arrived wrapped is one code, not two.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(part.strip(_FRAME_WHITESPACE) for part in text.split("\n"))
    text = _CONTROL_EXCEPT_GS_RE.sub("", text)
    text = text.strip(_FRAME_WHITESPACE)
    # Only a matched pair, and only once: an unbalanced quote is payload.
    while len(text) >= 2 and text[0] == text[-1] and text[0] in _WRAPPING_QUOTES:
        text = text[1:-1].strip(_FRAME_WHITESPACE)
    return text


def format_serial_component(serial: object, digits: int = SERIAL_DIGITS) -> str:
    """The serial half of an assigned serial, in the warehouse's own format.

    Purely numeric serials are zero-padded to ``digits`` so that ``7``, ``07``
    and ``007`` are one serial rather than three — SAP matches the upload sheet
    on the concatenated value, so the padding is part of identity, not
    cosmetics. Anything longer, or carrying letters, is passed through
    uppercased: a running number wider than three digits is still a real
    serial, and truncating or padding it would invent a different one.
    """
    text = clean_val(serial).upper()
    if text.isdigit() and len(text) < digits:
        return text.zfill(digits)
    return text


def decode_gs1_date(value: object) -> date | None:
    """Decode a ``YYMMDD`` GS1 date.

    The legacy code hard-codes the ``2000 + YY`` century everywhere; that is kept
    so historical scans decode identically. ``DD == 00`` means "end of month" in
    GS1 and is coerced to the 1st, matching ``bt-scanner.js``.
    """
    text = clean_val(value)
    if len(text) != 6 or not text.isdigit():
        return None
    year, month, day = 2000 + int(text[0:2]), int(text[2:4]), int(text[4:6])
    if not 1 <= month <= 12:
        return None
    day = day or 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def expiry_is_past(value: object, now: datetime | None = None) -> bool:
    """True when a GS1 expiry has elapsed. Expiry is valid through end of day."""
    parsed = decode_gs1_date(value)
    if parsed is None:
        return False
    end_of_day = datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59)
    return end_of_day < (now or datetime.now())


def _bracket_pairs(text: str, ais: Iterable[str] = BRACKET_AIS) -> dict[str, str]:
    """Extract ``(AI)value`` pairs. Variable-length values end at the next ``(``."""
    found: dict[str, str] = {}
    pattern = re.compile(r"\((" + "|".join(sorted(ais, key=len, reverse=True)) + r")\)([^()]+)")
    for match in pattern.finditer(text):
        found.setdefault(match.group(1), clean_val(match.group(2)))
    return found


#: AIs whose value is entirely numeric, so a non-numeric candidate is not that
#: field and the branch can be abandoned immediately.
_NUMERIC_AIS = frozenset({"00", "01", "02", "11", "13", "15", "17", "30", "37", "310", "311", "320"})
#: AIs holding a ``YYMMDD`` date. An undecodable value prunes the branch, which
#: is what stops a batch being cut short at a digit pair that merely looks like
#: a date AI.
_DATE_AIS = frozenset({"11", "13", "15", "17"})


def _ai_value_ok(ai: str, value: str) -> bool:
    """Whether ``value`` is a plausible payload for ``ai``.

    Cheap per-AI validation is what makes the search below accurate rather
    than merely terminating: most wrong splits produce a value that cannot
    belong to the AI it was assigned to, and rejecting those prunes the branch
    before it can be mistaken for an answer.
    """
    if not value:
        return False
    if ai in _NUMERIC_AIS and not value.isdigit():
        return False
    if ai in _DATE_AIS and decode_gs1_date(value) is None:
        return False
    return True


#: Ceiling on search steps. A real label resolves in a few dozen; the cap only
#: exists so a pathological string cannot make a scan hang.
_AI_STREAM_BUDGET = 1_200

#: Longest string the search will attempt. A GS1 element string for this stock
#: runs to about 60 characters, so this is generous; past it the input is not a
#: barcode and refusing it outright is cheaper than proving it with a search.
#: Both bounds matter because a bulk paste runs this up to BULK_SCAN_MAX_CODES
#: times in one request — the worst case is the cap times two thousand, not the
#: cap once.
_AI_STREAM_MAX_LENGTH = 120

#: Shortest a variable-length field may be when its end has to be *inferred*.
#:
#: One character is what lets the search manufacture a field out of another
#: one's contents. A real MLSIPL batch is ``V21432702``, and cutting it after
#: the ``V`` leaves ``21432702`` — which reads as AI (21) plus a serial, scores
#: better (more AIs, fewer characters buried in variable fields) and wins. The
#: result is a batch of ``V`` and a serial invented out of the batch's own
#: digits, which is worse than failing to parse at all.
#:
#: No real batch, serial or material code on Meril stock is one character, so
#: requiring two costs nothing and removes the whole class of split. A field
#: the scanner terminated with :data:`GS` is exempt: there the length is stated
#: rather than guessed, so a one-character value is a fact.
_MIN_INFERRED_FIELD = 2


def _parse_ai_stream(text: str) -> dict[str, str]:
    """Split an unbracketed GS1 string into its AIs, or return ``{}``.

    This replaces a left-to-right walker that ended every variable-length
    field at the first two or three digits that happened to spell an AI. That
    is a guess, and on real labels it guessed wrong: ``…240MK-UFPSLE00``
    terminated the material at the ``00`` inside it and yielded
    ``MK-UFPSLE``, and ``10A26050424112606…`` read ``241`` out of the middle
    of the batch and produced batch ``A260504`` with material ``12606``. Both
    are silently wrong values rather than a rejected scan, which is the worst
    failure a parser of dispatch data can have.

    Three things make this exact instead of a guess:

    * :data:`GS`, where the scanner sent it, ends a variable-length field
      outright. It survives to here now, and where it is present no inference
      happens at all.
    * Without it, the split is *searched*: the whole string must be consumed by
      a run of AIs whose values are each valid for the AI they are assigned to
      (a date AI must hold a decodable ``YYMMDD``, a numeric AI must hold
      digits), which rejects most wrong splits outright.
    * Where several splits still consume the string, the one with the **most
      AIs** wins, breaking ties on the fewest characters buried inside
      variable-length fields. That is what distinguishes the two readings of
      ``10A26050424112606011731053121007240…``: the right one recognises
      ``10 11 17 21 240`` (five AIs, 23 variable characters), while the wrong
      one hides the two dates inside a bogus ``241`` (four AIs, 36). Picking
      the first split found — shortest-first or otherwise — picks the wrong one
      about as often as the right one, so the choice has to be scored.

    The search is a dynamic program memoised on position and the AIs already
    read, rather than exponential in the number of fields, and it is bounded at
    both ends by :data:`_AI_STREAM_MAX_LENGTH` and :data:`_AI_STREAM_BUDGET`.
    A real label resolves in a fraction of a millisecond. Up to three leading
    junk characters are tolerated, as the walker did.
    """
    if not text or len(text) > _AI_STREAM_MAX_LENGTH:
        return {}

    #: (position, AIs already used) -> best split from there, or None.
    #:
    #: The used-set is part of the key because "no AI twice" makes the answer
    #: depend on it. That rule is what stops the search finishing a string by
    #: opening a SECOND field of an AI it has already read:
    #: ``…240MLUCXB109-K`` was being split as 240=``MLUCXB`` plus a second
    #: AI (10) holding ``9-K``, which scores better than the honest reading and
    #: truncates the material. An element string carries each of these AIs once.
    memo: dict[tuple[int, frozenset], tuple[int, int, dict[str, str]] | None] = {}
    budget = [_AI_STREAM_BUDGET]

    def better(a, b) -> bool:
        """More AIs wins; then fewer characters hidden in variable fields."""
        if b is None:
            return True
        return (-a[0], a[1]) < (-b[0], b[1])

    def walk(pos: int, used: frozenset) -> tuple[int, int, dict[str, str]] | None:
        if pos >= len(text):
            return (0, 0, {})
        key = (pos, used)
        if key in memo:
            return memo[key]
        budget[0] -= 1
        if budget[0] <= 0:
            return None
        memo[key] = None  # guards against a cycle via a zero-length advance

        # A separator with no field in front of it carries no information.
        if text[pos] == GS:
            result = walk(pos + 1, used)
            memo[key] = result
            return result

        best: tuple[int, int, dict[str, str]] | None = None
        for width in (3, 2):  # 3-digit AIs must be tried first
            ai = text[pos : pos + width]
            if ai not in AI_DEF or ai in used:
                continue
            fixed_len, max_var = AI_DEF[ai]
            start = pos + width
            onward = used | {ai}

            if fixed_len:
                value = text[start : start + fixed_len]
                if len(value) != fixed_len or not _ai_value_ok(ai, value):
                    continue
                sub = walk(start + fixed_len, onward)
                if sub is None:
                    continue
                fields = dict(sub[2])
                fields[ai] = clean_val(value)
                candidate = (sub[0] + 1, sub[1], fields)
                if better(candidate, best):
                    best = candidate
                continue

            limit = min(max_var, len(text) - start)
            separator = text.find(GS, start)
            if separator != -1:
                # A variable-length field is terminated BY the separator, so
                # its length is not a matter of inference: it runs to the GS
                # and no further, and it cannot stop short of one either. That
                # second half matters — without it the search was free to read
                # ``10B1121260601<GS>`` as batch ``B11`` plus a serial
                # ``260601`` that happened to end at the separator, which is
                # two readings of one unambiguous field.
                span = separator - start
                lengths: Sequence[int] = (span,) if 1 <= span <= limit else ()
            else:
                lengths = range(_MIN_INFERRED_FIELD, limit + 1)

            for length in lengths:
                value = text[start : start + length]
                if GS in value:
                    break  # cannot run past its own terminator
                if not _ai_value_ok(ai, value):
                    continue
                nxt = start + length
                # Step over the separator this field ended on, if any.
                sub = walk(nxt + 1 if nxt == separator else nxt, onward)
                if sub is None:
                    continue
                fields = dict(sub[2])
                fields[ai] = clean_val(value)
                candidate = (sub[0] + 1, sub[1] + length, fields)
                if better(candidate, best):
                    best = candidate

        memo[key] = best
        return best

    for offset in range(0, 4):
        if offset >= len(text):
            break
        memo.clear()
        parsed = walk(offset, frozenset())
        if parsed and parsed[2]:
            return parsed[2]
    return {}


def _lookup_master(master: Mapping[str, Mapping[str, object]] | None, key: str) -> Mapping[str, object] | None:
    """Look a code up tolerating case and leading zeros, as the legacy store did."""
    if not master or not key:
        return None
    for variant in (key, key.upper(), key.lstrip("0"), key.lstrip("0").upper()):
        if variant and variant in master:
            return master[variant]
    return None


def _master_code(row: Mapping[str, object] | None) -> str:
    """Pull the SAP material code out of a master row, trying the legacy aliases."""
    if not row:
        return ""
    for key in ("Material Code", "SAP Code", "SAP code", "Sap Code", "Material", "Code", "material_code"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


# ─── Parsing strategies ──────────────────────────────────────────────────────
def _ai21_holds_running_number(
    value: object,
    profile: EntityProfile,
    material_master: Mapping[str, Mapping[str, object]] | None,
) -> bool:
    """True when an ``ai21_is_material`` entity's AI (21) is a unit serial.

    Only asked of entities that overload AI (21) (MLSIPL); a standard GS1
    entity already reads it as the serial and never reaches here.

    The two tests are ordered deliberately:

    1. Anything the material master knows is a material, whatever it looks
       like. A numeric material code therefore stays a material — which is why
       this is not simply an ``isdigit`` check.
    2. Otherwise a purely numeric value is a running number. A material code
       on Meril stock always carries letters (``MLPSXG517-K``, ``FGALCRA109``),
       so "all digits and unknown to the master" leaves nothing else it could
       be, and the reported format is a three-digit number.

    Being wrong in the safe direction matters more than being clever: an
    unrecognised value falls through to today's behaviour (material fallback,
    blank serial, and ``process_scan`` assigns the next number for the batch),
    so a misread costs a generated serial rather than a failed match.
    """
    if not profile.ai21_is_material:
        return False
    text = clean_val(value)
    if not text:
        return False
    if _lookup_master(material_master, text):
        return False
    return text.isdigit()


def _apply_ai_map(
    found: Mapping[str, str],
    profile: EntityProfile,
    result: ParsedBarcode,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """Map extracted AIs onto domain fields, honouring per-entity overrides.

    Returns the deferred AI (21)-as-material candidate for entities configured
    with ``ai21_is_material`` (MLSIPL), or ``""``.

    The candidate is *returned rather than assigned* because it is a genuine
    last resort: the profile calls it "a material fallback", and a fallback must
    lose to the AI (01) GTIN. Assigning it here made ``result.material`` truthy,
    which then short-circuited the GTIN lookup in :func:`parse_barcode` — so an
    MLSIPL scan carrying both (01) and (21) had its serial written into the
    material field and its real material silently dropped. See the regression
    test ``test_mlsipl_gtin_wins_over_ai21_material_fallback``.

    For such an entity the AI (21) field is decided *per value*, not per
    entity, because both shapes are real on MLSIPL stock:

        (01)00811767021197(10)M07032701(21)MLPSXG517-K   -> a material code
        (01)08906067240040(10)V24412705(21)007(240)FG…   -> a running number

    Treating (21) as a material for the whole entity discarded the second
    label's serial and shipped a blank Serial column; treating it as a serial
    for the whole entity would feed ``MLPSXG517-K`` to the serial field and
    leave the first label with no material to match on, which is the
    regression ``TestMlsiplEntityPath`` was written to prevent. See
    :func:`_ai21_holds_running_number` for the discriminator.
    """
    result.material = found.get("240") or found.get("241") or ""
    ai21_running_number = _ai21_holds_running_number(found.get("21"), profile, material_master)
    ai21_material = ""
    if not result.material and profile.ai21_is_material and not ai21_running_number:
        ai21_material = found.get("21", "")
    result.batch = found.get("10", "")
    result.mfg = found.get("11", "")
    result.expiry = found.get("17") or found.get("15", "")

    if profile.supports_serial and not profile.ai21_is_material:
        serial = found.get("21") or found.get("250") or ""
        if serial:
            result.serial = serial
        else:
            result.serial = UNKNOWN_SERIAL
            result.is_default_serial = True
    elif ai21_running_number:
        # The label carries the unit's own number, so use it rather than
        # letting process_scan invent the next one for the batch.
        result.serial = clean_val(found.get("21"))
        result.serial_from_label = True
    else:
        result.serial = ""
        result.is_default_serial = True

    qty = found.get("30") or found.get("37")
    if qty:
        try:
            result.qty = float(qty)
        except ValueError:
            result.qty = None

    return ai21_material


def _stream_beats_compact(
    text: str,
    ai_stream: Mapping[str, str],
    expected_batches: set[str],
) -> bool:
    """Whether the AI-stream reading should be preferred to the locked layout.

    Only when it recovers a serial the layout would discard *and* the delivery
    data backs its batch. Both halves are load-bearing: without the first this
    would override the layout for nothing, and without the second it overrides
    it on a guess — which is how batch ``V03212711`` became ``V03`` plus a
    serial of ``2711`` on live data.
    """
    if not ai_stream.get("21") or not expected_batches:
        return False
    stream_batch = str(ai_stream.get("10") or "").strip().upper()
    if not stream_batch or stream_batch not in expected_batches:
        return False
    # And only if the layout would actually have got the batch wrong; when both
    # readings agree on it there is nothing to choose between them.
    compact = COMPACT_GS1_RE.match(text)
    if compact is None:
        return True
    return str(compact.group(2) or "").strip().upper() != stream_batch


def parse_barcode(
    raw: object,
    profile: EntityProfile,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
    known_batches: Iterable[str] | None = None,
) -> ParsedBarcode:
    """Parse one scan string into its domain fields.

    Strategies are tried in the order the legacy code used, which is
    significant: bracketed AIs always win over positional inference, and the
    raw-string fallback only fires when nothing else matched.

    ``known_batches`` are the batches the caller is actually expecting — the
    batches on the open delivery lines, for the outbound scanner. They are a
    tie-breaker, never a filter: an unbracketed string is genuinely ambiguous
    where a batch's own digits spell an AI, and this is the only evidence that
    settles it rather than guessing.

    The real case: MLSIPL batch ``V03212711`` reads equally well as batch
    ``V03`` plus AI (21) serial ``2711``, and batch ``V21432702`` as ``V`` plus
    serial ``432702``. Replaying 413 live scans, a parser that decided this on
    shape alone got 26 of them wrong. Asking which reading names a batch the
    delivery is waiting for gets all 413 right, and costs nothing when the
    caller has no delivery data — it just falls back to the locked layout.
    """
    text = normalize(raw)
    if profile.strip_whitespace:
        text = re.sub(r"\s+", "", text)
    text = _CONTROL_RE.sub("", text)

    result = ParsedBarcode(raw=str(raw or "").strip())
    if not text:
        result.error = "Empty barcode"
        return result

    if profile.allow_envelope:
        text = _strip_envelope(text)
        text = _CARET_AI_RE.sub(r"(\1)", text)

    # The same string with the GS separators kept, and the AI stream read out of
    # it. Done once here because two strategies below need the answer: the
    # locked compact layout has to know whether it would be discarding a serial,
    # and the unbracketed strategy is this reading.
    stream_text = normalize(raw)
    if profile.strip_whitespace:
        stream_text = re.sub(r"\s+", "", stream_text)
    stream_text = _CONTROL_EXCEPT_GS_RE.sub("", stream_text)
    if profile.allow_envelope:
        stream_text = _strip_envelope(stream_text)
    ai_stream = {} if "(" in text else _parse_ai_stream(stream_text)
    expected = {str(b).strip().upper() for b in (known_batches or ()) if str(b or "").strip()}

    # 1. Meril Trauma positional legacy (validators only) — checked before the
    #    bracket pass because the layout contains no parentheses at all.
    if profile.trauma_legacy:
        trauma = TRAUMA_RE.match(text)
        if trauma:
            result.material = trauma.group(1).upper()
            result.batch = trauma.group(2).upper()
            result.mfg = trauma.group(3)
            result.serial = ""
            result.is_default_serial = True
            result.strategy = "trauma"
            result.is_valid = True
            expiry = _iso_to_gs1(trauma.group(4))
            if expiry:
                result.expiry = expiry
            _resolve_material(result, profile, material_master)
            return result

    # 2. Compact GS1 with a locked field order and no brackets.
    #
    #    The locked layout has no AI (21) slot, so ``10(.+?)`` quietly absorbs a
    #    serial that IS on the label — ``…10V21432702 21 0456 11…`` came out as
    #    batch ``V21432702210456`` with no serial at all, and the scan was then
    #    given a generated running number as though the label had carried none.
    #
    #    So it steps aside for the AI-stream reading, but ONLY when the delivery
    #    data confirms that reading: the alternative batch has to be one the
    #    caller is waiting for. Deferring on the mere presence of a ``21`` in
    #    the stream is what broke 26 of 413 live scans, because a batch like
    #    ``V03212711`` contains one.
    if profile.compact_gs1 and "(" not in text and not _stream_beats_compact(
        text, ai_stream, expected
    ):
        compact = COMPACT_GS1_RE.match(text)
        if compact:
            result.batch = compact.group(2)
            result.mfg = compact.group(3)
            result.expiry = compact.group(4)
            result.material = compact.group(5)
            result.serial = "" if not profile.supports_serial else UNKNOWN_SERIAL
            result.is_default_serial = True
            result.strategy = "compact-gs1"
            result.is_valid = True
            _finalise(result, profile, material_master)
            return result

    # 3. Bracketed AIs, optionally followed by a positional walk of the remainder.
    found = _bracket_pairs(text)
    unbracketed = False
    if profile.positional_walker:
        flat = text
        for ai, _ in found.items():
            flat = re.sub(r"\(" + ai + r"\)[^()]+", "", flat, count=1)
        flat = flat.replace("(", "").replace(")", "")
        for ai, value in _parse_ai_stream(flat).items():
            found.setdefault(ai, value)  # bracketed results win
        if "01" not in found:
            gtin = re.match(r"^01(\d{13,14})", text)
            if gtin:
                found["01"] = clean_val(gtin.group(1))

    # 3b. An unbracketed AI stream, for every entity rather than only the two
    #     the legacy forks happened to enable a walker on.
    #
    #     A gun reading a GS1 DataMatrix emits the AIs with no parentheses at
    #     all, and that reached nothing here: MHPL, CARDIO and MDPL had no
    #     walker, so such a scan fell through to the raw fallback and came back
    #     material=UNKNOWN — rejected, with no hint that the string was
    #     perfectly good. Only MLSIPL's single locked field order was handled,
    #     and only when it carried no serial.
    #
    #     Runs on ``stream_text``, which keeps the GS separators the blanket
    #     control-character strip removes, so where the gun marked its field
    #     ends the split is exact rather than inferred.
    if not found and ai_stream:
        found = ai_stream
        unbracketed = True

    if found:
        ai21_material = _apply_ai_map(found, profile, result, material_master)
        result.strategy = (
            "gs1-unbracketed" if unbracketed
            else ("gs1-walker" if profile.positional_walker else "gs1")
        )
        gtin_resolved = False
        if not result.material and found.get("01"):
            gtin_resolved = _resolve_gtin(result, found["01"], material_master)
        # Only now, with the GTIN having had its chance, does AI (21) stand in
        # as the material — the order matters, see _apply_ai_map's docstring.
        #
        # "Had its chance" means RESOLVED, not merely "produced a string".
        # _resolve_gtin falls back to echoing the raw GTIN when the master has
        # no row for it, and that echo is truthy — so testing `not
        # result.material` let an unresolvable 14-digit number block the AI
        # (21) fallback, and the scan was then reported as "Not found in SAP"
        # with a GTIN where the material code should be, even though AI (21)
        # carried a code the master resolves perfectly well.
        #
        # The common case, not an edge case: only 37 of 3,192 MLSIPL material
        # rows carry a GTIN at all, so the master almost never knows the number
        # in AI (01). ai21_material is non-empty only for ai21_is_material
        # entities that had no AI (240)/(241) material, so no other entity's
        # path changes.
        if ai21_material and not gtin_resolved:
            result.material = ai21_material
        if result.material or result.batch:
            result.is_valid = True
            _finalise(result, profile, material_master)
            return result

    # 4. Length-keyed positional slicing (MLSIPL configurable rules).
    if profile.positional_rules and positional_rules:
        sliced = _apply_positional_rules(text, positional_rules, material_master)
        if sliced:
            result.material, result.batch, result.mfg, result.expiry = sliced
            result.serial = "" if not profile.supports_serial else UNKNOWN_SERIAL
            result.is_default_serial = True
            result.strategy = "positional"
            result.is_valid = True
            _finalise(result, profile, material_master)
            return result

    # 5. Bare GTIN.
    gtin_only = re.match(r"^01(\d{14})$", text)
    if gtin_only:
        _resolve_gtin(result, gtin_only.group(1), material_master)
        result.serial = "" if not profile.supports_serial else UNKNOWN_SERIAL
        result.is_default_serial = True
        result.strategy = "gtin"
        result.is_valid = bool(result.material)
        _finalise(result, profile, material_master)
        return result

    # 6. Raw fallback — the whole string is treated as an identifier.
    if "(" not in text and len(text) > 3:
        if profile.supports_serial:
            result.serial = text
            result.material = UNKNOWN
            result.batch = UNKNOWN
        else:
            result.material = text.upper()
            result.batch = UNKNOWN
        result.strategy = "raw"
        result.is_valid = True
        _finalise(result, profile, material_master)
        return result

    result.error = "Unrecognised barcode format"
    return result


def _strip_envelope(text: str) -> str:
    """Remove the ``A…A`` wrapper some Micro scanners emit."""
    if len(text) > 2 and text[0] == "A" and text[-1] == "A":
        inner = text[1:-1]
        if inner.startswith("01") or inner.startswith("(01)"):
            return inner
    return text


def _iso_to_gs1(iso: str) -> str:
    """``yyyy-mm-dd`` → ``YYMMDD`` so all downstream date handling stays uniform."""
    try:
        parsed = datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ""
    return f"{parsed.year % 100:02d}{parsed.month:02d}{parsed.day:02d}"


def _apply_positional_rules(
    text: str,
    rules_by_length: Mapping[int, Sequence[PositionalRule]],
    material_master: Mapping[str, Mapping[str, object]] | None,
) -> tuple[str, str, str, str] | None:
    """Slice by total string length; disambiguate using the material master."""
    rules = rules_by_length.get(len(text))
    if not rules:
        return None
    chosen = rules[0]
    for rule in rules:
        candidate = text[rule.mat_start : rule.mat_start + rule.mat_len].strip().upper()
        if _lookup_master(material_master, candidate):
            chosen = rule
            break
    material = text[chosen.mat_start : chosen.mat_start + chosen.mat_len].strip()
    batch = text[chosen.batch_start : chosen.batch_start + chosen.batch_len].strip()
    mfg = text[chosen.mfg_start : chosen.mfg_start + chosen.mfg_len] if chosen.mfg_len else ""
    expiry = text[chosen.exp_start : chosen.exp_start + chosen.exp_len] if chosen.exp_len else ""
    return material, batch, mfg, expiry


def _resolve_gtin(
    result: ParsedBarcode,
    gtin: str,
    material_master: Mapping[str, Mapping[str, object]] | None,
) -> bool:
    """Translate a GTIN into a material code, falling back to the GTIN itself.

    Returns whether the master actually knew the GTIN. Callers need that
    separately from ``result.material``, because the fallback writes the raw
    GTIN into it — a truthy value that looks like a successful resolution but
    really means "no idea, here is the number you gave me".
    """
    gtin = gtin.strip()
    row = _lookup_master(material_master, gtin) or _lookup_master(material_master, gtin.lstrip("0"))
    resolved = _master_code(row)
    result.material = resolved or gtin
    return bool(resolved)


def _resolve_material(
    result: ParsedBarcode,
    profile: EntityProfile,
    material_master: Mapping[str, Mapping[str, object]] | None,
) -> None:
    """Map a Meril scan code onto its SAP material code, keeping the original."""
    result.scan_code = result.material
    if not result.material or result.material == UNKNOWN:
        return
    row = _lookup_master(material_master, result.material)
    resolved = _master_code(row)
    if resolved:
        result.material = resolved


def _finalise(
    result: ParsedBarcode,
    profile: EntityProfile,
    material_master: Mapping[str, Mapping[str, object]] | None,
) -> None:
    """Normalise casing/punctuation and resolve the material against the master."""
    result.material = (result.material or "").strip().upper()
    batch = (result.batch or "").strip()
    if profile.sanitise_batch:
        batch = _BATCH_SANITISE_RE.sub("", batch)
    result.batch = batch.upper()
    # A serial read off the label survives even on a batch-level entity:
    # supports_serial answers "does SAP track this unit at serial level" (it
    # gates the stock dump's serial column in the Stock Validator), which is a
    # different question from "did this label print a unit number".
    if profile.supports_serial or result.serial_from_label:
        result.serial = (result.serial or "").strip()
    else:
        result.serial = ""
    _resolve_material(result, profile, material_master)


# ─── Serial identity ─────────────────────────────────────────────────────────
def is_non_serial(serial: str) -> bool:
    """True when a scan carries no usable serial number.

    The placeholder comparison is case-insensitive because callers reach this
    with the value at different stages of normalisation — ``clean()`` upper-cases,
    so a caller that cleans first was handing over ``"UNKNOWN"`` and slipping past
    an exact match against ``"Unknown"``. Case never distinguishes a real serial
    from the placeholder, so it must not decide this.
    """
    stripped = (serial or "").strip()
    return not stripped or stripped.casefold() == UNKNOWN_SERIAL.casefold()


def build_serial_identity(
    parsed: ParsedBarcode,
    profile: EntityProfile,
    rng: random.Random | None = None,
    sequence: int | None = None,
) -> tuple[str, str]:
    """Return ``(display_id, stored_serial)`` for a scan.

    Serialised items are keyed on ``BATCH || SERIAL`` — the serial alone is not
    unique across batches. The serial half is normalised through
    :func:`format_serial_component`, so a label printed ``(21)7`` and one
    printed ``(21)007`` resolve to the same assigned serial instead of two.

    Non-serialised items have no serial on the label, and the dispatch sheet
    still needs one: ``sequence`` is the running number the caller has picked
    for this batch, and the item is assigned ``BATCH || 001``, ``BATCH || 002``
    and so on. Pass it and the stored serial is never blank, which is what the
    SAP upload requires.

    With no ``sequence`` the legacy behaviour is kept — a random display id and
    an empty stored serial — because that is what makes duplicate detection a
    no-op for an entity that genuinely has nothing to deduplicate on, and it is
    what the engine's own unit tests pin. Callers that persist a scan always
    pass one; see :func:`merilv.services.outbound.process_scan`.
    """
    if is_non_serial(parsed.serial):
        if sequence is not None:
            identity = f"{parsed.batch.upper()}{format_serial_component(sequence)}"
            return identity, identity
        generator = rng or random
        lo = 10 ** (profile.pseudo_serial_digits - 1)
        hi = (10**profile.pseudo_serial_digits) - 1
        return f"[BATCH] {parsed.batch.upper()} (#{generator.randint(lo, hi)})", ""
    identity = label_serial_identity(parsed)
    return identity, identity


def label_serial_identity(parsed: ParsedBarcode) -> str:
    """The identity a label's OWN serial gives it, or "" if it carries none.

    The serialised half of :func:`build_serial_identity`, split out because the
    console needs to name the serial on a scan that was *refused*. A rejection
    never reaches the assignment above — no running number is minted for it and
    none should be, since nothing was assigned — but a picker looking at a
    refusal still has to know which unit it was about, and the label's own
    serial is the answer for every serialised item.

    Returns "" rather than a generated number for an item whose label carries
    no serial: the running number is an artefact of assignment, and showing one
    against a scan that assigned nothing would name a serial that does not
    exist anywhere.
    """
    if is_non_serial(parsed.serial):
        return ""
    return f"{(parsed.batch or '').upper()}{format_serial_component(parsed.serial)}"


__all__ = [
    "AI_DEF",
    "BUILTIN_PROFILES",
    "NOT_IN_MASTER",
    "SERIAL_DIGITS",
    "UNKNOWN",
    "UNKNOWN_SERIAL",
    "EntityProfile",
    "ParsedBarcode",
    "PositionalRule",
    "build_serial_identity",
    "clean_val",
    "decode_gs1_date",
    "expiry_is_past",
    "format_serial_component",
    "is_non_serial",
    "label_serial_identity",
    "normalise_scan_input",
    "normalize",
    "parse_barcode",
    "parse_positional_rules",
]
