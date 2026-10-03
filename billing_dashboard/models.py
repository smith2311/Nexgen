"""
billing_dashboard/models.py

Data model for the Export Billing Dump Upload & Dashboard.

Design notes
------------
* ``ExportBillingDetails`` promotes ~55 of the ~180 SAP columns into typed,
  queryable fields (the ones the dashboards / grid / filters actually use) and
  keeps the FULL normalized row in ``raw`` (a JSONField). That means:
    - every column in the dump is always retained and displayable, and
    - a SAP layout change (the business's #1 pain point) never loses data — new
      / renamed columns simply land in ``raw`` and can be promoted later without
      a re-upload.
* Derived / enrichment fields (region, subsidiary flag, LOB, financial period,
  value-in-lakhs, reversal / pending flags) are computed at ingest time so the
  dashboard engine can aggregate with plain ORM queries.
* Unique business key = ``billing_doc + item + material`` → upsert on re-upload.

All models inherit ``AuditSoftDeleteModel`` (created/updated/deleted audit + the
active-only default manager), matching the rest of the platform.
"""
from decimal import Decimal

from django.db import models

from logistics_performance.models import AuditSoftDeleteModel


# ── UPLOAD BATCH ─────────────────────────────────────────────────────────────
class BillingUpload(AuditSoftDeleteModel):
    """One row per uploaded SAP Billing Dump file — the ingestion audit trail."""

    class Status(models.TextChoices):
        PENDING    = "PENDING",    "Pending"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED  = "COMPLETED",  "Completed"
        FAILED     = "FAILED",     "Failed"

    file          = models.FileField(upload_to="billing_dashboard/%Y/%m/")
    original_name = models.CharField(max_length=255)
    status        = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)

    # Ingestion metrics
    total_rows    = models.IntegerField(default=0)
    inserted      = models.IntegerField(default=0)
    updated       = models.IntegerField(default=0)
    skipped       = models.IntegerField(default=0)
    unmapped      = models.IntegerField(default=0)

    # Live progress (updated while PROCESSING, for the status poll)
    processed_rows = models.IntegerField(default=0)
    progress_pct   = models.IntegerField(default=0)

    # Detected header signature — lets us track SAP layout versions over time.
    layout_signature = models.CharField(max_length=64, blank=True, null=True)
    detected_columns = models.IntegerField(default=0)

    # Human-readable report (validation warnings, skipped-row reasons, errors)
    message       = models.TextField(blank=True, null=True)

    # Layout transparency: which Excel header each promoted field was resolved
    # from (by NAME, not position), and headers kept only in the row JSON.
    column_map       = models.JSONField(default=dict, blank=True)   # {model_field: source_header}
    unmapped_headers = models.JSONField(default=list, blank=True)   # [headers preserved only in raw JSON]

    class Meta:
        db_table = "billing_upload"
        verbose_name = "Billing Upload"
        verbose_name_plural = "Billing Uploads"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_name} ({self.get_status_display()})"


