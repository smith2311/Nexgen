"""Stock reconciliation engine.

Matches physically-scanned barcodes against a SAP stock dump and classifies each
scan. Ported from ``public/script.js`` (the MHPL engine, which is the most
complete of the five legacy variants) with the MLSIPL/Cardio/MDPL behaviours
folded in as configuration rather than as forks.

Two details are load-bearing and easy to lose in a rewrite:

*   **Pass ordering is semantic.** Every scan is tried for an exact match before
    *any* scan is tried for a batch match. A per-scan cascade would let a loose
    material-only match consume a row that a later exact scan needed.
*   **Inventory rows are shared and mutable.** One SAP row is reachable under
    several index keys, so consumption must be tracked on the row itself and
    candidate lists must be de-duplicated by identity, not by value.

The engine is deliberately free of Django imports so it can be unit-tested
against plain dictionaries.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

from .barcode import EntityProfile, ParsedBarcode, is_non_serial

# ─── Status constants ────────────────────────────────────────────────────────
MATCHED = "matched"
PARTIAL = "partial"
WARNING = "warning"
VARIANCE = "variance"
VARIANCE_PARTIAL = "variance_partial"
BATCH_MISMATCH = "batch_mismatch"
STOCK_EXHAUSTED = "stock_exhausted"
MASTER_MISSING = "master_missing"
DUPE_SCAN = "dupe_scan"
OVER_SCAN = "over_scan"
PLANT_MISMATCH = "plant_mismatch"
EXPIRY_MISMATCH = "expiry_mismatch"
INCOMPLETE = "incomplete"
ERROR = "error"

#: Which verdict wins when several rules fire on one row. Higher takes precedence.
STATUS_PRIORITY = {
    ERROR: -1,
    VARIANCE: 0,
    WARNING: 1,
    PARTIAL: 2,
    MATCHED: 3,
    VARIANCE_PARTIAL: 4,
    DUPE_SCAN: 5,
    OVER_SCAN: 6,
}

# Stock condition buckets.
STOCK_GOOD = "good"
STOCK_DAMAGE = "damage"
STOCK_EXPIRED = "expired"
STOCK_NEAR_EXPIRY = "near_expiry"
STOCK_SHORT_EXPIRY = "short_expiry"
STOCK_NO_EXPIRY = "no_expiry"

#: Expiry-threshold conventions. The two legacy families disagree about which of
#: "near" and "short" is the tighter window, so the caller picks one explicitly.
CONVENTION_MHPL = "mhpl"  # near (5mo) is tighter than short (12mo)
CONVENTION_MLSIPL = "mlsipl"  # short (3mo) is tighter than near (6mo)

_DAMAGE_TERMS = ("dam", "bad", "broken")


def clean(value: object) -> str:
    """Normalise a value for comparison: trimmed and uppercased."""
    return str(value if value is not None else "").strip().upper()


def concat_serials(
    batch: str,
    serial: str,
    profile: EntityProfile | None,
    delimiter: str = "",
) -> tuple[str, ...]:
    """Every SAP serial form that ``batch`` + ``serial`` may be stored as.

    The single place the concatenation rules live. Nothing else in the codebase
    may build a concatenated serial by hand: :func:`serial_matches` is the only
    consumer that needs to *compare* one, and it calls this.

    ``delimiter`` is ``ValidationSettings.concat_delimiter``. It is a parameter
    rather than a read off ``profile`` because it is a per-entity *matching*
    setting, not a barcode-layout trait, and it defaults to empty so a caller
    holding only a profile still gets the plain forms.
    """
    batch = clean(batch)
    serial = clean(serial)
    # Not clean()ed: a configured delimiter may legitimately be whitespace, and
    # stripping it would silently match a form the entity never stores.
    delimiter = str(delimiter or "").upper()
    values = [f"{batch}{delimiter}{serial}"]
    if profile is not None and serial.isdigit():
        padded = serial.zfill(profile.pseudo_serial_digits)
        if padded != serial:
            values.append(f"{batch}{delimiter}{padded}")
    return tuple(values)


def serial_matches(
    batch: str,
    physical_serial: str,
    system_serial: str,
    profile: EntityProfile | None,
    delimiter: str = "",
) -> bool:
    """True when a scanned serial and a SAP serial are the same serial.

    The one authority on serial correspondence. Both the matching passes and
    every "the serial differs" report go through this, so the engine cannot
    accept a pair as a Concat match and then describe the same pair as a
    mismatch — which is exactly what happened while matching consulted
    :func:`concat_serials` and reporting compared the two strings raw: a scan of
    batch ``L2512001`` serial ``029`` against SAP's ``L2512001029`` matched, and
    was then labelled "scanned serial differs".

    Accepts only what :func:`concat_serials` generates, plus direct equality.
    Deliberately *not* a substring test — ``029`` occurring inside ``XYZ029999``
    is not a correspondence, and treating it as one would invent serial matches
    for every entity whose serials happen to share digits.

    Returns ``False`` when either side is blank: with nothing to compare there
    is no correspondence to assert. Callers that must tell "no serial was
    scanned" apart from "the serials disagree" test the blanks themselves —
    those are different facts for an operator and deserve different words.
    """
    physical = clean(physical_serial)
    system = clean(system_serial)
    if not physical or not system:
        return False
    if physical == system:
        return True
    return system in set(concat_serials(batch, physical, profile, delimiter))


# ─── Data structures ─────────────────────────────────────────────────────────
@dataclass
class InventoryItem:
    """One row of the SAP stock dump, with a running consumption counter."""

    material: str
    batch: str = ""
    serial: str = ""
    qty: float = 1.0
    plant: str = "-"
    location: str = "-"
    expiry: str = "-"
    ageing: str = ""
    description: str = ""
    customer_name: str = ""
    customer_code: str = ""
    billing_document: str = ""
    billing_date: str = ""
    used: float = 0.0
    row_index: int = 0

    @property
    def available(self) -> float:
        return self.qty - self.used


@dataclass
class ScanInput:
    """One line of the physical scan file, before parsing."""

    raw: str
    material: str = ""
    batch: str = ""
    serial: str = ""
    #: Plant as stated on a tabular scan sheet, when it carries one. Kept even
    #: though the engine reports ``system_plant`` from the matched SAP record:
    #: views.validator_run splats the parsed scan record straight into this
    #: dataclass, so a field the parser emits and this does not declare is a
    #: TypeError at upload time, not a quietly ignored extra.
    plant: str = ""
    condition: str = ""
    qty: float | None = None
    description: str = ""
    customer_name: str = ""
    customer_code: str = ""


@dataclass
class ResultRow:
    """The engine's verdict for a single scan."""

    sequence: int
    raw_scan: str
    material: str = ""
    physical_batch: str = ""
    physical_serial: str = ""
    scan_qty: float = 1.0
    consumed_qty: float = 0.0
    condition: str = ""
    expiry_date: date | None = None

    system_batch: str = ""
    system_serial: str = ""
    system_plant: str = ""
    system_location: str = ""
    system_expiry: str = ""
    available_batches: list[str] = field(default_factory=list)
    available_serials: list[str] = field(default_factory=list)
    material_description: str = ""
    #: From the material master, not from SAP and not from the barcode - #17
    #: asks for both on this screen and on the extractor. Blank when the
    #: caller reconciles without a master, which the engine allows.
    material_group: str = ""
    ph_level: str = ""
    customer_code: str = ""
    customer_name: str = ""
    billing_document: str = ""
    billing_date: str = ""

    status: str = VARIANCE
    status_priority: int = 0
    match_type: str = ""
    stock_status: str = ""
    target_location: str = ""
    detail: str = ""
    flags: list[str] = field(default_factory=list)
    is_default_serial: bool = False

    def add_flag(self, code: str, message: str) -> None:
        """Prepend an operator-facing note and record its machine-readable code."""
        self.flags.append(code)
        self.detail = f"[{message}] {self.detail}".strip()


