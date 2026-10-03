"""
reconciliation/importer.py — upload, stage, preview, commit.
=============================================================================
An import is two steps on purpose, and nothing is written to the database
between them:

1. **Read** — parse the workbook, type every cell, classify every row, and
   write the result to a staging file. The user sees what the file says and
   what each row was classified as, and can walk away.
2. **Confirm** — match the staged rows against what is already stored and
   write them, all inside one transaction. A failure rolls the whole batch
   back and *keeps the staging file*, so "Confirm" can simply be pressed again
   without re-reading the file.

Both steps run in a worker thread and report real counts into
``ReconImportJob``, which the page polls. The counts are genuine progress
through the actual work, not a spinner pretending.

**Matching semantics** (upsert mode) are the interesting part. A staged row
updates an existing record when they share a vendor, a document number and a
document type — and, when the staged row carries a GST date, that date too. A
staged row with no date matches on the triple alone, which is deliberately
broad: it lets a partial re-upload correct a record whose date was never
filled in. When that broad match would hit more than one stored record the
import stops and names the document rather than guessing which one was meant.

A match revives a soft-deleted record. A match against a record someone pinned
by hand (``ManualReason``) leaves it alone entirely. No match inserts.

Nothing here bulk-deletes or bulk-replaces: re-uploading a file only ever
touches the documents actually in it.
"""
import datetime as dt
import json
import logging
import os
import threading
import traceback
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from . import parsing, schema
from .classify import UNCLASSIFIED, classify_values, load_masters
from .models import ReconImportJob, ReconRecord, quantize_decimal

logger = logging.getLogger(__name__)

# How many progress updates one phase writes. Forty is enough for a bar that
# looks alive on a 10,000-row file without writing a row per record.
PROGRESS_STEPS = 40

# Batch sizes chosen against Postgres' 65,535 bound parameters per statement.
# A record carries 211 writable columns, so 150 × 211 ≈ 31,650 — comfortably
# under, with room for the audit columns.
INSERT_BATCH = 150
UPDATE_BATCH = 100

# How many document numbers to probe per lookup query.
LOOKUP_CHUNK = 2000

AUDIT_UPDATE_FIELDS = ("CreatedBy", "SourceFile", "IsDeleted", "DeletedAt",
                       "DeletedBy", "UploadedAt")


class ImportError_(Exception):
    """Something the user needs to fix, phrased for them rather than for a log."""


# ── where files live ─────────────────────────────────────────────────────────

def _media_dir(*parts):
    path = os.path.join(settings.MEDIA_ROOT, "reconciliation", *parts)
    os.makedirs(path, exist_ok=True)
    return path


def upload_dir():
    return _media_dir("uploads")


def staging_dir():
    return _media_dir("staging")


def save_upload(uploaded_file):
    """Write the uploaded file to disk and return its path.

    Timestamped rather than overwritten: when an import turns out to have been
    wrong, the file that caused it is still there to look at.
    """
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    safe = os.path.basename(uploaded_file.name).replace("/", "_").replace("\\", "_")
    path = os.path.join(upload_dir(), f"{stamp}_{uuid.uuid4().hex[:6]}_{safe}")
    with open(path, "wb") as handle:
        for chunk in uploaded_file.chunks():
            handle.write(chunk)
    return path


# ── staging file codec ───────────────────────────────────────────────────────
#
# JSON Lines: one row per line, so a 10,000-row stage streams back in without
# holding the whole file as one parsed object. Dates and decimals go out as
# strings and are re-typed on the way in from schema.py's own type buckets —
# the same single source of truth the model's columns come from.

