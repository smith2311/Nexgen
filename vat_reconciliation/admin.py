from django.contrib import admin

from .models import (
    VatCountryMaster,
    VatRateMaster,
    VatChaEmailMaster,
    VatInvoice,
    VatInvoiceDocument,
    VatVatCalculation,
)


@admin.register(VatCountryMaster)
class VatCountryMasterAdmin(admin.ModelAdmin):
    list_display = ("country", "language", "local_currency",
                    "standard_invoice_currency", "tolerance_limit", "is_active")
    list_filter = ("is_active", "is_deleted")
    search_fields = ("country", "cha_partner_name", "local_entity")


@admin.register(VatRateMaster)
class VatRateMasterAdmin(admin.ModelAdmin):
    list_display = ("country", "calc_type", "hsn_sac_code", "vat_percent", "is_active")
    list_filter = ("calc_type", "is_active", "is_deleted")
    search_fields = ("country", "hsn_sac_code")


@admin.register(VatChaEmailMaster)
class VatChaEmailMasterAdmin(admin.ModelAdmin):
    list_display = ("country", "company_name", "cha_name", "poc_name",
                    "poc_email", "vat_approval", "is_active")
    list_filter = ("vat_approval", "is_active", "is_deleted", "country")
    search_fields = ("country", "company_name", "cha_name", "poc_name", "poc_email")


class VatVatCalculationInline(admin.TabularInline):
    model = VatVatCalculation
    extra = 0


class VatInvoiceDocumentInline(admin.TabularInline):
    model = VatInvoiceDocument
    extra = 0


@admin.register(VatInvoice)
class VatInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "country", "invoice_currency",
                    "expected_vat", "charged_vat", "variance",
                    "recon_status", "stage")
    list_filter = ("recon_status", "stage", "country", "is_deleted")
    search_fields = ("invoice_number", "country")
    inlines = [VatVatCalculationInline, VatInvoiceDocumentInline]


@admin.register(VatVatCalculation)
class VatVatCalculationAdmin(admin.ModelAdmin):
    list_display = ("invoice", "hsn_sac_code", "taxable_amount", "vat_percent", "expected_vat")
    search_fields = ("hsn_sac_code",)
