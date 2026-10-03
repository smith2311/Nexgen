"""
shipment_notifications/admin.py
===============================
Django admin configuration for the Shipment Notification System.

Registers all 3 models in the admin panel at:
  https://gpt.e-meril.in/admin/shipment_notifications/

- ExportShipment: browse/search/filter synced shipment data.
- SalespersonMapping: manage which phone gets notifications for which country.
- ShipmentAccessToken: view generated tokens for dashboard access.
"""

from django.contrib import admin

from .models import ExportShipment, SalespersonMapping, ShipmentAccessToken


@admin.register(ExportShipment)
class ExportShipmentAdmin(admin.ModelAdmin):
    list_display = ("invoice_no", "awb_no", "country", "vertical", "consignee_name", "status", "etd", "updated_at")
    search_fields = ("invoice_no", "awb_no", "consignee_name", "so_no")
    list_filter = ("country", "vertical", "status", "whatsapp_sent_status")
    readonly_fields = ("created_at", "updated_at", "last_synced_at", "whatsapp_sent_at")


@admin.register(SalespersonMapping)
class SalespersonMappingAdmin(admin.ModelAdmin):
    list_display = ("country", "vertical", "to_email", "whatsapp_no")
    search_fields = ("country", "vertical", "to_email", "whatsapp_no")
    list_filter = ("country", "vertical")


@admin.register(ShipmentAccessToken)
class ShipmentAccessTokenAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "token", "created_at", "updated_at")
    search_fields = ("phone_number",)
    readonly_fields = ("created_at", "updated_at", "token")

    def token(self, obj):
        return obj.token[:32] + "..." if len(obj.token) > 32 else obj.token

    token.short_description = "Encrypted Token"
