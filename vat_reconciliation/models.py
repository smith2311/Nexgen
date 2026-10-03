from decimal import Decimal

from django.db import models

from logistics_performance.models import AuditSoftDeleteModel


# ── MASTER 1: Country ───────────────────────────────────────────────────────
class VatCountryMaster(AuditSoftDeleteModel):
    """Per-country configuration that drives country identification, currency
    normalization and the tolerance check."""
    country                   = models.CharField(max_length=100)
    language                  = models.CharField(max_length=50)
    local_currency            = models.CharField(max_length=10)
    standard_invoice_currency = models.CharField(max_length=10)
    cha_partner_name          = models.CharField(max_length=200, blank=True, null=True)
    local_entity              = models.CharField(max_length=200, blank=True, null=True)
    tolerance_limit           = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.05"))
    is_active                 = models.BooleanField(default=True)

    class Meta:
        db_table = "vat_country_master"
        verbose_name = "VAT Country Master"
        verbose_name_plural = "VAT Country Masters"
        ordering = ["country"]
        constraints = [
            models.UniqueConstraint(
                fields=["country"], condition=models.Q(is_deleted=False),
                name="uniq_active_vat_country",
            )
        ]

    def __str__(self):
        return self.country


# ── MASTER 2: VAT Rate ──────────────────────────────────────────────────────
class VatRateMaster(AuditSoftDeleteModel):
    """Source of truth for the applicable VAT %. Supports HSN-based rates
    (one row per HSN) and flat rates (one row per country, no HSN)."""
    class CalcType(models.TextChoices):
        HSN  = "HSN", "HSN Based"
        FLAT = "FLAT", "Flat Percentage"

    country      = models.CharField(max_length=100)
    calc_type    = models.CharField(max_length=4, choices=CalcType.choices, default=CalcType.HSN)
    hsn_sac_code = models.CharField(max_length=30, blank=True, null=True)  # blank for FLAT
    vat_percent  = models.DecimalField(max_digits=6, decimal_places=2)
    is_active    = models.BooleanField(default=True)

    class Meta:
        db_table = "vat_vat_rate_master"
        verbose_name = "VAT Rate Master"
        verbose_name_plural = "VAT Rate Masters"
        ordering = ["country", "hsn_sac_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "calc_type", "hsn_sac_code"],
                condition=models.Q(is_deleted=False),
                name="uniq_active_vat_rate",
            )
        ]

    def __str__(self):
        return f"{self.country} · {self.hsn_sac_code or 'FLAT'} · {self.vat_percent}%"


# ── MASTER 3: CHA Email ──────────────────────────────────────────────────────
class VatChaEmailMaster(AuditSoftDeleteModel):
    """CHA (Customs House Agent) contacts per country. A country can have more
    than one CHA (one row each), so reconciled invoices can be e-mailed to the
    right point-of-contact. `poc_email` may hold several addresses separated by
    comma / semicolon / newline."""
    country      = models.CharField(max_length=100)
    company_name = models.CharField(max_length=200, blank=True, null=True)
    vat_approval = models.BooleanField(default=False)          # True = "Yes", False = "Not Needed"
    cha_name     = models.CharField(max_length=200, blank=True, null=True)
    poc_name     = models.CharField(max_length=200, blank=True, null=True)
    poc_email    = models.CharField(max_length=300, blank=True, null=True)
    is_active    = models.BooleanField(default=True)

    class Meta:
        db_table = "vat_cha_email_master"
        verbose_name = "CHA Email Master"
        verbose_name_plural = "CHA Email Masters"
        ordering = ["country", "cha_name"]

    def __str__(self):
        return f"{self.country} · {self.cha_name or '—'}"


