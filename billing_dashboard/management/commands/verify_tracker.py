"""
Verify the corrected pipeline output against the stakeholder's tracker —
READ-ONLY (no DB writes). Runs the real normalize + enrich code over the raw
dump using masters parsed from the Lookup sheet, then reconciles the refined
row count and grand-total lakhs against the tracker's Data / Dashborad sheets.

    python manage.py verify_tracker \
        --dump   "media/Billing-Dashboard/Results/1/export.XLSX" \
        --lookup "media/Billing-Dashboard/Results/1/Endo export billing tracker 2026-27.xlsx"
"""
from collections import Counter
from decimal import Decimal

import openpyxl
from django.core.management.base import BaseCommand, CommandError

from billing_dashboard.services.enrichment import Enricher
from billing_dashboard.services.ingest import (
    _DATE_FIELDS, _DECIMAL_FIELDS, _detect_header_row, _to_char, _to_date, _to_decimal,
)
from billing_dashboard.services.normalize import map_headers, missing_mandatory, normalize_header

from .seed_billing_masters import parse_lookup_workbook


class Command(BaseCommand):
    help = "Reconcile the refined pipeline output against the tracker (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--dump", required=True, help="Raw SAP dump .xlsx (export.XLSX).")
        parser.add_argument("--lookup", required=True, help="Workbook containing the Lookup sheet.")
        parser.add_argument("--tracker", help="Tracker workbook with Data/Dashborad sheets (default: --lookup).")

    def handle(self, *args, **opts):
        tracker = opts.get("tracker") or opts["lookup"]

        # 1) Build an Enricher from the Lookup masters (no DB).
        party, lob_codes, divisions = parse_lookup_workbook(opts["lookup"])
        enr = Enricher(load=False)
        enr._party = {k: (v["country"], v["region"], v["is_subsidiary"], v["is_domestic"])
                      for k, v in party.items()}
        enr._lob_code = {k.strip().lower(): lob for k, lob in lob_codes.items()}
        enr._division = {normalize_header(c): d for c, d in divisions.items()}
        self.stdout.write(f"Masters: {len(party)} parties, {len(lob_codes)} material→LOB, {len(divisions)} divisions.")

        # 2) Stream the dump through the real normalize + enrich code.
        wb = openpyxl.load_workbook(opts["dump"], read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        header_offset, header_row = _detect_header_row(ws)
        field_by_col, rawkey_by_col, _ = map_headers(header_row)
        missing = missing_mandatory(field_by_col)
        if missing:
            raise CommandError(f"Dump missing mandatory columns: {missing}")

        def build_row(values):
            parsed, raw = {}, {}
            for idx, val in enumerate(values):
                rk = rawkey_by_col.get(idx)
                if rk is not None:
                    raw[rk] = "" if val is None else (
                        val.isoformat() if hasattr(val, "isoformat") else str(val).strip())
                field = field_by_col.get(idx)
                if not field:
                    continue
                if field in _DECIMAL_FIELDS:
                    parsed[field] = _to_decimal(val)
                elif field in _DATE_FIELDS:
                    parsed[field] = _to_date(val)
                else:
                    parsed[field] = _to_char(val, field)
            parsed["raw"] = raw
            return parsed

        total = skipped = f5 = domestic = refined = 0
        refined_lakhs = sub_lakhs = nonsub_lakhs = Decimal("0")
        lob_hits = region_from_soff = 0
        region_ct, division_ct = Counter(), Counter()
        for values in ws.iter_rows(min_row=header_offset + 2, values_only=True):
            if values is None or all(v in (None, "") for v in values):
                continue
            total += 1
            row = build_row(values)
            if not (row.get("billing_doc") and row.get("item") and row.get("material")):
                skipped += 1
                continue
            enr.enrich(row)
            if row["is_excluded"]:
                f5 += 1
            if row["is_domestic"]:
                domestic += 1
            if not row["is_excluded"] and not row["is_domestic"]:
                refined += 1
                v = row.get("value_lakhs") or Decimal("0")
                refined_lakhs += v
                if row["is_subsidiary"]:
                    sub_lakhs += v
                else:
                    nonsub_lakhs += v
                if (row.get("sales_office_description") or "").strip():
                    region_from_soff += 1
                if row.get("lob") and row["lob"] != "Unclassified":
                    lob_hits += 1
                region_ct[row.get("region") or "—"] += 1
                division_ct[row.get("push_division") or "—"] += 1
        wb.close()

        # 3) Expected numbers straight from the tracker's Data sheet.
        exp_rows, exp_lakhs = self._tracker_data(tracker)

        pct = (100 * refined / exp_rows) if exp_rows else 0
        self.stdout.write("\n─── PIPELINE (refined = not F5, not domestic) ───")
        self.stdout.write(f"  dump rows parsed     : {total}")
        self.stdout.write(f"  skipped (no key)     : {skipped}")
        self.stdout.write(f"  excluded F5          : {f5}")
        self.stdout.write(f"  domestic/neighbour   : {domestic}")
        self.stdout.write(f"  REFINED rows         : {refined}")
        self.stdout.write(f"  refined lakhs (total): {refined_lakhs:.2f}")
        self.stdout.write(f"    · subsidiary       : {sub_lakhs:.2f}")
        self.stdout.write(f"    · non-subsidiary   : {nonsub_lakhs:.2f}")
        self.stdout.write(f"  region from Sales_Office_Description: {region_from_soff}/{refined}")
        self.stdout.write(f"  LOB resolved         : {lob_hits}/{refined}")

        self.stdout.write("\n─── TRACKER (Data sheet) ───")
        self.stdout.write(f"  rows                 : {exp_rows}")
        self.stdout.write(f"  VALUE IN LAKH total  : {exp_lakhs:.2f}")

        self.stdout.write("\n─── RECONCILIATION ───")
        self.stdout.write(f"  refined vs tracker rows : {refined} vs {exp_rows}  ({pct:.1f}%, Δ {refined - exp_rows})")
        self.stdout.write(f"  lakhs Δ                 : {refined_lakhs - Decimal(str(exp_lakhs)):.2f}")

        self.stdout.write("\n  Top regions (refined): " + ", ".join(
            f"{r}={n}" for r, n in region_ct.most_common(12)))
        self.stdout.write("  Push divisions: " + ", ".join(
            f"{d}={n}" for d, n in division_ct.most_common()))

    def _tracker_data(self, path, sheet="Data"):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        if sheet not in wb.sheetnames:
            wb.close()
            raise CommandError(f"Tracker sheet '{sheet}' not found. Available: {wb.sheetnames}")
        ws = wb[sheet]
        rows = 0
        total = Decimal("0")
        header = None
        val_idx = None
        for r in ws.iter_rows(values_only=True):
            if header is None:
                header = [str(c).strip() if c is not None else "" for c in r]
                for i, h in enumerate(header):
                    if h.upper() == "VALUE IN LAKH":
                        val_idx = i
                continue
            if all(c in (None, "") for c in r):
                continue
            rows += 1
            if val_idx is not None and val_idx < len(r) and r[val_idx] not in (None, ""):
                try:
                    total += Decimal(str(r[val_idx]))
                except (ValueError, ArithmeticError):
                    pass
        wb.close()
        return rows, total