# ── FACT TABLE ───────────────────────────────────────────────────────────────
class ExportBillingDetails(AuditSoftDeleteModel):
    """One row per SAP billing line item (Billing Doc + Item + Material)."""

    class MappingStatus(models.TextChoices):
        OK               = "OK",               "Mapped"
        UNMAPPED_COUNTRY = "UNMAPPED_COUNTRY", "Country not in master"
        UNMAPPED_LOB     = "UNMAPPED_LOB",     "LOB not derived"
        UNMAPPED_BOTH    = "UNMAPPED_BOTH",    "Country & LOB unmapped"

    # Each upload is an independent snapshot: its rows are keyed and kept
    # separately, so every upload retains its own full dashboard even after a
    # later dump is ingested (the business key is unique *within* an upload, not
    # globally — see the Meta constraint below).
    upload = models.ForeignKey(
        BillingUpload, on_delete=models.CASCADE, null=True, blank=True,
        related_name="rows",
    )

    # ── Business key ─────────────────────────────────────────────────────────
    billing_doc = models.CharField(max_length=50, db_index=True)
    item        = models.CharField(max_length=20)
    material    = models.CharField(max_length=60, db_index=True)

    # ── Document classification ──────────────────────────────────────────────
    billing_type       = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    bill_type_descr    = models.CharField(max_length=120, blank=True, null=True)
    billing_category   = models.CharField(max_length=60, blank=True, null=True)
    document_category  = models.CharField(max_length=60, blank=True, null=True)
    doc_status         = models.CharField(max_length=60, blank=True, null=True)
    fi_status          = models.CharField(max_length=60, blank=True, null=True)
    cancelled          = models.CharField(max_length=20, blank=True, null=True)
    returns            = models.CharField(max_length=20, blank=True, null=True)

    # ── Dates ────────────────────────────────────────────────────────────────
    billing_date  = models.DateField(blank=True, null=True, db_index=True)
    so_sto_date   = models.DateField(blank=True, null=True)
    mfg_date      = models.DateField(blank=True, null=True)
    expiry_date   = models.DateField(blank=True, null=True)
    dispatch_date = models.DateField(blank=True, null=True)
    delivery_date = models.DateField(blank=True, null=True)
    fiscal_year   = models.CharField(max_length=10, blank=True, null=True)

    # ── Order / logistics ────────────────────────────────────────────────────
    so_sto      = models.CharField(max_length=40, blank=True, null=True)
    so_sto_item = models.CharField(max_length=20, blank=True, null=True)
    po_number   = models.CharField(max_length=80, blank=True, null=True)
    delivery    = models.CharField(max_length=40, blank=True, null=True)

    # ── Material ─────────────────────────────────────────────────────────────
    material_group    = models.CharField(max_length=60, blank=True, null=True, db_index=True)
    material_type     = models.CharField(max_length=40, blank=True, null=True)
    description       = models.CharField(max_length=255, blank=True, null=True)
    product_hierarchy = models.CharField(max_length=80, blank=True, null=True)
    ph_level01_desc   = models.CharField(max_length=120, blank=True, null=True)
    ph_level02_desc   = models.CharField(max_length=120, blank=True, null=True)
    hsn_sac           = models.CharField(max_length=40, blank=True, null=True)

    # ── Organisation ─────────────────────────────────────────────────────────
    sales_org     = models.CharField(max_length=20, blank=True, null=True)
    division      = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    distr_channel = models.CharField(max_length=20, blank=True, null=True)
    company_code  = models.CharField(max_length=20, blank=True, null=True)
    plant         = models.CharField(max_length=20, blank=True, null=True)
    plant_region  = models.CharField(max_length=60, blank=True, null=True)
    sales_office  = models.CharField(max_length=60, blank=True, null=True)
    sales_office_description = models.CharField(max_length=120, blank=True, null=True)  # .NET Sales_Office_Description → Region source
    sales_group   = models.CharField(max_length=60, blank=True, null=True)

    # ── Customer (sold-to / payer / ship-to) ─────────────────────────────────
    sold_to_party   = models.CharField(max_length=40, blank=True, null=True)
    sold_to_name    = models.CharField(max_length=200, blank=True, null=True, db_index=True)
    sold_to_city    = models.CharField(max_length=80, blank=True, null=True)
    sold_to_region  = models.CharField(max_length=80, blank=True, null=True)
    sold_to_country = models.CharField(max_length=60, blank=True, null=True)
    payer           = models.CharField(max_length=40, blank=True, null=True)
    payer_name      = models.CharField(max_length=200, blank=True, null=True)
    payer_country   = models.CharField(max_length=60, blank=True, null=True)
    payer_region    = models.CharField(max_length=80, blank=True, null=True)
    ship_to_party   = models.CharField(max_length=40, blank=True, null=True)
    ship_to_name    = models.CharField(max_length=200, blank=True, null=True)
    ship_to_country = models.CharField(max_length=60, blank=True, null=True)
    ship_to_region  = models.CharField(max_length=80, blank=True, null=True)

    # ── Values ───────────────────────────────────────────────────────────────
    sales_unit       = models.CharField(max_length=20, blank=True, null=True)
    billed_qty       = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0"))
    doc_currency     = models.CharField(max_length=10, blank=True, null=True)
    invoice_value_lc = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))  # local currency (INR) — primary revenue measure
    invoice_value    = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))  # document currency
    revenue          = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    taxable_amt      = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    tax_amount       = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    net_amt          = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    gross_amt        = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    mrp              = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    exchange_rate    = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))

    # ── GL / speciality / trade ──────────────────────────────────────────────
    revenue_gl      = models.CharField(max_length=40, blank=True, null=True)
    revenue_gl_desc = models.CharField(max_length=120, blank=True, null=True)
    speciality      = models.CharField(max_length=120, blank=True, null=True)
    export_type     = models.CharField(max_length=60, blank=True, null=True)
    incoterms       = models.CharField(max_length=20, blank=True, null=True)

    # ── Derived / enrichment (computed at ingest) ────────────────────────────
    country         = models.CharField(max_length=60, blank=True, null=True, db_index=True)  # resolved country name
    country_code    = models.CharField(max_length=10, blank=True, null=True)
    region          = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    is_subsidiary   = models.BooleanField(default=False, db_index=True)
    subsidiary_name = models.CharField(max_length=120, blank=True, null=True)
    lob             = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    push_division   = models.CharField(max_length=40, blank=True, null=True, db_index=True)  # Lookup Country → "Push Report Division"

    fin_year        = models.CharField(max_length=10, blank=True, null=True, db_index=True)  # e.g. "2026-27"
    fin_month_seq   = models.IntegerField(default=0)   # 1 = April … 12 = March (for ordering)
    fin_month_label = models.CharField(max_length=5, blank=True, null=True)  # "Apr" … "Mar"
    calendar_month  = models.IntegerField(default=0)   # 1-12

    value_lakhs     = models.DecimalField(max_digits=20, decimal_places=4, default=Decimal("0"))
    is_reversal     = models.BooleanField(default=False, db_index=True)   # S1 / negative value (summed IN — not filtered out)
    is_pending      = models.BooleanField(default=False, db_index=True)   # proforma / not posted in FI
    is_excluded     = models.BooleanField(default=False, db_index=True)   # Billing_Type = F5 → excluded from dashboards
    is_domestic     = models.BooleanField(default=False, db_index=True)   # India + SAARC neighbour → excluded from the EXPORT dashboard

    mapping_status  = models.CharField(
        max_length=20, choices=MappingStatus.choices,
        default=MappingStatus.OK, db_index=True,
    )

    # ── Full normalized row (all columns, keyed by normalized header) ─────────
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "export_billing_details"
        verbose_name = "Export Billing Detail"
        verbose_name_plural = "Export Billing Details"
        ordering = ["-billing_date", "billing_doc", "item"]
        constraints = [
            # Per-upload snapshot: the SAP business key is unique *within* one
            # upload (so a file never carries the same line twice), but the same
            # billing_doc+item+material may recur across uploads — each upload
            # keeps its own independent copy and its own dashboard.
            models.UniqueConstraint(
                fields=["upload", "billing_doc", "item", "material"],
                name="uniq_upload_billing_doc_item_material",
            )
        ]
        indexes = [
            models.Index(fields=["is_excluded", "is_domestic", "is_subsidiary", "fin_year"]),
            models.Index(fields=["region", "fin_month_seq"]),
        ]

    def __str__(self):
        return f"{self.billing_doc}/{self.item} · {self.material}"


