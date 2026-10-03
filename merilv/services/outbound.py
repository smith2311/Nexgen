"""Outbound delivery scanning engine.

A picker scans cartons; each scan is matched to an open delivery line and
consumes one unit of it. Ported from ``public/src/core/outbound-scanner.js`` and
its three forks.

The PRD (FR5) requires that a rejected scan explains *why* rather than just
failing, so every outcome carries a machine-readable :class:`ReasonCode` as well
as an operator-facing message. The gate order below is significant — it is what
makes "wrong batch" and "over-scan" distinguishable, which the warehouse team
called out as the main weakness of the previous tool.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping, Protocol, Sequence

import logging

from .barcode import (
    EntityProfile,
    ParsedBarcode,
    PositionalRule,
    build_serial_identity,
    expiry_is_past,
    format_serial_component,
    is_non_serial,
    label_serial_identity,
    normalise_scan_input,
    parse_barcode,
)

# ─── Outcome vocabulary ──────────────────────────────────────────────────────
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_WARNING = "warning"
STATUS_DUPLICATE = "duplicate"


class ReasonCode:
    """Stable codes for every way a scan can be resolved or rejected."""

    OK = "OK"
    NO_DELIVERY_DATA = "NO_DELIVERY_DATA"
    UNPARSEABLE = "UNPARSEABLE"
    EXPIRED_ITEM = "EXPIRED_ITEM"
    MISSING_MATERIAL = "MISSING_MATERIAL"
    MISSING_BATCH = "MISSING_BATCH"
    DUPLICATE_SERIAL = "DUPLICATE_SERIAL"
    #: The serial was dispatched on an earlier, finished delivery. Permanent —
    #: a serial identifies one physical unit and cannot ship twice.
    SERIAL_ALREADY_DISPATCHED = "SERIAL_ALREADY_DISPATCHED"
    #: The serial is assigned in another session that is still open. Not
    #: permanent: undoing it there releases the serial.
    SERIAL_IN_ANOTHER_SESSION = "SERIAL_IN_ANOTHER_SESSION"
    DELIVERY_FULL = "DELIVERY_FULL"
    CUSTOMER_FULL = "CUSTOMER_FULL"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    #: Nothing was focused, so the scan had no delivery to be assigned to.
    #: Raised by the view before this module is reached — the console must
    #: choose a delivery before any picking starts, so that a scan can only
    #: ever land where the operator said it should. See the focus gate in
    #: views.outbound_scan / views.outbound_bulk_scan.
    NO_FOCUS = "NO_FOCUS"
    WRONG_BATCH = "WRONG_BATCH"
    OVERSCAN = "OVERSCAN"
    WRONG_MATERIAL = "WRONG_MATERIAL"
    BOX_FULL = "BOX_FULL"
    MASTER_MISSING = "MASTER_MISSING"
    #: The matched line's delivery already has a FinishedDelivery record.
    #: Assigned by the view, not this module — the engine has no idea what a
    #: FinishedDelivery is, deliberately, since it stays testable without a
    #: database. See Row 2.5's fix note in views.py:outbound_scan.
    DELIVERY_ALREADY_FINISHED = "DELIVERY_ALREADY_FINISHED"


#: What the operator-facing error popup calls each outcome.
#:
#: The message already says what happened in a sentence; this is the two or
#: three words at the top of the popup, which is what gets read across a bench
#: and what an operator repeats to a supervisor. Named here rather than in the
#: console so the single-scan path, the bulk path and any future caller cannot
#: drift into calling the same rejection different things.
#:
#: The three the request named are exact: "Not in Delivery", "Duplicate" and
#: "String breakout / improper string". The rest follow the same shape — what
#: is wrong, not which gate caught it.
ERROR_TITLE = {
    ReasonCode.TARGET_MISMATCH: "Not in Delivery",
    ReasonCode.DUPLICATE_SERIAL: "Duplicate",
    # Both of these are one physical unit turning up twice — the difference is
    # only where the other one is, which the message carries.
    ReasonCode.SERIAL_ALREADY_DISPATCHED: "Duplicate",
    ReasonCode.SERIAL_IN_ANOTHER_SESSION: "Duplicate",
    # The label could not be read as a barcode: a broken GS1 stream, a missing
    # AI, a code pasted in half.
    ReasonCode.UNPARSEABLE: "String breakout / improper string",
    ReasonCode.MISSING_MATERIAL: "String breakout / improper string",
    ReasonCode.MISSING_BATCH: "String breakout / improper string",
    ReasonCode.WRONG_MATERIAL: "Wrong Material",
    ReasonCode.WRONG_BATCH: "Wrong Batch",
    ReasonCode.OVERSCAN: "Already Complete",
    ReasonCode.DELIVERY_FULL: "Already Complete",
    ReasonCode.CUSTOMER_FULL: "Already Complete",
    ReasonCode.EXPIRED_ITEM: "Expired",
    ReasonCode.BOX_FULL: "Box Full",
    ReasonCode.DELIVERY_ALREADY_FINISHED: "Delivery Finished",
    ReasonCode.NO_FOCUS: "No Delivery Focused",
    ReasonCode.MASTER_MISSING: "Not in Master",
    ReasonCode.NO_DELIVERY_DATA: "No Delivery Data",
}

#: Fallback for a reason code with no title of its own — the code itself, made
#: readable. Better than a blank heading, and it names the thing to grep for.
DEFAULT_ERROR_TITLE = "Scan Rejected"

#: What an unreadable label is called, wherever it surfaces.
STRING_ERROR_TITLE = "String breakout / improper string"

#: "We read the label and it does not belong here." True only if the label was
#: actually read — see :meth:`ScanOutcome.error_title`.
_READ_BUT_UNWANTED = frozenset(
    {ReasonCode.WRONG_MATERIAL, ReasonCode.WRONG_BATCH, ReasonCode.TARGET_MISMATCH}
)


def label_unreadable(parsed: ParsedBarcode | None) -> bool:
    """True when nothing was actually read off the label.

    ``parse_barcode``'s last-resort strategy treats an unrecognised string as a
    bare identifier and returns ``is_valid=True`` with the material set to
    ``UNKNOWN`` (or, on an entity with no serials, to the garbage string
    itself). So a mangled label never reaches UNPARSEABLE — it arrives at the
    material gate and is refused there, correctly, as an item this shipment
    does not want.

    Correct, and the wrong thing to *say*: the operator is not holding the
    wrong carton, they are holding a label that did not scan. Both the popup's
    heading and its message ask this before choosing their words.
    """
    return parsed is not None and getattr(parsed, "strategy", "") == "raw"


#: What a scan is told when its label did not read. Replaces a wrong-material
#: sentence that would otherwise name ``UNKNOWN`` as the material — which sent
#: operators to check master data for a code that was never on the label.
UNREADABLE_LABEL_MESSAGE = (
    "This barcode did not read — no material or batch could be taken from it. "
    "Check the label for damage and scan it again."
)


def material_name(parsed: ParsedBarcode | None) -> str:
    """How to name a material in a sentence an operator reads.

    The code on the carton and the code we match on are not always the same
    string: ``scan_code`` is what the label printed, ``material`` is what it
    resolves to in the material master. Naming only the resolved one describes
    the carton with a code that is not written anywhere on it, which is how a
    picker ends up certain the scanner is wrong.
    """
    if parsed is None:
        return "this item"
    resolved = (parsed.material or "").strip()
    scanned = (parsed.scan_code or "").strip()
    if scanned and resolved and scanned.upper() != resolved.upper():
        return f"{resolved} (label says {scanned})"
    return resolved or scanned or "this item"


def wrong_material_message(parsed: ParsedBarcode | None) -> str:
    """Why this carton was refused, and what to do with it.

    "Not required anywhere in this shipment" was accurate and unhelpful: it
    states a fact about the data and leaves the operator holding a box. What
    they need is that the item is not part of this session at all — so it is
    not a delivery they can switch to, and the box goes back rather than on the
    trolley.
    """
    if label_unreadable(parsed):
        return UNREADABLE_LABEL_MESSAGE
    return (
        f"Material {material_name(parsed)} is not on any delivery in this session — "
        "this item belongs to a different shipment. Set it aside; no delivery here wants it."
    )


#: Audio cue per outcome (PRD FR7). The UI maps these onto sound files.
AUDIO_CUE = {
    ReasonCode.OK: "match",
    ReasonCode.DUPLICATE_SERIAL: "duplicate",
    ReasonCode.SERIAL_ALREADY_DISPATCHED: "duplicate",
    ReasonCode.SERIAL_IN_ANOTHER_SESSION: "duplicate",
    ReasonCode.WRONG_BATCH: "mismatch",
    ReasonCode.WRONG_MATERIAL: "mismatch",
    ReasonCode.TARGET_MISMATCH: "wrong_delivery",
    # Its own cue, not a rejection's. Nothing is wrong with the item — the
    # operator has not said where it is going — and a scan that sounds like a
    # duplicate or a wrong delivery sends them looking at the barcode instead
    # of at the delivery list.
    ReasonCode.NO_FOCUS: "no_focus",
    ReasonCode.DELIVERY_FULL: "mismatch",
    ReasonCode.CUSTOMER_FULL: "mismatch",
    ReasonCode.OVERSCAN: "error",
    ReasonCode.EXPIRED_ITEM: "error",
    ReasonCode.BOX_FULL: "error",
    ReasonCode.DELIVERY_ALREADY_FINISHED: "error",
}


class DeliveryLineLike(Protocol):
    """The subset of a delivery line the engine needs.

    ``merilv.models.DeliveryLine`` satisfies this, and so does a plain dataclass,
    which keeps the engine unit-testable without a database.
    """

    delivery_no: str
    item_no: str
    material: str
    batch: str
    required_qty: Decimal | float
    scanned_qty: Decimal | float
    bill_to_code: str
    ship_to_code: str


@dataclass
class OutboundSettings:
    enforce_box_capacity: bool = False
    allow_customer_focus: bool = False
    allow_partial_export: bool = False
    block_expired: bool = True
    require_batch: bool = True
    #: Log why each scan matched or did not. Carried on the settings dataclass
    #: rather than read from django.conf here, because this module — like every
    #: engine in services/ — imports no Django so it stays unit-testable against
    #: plain dicts. entities.get_outbound_settings is the bridge that reads the
    #: DEBUG_SCANNER_MATCH setting.
    debug_match: bool = False
    #: Give an item whose label carries no serial a running ``BATCH || 001``
    #: serial instead of leaving the stored serial blank.
    #:
    #: The SAP "Serial Format" upload has a Serial column and it may not be
    #: empty, but a batch-managed entity (MLSIPL) and any MHPL label printed
    #: without AI (21) produced exactly that — the scan was accepted, the
    #: quantity counted, and the exported row shipped with a blank serial.
    #: Numbering restarts per batch and skips anything already assigned, so an
    #: assigned serial stays unique within a session without the engine having
    #: to know what a database is.
    assign_batch_serials: bool = True


@dataclass
class ScanOutcome:
    """What the engine decided about one scan."""

    accepted: bool
    status: str
    reason_code: str
    message: str
    parsed: ParsedBarcode | None = None
    line: DeliveryLineLike | None = None
    serial_identity: str = ""
    serial_only: str = ""
    #: True when this scan completed every line of its delivery.
    delivery_completed: bool = False
    #: Batches that would have been accepted, for WRONG_BATCH.
    expected_batches: list[str] = field(default_factory=list)
    #: The open session holding this serial, for SERIAL_IN_ANOTHER_SESSION.
    #:
    #: The message has always NAMED it ("undo it there before packing it
    #: here"), and the operator had no way to get there: the landing page lists
    #: only the twenty newest sessions, and the one holding the serial is by
    #: definition an older one somebody left open. Carried as an id so the
    #: console can offer the link rather than asking them to find it.
    blocking_session_id: int | None = None

    @property
    def audio_cue(self) -> str:
        return AUDIO_CUE.get(self.reason_code, "error")

    @property
    def error_title(self) -> str:
        """What the error popup calls this outcome. Empty when it is not one.

        One case is not a straight lookup. ``parse_barcode``'s last-resort
        strategy treats an unrecognised string as a bare identifier and returns
        ``is_valid=True`` with the material and batch set to ``UNKNOWN`` — so a
        code that is simply mangled never reaches ``UNPARSEABLE``. It arrives
        here as WRONG_MATERIAL ("material UNKNOWN is not required anywhere"),
        which sends an operator to check the master data when what they are
        holding is a label that did not read.

        So when the parse fell back to ``raw`` — nothing was actually read off
        the label — a does-not-fit rejection is reported as the string problem
        it is. The reason code, the log and the audit trail are untouched; this
        is only what the popup calls it.
        """
        if self.accepted:
            return ""
        if self.reason_code in _READ_BUT_UNWANTED and label_unreadable(self.parsed):
            return STRING_ERROR_TITLE
        return ERROR_TITLE.get(self.reason_code, DEFAULT_ERROR_TITLE)

    @property
    def display_serial(self) -> str:
        """The serial this outcome is ABOUT, accepted or not.

        Three sources, in the order that says the most:

        * ``serial_only`` — what was actually assigned and will be exported.
        * ``serial_identity`` — set on the duplicate gates, where it names the
          serial this scan collided with. Nothing was assigned, but the serial
          is the entire point of the refusal.
        * the label's own serial, for every other rejection. The engine never
          got as far as minting an identity for those, and reconstructing it
          from the parse is what lets a wrong-batch or wrong-material popup
          still say which unit the picker is holding.

        Empty for an item whose label carries no serial and that assigned
        none — there is no serial to name, and inventing one would be worse
        than saying nothing.
        """
        if self.serial_only:
            return self.serial_only
        if self.serial_identity:
            return self.serial_identity
        return label_serial_identity(self.parsed) if self.parsed is not None else ""

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "audio_cue": self.audio_cue,
            # The popup's heading. Carried on every outcome so the console never
            # has to map a reason code to words of its own.
            "error_title": self.error_title,
            "serial_identity": self.serial_identity,
            # The serial actually written to the dispatch record and the SAP
            # sheet. The console shows it back to the operator so "what did
            # that scan become?" is answerable without opening the line.
            "serial_only": self.serial_only,
            # What the console prints next to this outcome — see display_serial.
            # A rejection has no assigned serial and must still be able to name
            # the unit it refused.
            "display_serial": self.display_serial,
            "delivery_completed": self.delivery_completed,
            "expected_batches": self.expected_batches,
            "blocking_session_id": self.blocking_session_id,
            "parsed": self.parsed.as_dict() if self.parsed else None,
            "line_id": getattr(self.line, "pk", None) or getattr(self.line, "id", None),
        }


logger = logging.getLogger(__name__)


def _qty(value: Decimal | float) -> float:
    return float(value or 0)


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


#: Master-data keys that hold a material code under one spelling or another.
#: build_material_master registers each row under all of them.
_MASTER_CODE_KEYS = ("Material Code", "Material", "Code", "Scan Code")


def _material_variants(parsed: ParsedBarcode, material_master: Mapping[str, Mapping[str, object]] | None) -> list[str]:
    """Every code a line's material might legitimately be written as.

    A label carries Meril's own scan code, while the delivery dump carries the
    SAP code, and either may be padded with leading zeros. All plausible forms
    are collected so the comparison stays a cheap set membership test.

    The master lookup matters because the two codes genuinely diverge: 454 of
    MLSIPL's 3,192 materials have a scan code unlike their SAP code
    (ALCRXA109-K vs FGALCRA109), and the delivery dump carries the SAP one.
    ``parse_barcode`` normally resolves that itself, but only when its own
    lookup hits — a padding or spelling difference leaves ``parsed.material``
    holding the label code, and the dump would never match it. This argument
    was accepted and then ignored, so that fallback did not exist.
    """
    variants: list[str] = []
    candidates = [parsed.material, parsed.scan_code]

    # Whatever the master knows this code as, in either direction.
    if material_master:
        for probe in (parsed.material, parsed.scan_code):
            code = _norm(probe)
            if not code:
                continue
            row = material_master.get(code) or material_master.get(code.lstrip("0"))
            if not row:
                continue
            candidates.extend(str(row.get(key, "") or "") for key in _MASTER_CODE_KEYS)

    for candidate in candidates:
        code = _norm(candidate)
        if not code:
            continue
        variants.append(code)
        stripped = code.lstrip("0")
        if stripped and stripped != code:
            variants.append(stripped)
    seen: set[str] = set()
    out: list[str] = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            out.append(variant)
    return out


def _line_matches_material(line: DeliveryLineLike, variants: Sequence[str]) -> bool:
    material = _norm(line.material)
    return material in variants or material.lstrip("0") in variants


#: Ceiling for the running-number search below. A batch with this many units
#: already assigned is far past anything the warehouse picks in one session, and
#: an unbounded loop here would hang the request rather than reject the scan.
MAX_BATCH_SERIAL = 99_999


def _next_batch_serial(batch: str, taken: set[str]) -> int | None:
    """The lowest running number free for ``batch`` within this session.

    ``taken`` is the set of serial identities already assigned, so the search
    is over exactly the values that would collide — an assigned ``B1007`` read
    off a label is skipped just as surely as one this function handed out
    earlier. Returns ``None`` when no number is free, which the caller treats
    as "fall back to the legacy synthetic id" rather than rejecting the scan:
    losing the running number is better than refusing to pack stock.
    """
    prefix = (batch or "").upper()
    for number in range(1, MAX_BATCH_SERIAL + 1):
        if f"{prefix}{format_serial_component(number)}" not in taken:
            return number
    return None


def process_scan(
    raw_scan: str,
    profile: EntityProfile,
    lines: Sequence[DeliveryLineLike],
    settings: OutboundSettings | None = None,
    *,
    existing_serials: Iterable[str] = (),
    dispatched_serials: Mapping[str, str] | None = None,
    reserved_serials: Mapping[str, str] | None = None,
    #: serial -> the id of the open session holding it. Separate from
    #: ``reserved_serials`` so the human label and the machine-readable id
    #: cannot drift into one string that has to be parsed back apart.
    reserved_sessions: Mapping[str, int] | None = None,
    focus_delivery: str = "",
    focus_customer: str = "",
    selected_box: str = "",
    box_capacity: int = 0,
    box_usage: int = 0,
    material_master: Mapping[str, Mapping[str, object]] | None = None,
    positional_rules: Mapping[int, Sequence[PositionalRule]] | None = None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> ScanOutcome:
    """Resolve one scan against the open delivery lines.

    The caller is responsible for persisting the result; this function does not
    mutate ``lines`` so that a rejected scan can never leave partial state
    behind. On acceptance the returned outcome names the line to increment.

    ``existing_serials`` are the serials already assigned in *this* session.
    ``dispatched_serials`` and ``reserved_serials`` extend that beyond it, both
    mapping a serial to a label naming where it went, because a serial
    identifies one physical unit and may only ever be assigned once:

    ``dispatched_serials``
        Shipped on a finished delivery. Permanent — see
        :class:`merilv.models.DispatchedSerial`.
    ``reserved_serials``
        Assigned in another session that is still open, so releasable by
        undoing it there.

    Both are mappings rather than sets so the rejection can say *which*
    delivery already holds the serial; the caller supplies them, keeping this
    module free of any database.
    """
    settings = settings or OutboundSettings()
    serials = {s for s in existing_serials if s}
    dispatched = {s: where for s, where in (dispatched_serials or {}).items() if s}
    reserved = {s: where for s, where in (reserved_serials or {}).items() if s}
    raw_scan = normalise_scan_input(raw_scan)

    # Gate 0 — nothing to scan against.
    if not lines:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.NO_DELIVERY_DATA,
            message="No delivery data loaded. Upload a YVL06 dump before scanning.",
        )

    # The batches these lines are waiting for, as the tie-breaker for an
    # unbracketed string whose batch digits also spell an AI — see
    # parse_barcode's ``known_batches``. The engine already holds the only
    # evidence that settles it, so it would be perverse not to hand it over.
    parsed = parse_barcode(
        raw_scan, profile, material_master, positional_rules,
        known_batches={_norm(line.batch) for line in lines},
    )

    if not parsed.is_valid:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.UNPARSEABLE,
            message=parsed.error or "Barcode could not be read.",
            parsed=parsed,
        )

    # Gate 1 — expired stock must never be packed.
    if settings.block_expired and parsed.expiry and expiry_is_past(parsed.expiry, now):
        expiry = parsed.expiry_date
        shown = expiry.strftime("%d-%m-%Y") if expiry else parsed.expiry
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.EXPIRED_ITEM,
            message=f"EXPIRED ITEM — do not pack. Expiry {shown}.",
            parsed=parsed,
        )

    # Gate 2 — the barcode must identify both a material and a batch.
    if not parsed.material:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.MISSING_MATERIAL,
            message="Barcode carries no material code.",
            parsed=parsed,
        )
    if settings.require_batch and not parsed.batch:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.MISSING_BATCH,
            message="Barcode carries no batch number (GS1 AI 10).",
            parsed=parsed,
        )

    # A generated running number must avoid every serial that is spoken for,
    # not just this session's — otherwise it would mint one that the gate below
    # immediately rejects, and the operator would see a carton refused for a
    # serial they never scanned.
    sequence = (
        _next_batch_serial(parsed.batch, serials | set(dispatched) | set(reserved))
        if (settings.assign_batch_serials and is_non_serial(parsed.serial))
        else None
    )
    serial_identity, serial_only = build_serial_identity(parsed, profile, rng, sequence)

    # Gate 3 — a serialised item may only be packed once.
    if not is_non_serial(parsed.serial) and serial_identity in serials:
        return ScanOutcome(
            accepted=False,
            status=STATUS_DUPLICATE,
            reason_code=ReasonCode.DUPLICATE_SERIAL,
            message=f"Duplicate scan — serial {parsed.serial} is already packed.",
            parsed=parsed,
            serial_identity=serial_identity,
        )

    # Gate 3a — and only once ever, across every session and every upload. A
    # serial names one physical unit: if it has already shipped, this carton is
    # either a relabel or a picking error, and either way it must not be packed
    # against a second delivery. Checked for generated serials too — the
    # sequence above avoids collisions it can see, but a batch whose numbers are
    # exhausted falls back to the legacy synthetic id and must still be caught.
    if serial_identity in dispatched:
        return ScanOutcome(
            accepted=False,
            status=STATUS_DUPLICATE,
            reason_code=ReasonCode.SERIAL_ALREADY_DISPATCHED,
            message=(
                f"Serial {serial_identity} was already dispatched on "
                f"{dispatched[serial_identity]} — it cannot be packed again."
            ),
            parsed=parsed,
            serial_identity=serial_identity,
        )
    if serial_identity in reserved:
        return ScanOutcome(
            accepted=False,
            status=STATUS_DUPLICATE,
            reason_code=ReasonCode.SERIAL_IN_ANOTHER_SESSION,
            message=(
                f"Serial {serial_identity} is already assigned in "
                f"{reserved[serial_identity]}. Undo it there before packing it here."
            ),
            parsed=parsed,
            serial_identity=serial_identity,
            blocking_session_id=(reserved_sessions or {}).get(serial_identity),
        )

    variants = _material_variants(parsed, material_master)

    # Why a scan matched or did not, behind a setting. A rejection here reads
    # as "the scanner is broken" to an operator, and the difference between a
    # wrong batch, an unresolved material code and an exhausted line is
    # invisible from the outside. Off by default; the guard keeps the cost to
    # one attribute lookup per scan when it is.
    if settings.debug_match:
        logger.info(
            "scan match | raw=%r parsed_material=%r scan_code=%r batch=%r serial=%r "
            "variants=%s line_materials=%s",
            raw_scan, parsed.material, parsed.scan_code, parsed.batch, parsed.serial,
            variants, sorted({_norm(line.material) for line in lines})[:20],
        )
    batch = _norm(parsed.batch)

    # Candidate lines: right material, right batch, still short of requirement.
    # Open quantity is the only guard against over-assignment.
    candidates = [
        line
        for line in lines
        if _line_matches_material(line, variants)
        and _norm(line.batch) == batch
        and _qty(line.scanned_qty) < _qty(line.required_qty)
    ]

    focused = _apply_focus(candidates, focus_delivery, focus_customer, settings)
    if focused is not None:
        candidates = focused
        if not candidates:
            return _focus_rejection(lines, variants, batch, focus_delivery, focus_customer, parsed, settings)

    if not candidates:
        return _explain_no_candidate(lines, variants, batch, parsed)

    line = candidates[0]  # source-file order; the dump is already picking order

    # Gate 6 — the selected carton is full.
    if settings.enforce_box_capacity and selected_box and box_capacity > 0 and box_usage >= box_capacity:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.BOX_FULL,
            message=f"Box {selected_box} is full ({box_usage}/{box_capacity}). Start a new box.",
            parsed=parsed,
            line=line,
        )

    completed = _would_complete_delivery(lines, line)
    return ScanOutcome(
        accepted=True,
        status=STATUS_SUCCESS,
        reason_code=ReasonCode.OK,
        message=f"Assigned to delivery {line.delivery_no}.",
        parsed=parsed,
        line=line,
        serial_identity=serial_identity,
        serial_only=serial_only,
        delivery_completed=completed,
    )


def _apply_focus(
    candidates: Sequence[DeliveryLineLike],
    focus_delivery: str,
    focus_customer: str,
    settings: OutboundSettings,
) -> list[DeliveryLineLike] | None:
    """Narrow candidates to the focused delivery or customer, if one is set."""
    if focus_delivery:
        target = _norm(focus_delivery)
        return [line for line in candidates if _norm(line.delivery_no) == target]
    if focus_customer and settings.allow_customer_focus:
        target = _norm(focus_customer)
        return [
            line
            for line in candidates
            if _norm(line.bill_to_code) == target or _norm(line.ship_to_code) == target
        ]
    return None


def _focus_rejection(
    lines: Sequence[DeliveryLineLike],
    variants: Sequence[str],
    batch: str,
    focus_delivery: str,
    focus_customer: str,
    parsed: ParsedBarcode,
    settings: OutboundSettings,
) -> ScanOutcome:
    """Distinguish "focus is already complete" from "wrong item for this focus".

    Both leave zero candidates, but they need very different operator responses,
    so they must not collapse into one message.
    """
    if focus_delivery:
        target = _norm(focus_delivery)
        in_scope = [line for line in lines if _norm(line.delivery_no) == target]
        label, code, full_code = f"delivery {focus_delivery}", ReasonCode.TARGET_MISMATCH, ReasonCode.DELIVERY_FULL
    else:
        target = _norm(focus_customer)
        in_scope = [
            line for line in lines if _norm(line.bill_to_code) == target or _norm(line.ship_to_code) == target
        ]
        label, code, full_code = f"customer {focus_customer}", ReasonCode.TARGET_MISMATCH, ReasonCode.CUSTOMER_FULL

    same_item = [line for line in in_scope if _line_matches_material(line, variants) and _norm(line.batch) == batch]
    if same_item:
        return ScanOutcome(
            accepted=False,
            status=STATUS_WARNING,
            reason_code=full_code,
            message=f"All lines for this item on {label} are already fulfilled.",
            parsed=parsed,
        )

    # Is this exact unit wanted somewhere else in the shipment? Then it is a
    # wrong-delivery scan, whatever else is true — that is the routine outcome
    # of picking two deliveries from one trolley, and of pasting a multi-
    # delivery fixture, and the console groups these into one line rather than
    # one alarming row per code.
    in_scope_ids = {id(line) for line in in_scope}
    elsewhere = [
        line
        for line in lines
        if id(line) not in in_scope_ids
        and _line_matches_material(line, variants)
        and _norm(line.batch) == batch
    ]
    if elsewhere:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=code,
            message=(
                f"This item is not required on {label} — "
                f"it belongs to delivery {elsewhere[0].delivery_no}."
            ),
            parsed=parsed,
        )

    # The material IS wanted here, just not in the batch that was scanned, and
    # that batch is wanted nowhere else in the shipment.
    #
    # Worth separating, and it did not used to be: everything that was not an
    # exact material+batch hit came back as "not required on delivery X". Since
    # nothing may be picked unfocused, this branch is now the *only* way a
    # mis-picked item is explained, and "wrong delivery" sends an operator
    # hunting for another delivery when what they are holding is the right item
    # from the wrong batch — a mistake they fix at the same shelf.
    on_target = [line for line in in_scope if _line_matches_material(line, variants)]
    if on_target:
        open_lines = [line for line in on_target if _qty(line.scanned_qty) < _qty(line.required_qty)]
        if open_lines:
            expected = sorted({line.batch for line in open_lines if line.batch})
            listed = ", ".join(expected) if expected else "an unspecified batch"
            return ScanOutcome(
                accepted=False,
                status=STATUS_ERROR,
                reason_code=ReasonCode.WRONG_BATCH,
                message=f"Wrong batch — {label} needs {listed}, you scanned {parsed.batch}.",
                parsed=parsed,
                expected_batches=expected,
            )
        return ScanOutcome(
            accepted=False,
            status=STATUS_WARNING,
            reason_code=full_code,
            message=f"Every line for material {parsed.material} on {label} is already fulfilled.",
            parsed=parsed,
        )

    # Not wanted here. Whether it is wanted anywhere in the shipment is the
    # difference between "you are on the wrong delivery" and "this item does not
    # belong to this shipment at all", which are different things to go and do.
    if not any(_line_matches_material(line, variants) for line in lines):
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.WRONG_MATERIAL,
            message=wrong_material_message(parsed),
            parsed=parsed,
        )
    return ScanOutcome(
        accepted=False,
        status=STATUS_ERROR,
        reason_code=code,
        message=f"This item is not required on {label}.",
        parsed=parsed,
    )


def _explain_no_candidate(
    lines: Sequence[DeliveryLineLike],
    variants: Sequence[str],
    batch: str,
    parsed: ParsedBarcode,
) -> ScanOutcome:
    """Explain an unmatched scan: wrong batch, over-scan, or wrong material."""
    material_lines = [line for line in lines if _line_matches_material(line, variants)]

    if not material_lines:
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.WRONG_MATERIAL,
            message=wrong_material_message(parsed),
            parsed=parsed,
        )

    open_lines = [line for line in material_lines if _qty(line.scanned_qty) < _qty(line.required_qty)]
    if open_lines:
        expected = sorted({line.batch for line in open_lines if line.batch})
        listed = ", ".join(expected) if expected else "an unspecified batch"
        return ScanOutcome(
            accepted=False,
            status=STATUS_ERROR,
            reason_code=ReasonCode.WRONG_BATCH,
            message=f"Wrong batch — this delivery needs {listed}, you scanned {parsed.batch}.",
            parsed=parsed,
            expected_batches=expected,
        )

    return ScanOutcome(
        accepted=False,
        status=STATUS_WARNING,
        reason_code=ReasonCode.OVERSCAN,
        message=f"Over-scan — every line for material {parsed.material} is already fulfilled.",
        parsed=parsed,
    )


def _would_complete_delivery(lines: Sequence[DeliveryLineLike], line: DeliveryLineLike) -> bool:
    """True when accepting this scan leaves no open lines on the delivery."""
    target = _norm(line.delivery_no)
    for candidate in lines:
        if _norm(candidate.delivery_no) != target:
            continue
        scanned = _qty(candidate.scanned_qty)
        if candidate is line:
            scanned += 1
        if scanned < _qty(candidate.required_qty):
            return False
    return True


# ─── Progress reporting ──────────────────────────────────────────────────────
@dataclass
class DeliveryProgress:
    delivery_no: str
    customer_name: str = ""
    customer_code: str = ""
    required_qty: float = 0.0
    scanned_qty: float = 0.0
    line_count: int = 0
    completed_lines: int = 0
    #: Every distinct plant across this delivery's lines. A set rather than a
    #: single value because one delivery genuinely can span plants — the SAP
    #: stock extracts carry up to seven distinct plants for one customer — and
    #: silently showing the first would be worse than showing both.
    plants: set[str] = field(default_factory=set)

    @property
    def plant(self) -> str:
        """Plant for display: the code when there is one, all of them when not."""
        codes = sorted(code for code in self.plants if code)
        if not codes:
            return ""
        return codes[0] if len(codes) == 1 else " / ".join(codes)

    @property
    def pending_qty(self) -> float:
        return max(self.required_qty - self.scanned_qty, 0)

    @property
    def is_complete(self) -> bool:
        return self.line_count > 0 and self.completed_lines == self.line_count

    @property
    def percent(self) -> float:
        if self.required_qty <= 0:
            return 0.0
        return round(min(self.scanned_qty / self.required_qty, 1) * 100, 1)


def summarise(lines: Iterable[DeliveryLineLike]) -> list[DeliveryProgress]:
    """Per-delivery required/scanned/pending totals for the progress panel."""
    grouped: dict[str, DeliveryProgress] = {}
    for line in lines:
        key = str(line.delivery_no)
        progress = grouped.get(key)
        if progress is None:
            progress = DeliveryProgress(
                delivery_no=key,
                customer_name=getattr(line, "customer_name", "") or getattr(line, "bill_to_name", ""),
                customer_code=getattr(line, "bill_to_code", ""),
            )
            grouped[key] = progress
        plant = str(getattr(line, "plant", "") or "").strip()
        if plant:
            progress.plants.add(plant)
        required, scanned = _qty(line.required_qty), _qty(line.scanned_qty)
        progress.required_qty += required
        progress.scanned_qty += scanned
        progress.line_count += 1
        if scanned >= required:
            progress.completed_lines += 1
    return sorted(grouped.values(), key=lambda p: p.delivery_no)


def shortfall(lines: Iterable[DeliveryLineLike], delivery_no: str) -> list[DeliveryLineLike]:
    """Lines on a delivery that are still short, for the discrepancy report."""
    target = _norm(delivery_no)
    return [
        line
        for line in lines
        if _norm(line.delivery_no) == target and _qty(line.scanned_qty) < _qty(line.required_qty)
    ]


__all__ = [
    "AUDIO_CUE",
    "STATUS_DUPLICATE",
    "STATUS_ERROR",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "DeliveryProgress",
    "OutboundSettings",
    "ReasonCode",
    "ScanOutcome",
    "ERROR_TITLE",
    "STRING_ERROR_TITLE",
    "UNREADABLE_LABEL_MESSAGE",
    "label_unreadable",
    "material_name",
    "process_scan",
    "wrong_material_message",
    "shortfall",
    "summarise",
]
