"""
Management command: fix_truncated_serials
==========================================
Fix serial numbers and batch/lot codes that were wrongly split by the
GS1 parser's _find_ai_boundary function.

Root cause
----------
For barcodes in the no-FNC1 format  01{GTIN}17{EXPIRY}21{SERIAL}
the boundary-finder scanned inside the serial for GS1 Application
Identifier codes and incorrectly stopped parsing when it hit digit
sequences that look like AIs (e.g. "10" = Lot AI, "20" = Variant AI,
"30" = Quantity AI) embedded in the sequence number.

Example:
    raw qr_data : 01189042249069421728051221CS32EFG69105
    stored wrong: serial=CS32EFG69  lot=5
    correct     : serial=CS32EFG69105  lot=EFG69

Detection criteria (all must be true):
  1. qr_data matches pattern  01{14d}17{6d}21{serial}  (no explicit lot AI)
  2. Stored serial_number is a strict *prefix* of the correct serial
  3. The correct serial follows Meril naming  C[SML]{size(2)}{lot(5)}{seq(3)}

Lot extraction
--------------
The lot code is always embedded at positions 4-8 (0-indexed) of the
serial, e.g.  CS32EFG69105  →  lot = EFG69

Usage
-----
  # Show what would change (safe, no writes):
  python manage.py fix_truncated_serials

  # Apply to a single session only:
  python manage.py fix_truncated_serials --session 220526-SO-11200031

  # Apply all fixes:
  python manage.py fix_truncated_serials --apply

  # Apply a single session without the confirmation prompt:
  python manage.py fix_truncated_serials --apply --session 220526-SO-11200031 --yes
"""

import logging
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from product_scanning.models import ScannedProduct

logger = logging.getLogger(__name__)

# GS1 no-FNC1 format:  01{GTIN(14)}17{EXPIRY(6)}21{SERIAL}
# No explicit lot AI (10) — lot is embedded in the serial number.
_QR_PATTERN = re.compile(r"^01(\d{14})17(\d{6})21(.{6,})$")

# Meril serial naming convention: C[SML]{size(2)}{lot(5)}{seq(3)} = 12 chars
# Lot is always at serial[4:9] — verified across all 41 affected records.
_SERIAL_LOT_RE = re.compile(r"^C[A-Z]\d{2}([A-Z0-9]{5})\d{3}$")


def _correct_serial(qr_data: str) -> str | None:
    """Return the correct serial number from qr_data, or None if format not matched."""
    m = _QR_PATTERN.match(qr_data)
    return m.group(3) if m else None


def _lot_from_serial(serial: str) -> str | None:
    """Extract the 5-char lot code embedded in a Meril serial number."""
    m = _SERIAL_LOT_RE.match(serial)
    return m.group(1) if m else None