# ── INVOICE (hub — OCR / translation / currency / reconciliation folded in) ──
class VatInvoice(AuditSoftDeleteModel):
    class Stage(models.TextChoices):
        UPLOADED   = "UPLOADED",   "Uploaded"
        OCR        = "OCR",        "OCR Extracted"
        TRANSLATED = "TRANSLATED", "Translated"
        CONVERTED  = "CONVERTED",  "Currency Converted"
        CALCULATED = "CALCULATED", "VAT Calculated"
        RECONCILED = "RECONCILED", "Reconciled"

    class ReconStatus(models.TextChoices):
        PENDING     = "PENDING",     "Pending"
        MATCHED     = "MATCHED",     "Matched"
        DISCREPANCY = "DISCREPANCY", "Discrepancy"

    # Header
    invoice_number   = models.CharField(max_length=100)
    invoice_date     = models.DateField(blank=True, null=True)

    # Resolved against Country Master after OCR
    country          = models.CharField(max_length=100, blank=True, null=True)
    language         = models.CharField(max_length=50, blank=True, null=True)
    local_entity     = models.CharField(max_length=200, blank=True, null=True)
    cha_partner      = models.CharField(max_length=200, blank=True, null=True)

    # Currency
    invoice_currency  = models.CharField(max_length=10, blank=True, null=True)
    local_currency    = models.CharField(max_length=10, blank=True, null=True)
    exchange_rate     = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    converted_taxable = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    rate_source       = models.CharField(max_length=50, blank=True, null=True)
    rate_date         = models.DateField(blank=True, null=True)

    # Amounts
    taxable_amount   = models.DecimalField(max_digits=16, decimal_places=2, blank=True, null=True)
    charged_vat      = models.DecimalField(max_digits=16, decimal_places=2, blank=True, null=True)
    total_amount     = models.DecimalField(max_digits=16, decimal_places=2, blank=True, null=True)
    expected_vat     = models.DecimalField(max_digits=16, decimal_places=2, blank=True, null=True)
    variance         = models.DecimalField(max_digits=16, decimal_places=2, blank=True, null=True)
    tolerance_used   = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))

    # OCR + translation (folded in)
    raw_ocr_text     = models.TextField(blank=True, null=True)
    ocr_json         = models.JSONField(default=dict, blank=True)
    source_language  = models.CharField(max_length=50, blank=True, null=True)
    translated_text  = models.TextField(blank=True, null=True)
    ocr_provider     = models.CharField(max_length=50, blank=True, null=True)

    # Pipeline state
    stage            = models.CharField(max_length=12, choices=Stage.choices, default=Stage.UPLOADED)
    recon_status     = models.CharField(max_length=12, choices=ReconStatus.choices, default=ReconStatus.PENDING)
    process_error    = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "vat_invoice"
        verbose_name = "VAT Invoice"
        verbose_name_plural = "VAT Invoices"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.invoice_number} ({self.country or '—'})"


# ── DOCUMENTS (multiple files per invoice) ──────────────────────────────────
class VatInvoiceDocument(AuditSoftDeleteModel):
    invoice   = models.ForeignKey(VatInvoice, on_delete=models.CASCADE, related_name="documents")
    file      = models.FileField(upload_to="vat_invoices/%Y/%m/")
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "vat_invoice_document"
        verbose_name = "VAT Invoice Document"
        verbose_name_plural = "VAT Invoice Documents"

    def __str__(self):
        return self.file_name


# ── VAT WORKING SUMMARY (one row per HSN line) ──────────────────────────────
class VatVatCalculation(AuditSoftDeleteModel):
    invoice        = models.ForeignKey(VatInvoice, on_delete=models.CASCADE, related_name="calc_lines")
    hsn_sac_code   = models.CharField(max_length=30, blank=True, null=True)
    taxable_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    vat_percent    = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    expected_vat   = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "vat_vat_calculation"
        verbose_name = "VAT Calculation Line"
        verbose_name_plural = "VAT Calculation Lines"
        ordering = ["id"]

    def __str__(self):
        return f"{self.hsn_sac_code or 'FLAT'} · {self.taxable_amount} @ {self.vat_percent}%"