@dataclass
class ValidationSettings:
    """Everything the engine needs that isn't the data itself.

    Mirrors :class:`merilv.models.Entity`; kept separate so the engine stays
    importable without Django.
    """

    match_concat: bool = True
    match_batch: bool = True
    match_material: bool = True
    concat_delimiter: str = ""
    triple_sort: bool = True
    multi_row_consume: bool = True
    smart_zero: bool = True
    use_scan_qty: bool = True
    strict_batch: bool = False
    plant_priority: Sequence[str] = ()
    good_location: str = "RT01"
    damage_location: str = "DMG1"
    auto_location: bool = True

    expiry_convention: str = CONVENTION_MHPL
    near_expiry_months: float = 5
    short_expiry_months: float = 12
    month_divisor: float = 30.0

    # Post-processing modules — all on by default except group suggestions,
    # matching the legacy defaults.
    check_duplicate_scan: bool = True
    check_over_scan: bool = True
    check_plant_mismatch: bool = True
    check_incomplete: bool = True
    check_expiry_mismatch: bool = True
    expiry_tolerance_days: int = 7


@dataclass
class ValidationOutcome:
    rows: list[ResultRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_scanned: float = 0.0
    total_system: float = 0.0
    matched_qty: float = 0.0
    batch_matched_qty: float = 0.0
    material_matched_qty: float = 0.0
    variance_qty: float = 0.0
    duplicate_serials: list[dict[str, object]] = field(default_factory=list)

    @property
    def reconciliation_pct(self) -> float:
        """Share of SCANNED quantity that resolved to a real SAP record — exact,
        batch, or material-only match — rather than a genuine variance.

        Was previously computed as ``scanned / total_system``, a stock-coverage
        figure (how much of all SAP stock got physically scanned) rather than a
        match rate. Every screen that surfaces this field treats it as a quality
        indicator (the history list colours it green at ≥99%, red otherwise),
        so a full stock take with low scan coverage but perfect matching used to
        read as a failure. Matches the legacy validator's own "Match Rate".
        """
        if self.total_scanned <= 0:
            return 0.0
        matched_total = self.matched_qty + self.batch_matched_qty + self.material_matched_qty
        return round(min(matched_total, self.total_scanned) / self.total_scanned * 100, 2)

    @property
    def qty_difference(self) -> float:
        """Positive means unscanned stock; negative means over-scanning."""
        return self.total_system - self.total_scanned

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for row in self.rows:
            counts[row.status] += 1
        return dict(counts)


# ─── Inventory index ─────────────────────────────────────────────────────────
class InventoryIndex:
    """Three-way index over the stock dump.

    Each row is registered under a full ``material|batch|serial`` key, a
    batch-level key and a material-only key, so all three match tiers are simple
    lookups. Rows are stored once and referenced by position, which keeps
    identity de-duplication trivial.
    """

    def __init__(self, items: Iterable[InventoryItem], smart_zero: bool = True):
        self.items: list[InventoryItem] = []
        self._index: dict[str, list[int]] = defaultdict(list)
        self.total_qty = 0.0
        for item in items:
            self.add(item, smart_zero=smart_zero)

    def add(self, item: InventoryItem, smart_zero: bool = True) -> None:
        position = len(self.items)
        item.row_index = position
        self.items.append(item)
        self.total_qty += item.qty

        materials = [clean(item.material)]
        if smart_zero:
            stripped = materials[0].lstrip("0")
            if stripped and stripped != materials[0]:
                materials.append(stripped)

        batch, serial = clean(item.batch), clean(item.serial)
        for material in materials:
            self._index[f"{material}|{batch}|{serial}"].append(position)
            self._index[f"{material}|{batch}|"].append(position)
            self._index[f"{material}||"].append(position)

    def candidates(self, key: str) -> list[InventoryItem]:
        """Rows registered under ``key``, de-duplicated by identity."""
        seen: set[int] = set()
        out: list[InventoryItem] = []
        for position in self._index.get(key, ()):
            if position not in seen:
                seen.add(position)
                out.append(self.items[position])
        return out

    def has_material(self, material: str) -> bool:
        return bool(self._index.get(f"{clean(material)}||"))

    def reset_consumption(self) -> None:
        for item in self.items:
            item.used = 0.0

    def batch_level_totals(self) -> dict[str, float]:
        """Total SAP quantity per ``material|batch|`` key.

        Only genuine batch keys are included. The legacy implementation selected
        keys with ``endswith('|')``, which also caught the material-only
        ``MAT||`` keys and inflated the totals; that made the over-scan check
        under-report. Excluding them here is a deliberate fix.
        """
        totals: dict[str, float] = {}
        for key, positions in self._index.items():
            if not key.endswith("|") or key.endswith("||"):
                continue
            seen: set[int] = set()
            total = 0.0
            for position in positions:
                if position not in seen:
                    seen.add(position)
                    total += self.items[position].qty
            totals[key] = total
        return totals

    def duplicate_serials(self) -> list[dict[str, object]]:
        """SAP-side rows sharing one material/batch/serial triple.

        Genuine duplicates in the source data skew matching, so they are surfaced
        as a warning rather than silently consumed.
        """
        duplicates: list[dict[str, object]] = []
        for key, positions in self._index.items():
            if "||" in key or key.endswith("|"):
                continue
            unique = set(positions)
            if len(unique) <= 1:
                continue
            material, batch, serial = key.split("|", 2)
            if not serial:
                continue
            duplicates.append({"material": material, "batch": batch, "serial": serial, "count": len(unique)})
        return duplicates


# ─── Candidate selection ─────────────────────────────────────────────────────
def _expiry_value(item: InventoryItem) -> date | None:
    return parse_loose_date(item.expiry)


def _sort_candidates(candidates: list[InventoryItem], settings: ValidationSettings) -> list[InventoryItem]:
    """Order candidates by plant priority, then nearest expiry, then most stock.

    Expiry differences within a day are treated as equal so that a few hours of
    clock skew in the dump can't reorder otherwise-identical rows.
    """
    priority = [clean(p) for p in settings.plant_priority]

    def plant_rank(item: InventoryItem) -> int:
        if not priority:
            return -1
        try:
            return priority.index(clean(item.plant))
        except ValueError:
            return 999

    if not settings.triple_sort:
        if priority:
            return sorted(candidates, key=plant_rank)
        return list(candidates)

    import functools

    def compare(a: InventoryItem, b: InventoryItem) -> int:
        rank_a, rank_b = plant_rank(a), plant_rank(b)
        if rank_a != rank_b:
            return rank_a - rank_b

        exp_a, exp_b = _expiry_value(a), _expiry_value(b)
        if exp_a and exp_b:
            delta = (exp_a - exp_b).days
            if abs(delta) > 1:
                return delta
        elif exp_a and not exp_b:
            return -1
        elif exp_b and not exp_a:
            return 1

        remaining = b.available - a.available
        return -1 if remaining < 0 else (1 if remaining > 0 else 0)

    return sorted(candidates, key=functools.cmp_to_key(compare))


def _consume(
    row: ResultRow,
    candidates: list[InventoryItem],
    match_type: str,
    settings: ValidationSettings,
    outcome: ValidationOutcome,
) -> bool:
    """Greedily consume stock for a scan, optionally splitting across rows.

    The first row consumed becomes the display reference: its plant, expiry and
    billing details are what appear in the export, even when the quantity was
    satisfied from several rows.
    """
    available = [item for item in candidates if item.available > 0]
    if not available:
        return False
    available = _sort_candidates(available, settings)

    rows = available if settings.multi_row_consume else available[:1]
    remaining = row.scan_qty
    consumed = 0.0
    primary: InventoryItem | None = None

    for item in rows:
        if remaining <= 0:
            break
        take = min(remaining, item.available)
        if take <= 0:
            continue
        item.used += take
        consumed += take
        remaining -= take
        if primary is None:
            primary = item

    if consumed <= 0 or primary is None:
        return False

    row.consumed_qty = consumed
    row.match_type = match_type
    row.system_batch = primary.batch
    row.system_serial = primary.serial
    row.system_plant = primary.plant
    row.system_location = primary.location
    row.system_expiry = primary.expiry
    row.material_description = row.material_description or primary.description
    row.customer_code = row.customer_code or primary.customer_code
    row.customer_name = row.customer_name or primary.customer_name
    row.billing_document = primary.billing_document
    row.billing_date = primary.billing_date

    if match_type in ("Exact", "Concat"):
        outcome.matched_qty += consumed
    elif match_type == "Batch Match":
        outcome.batch_matched_qty += consumed
    elif match_type == "Material Only":
        outcome.material_matched_qty += consumed
    return True


def _record_available_options(row: ResultRow, candidates: Iterable[InventoryItem]) -> None:
    """Keep the usable SAP alternatives visible when a scan is not exact."""
    available = _sort_candidates([item for item in candidates if item.available > 0], ValidationSettings())
    row.available_batches = list(dict.fromkeys(item.batch for item in available if item.batch))
    row.available_serials = list(dict.fromkeys(item.serial for item in available if item.serial))


# ─── Stock condition ─────────────────────────────────────────────────────────
def parse_loose_date(value: object) -> date | None:
    """Parse the assorted date shapes SAP exports use."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text in ("-", "NA", "N/A"):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def classify_stock(
    condition: str,
    expiry: date | None,
    settings: ValidationSettings,
    today: date | None = None,
) -> str:
    """Bucket a scan as damaged, expired, near/short expiry, or good.

    Damage always wins over any expiry state — a broken item is unsellable
    regardless of its date.
    """
    today = today or date.today()
    condition_text = (condition or "").strip().lower()

    if settings.expiry_convention == CONVENTION_MHPL:
        if condition_text == "damage":
            return STOCK_DAMAGE
    elif any(term in condition_text for term in _DAMAGE_TERMS):
        return STOCK_DAMAGE

    if expiry is None:
        return STOCK_NO_EXPIRY
    days_left = (expiry - today).days
    if days_left < 0:
        return STOCK_EXPIRED

    divisor = settings.month_divisor or 30.0
    months_left = days_left / divisor

    if settings.expiry_convention == CONVENTION_MHPL:
        # Near is the tighter window here, short the looser one.
        if months_left <= settings.near_expiry_months:
            return STOCK_NEAR_EXPIRY
        if months_left <= settings.short_expiry_months:
            return STOCK_SHORT_EXPIRY
        return STOCK_GOOD

    # MLSIPL family inverts the two thresholds.
    if months_left < settings.short_expiry_months:
        return STOCK_SHORT_EXPIRY
    if months_left < settings.near_expiry_months:
        return STOCK_NEAR_EXPIRY
    return STOCK_GOOD


# ─── Engine ──────────────────────────────────────────────────────────────────
def reconcile(
    scans: Sequence[ScanInput],
    parsed_scans: Sequence[ParsedBarcode],
    inventory: InventoryIndex,
    settings: ValidationSettings,
    profile: EntityProfile | None = None,
    today: date | None = None,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
) -> ValidationOutcome:
    """Reconcile parsed scans against indexed SAP stock.

    ``scans`` and ``parsed_scans`` are parallel sequences; the caller parses so
    that parsing failures stay visible as their own row rather than vanishing.

    ``material_master`` is optional. It supplies Material Group and PH Level,
    which live on the material master rather than in SAP stock or the barcode
    (#17). Optional and not required because the engine is deliberately
    importable without Django and a caller with no master should still
    reconcile - it just gets those two columns blank.
    """
    if len(scans) != len(parsed_scans):
        raise ValueError("scans and parsed_scans must be the same length")

    # Imported here, not at module level: `extractor` imports this module for
    # classify_stock and ValidationSettings, so a top-level import back would
    # be circular. Bound once per reconcile, not once per row.
    from .extractor import _master_entry as extractor_master_entry

    today = today or date.today()
    inventory.reset_consumption()
    outcome = ValidationOutcome(total_system=inventory.total_qty)
    outcome.duplicate_serials = inventory.duplicate_serials()
    if outcome.duplicate_serials:
        outcome.warnings.append(
            f"Found {len(outcome.duplicate_serials)} duplicate serial(s) in the SAP data. "
            "Matching results may be unreliable for those rows."
        )

    # ── Pass 0: build result rows ──────────────────────────────────────
    rows: list[ResultRow] = []
    for index, (scan, parsed) in enumerate(zip(scans, parsed_scans)):
        row = ResultRow(
            sequence=index,
            raw_scan=scan.raw,
            condition=scan.condition,
            material_description=scan.description,
            customer_code=scan.customer_code,
            customer_name=scan.customer_name,
        )
        direct_material = clean(scan.material)
        if (not parsed.is_valid or not parsed.material) and not direct_material:
            row.status = ERROR
            row.status_priority = STATUS_PRIORITY[ERROR]
            row.detail = parsed.error or "Could not parse barcode"
            row.scan_qty = 0
            rows.append(row)
            outcome.variance_qty += 0
            continue

        row.material = direct_material or clean(parsed.material)
        # Resolved through the extractor's own lookup helper rather than a
        # second copy of the four-spelling / zero-stripped resolution that
        # build_material_master registers its keys under.
        if material_master:
            entry = (
                extractor_master_entry(material_master, row.material)
                or extractor_master_entry(material_master, clean(parsed.scan_code))
            )
            if entry:
                row.material_group = str(entry.get("Material Group") or "")
                row.ph_level = str(entry.get("PH Level") or "")
        row.physical_batch = clean(scan.batch) or clean(parsed.batch)
        # The parser substitutes the literal "Unknown" when a barcode carries no
        # AI (21), so a raw truthiness test treats a serial-less scan as having
        # scanned the serial "Unknown". That produced four downstream faults: the
        # exact-match pass burned a lookup on a key that can never hit, the batch
        # pass then reported "scanned serial differs" when nothing had been
        # scanned, the screen and export printed "Unknown" as if it were data,
        # and the duplicate key collapsed every serial-less scan of one
        # material+batch into one another. is_non_serial() is the helper that
        # already encodes this rule — outbound.py used it; this module did not.
        scanned_serial = clean(scan.serial) or clean(parsed.serial)
        if is_non_serial(scanned_serial):
            scanned_serial = ""
        row.physical_serial = scanned_serial
        row.is_default_serial = not scanned_serial
        row.expiry_date = parsed.expiry_date

        qty = 1.0
        if parsed.qty:
            qty = float(parsed.qty)
        elif settings.use_scan_qty and scan.qty and scan.qty > 0:
            qty = float(scan.qty)
        row.scan_qty = qty
        outcome.total_scanned += qty
        row.stock_status = classify_stock(scan.condition, row.expiry_date, settings, today)
        rows.append(row)

    matchable = [row for row in rows if row.status != ERROR]

    # ── Pass 1: exact, then concatenated serial ────────────────────────
    unmatched: list[ResultRow] = []
    for row in matchable:
        if row.physical_serial:
            key = f"{row.material}|{row.physical_batch}|{row.physical_serial}"
            if _consume(row, inventory.candidates(key), "Exact", settings, outcome):
                continue
        if settings.match_concat and row.physical_serial:
            # Some SAP exports store the serial as batch+serial concatenated.
            matched_concat = False
            for long_serial in concat_serials(
                row.physical_batch, row.physical_serial, profile, settings.concat_delimiter,
            ):
                concat_key = f"{row.material}|{row.physical_batch}|{long_serial}"
                if _consume(row, inventory.candidates(concat_key), "Concat", settings, outcome):
                    matched_concat = True
                    break
            if matched_concat:
                continue
        unmatched.append(row)

    # ── Pass 2: material + batch, serial ignored ───────────────────────
    if settings.match_batch:
        still: list[ResultRow] = []
        for row in unmatched:
            if not row.physical_batch:
                still.append(row)
                continue
            key = f"{row.material}|{row.physical_batch}|"
            candidates = inventory.candidates(key)
            if not _consume(row, candidates, "Batch Match", settings, outcome):
                _record_available_options(row, candidates)
                still.append(row)
            elif row.physical_serial and row.system_serial and not serial_matches(
                row.physical_batch, row.physical_serial, row.system_serial,
                profile, settings.concat_delimiter,
            ):
                # Matched on batch while the serial disagreed. This is the one
                # case where an operator has to decide something by hand — "the
                # batch is right, so which serial should this have been?" — and
                # the answer was being withheld, because options were only
                # recorded when the match *failed*. Recording them on a
                # serial-divergent hit puts the candidate serials on the row
                # that raises the question.
                _record_available_options(row, candidates)
        unmatched = still

    # ── Pass 3: material only ──────────────────────────────────────────
    if settings.match_material:
        still = []
        for row in unmatched:
            key = f"{row.material}||"
            candidates = inventory.candidates(key)
            _record_available_options(row, candidates)
            if not _consume(row, candidates, "Material Only", settings, outcome):
                still.append(row)
        unmatched = still

    # ── Finalise verdicts ──────────────────────────────────────────────
    for row in rows:
        if row.status == ERROR:
            continue
        if row.match_type:
            shortfall = row.scan_qty - row.consumed_qty
            if shortfall > 0:
                outcome.variance_qty += shortfall
                row.status = VARIANCE_PARTIAL
                row.detail = (
                    f"{row.detail} Stock exhausted — matched {row.consumed_qty:g}, "
                    f"variance {shortfall:g}."
                ).strip()
            elif row.match_type in ("Exact", "Concat"):
                row.status = MATCHED
                row.detail = row.detail or "Matched OK"
            elif row.match_type == "Batch Match":
                row.status = PARTIAL
                # Only claim a serial difference when there is a scanned serial
                # AND a system serial to differ from, and they actually differ.
                # Matching on batch means the serial was *ignored*, not that it
                # conflicted — reporting a mismatch against a SAP row that
                # carries no serial at all sent operators looking for a problem
                # that did not exist.
                if not row.physical_serial:
                    row.detail = row.detail or "Batch matched"
                elif not row.system_serial:
                    row.detail = row.detail or "Batch matched; SAP holds no serial for this batch"
                elif not serial_matches(
                    row.physical_batch, row.physical_serial, row.system_serial,
                    profile, settings.concat_delimiter,
                ):
                    row.detail = row.detail or "Batch matched; scanned serial differs"
                else:
                    row.detail = row.detail or "Batch matched"
            else:
                row.status = WARNING
                row.detail = row.detail or "Material matched; available SAP batch selected"
        else:
            outcome.variance_qty += row.scan_qty
            # "Stock exhausted" means the material is in SAP but every unit has
            # already been claimed by an earlier scan. If units remain, the scan
            # failed on the batch or serial instead, which is a plain variance —
            # conflating the two would send the operator hunting for stock that
            # is sitting right there under a different batch.
            candidates = inventory.candidates(f"{row.material}||")
            if candidates and not any(item.available > 0 for item in candidates):
                row.status = STOCK_EXHAUSTED
                row.detail = row.detail or "Material present in SAP but fully consumed"
            else:
                row.status = VARIANCE
                row.detail = row.detail or "Not found in SAP"
                # The spec asks for the *available* batch to be shown whenever the
                # scanned batch is not the required one. Passes 2 and 3 only record
                # options when they actually ran, so a scan that reached neither —
                # for example one whose batch simply is not in SAP — arrived here
                # with nothing to offer. Recording them makes the alternatives
                # visible on precisely the rows an operator has to resolve by hand.
                if candidates and not row.available_batches:
                    _record_available_options(row, candidates)
        row.status_priority = STATUS_PRIORITY.get(row.status, 0)

        if settings.auto_location:
            row.target_location = (
                settings.good_location if row.stock_status == STOCK_GOOD else settings.damage_location
            )

    _apply_post_checks(rows, inventory, settings, outcome, profile)

    outcome.rows = rows
    return outcome


def _apply_post_checks(
    rows: Sequence[ResultRow],
    inventory: InventoryIndex,
    settings: ValidationSettings,
    outcome: ValidationOutcome,
    profile: EntityProfile | None,
) -> None:
    """Layer the advisory checks on top of the match verdicts.

    These only ever escalate a row's status; they never turn a variance into a
    match. The over-scan check additionally moves an over-scanned row's
    quantity from the serial tier down to the batch tier — see the note there.
    It cannot invent or destroy quantity: the total across the tiers, and so
    ``reconciliation_pct``, is unchanged.
    """
    # Duplicate scans — the same material/batch/serial read more than once.
    # Identical batches with different serials are distinct items, not duplicates.
    if settings.check_duplicate_scan:
        frequency = Counter(
            f"{row.material}|{row.physical_batch}|{row.physical_serial}"
            for row in rows
            if row.material and row.physical_serial and row.status != ERROR
        )
        for row in rows:
            key = f"{row.material}|{row.physical_batch}|{row.physical_serial}"
            count = frequency.get(key, 0)
            if count > 1:
                row.add_flag("DUPE_SCAN", f"Duplicate scan ×{count}")
                if row.status in (MATCHED, PARTIAL, WARNING):
                    row.status = DUPE_SCAN
                    row.status_priority = STATUS_PRIORITY[DUPE_SCAN]

    # Over-scan — more physical units than SAP holds for that material/batch.
    if settings.check_over_scan:
        sap_totals = inventory.batch_level_totals()
        scanned_totals: dict[str, float] = defaultdict(float)
        for row in rows:
            if row.material and row.status != ERROR:
                scanned_totals[f"{row.material}|{row.physical_batch}|"] += row.scan_qty
        for row in rows:
            key = f"{row.material}|{row.physical_batch}|"
            sap_qty = sap_totals.get(key)
            scanned = scanned_totals.get(key)
            if sap_qty is not None and scanned is not None and scanned > sap_qty:
                row.add_flag("OVER_SCAN", f"Over scan — scanned {scanned:g} vs SAP {sap_qty:g}")
                if row.status == MATCHED:
                    row.status = OVER_SCAN
                    row.status_priority = STATUS_PRIORITY[OVER_SCAN]
                # A serial-tier match asserts "this physical unit is the one SAP
                # lists". Over-scanning the batch withdraws the evidence for
                # that: more units were scanned than SAP holds, so which of them
                # SAP actually knows about is undecidable — however cleanly one
                # scan's serial happens to concatenate, another scan of the same
                # batch is making an equally good claim on the same SAP row. The
                # batch itself is still confirmed, so the quantity drops one
                # tier instead of becoming a variance.
                #
                # Only the KPI tier moves. `match_type` stays "Exact"/"Concat",
                # so the row still reports which SAP serial it corresponds to,
                # consumption is untouched, and serial-mismatch reporting —
                # which asks the different question "do these two serials
                # correspond" — is unaffected.
                if row.match_type in ("Exact", "Concat") and row.consumed_qty:
                    outcome.matched_qty -= row.consumed_qty
                    outcome.batch_matched_qty += row.consumed_qty

    # Plant mismatch — stock found outside the configured priority plants.
    priority = [clean(p) for p in settings.plant_priority]
    if settings.check_plant_mismatch and priority:
        for row in rows:
            plant = clean(row.system_plant)
            if not plant or plant == "-":
                continue
            if plant not in priority:
                row.add_flag("PLANT_MISMATCH", f"Plant mismatch — {row.system_plant} not in priority list")
                if row.status in (MATCHED, PARTIAL):
                    row.status = PLANT_MISMATCH

    # Incomplete barcode — no batch, or the serial had to be defaulted.
    if settings.check_incomplete:
        for row in rows:
            if row.status == ERROR:
                continue
            missing = []
            if not row.physical_batch or row.physical_batch == "-":
                missing.append("Batch")
            if (profile is None or profile.supports_serial) and row.is_default_serial:
                missing.append("Serial")
            if missing:
                row.add_flag("INCOMPLETE", f"Incomplete barcode — missing {' & '.join(missing)}")
                if row.status in (VARIANCE, WARNING):
                    row.status = INCOMPLETE

    # Expiry mismatch — barcode date disagrees with the SAP shelf-life date.
    if settings.check_expiry_mismatch:
        tolerance = settings.expiry_tolerance_days
        for row in rows:
            if not row.expiry_date or not row.system_expiry or row.system_expiry == "-":
                continue
            system_expiry = parse_loose_date(row.system_expiry)
            if not system_expiry:
                continue
            delta = abs((row.expiry_date - system_expiry).days)
            if delta > tolerance:
                row.add_flag("EXPIRY_MISMATCH", f"Expiry mismatch — {delta} days from SAP")
                if row.status in (MATCHED, PARTIAL):
                    row.status = EXPIRY_MISMATCH


def months_between(start: date, end: date, divisor: float = 30.0) -> float:
    """Approximate month gap, using the divisor the entity is configured with."""
    return math.floor(((end - start).days / divisor) * 100) / 100


__all__ = [
    "BATCH_MISMATCH",
    "CONVENTION_MHPL",
    "CONVENTION_MLSIPL",
    "DUPE_SCAN",
    "ERROR",
    "EXPIRY_MISMATCH",
    "INCOMPLETE",
    "MASTER_MISSING",
    "MATCHED",
    "OVER_SCAN",
    "PARTIAL",
    "PLANT_MISMATCH",
    "STATUS_PRIORITY",
    "STOCK_DAMAGE",
    "STOCK_EXHAUSTED",
    "STOCK_EXPIRED",
    "STOCK_GOOD",
    "STOCK_NEAR_EXPIRY",
    "STOCK_NO_EXPIRY",
    "STOCK_SHORT_EXPIRY",
    "VARIANCE",
    "VARIANCE_PARTIAL",
    "WARNING",
    "InventoryIndex",
    "InventoryItem",
    "ResultRow",
    "ScanInput",
    "ValidationOutcome",
    "ValidationSettings",
    "classify_stock",
    "clean",
    "parse_loose_date",
    "reconcile",
]
