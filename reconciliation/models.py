"""
reconciliation/models.py — GST / E-Invoice / E-Way Bill reconciliation storage.
=============================================================================
``ReconRecord`` is one reconciled document. It is backed by 204 columns named
exactly as the source workbook names them, plus seven derived analytical
columns and a short audit tail.

The 204 are **not** typed out here. They are attached from ``schema.py`` in a
loop at import time, for the same reason the C# original generated its DDL from
a column list: a hand-written model, a hand-written mapper and a hand-written
CREATE TABLE are three places that drift apart, and with 204 columns the drift
is guaranteed rather than merely likely. ``makemigrations`` sees the attached
fields exactly as if they had been declared, so migrations behave normally.

Deletes are soft. More than one vendor's data shares this table, and a bad
import is a routine event — ``IsDeleted`` means a wrong upload can be undone
from the Record status filter instead of from a database backup.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from . import schema

# DECIMAL(19,4) in the original. Postgres NUMERIC(19,4) is the same thing:
# fifteen digits ahead of the point, four behind.
DECIMAL_DIGITS = 19
DECIMAL_PLACES = 4
_QUANT = Decimal("0.0001")
_MAX_DECIMAL = Decimal(10) ** (DECIMAL_DIGITS - DECIMAL_PLACES)


def quantize_decimal(value):
    """Coerce ``value`` into something NUMERIC(19,4) will accept, or None.

    The export is free text, so a column that is decimal 99.9% of the time can
    still arrive holding a stray label. Anything that will not fit — a value
    wider than fifteen integer digits, or not a number at all — becomes None
    rather than blowing up a 10,000-row import on one bad cell.
    """
    if value is None:
        return None
    try:
        dec = Decimal(value) if not isinstance(value, Decimal) else value
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not dec.is_finite() or abs(dec) >= _MAX_DECIMAL:
        return None
    return dec.quantize(_QUANT)


def _field_for(column):
    """The Django field for one source column.

    Text columns are ``TextField`` rather than a capped ``CharField``: the
    original used NVARCHAR(400) with a handful of NVARCHAR(MAX) exceptions, and
    getting that list wrong silently truncates a description or a rejection
    reason. On Postgres an unbounded text column costs nothing over a bounded
    one, so there is no reason to keep the trap.
    """
    if column in schema.DATE_COLUMNS:
        return models.DateField(null=True, blank=True)
    if column in schema.INT_COLUMNS:
        return models.IntegerField(null=True, blank=True)
    if column in schema.DECIMAL_COLUMNS:
        return models.DecimalField(
            max_digits=DECIMAL_DIGITS, decimal_places=DECIMAL_PLACES,
            null=True, blank=True,
        )
    return models.TextField(null=True, blank=True)


class ReconRecordQuerySet(models.QuerySet):
    def active(self):
        return self.filter(IsDeleted=False)

    def deleted(self):
        return self.filter(IsDeleted=True)


class ReconRecord(models.Model):
    """One document, as GSTR-1, the IRP and the e-way bill portal each see it.

    The three sources are kept side by side rather than merged — comparing
    ``GSTDocumentValue`` against ``EWBDocumentValue`` is the entire job, so
    collapsing them into one "value" would throw away the question.
    """

    # -- Derived analytical columns (see classify.py) -------------------------

    # Which of the three sources carry this document, as a three-slot mask:
    # "GEW", "GE-", "--W" and so on. Cheap to filter on, and reads at a glance.
    Presence = models.CharField(max_length=3, null=True, blank=True)
    ReasonRemark = models.CharField(max_length=200, null=True, blank=True)
    Segment = models.CharField(max_length=40, null=True, blank=True)
    Gstr1Section = models.CharField(max_length=40, null=True, blank=True)
    Health = models.CharField(max_length=10, null=True, blank=True)
    MaxGap = models.DecimalField(
        max_digits=DECIMAL_DIGITS, decimal_places=DECIMAL_PLACES,
        default=Decimal("0"),
    )
    # Pins the four classification fields above against the rules. Set by hand
    # on the edit screen when a human has decided what a row really is; both
    # the classifier and a re-import leave a pinned row alone.
    ManualReason = models.BooleanField(default=False)

    # -- Audit ----------------------------------------------------------------

    CreatedBy = models.CharField(max_length=150, null=True, blank=True)
    SourceFile = models.CharField(max_length=260, null=True, blank=True)
    IsDeleted = models.BooleanField(default=False)
    DeletedAt = models.DateTimeField(null=True, blank=True)
    DeletedBy = models.CharField(max_length=150, null=True, blank=True)
    UpdatedAt = models.DateTimeField(auto_now=True)
    # When the file that put this row into the database was uploaded. Set on
    # insert and on each upsert so the records grid can be filtered by *the
    # date the file was uploaded* rather than the GST date on the invoice —
    # the business team uploads one file per month and invoice dates spill
    # across months, so filtering by invoice date mixes batches.
    UploadedAt = models.DateTimeField(null=True, blank=True)

    objects = ReconRecordQuerySet.as_manager()

    class Meta:
        db_table = "recon_records"
        verbose_name = "reconciliation record"
        verbose_name_plural = "reconciliation records"
        indexes = [
            # The upsert match key. Every import probes this one.
            models.Index(
                fields=["EntityGstin", "DocumentNumber", "DocumentType"],
                name="recon_doc_key_idx",
            ),
            models.Index(fields=["ReasonRemark"], name="recon_reason_idx"),
            models.Index(fields=["IsDeleted", "EntityGstin"], name="recon_deleted_idx"),
            models.Index(fields=["GstDocumentDate"], name="recon_gst_date_idx"),
            models.Index(fields=["Health"], name="recon_health_idx"),
            models.Index(fields=["UploadedAt"], name="recon_uploaded_idx"),
        ]

    def __str__(self):
        return f"{self.DocumentNumber or '(no number)'} · {self.DocumentType or '?'}"

    # -- Column access by name ------------------------------------------------
    #
    # The import, the export and the edit screen all walk schema.ALL_COLUMNS
    # rather than naming fields, so they need to read and write by string.

    def get(self, column):
        return getattr(self, column, None)

    def set(self, column, value):
        setattr(self, column, value)

    @property
    def display_party(self):
        """The counterparty name for display only.

        Reads whichever source happens to carry it, without merging or
        renaming the stored columns — the export fills a different one of
        these depending on how the document was raised.
        """
        for column in ("EINVBillToTradeName", "EWBBillToTradeName",
                       "GSTBillToTradeName", "GSTBillToLegalName"):
            value = getattr(self, column, None)
            if value and value.strip():
                return value.strip()
        return ""


# The 204 source columns, attached in workbook order.
for _column in schema.ALL_COLUMNS:
    ReconRecord.add_to_class(_column, _field_for(_column))
del _column


class ReconBillingType(models.Model):
    """One row of the billing-type master — see billing_types.py.

    Kept as a table rather than a constant so the master can be corrected from
    the Billing Type Master screen. The classifier reads this table, so an
    edit takes effect the next time the rules are re-run.
    """

    HEALTH_CHOICES = [("ok", "Clear"), ("warn", "Watch"), ("bad", "Exception")]

    code = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=120, blank=True)
    doc_type = models.CharField(
        max_length=20, blank=True,
        help_text="The master's own vocabulary: Invoice, Service, Challan, Jobwork, …",
    )
    gst_applicable = models.BooleanField(default=True)
    einv_applicable = models.BooleanField(default=True)
    ewb_applicable = models.BooleanField(default=True)
    final_remark = models.CharField(
        max_length=200, blank=True,
        help_text="The reason remark to file a fully-compliant row of this type under.",
    )
    pending_remark = models.CharField(
        max_length=200, blank=True,
        help_text="Overrides the 'Dispatched Pending' wording for rows of this "
                  "billing type. Leave blank to use the shipped wording.",
    )
    health = models.CharField(max_length=10, choices=HEALTH_CHOICES, default="ok")
    other_conditions = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive codes fall through to the generic rules, as an "
                  "unrecognised code does.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recon_billing_types"
        ordering = ["code"]
        verbose_name = "billing type"

    def __str__(self):
        return f"{self.code} — {self.description}"

    def as_rule(self):
        from .billing_types import BillingTypeRule

        return BillingTypeRule(
            code=self.code,
            description=self.description,
            doc_type=self.doc_type,
            gst_applicable=self.gst_applicable,
            einv_applicable=self.einv_applicable,
            ewb_applicable=self.ewb_applicable,
            final_remark=self.final_remark,
            health=self.health,
            other_conditions=self.other_conditions,
            pending_remark=self.pending_remark,
        )


class ReconSalesType(models.Model):
    """One row of the sales-type master — see sales_types.py.

    A table rather than a constant so a new ``GstTransactionType`` can be
    handled by whoever notices it, from the Sales Type Master screen, instead
    of waiting for a release. The classifier reads this table, so a row added
    here takes effect the next time the rules are re-run.
    """

    code = models.CharField(
        max_length=20, unique=True,
        help_text="The GstTransactionType exactly as the export spells it — B2B, EXPWOP, OTH.",
    )
    description = models.CharField(max_length=120, blank=True)
    gstr1_section = models.CharField(
        max_length=40,
        help_text="Which GSTR-1 section documents of this type belong to.",
    )
    is_export = models.BooleanField(
        default=False,
        help_text="Export business: this section wins over the credit/debit-note rule.",
    )
    is_b2c = models.BooleanField(
        default=False,
        help_text="Apply the B2CL split — inter-state and above Rs 2,50,000 becomes B2CL.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive codes fall through to the built-in rules, as an "
                  "unrecognised code does.",
    )
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recon_sales_types"
        ordering = ["code"]
        verbose_name = "sales type"

    def __str__(self):
        return f"{self.code} — {self.description}"

    def as_rule(self):
        from .sales_types import SalesTypeRule

        return SalesTypeRule(
            code=self.code,
            description=self.description,
            gstr1_section=self.gstr1_section,
            is_export=self.is_export,
            is_b2c=self.is_b2c,
            notes=self.notes,
        )


class ReconImportJob(models.Model):
    """Progress for one upload, so the browser can show real counts.

    The ASP.NET original streamed ``<script>setProgress(...)</script>``
    fragments into a half-finished response, because a Web Forms page has no
    other way to push. Django has no reason to do that: the work runs in a
    worker thread, writes its counts here, and the page polls this row. Same
    honest numbers, no half-written response to keep alive, and a record of
    what was imported that outlives the request.
    """

    KIND_READ = "read"
    KIND_IMPORT = "import"
    KIND_RERUN = "rerun"
    KIND_CHOICES = [
        (KIND_READ, "Reading file"),
        (KIND_IMPORT, "Importing"),
        (KIND_RERUN, "Re-running rules"),
    ]

    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_DONE = "done"
    STATE_ERROR = "error"
    STATE_CHOICES = [
        (STATE_QUEUED, "Queued"),
        (STATE_RUNNING, "Running"),
        (STATE_DONE, "Done"),
        (STATE_ERROR, "Failed"),
    ]

    MODE_UPSERT = "upsert"
    MODE_APPEND = "append"
    MODE_CHOICES = [
        (MODE_UPSERT, "Update matching document"),
        (MODE_APPEND, "Append"),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_QUEUED)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_UPSERT)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="recon_import_jobs",
    )
    filename = models.CharField(max_length=260, blank=True)
    # Where the parsed rows wait between "Read file" and "Confirm import".
    # A staging file rather than the session: a 10,000-row import is tens of
    # megabytes, which has no business round-tripping through a session store.
    stage_path = models.CharField(max_length=500, blank=True)
    source_path = models.CharField(max_length=500, blank=True)

    phase = models.CharField(max_length=80, blank=True)
    done = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    message = models.TextField(blank=True)

    row_count = models.IntegerField(default=0)
    matched_headers = models.IntegerField(default=0)
    unclassified = models.IntegerField(default=0)
    inserted = models.IntegerField(default=0)
    updated = models.IntegerField(default=0)
    # Matched an existing row that was pinned by hand, so it was left alone.
    skipped_manual = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "recon_import_jobs"
        ordering = ["-created_at"]
        verbose_name = "import job"

    def __str__(self):
        return f"{self.get_kind_display()} · {self.filename or '—'} · {self.state}"

    @property
    def percent(self):
        if self.total <= 0:
            return 2
        return max(2, min(100, round(self.done / self.total * 100)))


class ReconciliationAccess(get_user_model()):
    """Who may use this module, and as what — a proxy over the user table.

    A proxy rather than a model of its own because the thing being managed
    already exists: membership of the ``Recon-Admin`` and ``Recon-User``
    groups. Giving the module its own role table would mean two records of the
    same fact, and the day they disagree is the day someone keeps access they
    were supposed to lose.

    It exists so granting module access is one screen in Django admin — pick a
    person, pick a role — rather than "find the user, scroll to Groups, know
    which of the fourteen group names belongs to reconciliation".
    """

    class Meta:
        proxy = True
        verbose_name = "module access"
        verbose_name_plural = "module access"
        # Proxies inherit the parent's permissions by default, so this adds
        # none of its own: being allowed to change a user is what lets someone
        # change who has access here.
        default_permissions = ()
