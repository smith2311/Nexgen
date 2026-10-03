"""Branch transfer picking, inbound scanning, and ZVA05 auto-assign.

Three secondary scanning modes that share the outbound parser but have their own
matching rules:

**Branch transfer** picks stock against a transfer plan using an MB52 stock
extract. Unlike outbound, the plan specifies only a SKU and a quantity — the
batch is chosen by the picker, so matching is on material alone and the engine's
job is to allocate from available stock rather than to verify a batch.

**Inbound scanning** is the destination-plant half of the same move.
Its expected quantity is what the sender actually *picked*, not what the plan
asked for: a line planned for 5 that shipped 3 leaves the receiver expecting 3,
and measuring against the plan would report a permanent phantom shortfall. What
is still outstanding at the end is stock lost in transit.

**ZVA05** assigns scans against open sales-order lines, again on material only.
It exists for the case where deliveries have not been created yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Iterable, Mapping, Protocol, Sequence

from .barcode import (
    UNKNOWN as UNKNOWN_MATERIAL,
    EntityProfile,
    ParsedBarcode,
    PositionalRule,
    parse_barcode,
)
from .validation import clean


class ReasonCode:
    """Outcomes shared by both transfer modes."""

    OK = "OK"
    UNPARSEABLE = "UNPARSEABLE"
    NO_PLAN_DATA = "NO_PLAN_DATA"
    MATERIAL_NOT_IN_PLAN = "MATERIAL_NOT_IN_PLAN"
    LINE_FULFILLED = "LINE_FULFILLED"
    NO_STOCK = "NO_STOCK"
    DUPLICATE_SERIAL = "DUPLICATE_SERIAL"
    OUT_OF_FOCUS = "OUT_OF_FOCUS"
    #: Inbound only: the material is on the plan, but the sending plant never
    #: picked any of it — so nothing should be arriving. Distinct from
    #: LINE_FULFILLED (dispatched and already fully booked in) because the
    #: operator needs to know whether to chase the sender or their own count.
    NOTHING_DISPATCHED = "NOTHING_DISPATCHED"
    #: Inbound only: more units arrived than were dispatched.
    OVER_RECEIVED = "OVER_RECEIVED"


@dataclass
class TransferOutcome:
    accepted: bool
    reason_code: str
    message: str
    parsed: ParsedBarcode | None = None
    line_key: str = ""
    batch: str = ""
    location: str = ""

    @property
    def audio_cue(self) -> str:
        return "match" if self.accepted else ("duplicate" if self.reason_code == ReasonCode.DUPLICATE_SERIAL else "mismatch")

    @property
    def status(self) -> str:
        """Log vocabulary, matching ``outbound.STATUS_*`` and ``ScanLog``.

        Kept as literals rather than importing either, so this module stays free
        of Django and of the outbound engine. Both transfer consoles write into
        the one shared audit table, so they must agree on these four words.
        """
        if self.accepted:
            return "success"
        if self.reason_code == ReasonCode.DUPLICATE_SERIAL:
            return "duplicate"
        if self.reason_code == ReasonCode.NOTHING_DISPATCHED:
            # The scan is fine and the goods are real — it is the sender's
            # paperwork that is wrong. A warning, not an operator error.
            return "warning"
        return "error"


class TransferLineLike(Protocol):
    sku_code: str
    quantity: Decimal | float
    picked_qty: Decimal | float
    #: Advisory only — see ``assign_transfer_batches``. Absent on plain test
    #: doubles, so the engine reads it with ``getattr(..., "")``.
    assigned_batch: str


def _qty(value) -> float:
    return float(value or 0)


# ─── Branch transfer ─────────────────────────────────────────────────────────
@dataclass
class StockLot:
    """One MB52 row: unrestricted stock of a material in a batch at a location."""

    material: str
    batch: str
    qty: float
    location: str = ""
    plant: str = ""
    description: str = ""
    consumed: float = 0.0

    @property
    def available(self) -> float:
        return max(self.qty - self.consumed, 0)


@dataclass
class StockPool:
    """Available stock, indexed by material.

    Lots are consumed nearest-location-first so a picker isn't sent across the
    warehouse while closer stock sits available.
    """

    lots: dict[str, list[StockLot]] = field(default_factory=dict)
    location_priority: Sequence[str] = ()

    @classmethod
    def from_records(
        cls,
        records: Iterable[Mapping[str, object]],
        location_priority: Sequence[str] = (),
    ) -> "StockPool":
        pool = cls(location_priority=[clean(loc) for loc in location_priority])
        for record in records:
            material = clean(record.get("material"))
            qty = float(record.get("qty") or 0)
            if not material or qty <= 0:
                continue
            pool.lots.setdefault(material, []).append(
                StockLot(
                    material=material,
                    batch=clean(record.get("batch")),
                    qty=qty,
                    location=clean(record.get("location")),
                    plant=clean(record.get("plant")),
                    description=str(record.get("description") or ""),
                )
            )
        return pool

    def available_for(self, material: str) -> float:
        return sum(lot.available for lot in self.lots.get(clean(material), ()))

    def take(self, material: str, batch: str = "", units: float = 1.0) -> StockLot | None:
        """Consume ``units`` of a material, preferring an explicit batch."""
        lots = [lot for lot in self.lots.get(clean(material), ()) if lot.available > 0]
        if not lots:
            return None

        def rank(lot: StockLot) -> tuple[int, int]:
            batch_rank = 0 if (batch and lot.batch == clean(batch)) else 1
            if self.location_priority:
                try:
                    location_rank = self.location_priority.index(lot.location)
                except ValueError:
                    location_rank = 999
            else:
                location_rank = 0
            return batch_rank, location_rank

        lots.sort(key=rank)
        chosen = lots[0]
        chosen.consumed += min(units, chosen.available)
        return chosen


def _transfer_line_key(line: TransferLineLike, material: str) -> str:
    return str(getattr(line, "pk", "") or getattr(line, "unique_number", "") or material)


def assign_transfer_batches(
    pool: StockPool,
    lines: Sequence[TransferLineLike],
    *,
    adjusted_qty: Mapping[str, float] | None = None,
) -> dict[str, str]:
    """Pre-assign a system-recommended batch to each open plan line.

    Ported from ``bt-scanner.js:835-902`` (``assignBTBatches``): every
    (batch, location) with qty>0 is ranked by ``pool.location_priority``
    (unlisted locations sort last), then for each line the first ranked option
    whose single location covers the whole requirement is chosen; if none
    does, the highest-priority option's batch is taken and drawn down across
    its locations in priority order.

    A line that already carries ``assigned_batch`` (a manual override, or a
    previous run of this same function) keeps it — but unlike the legacy tool,
    that reserved batch's stock is subtracted from the pool *before* any new
    line is assigned, so two lines can never be recommended the same physical
    units. ``need`` is the line's still-pending quantity (``quantity -
    picked_qty``), not the full plan quantity, since units already picked no
    longer have to come from this stock dump.
    """
    adjusted_qty = adjusted_qty or {}

    # material -> batch -> location -> available qty. A fresh copy so this
    # function never mutates the caller's pool.
    virtual: dict[str, dict[str, dict[str, float]]] = {}
    for material, lots in pool.lots.items():
        by_batch: dict[str, dict[str, float]] = {}
        for lot in lots:
            by_batch.setdefault(lot.batch, {})
            by_batch[lot.batch][lot.location] = by_batch[lot.batch].get(lot.location, 0) + lot.available
        virtual[material] = by_batch

    priority = [clean(loc) for loc in pool.location_priority]

    def location_rank(location: str) -> int:
        try:
            return priority.index(clean(location))
        except ValueError:
            return 99999

    def pending(line: TransferLineLike, key: str) -> float:
        default = max(_qty(line.quantity) - _qty(getattr(line, "picked_qty", 0)), 0)
        return adjusted_qty.get(key, default)

    def consume(material: str, batch: str, need: float) -> None:
        by_batch = virtual.get(material, {}).get(batch)
        if not by_batch:
            return
        remaining = need
        for location in sorted(by_batch, key=location_rank):
            if remaining <= 0:
                break
            take = min(remaining, by_batch[location])
            by_batch[location] -= take
            remaining -= take

    assigned: dict[str, str] = {}

    # Pass 1 — reserve stock already committed to a line that has a batch
    # (manually overridden, or assigned by an earlier run of this function).
    for line in lines:
        material = clean(line.sku_code)
        key = _transfer_line_key(line, material)
        existing_batch = clean(getattr(line, "assigned_batch", "") or "")
        if not existing_batch:
            continue
        assigned[key] = existing_batch
        consume(material, existing_batch, pending(line, key))

    # Pass 2 — assign the rest from whatever stock is left.
    for line in lines:
        material = clean(line.sku_code)
        key = _transfer_line_key(line, material)
        if key in assigned:
            continue
        by_batch = virtual.get(material)
        if not by_batch:
            continue

        options = [
            (batch, location, qty)
            for batch, locations in by_batch.items()
            for location, qty in locations.items()
            if qty > 0
        ]
        if not options:
            continue
        options.sort(key=lambda option: location_rank(option[1]))

        need = pending(line, key)
        chosen_batch = next((batch for batch, _location, qty in options if qty >= need), options[0][0])
        consume(material, chosen_batch, need)
        assigned[key] = chosen_batch

    return assigned


def process_transfer_scan(
    raw: str,
    profile: EntityProfile,
    lines: Sequence[TransferLineLike],
    pool: StockPool | None = None,
    *,
    existing_serials: Iterable[str] = (),
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
) -> TransferOutcome:
    """Allocate one scan against a branch-transfer plan.

    Matching is on material only — the plan does not name a batch, so whichever
    batch the picker physically has is the correct one, provided stock exists.
    A line carrying a recommended ``assigned_batch`` (see
    ``assign_transfer_batches``) still accepts a different batch; it only adds
    a note to the message so the operator knows they deviated from the plan.
    """
    if not lines:
        return TransferOutcome(False, ReasonCode.NO_PLAN_DATA, "No transfer plan loaded.")

    parsed = parse_barcode(raw, profile, material_master, positional_rules)

    # "Ask material" (#4): the picker presents a bare material code rather than
    # a barcode, and the console answers with which batch and location to take.
    #
    # `parse_barcode` expects GS1 or positional input, so a bare code yields the
    # placeholder material "UNKNOWN" and the line lookup below fails — which is
    # what "batch assignment on ask material is broken" was. The "UNKNOWN" in
    # that rejection is the parser's placeholder, not anything the picker typed.
    #
    # Narrow on purpose, and deliberately not a loosening of barcode parsing: it
    # runs only when the parse produced no usable material, and it accepts the
    # input only if it exactly names a line on THIS plan, so an unparseable
    # smudge still cannot become a pick. Everything after this point — priority,
    # stock, focus mode, cartons, the duplicate guard — is the ordinary path.
    #
    # Consistent with the rest of the engine rather than a special case: BT
    # matching is material-only by design (see this function's docstring), so
    # admitting a material by name is the same question asked a different way.
    if not parsed.is_valid or not parsed.material or parsed.material == UNKNOWN_MATERIAL:
        asked = clean(raw)
        if asked and any(clean(line.sku_code) == asked for line in lines):
            parsed = replace(
                parsed, material=asked, is_valid=True, error="", strategy="ask_material",
            )
        else:
            return TransferOutcome(
                False, ReasonCode.UNPARSEABLE, parsed.error or "Barcode could not be read.", parsed
            )

    serials = {s for s in existing_serials if s}
    if parsed.serial:
        identity = f"{clean(parsed.batch)}{clean(parsed.serial)}"
        if identity in serials:
            return TransferOutcome(
                False, ReasonCode.DUPLICATE_SERIAL, f"Serial {parsed.serial} is already picked.", parsed
            )

    material = clean(parsed.material)
    plan_lines = [line for line in lines if clean(line.sku_code) == material]
    if not plan_lines:
        return TransferOutcome(
            False, ReasonCode.MATERIAL_NOT_IN_PLAN, f"{material} is not on this transfer plan.", parsed
        )

    open_lines = [line for line in plan_lines if _qty(line.picked_qty) < _qty(line.quantity)]
    if not open_lines:
        return TransferOutcome(
            False, ReasonCode.LINE_FULFILLED, f"All planned quantity for {material} is already picked.", parsed
        )

    lot = None
    if pool is not None:
        if pool.available_for(material) <= 0:
            return TransferOutcome(
                False, ReasonCode.NO_STOCK, f"No unrestricted stock available for {material}.", parsed
            )
        lot = pool.take(material, parsed.batch)

    line = open_lines[0]
    message = f"Picked against {material}."

    # What the picker physically has wins over what the pool drew down.
    #
    # `pool.take` prefers the scanned batch but falls back to the
    # highest-priority lot when that batch is not in the dump — so reading the
    # batch off the lot silently rewrote a scanned batch into a different one.
    # The export is a "carton-level pick list — what physically goes on the
    # pallet", so that made the pallet paperwork disagree with the pallet.
    #
    # It also disarmed the deviation note below: `picked_batch` had already
    # been replaced by the recommendation's own lot, so a picker who took a
    # different batch was never told.
    #
    # The lot still supplies the LOCATION — that is the pool's answer to
    # "where do I fetch it from", which the barcode cannot know. An ask-material
    # pick carries no batch of its own, so it falls through to the lot as
    # before, which is the whole point of asking.
    # `UNKNOWN` is the parser's placeholder for "no batch in this barcode", not
    # a batch — an ask-material pick and a barcode with no AI (10) both carry
    # it, and treating it as scanned would stamp the literal "UNKNOWN" onto the
    # pick list in place of the lot the pool actually chose.
    scanned_batch = "" if clean(parsed.batch) == UNKNOWN_MATERIAL else parsed.batch
    picked_batch = clean(scanned_batch) or clean(lot.batch if lot else "")
    recommended = clean(getattr(line, "assigned_batch", "") or "")
    if recommended and picked_batch and picked_batch != recommended:
        message += f" Note: recommended batch was {recommended}."

    return TransferOutcome(
        accepted=True,
        reason_code=ReasonCode.OK,
        message=message,
        parsed=parsed,
        line_key=_transfer_line_key(line, material),
        batch=(scanned_batch or (lot.batch if lot else "")),
        location=(lot.location if lot else ""),
    )


# ─── ZVA05 auto-assign ───────────────────────────────────────────────────────
@dataclass
class OrderLine:
    """One open sales-order line from a ZVA05 extract."""

    material: str
    required_qty: float
    customer: str = ""
    sales_document: str = ""
    item_no: str = ""
    scanned_qty: float = 0.0
    raw_data: dict = field(default_factory=dict)

    @property
    def focus_key(self) -> str:
        """Groups lines the way the operator focuses them: per customer order."""
        return f"{self.customer} - {self.sales_document}".strip(" -")

    @property
    def pending_qty(self) -> float:
        return max(self.required_qty - self.scanned_qty, 0)


@dataclass
class Zva05State:
    lines: list[OrderLine] = field(default_factory=list)
    focus_key: str = ""
    #: material|batch|serial identities already recorded.
    seen: set[str] = field(default_factory=set)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> "Zva05State":
        state = cls()
        for record in records:
            state.lines.append(
                OrderLine(
                    material=clean(record.get("material")),
                    required_qty=float(record.get("required_qty") or 0),
                    customer=str(record.get("customer") or ""),
                    sales_document=str(record.get("sales_document") or ""),
                    item_no=str(record.get("item_no") or ""),
                    raw_data=dict(record.get("raw_data") or {}),
                )
            )
        return state

    def focus_groups(self) -> list[str]:
        seen: list[str] = []
        for line in self.lines:
            if line.focus_key not in seen:
                seen.append(line.focus_key)
        return seen


def process_zva05_scan(
    raw: str,
    profile: EntityProfile,
    state: Zva05State,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
) -> TransferOutcome:
    """Assign one scan to an open order line, matching on material only."""
    if not state.lines:
        return TransferOutcome(False, ReasonCode.NO_PLAN_DATA, "No ZVA05 data loaded.")

    parsed = parse_barcode(raw, profile, material_master, positional_rules)
    if not parsed.is_valid or not parsed.material:
        return TransferOutcome(
            False, ReasonCode.UNPARSEABLE, parsed.error or "Barcode could not be read.", parsed
        )

    material = clean(parsed.material)
    if parsed.serial:
        identity = f"{material}|{clean(parsed.batch)}|{clean(parsed.serial)}"
        if identity in state.seen:
            return TransferOutcome(
                False, ReasonCode.DUPLICATE_SERIAL, f"Serial {parsed.serial} is already assigned.", parsed
            )

    candidates = [
        line
        for line in state.lines
        if line.material == material
        and line.pending_qty > 0
        and (not state.focus_key or line.focus_key == state.focus_key)
    ]
    if not candidates:
        in_scope = any(line.material == material for line in state.lines)
        if in_scope and state.focus_key:
            message = f"{material} is not open on the focused order."
            code = ReasonCode.OUT_OF_FOCUS
        elif in_scope:
            message = f"All ordered quantity for {material} is already assigned."
            code = ReasonCode.LINE_FULFILLED
        else:
            message = f"{material} is not in the ZVA05 extract."
            code = ReasonCode.MATERIAL_NOT_IN_PLAN
        return TransferOutcome(False, code, message, parsed)

    line = candidates[0]
    line.scanned_qty += 1
    if parsed.serial:
        state.seen.add(f"{material}|{clean(parsed.batch)}|{clean(parsed.serial)}")

    return TransferOutcome(
        accepted=True,
        reason_code=ReasonCode.OK,
        message=f"Assigned to {line.focus_key or material}.",
        parsed=parsed,
        line_key=f"{line.sales_document}/{line.item_no}",
        batch=parsed.batch,
    )


# ─── Inbound scanning (branch transfer, destination side) ────────────────────
@dataclass
class InboundLine:
    """One plan line as the destination plant sees it."""

    material: str
    #: What the sending plant actually picked — the receiver's expected quantity.
    dispatched_qty: float
    received_qty: float = 0.0
    receiving_node: str = ""
    unique_number: str = ""
    line_id: object = None

    @property
    def focus_key(self) -> str:
        """Receiving groups by destination plant, not by customer."""
        return self.receiving_node

    @property
    def pending_qty(self) -> float:
        return max(self.dispatched_qty - self.received_qty, 0)


@dataclass
class InboundState:
    lines: list[InboundLine] = field(default_factory=list)
    focus_key: str = ""
    #: material|batch|serial identities already booked in.
    seen: set[str] = field(default_factory=set)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> "InboundState":
        state = cls()
        for record in records:
            state.lines.append(
                InboundLine(
                    material=clean(record.get("material")),
                    dispatched_qty=_qty(record.get("dispatched_qty")),
                    received_qty=_qty(record.get("received_qty")),
                    receiving_node=str(record.get("receiving_node") or ""),
                    unique_number=str(record.get("unique_number") or ""),
                    line_id=record.get("line_id"),
                )
            )
        return state

    def focus_groups(self) -> list[str]:
        seen: list[str] = []
        for line in self.lines:
            if line.focus_key not in seen:
                seen.append(line.focus_key)
        return seen


def process_inbound_scan(
    raw: str,
    profile: EntityProfile,
    state: InboundState,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
) -> TransferOutcome:
    """Book one arriving unit in against what the sending plant dispatched.

    Material-only matching, like the picking side — the plan never dictated a
    batch, so whatever batch physically turns up is the right one.
    """
    if not state.lines:
        return TransferOutcome(False, ReasonCode.NO_PLAN_DATA, "No dispatched plan loaded.")

    parsed = parse_barcode(raw, profile, material_master, positional_rules)
    if not parsed.is_valid or not parsed.material:
        return TransferOutcome(
            False, ReasonCode.UNPARSEABLE, parsed.error or "Barcode could not be read.", parsed
        )

    material = clean(parsed.material)
    identity = f"{material}|{clean(parsed.batch)}|{clean(parsed.serial)}"
    if parsed.serial and identity in state.seen:
        return TransferOutcome(
            False, ReasonCode.DUPLICATE_SERIAL, f"Serial {parsed.serial} is already received.", parsed
        )

    in_focus = [
        line for line in state.lines
        if not state.focus_key or line.focus_key == state.focus_key
    ]
    same_material = [line for line in in_focus if line.material == material]
    candidates = [line for line in same_material if line.pending_qty > 0]

    if not candidates:
        # Ordered most-specific first, so the message names the real problem
        # rather than a generic "not expected".
        if not same_material and any(line.material == material for line in state.lines):
            code = ReasonCode.OUT_OF_FOCUS
            message = f"{material} is not inbound to the focused plant."
        elif not same_material:
            code = ReasonCode.MATERIAL_NOT_IN_PLAN
            message = f"{material} is not on this transfer."
        elif all(line.dispatched_qty <= 0 for line in same_material):
            code = ReasonCode.NOTHING_DISPATCHED
            message = f"{material} is on the plan but the sending plant never dispatched any."
        else:
            code = ReasonCode.OVER_RECEIVED
            message = f"All dispatched units of {material} are already received."
        return TransferOutcome(False, code, message, parsed)

    line = candidates[0]
    line.received_qty += 1
    if parsed.serial:
        state.seen.add(identity)

    return TransferOutcome(
        accepted=True,
        reason_code=ReasonCode.OK,
        message=f"Received into {line.receiving_node or 'destination'}.",
        parsed=parsed,
        line_key=str(line.line_id or line.unique_number or material),
        batch=parsed.batch,
    )


__all__ = [
    "OrderLine",
    "ReasonCode",
    "InboundLine",
    "InboundState",
    "StockLot",
    "StockPool",
    "TransferOutcome",
    "Zva05State",
    "assign_transfer_batches",
    "process_inbound_scan",
    "process_transfer_scan",
    "process_zva05_scan",
]
