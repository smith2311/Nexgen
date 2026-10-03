from django.contrib import admin

from .models import (
    BillingUpload,
    CountryRegionMap,
    ExportBillingDetails,
    LobMap,
    SoldToPartyMap,
)


@admin.register(BillingUpload)
class BillingUploadAdmin(admin.ModelAdmin):
    list_display = ("original_name", "status", "total_rows", "inserted",
                    "updated", "skipped", "unmapped", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("original_name", "layout_signature")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by",
                       "layout_signature", "detected_columns")


@admin.register(ExportBillingDetails)
class ExportBillingDetailsAdmin(admin.ModelAdmin):
    list_display = ("billing_doc", "item", "material", "billing_type",
                    "billing_date", "country", "region", "is_subsidiary",
                    "lob", "push_division", "invoice_value_lc", "mapping_status")
    list_filter = ("is_subsidiary", "is_excluded", "is_domestic", "is_reversal",
                   "is_pending", "mapping_status", "region", "fin_year", "billing_type")
    search_fields = ("billing_doc", "material", "sold_to_name", "payer_name")
    list_select_related = ("upload",)
    date_hierarchy = "billing_date"


@admin.register(CountryRegionMap)
class CountryRegionMapAdmin(admin.ModelAdmin):
    list_display = ("country_name", "country_code", "region",
                    "is_subsidiary", "subsidiary_name", "push_division", "is_active")
    list_filter = ("is_subsidiary", "is_active", "region")
    search_fields = ("country_name", "country_code", "region")


@admin.register(SoldToPartyMap)
class SoldToPartyMapAdmin(admin.ModelAdmin):
    list_display = ("soldto_name", "country", "region", "subs_flag",
                    "is_subsidiary", "is_domestic", "is_active")
    list_filter = ("subs_flag", "is_subsidiary", "is_domestic", "is_active", "region")
    search_fields = ("soldto_name", "soldto_name_key", "country")


@admin.register(LobMap)
class LobMapAdmin(admin.ModelAdmin):
    list_display = ("key_type", "key_value", "lob", "is_active")
    list_filter = ("key_type", "is_active")
    search_fields = ("key_value", "lob")