def _encode(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _decode(column, value):
    if value is None:
        return None
    if column in schema.DATE_COLUMNS:
        return dt.date.fromisoformat(value) if isinstance(value, str) else value
    if column in schema.DECIMAL_COLUMNS or column == "MaxGap":
        return quantize_decimal(Decimal(str(value)))
    if column in schema.INT_COLUMNS:
        return int(value)
    return value


def write_stage(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({k: _encode(v) for k, v in row.items()}) + "\n")


def read_stage(path, limit=None, offset=0):
    """Staged rows, re-typed. ``limit``/``offset`` page the preview without
    reading the whole file."""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < offset:
                continue
            if limit is not None and len(rows) >= limit:
                break
            raw = json.loads(line)
            rows.append({key: _decode(key, value) for key, value in raw.items()})
    return rows


def count_stage(path):
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


# ── job running ──────────────────────────────────────────────────────────────

def _progress(job, done, total, phase):
    ReconImportJob.objects.filter(pk=job.pk).update(
        done=done, total=total, phase=phase, state=ReconImportJob.STATE_RUNNING)


def _finish(job, **fields):
    fields.setdefault("finished_at", timezone.now())
    ReconImportJob.objects.filter(pk=job.pk).update(**fields)


def _fail(job, message):
    _finish(job, state=ReconImportJob.STATE_ERROR, message=message, phase="Failed")


def run_job(job_id):
    """Run one job to completion. Safe to call directly — tests do."""
    job = ReconImportJob.objects.get(pk=job_id)
    try:
        if job.kind == ReconImportJob.KIND_READ:
            _run_read(job)
        elif job.kind == ReconImportJob.KIND_IMPORT:
            _run_import(job)
        elif job.kind == ReconImportJob.KIND_RERUN:
            _run_rerun(job)
        else:
            _fail(job, f"Unknown job kind {job.kind!r}.")
    except ImportError_ as exc:
        _fail(job, str(exc))
    except parsing.ReadError as exc:
        _fail(job, str(exc))
    except Exception as exc:  # noqa: BLE001 — the message is shown to the user
        logger.exception("Reconciliation job %s failed", job_id)
        _fail(job, f"{exc.__class__.__name__}: {exc}"[:2000])
        if settings.DEBUG:
            traceback.print_exc()


def start_job(job):
    """Run ``job`` in the background, or inline when the setting says to.

    ``RECON_SYNC_JOBS`` exists for tests and for management commands, where a
    thread that outlives the command is worse than a slow command.
    """
    if getattr(settings, "RECON_SYNC_JOBS", False):
        run_job(job.pk)
        return job

    def worker():
        try:
            run_job(job.pk)
        finally:
            # A worker thread gets its own connection; Django will not close it.
            close_old_connections()

    thread = threading.Thread(target=worker, name=f"recon-job-{job.pk}", daemon=True)
    thread.start()
    return job


# ── step 1: read and stage ───────────────────────────────────────────────────

def _run_read(job):
    _progress(job, 0, 1, "Opening the file…")

    raw_rows = parsing.read_file(job.source_path, job.filename)
    if not raw_rows:
        raise ImportError_("No data rows were found below the heading row.")

    matched = parsing.count_known_headers(raw_rows[0].keys())
    total = len(raw_rows)
    step = max(1, total // PROGRESS_STEPS)
    masters = load_masters()

    stage_path = os.path.join(staging_dir(), f"stage_{job.pk}_{uuid.uuid4().hex[:8]}.jsonl")

    unclassified = 0
    with open(stage_path, "w", encoding="utf-8") as handle:
        for index, raw in enumerate(raw_rows, start=1):
            values = parsing.map_row(raw)
            values.update(classify_values(values, **masters))
            if values["ReasonRemark"] == UNCLASSIFIED:
                unclassified += 1
            handle.write(json.dumps({k: _encode(v) for k, v in values.items()}) + "\n")
            if index % step == 0 or index == total:
                _progress(job, index, total, "Reading and classifying rows…")

    if unclassified == 0:
        message = "Every row was classified. Review below, then confirm."
    else:
        message = f"{unclassified:,} row(s) matched no rule and need review."

    _finish(
        job,
        state=ReconImportJob.STATE_DONE,
        phase="Ready to import",
        done=total, total=total,
        stage_path=stage_path,
        row_count=total,
        matched_headers=matched,
        unclassified=unclassified,
        message=message,
    )


# ── step 2: match and write ──────────────────────────────────────────────────

def _match_key(values):
    return (
        (values.get("EntityGstin") or "").strip(),
        (values.get("DocumentNumber") or "").strip(),
        (values.get("DocumentType") or "").strip(),
    )


def _build_record(values, created_by, source_file, uploaded_at):
    record = ReconRecord(
        CreatedBy=created_by, SourceFile=source_file, UploadedAt=uploaded_at,
    )
    _apply(record, values)
    return record


def _apply(record, values):
    """Copy every writable column off a staged row onto a record."""
    for column in schema.ALL_COLUMNS:
        value = values.get(column)
        if column in schema.DECIMAL_COLUMNS:
            value = quantize_decimal(value)
        setattr(record, column, value)
    record.Presence = values.get("Presence")
    record.ReasonRemark = values.get("ReasonRemark")
    record.Segment = values.get("Segment")
    record.Gstr1Section = values.get("Gstr1Section")
    record.Health = values.get("Health")
    record.MaxGap = quantize_decimal(values.get("MaxGap")) or Decimal("0")


def _run_import(job):
    if not job.stage_path or not os.path.exists(job.stage_path):
        raise ImportError_("The staged file is no longer available — please upload it again.")

    _progress(job, 0, 1, "Preparing the batch…")
    staged = read_stage(job.stage_path)
    total = len(staged)
    if not total:
        raise ImportError_("There is nothing staged to import.")

    created_by = job.created_by.get_username() if job.created_by else None
    source_file = job.filename
    # One stamp for the whole batch — every row in this upload shares a date,
    # which is the whole point of filtering by it. Keep it UTC-aware (timezone
    # already handles that) so the stored value survives the project switching
    # on USE_TZ later.
    uploaded_at = timezone.now()

    if job.mode == ReconImportJob.MODE_APPEND:
        inserted = _insert_all(job, staged, created_by, source_file, uploaded_at)
        updated = skipped = 0
    else:
        inserted, updated, skipped = _upsert_all(
            job, staged, created_by, source_file, uploaded_at)

    # The staging file has served its purpose; the uploaded original is kept.
    try:
        os.remove(job.stage_path)
    except OSError:
        pass

    parts = [f"Imported {total:,} row(s) — {inserted:,} inserted"]
    if updated:
        parts.append(f"{updated:,} updated")
    if skipped:
        parts.append(f"{skipped:,} left alone (pinned by hand)")
    message = ", ".join(parts) + "."

    _finish(
        job,
        state=ReconImportJob.STATE_DONE,
        phase="Imported",
        done=total, total=total,
        stage_path="",
        inserted=inserted, updated=updated, skipped_manual=skipped,
        message=message,
    )


def _insert_all(job, staged, created_by, source_file, uploaded_at):
    total = len(staged)
    step = max(1, total // PROGRESS_STEPS)
    inserted = 0
    with transaction.atomic():
        batch = []
        for index, values in enumerate(staged, start=1):
            batch.append(_build_record(
                values, created_by, source_file, uploaded_at))
            if len(batch) >= INSERT_BATCH:
                ReconRecord.objects.bulk_create(batch)
                inserted += len(batch)
                batch = []
            if index % step == 0 or index == total:
                _progress(job, index, total, "Saving…")
        if batch:
            ReconRecord.objects.bulk_create(batch)
            inserted += len(batch)
    return inserted


def _upsert_all(job, staged, created_by, source_file, uploaded_at):
    total = len(staged)

    # A document number can legitimately repeat inside one upload — it happens
    # in this data. Keeping only the last occurrence per key stops two staged
    # rows from both claiming the same stored record.
    deduped = {}
    for values in staged:
        deduped[_match_key(values)] = values
    incoming = list(deduped.values())

    _progress(job, 0, total, "Matching existing documents…")
    existing = _load_candidates(deduped.keys())

    to_insert, to_update = [], []
    skipped = 0
    step = max(1, len(incoming) // PROGRESS_STEPS)

    for index, values in enumerate(incoming, start=1):
        key = _match_key(values)
        candidates = existing.get(key, ())
        staged_date = values.get("GstDocumentDate")

        if staged_date is None:
            matches = list(candidates)
        else:
            matches = [row for row in candidates if row.GstDocumentDate == staged_date]

        if not matches:
            to_insert.append(_build_record(
                values, created_by, source_file, uploaded_at))
        elif len(matches) > 1:
            raise ImportError_(
                f"Document {key[1] or '(no number)'} for {key[0] or '(no vendor)'} "
                f"matches {len(matches)} stored records and the uploaded row has no "
                "GST date to tell them apart. Nothing was imported. Add the date to "
                "that row and upload again."
            )
        else:
            record = matches[0]
            if record.ManualReason:
                # Pinned by hand: the whole point is that a re-import does not
                # undo someone's decision.
                skipped += 1
            else:
                _apply(record, values)
                record.SourceFile = source_file
                record.UploadedAt = uploaded_at
                record.IsDeleted = False
                record.DeletedAt = None
                record.DeletedBy = None
                to_update.append(record)

        if index % step == 0 or index == len(incoming):
            _progress(job, index, total, "Matching existing documents…")

    update_fields = list(schema.WRITABLE_COLUMNS) + ["SourceFile", "UploadedAt",
                                                     "IsDeleted", "DeletedAt",
                                                     "DeletedBy"]

    written = 0
    with transaction.atomic():
        for start in range(0, len(to_insert), INSERT_BATCH):
            ReconRecord.objects.bulk_create(to_insert[start:start + INSERT_BATCH])
            written += len(to_insert[start:start + INSERT_BATCH])
            _progress(job, min(written, total), total, "Saving new documents…")

        for start in range(0, len(to_update), UPDATE_BATCH):
            chunk = to_update[start:start + UPDATE_BATCH]
            ReconRecord.objects.bulk_update(chunk, update_fields)
            written += len(chunk)
            _progress(job, min(written, total), total, "Updating matched documents…")

    return len(to_insert), len(to_update), skipped


def _load_candidates(keys):
    """Stored records that could match any of ``keys``, grouped by key.

    Probed by (vendor, document number) so Postgres can use the composite
    document-key index, then narrowed on the full triple in Python. Deleted
    records are included on purpose: a re-upload of a document someone removed
    should bring it back rather than create a second copy beside it.
    """
    entities = {key[0] for key in keys if key[0]}
    numbers = sorted({key[1] for key in keys if key[1]})

    grouped = {}
    for start in range(0, len(numbers), LOOKUP_CHUNK):
        chunk = numbers[start:start + LOOKUP_CHUNK]
        query = ReconRecord.objects.filter(DocumentNumber__in=chunk)
        if entities:
            query = query.filter(EntityGstin__in=entities)
        for record in query:
            key = (
                (record.EntityGstin or "").strip(),
                (record.DocumentNumber or "").strip(),
                (record.DocumentType or "").strip(),
            )
            grouped.setdefault(key, []).append(record)
    return grouped


# ── re-running the rules over what is already stored ─────────────────────────

def _run_rerun(job):
    """Re-classify every stored record against the current master.

    Records pinned by hand keep their remark — ``classify_values`` returns it
    untouched — but their Presence and MaxGap are still refreshed, so a pinned
    row does not quietly hold a stale gap figure.
    """
    masters = load_masters()
    queryset = ReconRecord.objects.all()
    total = queryset.count()
    _progress(job, 0, total, "Re-running the rules…")
    step = max(1, total // PROGRESS_STEPS)

    changed = 0
    done = 0
    pending = []

    for record in queryset.iterator(chunk_size=500):
        before = (record.ReasonRemark, record.Segment, record.Gstr1Section,
                  record.Health, record.Presence, record.MaxGap)
        values = {column: getattr(record, column) for column in schema.ALL_COLUMNS}
        values["ManualReason"] = record.ManualReason
        values["ReasonRemark"] = record.ReasonRemark
        values["Segment"] = record.Segment
        values["Gstr1Section"] = record.Gstr1Section
        values["Health"] = record.Health

        derived = classify_values(values, **masters)
        derived["MaxGap"] = quantize_decimal(derived["MaxGap"]) or Decimal("0")
        for key, value in derived.items():
            setattr(record, key, value)

        after = (record.ReasonRemark, record.Segment, record.Gstr1Section,
                 record.Health, record.Presence, record.MaxGap)
        if before != after:
            pending.append(record)
            changed += 1

        done += 1
        if len(pending) >= UPDATE_BATCH:
            ReconRecord.objects.bulk_update(
                pending, ["Presence", "ReasonRemark", "Segment", "Gstr1Section",
                          "Health", "MaxGap"])
            pending = []
        if done % step == 0 or done == total:
            _progress(job, done, total, "Re-running the rules…")

    if pending:
        ReconRecord.objects.bulk_update(
            pending, ["Presence", "ReasonRemark", "Segment", "Gstr1Section",
                      "Health", "MaxGap"])

    if changed:
        message = (f"Re-ran the rules over {total:,} record(s). "
                   f"{changed:,} classification(s) changed.")
    else:
        message = f"Re-ran the rules over {total:,} record(s). Nothing changed."

    _finish(job, state=ReconImportJob.STATE_DONE, phase="Done",
            done=total, total=total, row_count=total, updated=changed, message=message)