# ── MASTER: Country → Region + Subsidiary classification ─────────────────────
class CountryRegionMap(AuditSoftDeleteModel):
    """Enrichment master: maps a SAP country (code or name) to a reporting
    region and flags whether that country is a Meril subsidiary. Seeded with the
    examples from the requirement meeting and extendable from the UI/admin.
    New customers/countries not found here are flagged for manual maintenance
    (surfaced in the dashboard Exceptions view)."""

    country_code    = models.CharField(max_length=10, blank=True, null=True, db_index=True)  # ISO-2, e.g. "IT"
    country_name    = models.CharField(max_length=80, db_index=True)
    region          = models.CharField(max_length=80)
    is_subsidiary   = models.BooleanField(default=False)
    subsidiary_name = models.CharField(max_length=120, blank=True, null=True)
    push_division   = models.CharField(max_length=40, blank=True, null=True)  # Lookup "Push Report Division" (AFRO I/II, ROW I/II, Subsidiary)
    is_active       = models.BooleanField(default=True)

    class Meta:
        db_table = "billing_country_region_map"
        verbose_name = "Country / Region Map"
        verbose_name_plural = "Country / Region Maps"
        ordering = ["country_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_name"], condition=models.Q(is_deleted=False),
                name="uniq_active_billing_country",
            )
        ]

    def __str__(self):
        return f"{self.country_name} → {self.region}" + (" (subsidiary)" if self.is_subsidiary else "")


