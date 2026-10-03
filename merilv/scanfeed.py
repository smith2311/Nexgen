"""Shared builder for the scan-log feed behind the console status filters.

Both the Outbound Scanner and the ZVA05 console show the same thing: every scan
attempt, newest first, filterable by whether the code is currently assigned.
The logic is subtle enough that keeping one copy matters:

``ScanLog`` is append-only, so a code that was accepted and then undone still has
its ``success`` row. Reading that row alone would park a reversed code under
"Assigned" — the exact question the filter exists to answer, answered wrongly.
So each success row is cross-checked against the scans that actually survive
(``OutboundScan`` / ``Zva05Scan``, which undo deletes), and only the survivors
count as assigned.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import ScanLog
from .services.barcode import ParsedBarcode, label_serial_identity

#: Reason code stamped on an accept that a later undo cancelled. There is no such
#: row in the database — it is derived here, because an append-only log cannot go
#: back and amend the entry it superseded.
REVERSED = "REVERSED"


def _serial_from_parsed(parsed: Mapping[str, Any] | None) -> str:
    """The serial a logged attempt was about, reconstructed from its parse.

    ``ScanLog.parsed`` is the dict form of a ``ParsedBarcode``, so the rule for
    turning one into a serial lives in the barcode module and is borrowed here
    rather than restated — a feed row and a live row spelling the same serial
    two different ways is the bug this avoids.
    """
    if not parsed:
        return ""
    return label_serial_identity(
        ParsedBarcode(
            raw=str(parsed.get("raw") or ""),
            batch=str(parsed.get("batch") or ""),
            serial=str(parsed.get("serial") or ""),
        )
    )


def build_scan_feed(
    logs: Iterable[ScanLog],
    live: Mapping[str, str],
    serials: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Turn ``ScanLog`` rows into filterable feed rows plus tab counts.

    ``logs`` should already be ordered newest-first and sliced.
    ``live`` maps the ``raw_scan`` of every still-standing accepted scan to the
    target it landed on (a delivery number, or a sales document) — anything not
    in here was either rejected outright or later reversed.
    ``serials`` maps it to the serial that scan was ASSIGNED, which the log
    cannot work out on its own: an item whose label carries no serial is given
    a running ``BATCH || 001``, and that number exists only on the saved scan.
    Optional, because only the outbound console shows a serial column.
    """
    assigned_serials = serials or {}
    feed: list[dict[str, Any]] = []
    for log in logs:
        assigned = log.status == ScanLog.STATUS_SUCCESS and log.raw_scan in live
        if assigned:
            family, reason, note = "ok", log.reason_code or "OK", ""
        elif log.status == ScanLog.STATUS_SUCCESS:
            family, reason, note = "warn", REVERSED, " — later reversed"
        elif log.status in (ScanLog.STATUS_WARNING, ScanLog.STATUS_DUPLICATE):
            family, reason, note = "warn", log.reason_code or log.status.upper(), ""
        else:
            family, reason, note = "danger", log.reason_code or "REJECTED", ""

        feed.append(
            {
                "raw_scan": log.raw_scan,
                # Shown in the code column: the parsed material is friendlier than
                # the full barcode, but fall back to the raw string so a scan that
                # parsed to nothing is still identifiable.
                "label": (log.parsed or {}).get("material") or log.raw_scan,
                "family": family,
                "reason_code": reason,
                "message": f"{log.message}{note}",
                "created_at": log.created_at,
                "accepted": assigned,
                # Which unit the attempt was about. The assigned serial where
                # there is one, otherwise the serial printed on the label —
                # a refusal has assigned nothing and still has to name the
                # carton the picker is holding. Blank for an item whose label
                # carries no serial and that assigned none.
                "serial": (
                    assigned_serials.get(log.raw_scan, "")
                    or _serial_from_parsed(log.parsed)
                ),
                "target": live.get(log.raw_scan, ""),
                # Which delivery this attempt belongs to, so the console can
                # narrow the log to the delivery being picked. ``target`` only
                # answers that for scans that are still assigned; the stored
                # value covers rejections and reversals too, and is the reason
                # ScanLog carries a delivery of its own. Blank for rows written
                # before the field existed, and for the consoles (ZVA05, branch
                # transfer) whose target is not a delivery at all.
                "delivery_no": log.delivery_no or live.get(log.raw_scan, ""),
            }
        )

    counts = {
        "all": len(feed),
        "accepted": sum(1 for row in feed if row["accepted"]),
        "rejected": sum(1 for row in feed if not row["accepted"]),
    }
    return feed, counts


__all__ = ["REVERSED", "build_scan_feed"]