class Command(BaseCommand):
    help = (
        "Fix serial numbers and batch/lot codes wrongly truncated by the GS1 "
        "AI false-positive boundary bug (DRY RUN by default, pass --apply to save)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Write fixes to the database (default: dry run, no changes made).",
        )
        parser.add_argument(
            "--session",
            metavar="BATCH_ID",
            default=None,
            help=(
                "Limit to a single batch/session key "
                "(e.g. --session 220526-SO-11200031). "
                "Without this, all affected sessions are processed."
            ),
        )
        parser.add_argument(
            "--yes",
            "-y",
            action="store_true",
            default=False,
            help="Skip the confirmation prompt when --apply is used.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        apply = options["apply"]
        session_filter = options.get("session")
        skip_confirm = options["yes"]

        mode_label = "APPLYING FIXES" if apply else "DRY RUN (no changes)"
        self.stdout.write(
            self.style.WARNING(f"\n{'=' * 60}\n  {mode_label}\n{'=' * 60}")
        )
        if session_filter:
            self.stdout.write(f"  Session filter: {session_filter}\n")

        # ----------------------------------------------------------
        # 1. Identify affected records
        # ----------------------------------------------------------
        qs = (
            ScannedProduct.objects
            .filter(qr_data__regex=r"^01\d{14}17\d{6}21.{6,}")
            .select_related("scanning_session__batch")
        )
        if session_filter:
            qs = qs.filter(scanning_session__batch__batch_id=session_filter)

        fixes = []
        skipped_already_ok = 0
        skipped_wrong_format = 0

        for sp in qs.iterator(chunk_size=200):
            correct_ser = _correct_serial(sp.qr_data)
            if correct_ser is None:
                skipped_wrong_format += 1
                continue

            if sp.serial_number == correct_ser:
                skipped_already_ok += 1
                continue

            # Only fix truncation (stored serial must be a strict prefix)
            if not correct_ser.startswith(sp.serial_number):
                skipped_wrong_format += 1
                continue

            correct_lot = _lot_from_serial(correct_ser)

            fixes.append(
                {
                    "sp": sp,
                    "old_serial": sp.serial_number,
                    "new_serial": correct_ser,
                    "old_lot": sp.batch_number,
                    "new_lot": correct_lot,
                    "session_key": (
                        sp.scanning_session.batch.batch_id
                        if sp.scanning_session and sp.scanning_session.batch
                        else "?"
                    ),
                }
            )

        # ----------------------------------------------------------
        # 2. Print report
        # ----------------------------------------------------------
        self.stdout.write(
            f"\n{'ID':>6}  {'Session':<25}  {'Old serial':<20}  {'New serial':<20}  "
            f"{'Old lot':<10}  {'New lot':<10}"
        )
        self.stdout.write("-" * 98)

        for fix in fixes:
            old_lot_str = str(fix["old_lot"]) if fix["old_lot"] is not None else "(none)"
            new_lot_str = str(fix["new_lot"]) if fix["new_lot"] else "(cannot extract)"
            self.stdout.write(
                f"{fix['sp'].id:>6}  {fix['session_key']:<25}  "
                f"{self.style.ERROR(fix['old_serial']):<29}  "   # ANSI adds ~9 chars
                f"{self.style.SUCCESS(fix['new_serial']):<29}  "
                f"{old_lot_str:<10}  {new_lot_str:<10}"
            )

        self.stdout.write(
            f"\nTotal to fix  : {len(fixes)}\n"
            f"Already OK    : {skipped_already_ok}\n"
            f"Format mismatch (skipped): {skipped_wrong_format}\n"
        )

        if not fixes:
            self.stdout.write(self.style.SUCCESS("Nothing to fix — all records are correct."))
            return

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDry run complete. Run with --apply to save {len(fixes)} fixes.\n"
                    f"  python manage.py fix_truncated_serials --apply\n"
                )
            )
            return

        # ----------------------------------------------------------
        # 3. Confirmation prompt
        # ----------------------------------------------------------
        if not skip_confirm:
            answer = input(
                f"\nAbout to update {len(fixes)} records. Continue? (yes/no): "
            ).strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Cancelled — no changes made."))
                return

        # ----------------------------------------------------------
        # 4. Apply in a single transaction (all-or-nothing)
        # ----------------------------------------------------------
        updated = 0
        errors = 0

        with transaction.atomic():
            for fix in fixes:
                sp = fix["sp"]
                try:
                    sp.serial_number = fix["new_serial"]

                    if fix["new_lot"]:
                        sp.batch_number = fix["new_lot"]

                    # Patch gs1_parsed_data JSON to stay consistent
                    if isinstance(sp.gs1_parsed_data, dict):
                        sp.gs1_parsed_data["serial_number"] = fix["new_serial"]
                        if fix["new_lot"]:
                            sp.gs1_parsed_data["batch_number"] = fix["new_lot"]
                        # Remove false fields injected by the old bad parse
                        for false_field in ("variant", "quantity"):
                            sp.gs1_parsed_data.pop(false_field, None)

                    sp.save(
                        update_fields=["serial_number", "batch_number", "gs1_parsed_data"]
                    )
                    updated += 1
                    logger.info(
                        "fix_truncated_serials: id=%d session=%s "
                        "serial %r→%r  lot %r→%r",
                        sp.id,
                        fix["session_key"],
                        fix["old_serial"],
                        fix["new_serial"],
                        fix["old_lot"],
                        fix["new_lot"],
                    )

                except Exception as exc:
                    errors += 1
                    self.stderr.write(
                        self.style.ERROR(f"  ERROR on id={sp.id}: {exc}")
                    )
                    raise CommandError(
                        f"Aborting — error on record {sp.id}: {exc}"
                    ) from exc

        self.stdout.write(
            self.style.SUCCESS(f"\n✅ Fixed {updated} records successfully.")
        )
        if errors:
            self.stdout.write(self.style.ERROR(f"⚠️  {errors} errors (transaction rolled back)."))

        logger.info("fix_truncated_serials: completed, %d records updated", updated)