# ── MASTER: Product family / code → LOB ──────────────────────────────────────
class LobMap(AuditSoftDeleteModel):
    """Enrichment master: maps a material-group / product-hierarchy / material
    prefix to a Line-of-Business (LOB) label (mechanical, biosurgical, …)."""

    class KeyType(models.TextChoices):
        MATERIAL_CODE   = "MATERIAL_CODE",   "Material Code (exact)"
        MATERIAL_GROUP  = "MATERIAL_GROUP",  "Material Group"
        MATERIAL_PREFIX = "MATERIAL_PREFIX", "Material Code Prefix"
        PH_LEVEL        = "PH_LEVEL",        "Product Hierarchy Level"

    key_type  = models.CharField(max_length=20, choices=KeyType.choices, default=KeyType.MATERIAL_GROUP)
    key_value = models.CharField(max_length=80, db_index=True)
    lob       = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "billing_lob_map"
        verbose_name = "LOB Map"
        verbose_name_plural = "LOB Maps"
        ordering = ["key_type", "key_value"]
        constraints = [
            models.UniqueConstraint(
                fields=["key_type", "key_value"], condition=models.Q(is_deleted=False),
                name="uniq_active_billing_lob",
            )
        ]

    def __str__(self):
        return f"{self.get_key_type_display()}:{self.key_value} → {self.lob}"


# ── MASTER: Sold-to Party (Soldto-Name) → Country / Domestic classification ──
class SoldToPartyMap(AuditSoftDeleteModel):
    """Enrichment master seeded from the tracker's Lookup sheet (Soldto-Name
    block). Maps a customer name to its country, a fine-grained region, and a
    subsidiary flag with three states — ``Subsidiary`` / ``Non Subsidiary`` /
    ``India`` (domestic). Per the agreed logic the *region* and *subsidiary*
    reporting fields are still derived from the .NET rules (Sales_Office_Description
    and the ``%meril%`` name rule); this master is used to resolve the customer's
    **country** and to flag **domestic** customers (India + SAARC) that must be
    dropped from the export dashboard even when the SAP country code is blank."""

    soldto_name     = models.CharField(max_length=200, db_index=True)
    soldto_name_key = models.CharField(max_length=200, db_index=True)  # normalized match key
    country         = models.CharField(max_length=80, blank=True, null=True)
    region          = models.CharField(max_length=80, blank=True, null=True)
    subs_flag       = models.CharField(max_length=20, blank=True, null=True)  # "Subsidiary" / "Non Subsidiary" / "India"
    is_subsidiary   = models.BooleanField(default=False)
    is_domestic     = models.BooleanField(default=False)
    is_active       = models.BooleanField(default=True)

    class Meta:
        db_table = "billing_soldto_party_map"
        verbose_name = "Sold-to Party Map"
        verbose_name_plural = "Sold-to Party Maps"
        ordering = ["soldto_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["soldto_name_key"], condition=models.Q(is_deleted=False),
                name="uniq_active_billing_soldto",
            )
        ]

    def __str__(self):
        return f"{self.soldto_name} → {self.country or '—'} ({self.subs_flag or '—'})"
