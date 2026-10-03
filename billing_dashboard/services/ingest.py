"""
billing_dashboard/services/ingest.py

End-to-end ingestion for a SAP Billing Dump:

    Excel → header detection/normalization → type conversion → validation
          → business enrichment → upsert (Billing_Doc + Item + Material)

Called by the upload view with a saved :class:`BillingUpload`. Returns the
updated upload with metrics and a human-readable report.
"""
import datetime as dt
import logging
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction
from django.utils import timezone

from ..models import BillingUpload, ExportBillingDetails
from .enrichment import Enricher
from .normalize import map_headers, missing_mandatory

logger = logging.getLogger("billing_dashboard")

# Model-field type maps (introspected once at import).
_DECIMAL_FIELDS, _DATE_FIELDS, _CHAR_MAXLEN = set(), set(), {}
_MODEL_FIELDS = set()
for _f in ExportBillingDetails._meta.get_fields():
    itype = getattr(_f, "get_internal_type", lambda: "")()
    if itype == "DecimalField":
        _DECIMAL_FIELDS.add(_f.name)
    elif itype in ("DateField", "DateTimeField"):
        _DATE_FIELDS.add(_f.name)
    elif itype == "CharField":
        _CHAR_MAXLEN[_f.name] = _f.max_length

# Fields written on a conflicting-row UPDATE — everything except the identity /
# creation-audit columns (those are preserved on existing rows).
# ``upload`` is part of the conflict key: each upload is an independent snapshot,
# so the business key is scoped per-upload (matches the model's UniqueConstraint).
# A fresh upload never conflicts; keeping update_conflicts only makes a re-run of
# the *same* upload idempotent instead of raising.
_UPSERT_KEYS = ["upload", "billing_doc", "item", "material"]
_UPDATE_FIELDS = [
    f.name for f in ExportBillingDetails._meta.concrete_fields
    if f.name not in {"id", "created_at", "created_by", *_UPSERT_KEYS}
]
_CONCRETE_NAMES = {f.name for f in ExportBillingDetails._meta.concrete_fields}
_BATCH = 5000            # bulk upsert batch (fewer round-trips than 1000)
_PROGRESS_EVERY = 2000   # how often to persist processed-row progress

_BOOL_TRUE = {"1", "yes", "y", "true", "t", "x"}


def _to_decimal(value):
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in ("", "-", "X", "x"):
        return Decimal("0")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y",
                "%Y/%m/%d", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_char(value, field):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    maxlen = _CHAR_MAXLEN.get(field)
    return text[:maxlen] if maxlen else text


def _detect_header_row(ws, scan=8):
    """Find the header row (first row that supplies all mandatory fields, else
    the row that maps the most promoted fields). Handles preamble/title rows."""
    best_idx, best_row, best_fields = 0, None, -1
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=scan, values_only=True)):
        field_by_col, _, _ = map_headers(row)
        if not missing_mandatory(field_by_col):
            return i, list(row)
        if len(field_by_col) > best_fields:
            best_idx, best_row, best_fields = i, list(row), len(field_by_col)
    return best_idx, best_row


