from django.contrib import admin

from .models import Capability, CountrySearchEvent, Vendor, VendorEvaluation, VendorRegistration, VendorRequest, VendorScore


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "country", "state", "city", "contact_person", "email", "is_active")
    list_filter = ("country", "state", "is_active", "capabilities")
    search_fields = ("vendor_name", "contact_person", "email", "country", "state", "city", "warehouse_location")
    readonly_fields = ("created_at", "updated_at", "created_by")
    filter_horizontal = ("capabilities",)
    list_per_page = 50


@admin.register(CountrySearchEvent)
class CountrySearchEventAdmin(admin.ModelAdmin):
    list_display = ("country", "user", "created_at")
    list_filter = ("country",)
    readonly_fields = ("country", "user", "created_at")


@admin.register(VendorRequest)
class VendorRequestAdmin(admin.ModelAdmin):
    list_display = ("subject", "raised_by", "recipient_count", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("subject", "country", "product_verticals", "raised_by__username")
    readonly_fields = (
        "subject", "country", "raised_by", "recipients", "recipient_count", "status", "created_at",
        "intro_message", "header_message", "scope_of_work", "footer_message",
        "warehouse_space_from", "warehouse_space_to",
        "storage_temp_from", "storage_temp_to",
        "monthly_shipment_from", "monthly_shipment_to",
        "inbound_from", "inbound_to",
        "outbound_from", "outbound_to",
        "product_verticals",
    )


@admin.register(VendorRegistration)
class VendorRegistrationAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "country", "state", "city", "contact_person", "email", "status", "submitted_at", "reviewed_by")
    list_filter = ("status", "country", "state")
    search_fields = ("vendor_name", "email", "contact_person", "country", "state", "city")
    readonly_fields = ("submitted_at", "reviewed_at", "reviewed_by", "vendor")
    fieldsets = (
        ("Company", {"fields": ("vendor_name", "country", "state", "city", "warehouse_location")}),
        ("Contact", {"fields": ("contact_person", "email", "mobile", "website")}),
        ("Capabilities", {"fields": ("capabilities",)}),
        ("Workflow", {"fields": ("status", "rejection_reason", "reviewed_by", "reviewed_at", "vendor", "submitted_at")}),
    )


@admin.register(VendorScore)
class VendorScoreAdmin(admin.ModelAdmin):
    list_display = ("vendor", "facility_rating", "support_rating", "compliance_rating", "delivery_reliability", "is_blacklisted", "scored_by")
    list_filter = ("is_blacklisted",)
    search_fields = ("vendor__vendor_name",)
    readonly_fields = ("created_at", "updated_at", "scored_by")


@admin.register(VendorEvaluation)
class VendorEvaluationAdmin(admin.ModelAdmin):
    list_display = ("vendor_request", "status", "recommended_vendor_name", "triggered_by", "triggered_at", "completed_at")
    list_filter = ("status",)
    readonly_fields = ("triggered_at", "completed_at", "triggered_by")


@admin.register(Capability)
class CapabilityAdmin(admin.ModelAdmin):
    """The capability master. Also editable from VendorLink's own admin console —
    this registration is for superusers who are already in Django admin."""

    list_display = ("label", "code", "sort_order", "is_active", "vendor_count")
    list_filter = ("is_active",)
    search_fields = ("label", "code", "description")
    ordering = ("sort_order", "label")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        """``code`` is frozen after creation.

        Saved filter links and the circulating Excel exports carry the code, so
        renaming it silently breaks both. The label is the thing meant to be
        edited; the code is the identity.
        """
        base = list(super().get_readonly_fields(request, obj))
        return base + ["code"] if obj else base