def process_upload(upload: BillingUpload, username: str = None) -> BillingUpload:
    upload.status = BillingUpload.Status.PROCESSING
    upload.save(update_fields=["status", "updated_at"])
    username = username or upload.created_by

    warnings = []
    try:
        wb = openpyxl.load_workbook(upload.file.path, read_only=True, data_only=True)
    except Exception as exc:  # not a valid .xlsx
        upload.status = BillingUpload.Status.FAILED
        upload.message = f"Could not read the file as Excel (.xlsx/.xlsm): {exc}"
        upload.save(update_fields=["status", "message", "updated_at"])
        return upload

    ws = wb[wb.sheetnames[0]]
    header_offset, header_row = _detect_header_row(ws)
    if not header_row:
        upload.status = BillingUpload.Status.FAILED
        upload.message = "The sheet appears to be empty — no header row found."
        upload.save(update_fields=["status", "message", "updated_at"])
        wb.close()
        return upload

    field_by_col, rawkey_by_col, signature = map_headers(header_row)
    missing = missing_mandatory(field_by_col)
    if missing:
        upload.status = BillingUpload.Status.FAILED
        upload.layout_signature = signature
        upload.detected_columns = len([c for c in header_row if c not in (None, "")])
        upload.message = (
            "Upload rejected — mandatory column(s) not found: "
            + ", ".join(missing)
            + ". Expected headers like 'Billing Doc.', 'Item', 'Material' "
            "(spelling/spacing is flexible). Please check the SAP layout."
        )
        upload.save()
        wb.close()
        return upload

    # Layout transparency — record how each promoted field resolved (by header
    # NAME, not position) and which columns are preserved only in the row JSON.
    column_map = {field_by_col[idx]: str(header_row[idx]) for idx in field_by_col}
    unmapped_headers = [
        str(header_row[idx]) for idx in range(len(header_row))
        if idx not in field_by_col and header_row[idx] not in (None, "")
    ]
    upload.column_map = column_map
    upload.unmapped_headers = unmapped_headers

    enricher = Enricher()
    now = timezone.now()
    total = skipped = unmapped = 0
    # Rough row count for the progress bar (read_only max_row may be absent).
    est_total = max((ws.max_row or 0) - header_offset - 1, 0)

    def build_row(values):
        parsed, raw = {}, {}
        for idx, val in enumerate(values):
            rk = rawkey_by_col.get(idx)
            if rk is not None:
                if isinstance(val, (dt.date, dt.datetime)):
                    raw[rk] = val.isoformat()
                else:
                    raw[rk] = "" if val is None else str(val).strip()
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

    # Build & enrich, de-duplicating within the file by the business key
    # (last occurrence wins — required so the bulk upsert never hits the same
    # conflict row twice in one statement).
    by_key = {}
    for values in ws.iter_rows(min_row=header_offset + 2, values_only=True):
        if values is None or all(v in (None, "") for v in values):
            continue
        total += 1
        if total % _PROGRESS_EVERY == 0:
            pct = min(int(total * 100 / est_total), 99) if est_total else 0
            upload.processed_rows = total
            upload.progress_pct = pct
            upload.save(update_fields=["processed_rows", "progress_pct", "updated_at"])
        row = build_row(values)
        if not (row.get("billing_doc") and row.get("item") and row.get("material")):
            skipped += 1
            if len(warnings) < 25:
                warnings.append(
                    f"Row {total + header_offset + 1}: missing Billing Doc / Item / Material — skipped.")
            continue
        enricher.enrich(row)
        if row.get("mapping_status") != ExportBillingDetails.MappingStatus.OK:
            unmapped += 1
        row["upload"] = upload
        row["created_at"] = now
        row["updated_at"] = now
        row["created_by"] = username
        row["updated_by"] = username
        row["is_deleted"] = False  # revive if a soft-deleted row existed
        by_key[(row["billing_doc"], row["item"], row["material"])] = row

    wb.close()

    # inserted vs updated — compare the file's keys against what already exists
    # (scoped to the billing docs in this file to keep the query bounded).
    docs = {k[0] for k in by_key}
    existing = set(
        ExportBillingDetails.all_objects.filter(billing_doc__in=docs)
        .values_list("billing_doc", "item", "material")
    ) if docs else set()
    inserted = sum(1 for k in by_key if k not in existing)
    updated = len(by_key) - inserted

    # Batched upsert — one INSERT … ON CONFLICT DO UPDATE per batch.
    instances = [
        ExportBillingDetails(**{k: v for k, v in row.items() if k in _CONCRETE_NAMES})
        for row in by_key.values()
    ]
    with transaction.atomic():
        for i in range(0, len(instances), _BATCH):
            ExportBillingDetails.objects.bulk_create(
                instances[i:i + _BATCH],
                update_conflicts=True,
                unique_fields=_UPSERT_KEYS,
                update_fields=_UPDATE_FIELDS,
            )

    upload.status = BillingUpload.Status.COMPLETED
    upload.total_rows = total
    upload.processed_rows = total
    upload.progress_pct = 100
    upload.inserted = inserted
    upload.updated = updated
    upload.skipped = skipped
    upload.unmapped = unmapped
    upload.layout_signature = signature
    upload.detected_columns = len([c for c in header_row if c not in (None, "")])
    msg = [
        f"Processed {total} data row(s) into this snapshot: {inserted} new, "
        f"{updated} recurring (also seen in earlier uploads), {skipped} skipped.",
        f"{unmapped} row(s) have a country not yet in the master (add them to include in region views).",
        f"Detected {upload.detected_columns} columns · mapped {len(column_map)} known fields by header "
        f"name (column order does not matter) · {len(unmapped_headers)} extra column(s) preserved in JSON.",
        f"Layout signature {signature}.",
    ]
    if warnings:
        msg.append("")
        msg.append("First skipped-row notes:")
        msg.extend(warnings)
    upload.message = "\n".join(msg)
    upload.save()
    logger.info("Billing upload %s complete: %s", upload.pk, msg[0])
    return upload
