from django.contrib import admin

from .models import (
    AuditLog,
    Batch,
    ExpiredProductReport,
    GTINMaterialMapping,
    Hospital,
    MonthlyReminderLog,
    Notification,
    QRTemplate,
    ScannedProduct,
    ScanningSession,
    SessionAttachment,
    TransferAttachment,
    TransferredProduct,
    TransferSession,
)


@admin.register(GTINMaterialMapping)
class GTINMaterialMappingAdmin(admin.ModelAdmin):
    list_display = (
        "material_code",
        "brand",
        "product_desc",
        "gtin_code",
        "control_code",
        "material_type_desc",
        "updated_by",
        "updated_at",
    )
    search_fields = ("gtin_code", "material_code", "brand", "product_desc", "material_description")
    list_filter = ("brand", "material_type_desc", "updated_at")
    readonly_fields = ("created_at", "updated_at", "updated_by")


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "sap_code", "province", "location", "phone", "email")
    search_fields = ("name", "sap_code", "province", "location", "email")
    list_filter = ("province", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Hospital Information", {"fields": ("name", "sap_code", "province", "location")}),
        ("Contact Details", {"fields": ("contact_person", "phone", "email")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        # Order by province, then by name for better readability
        return super().get_queryset(request).order_by("province", "name")


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("batch_id", "hospital", "created_by", "created_at")
    search_fields = ("batch_id", "hospital__name")
    list_filter = ("created_at", "hospital")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Batch Details", {"fields": ("batch_id", "hospital", "created_by")}),
        ("Description", {"fields": ("description",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(QRTemplate)
class QRTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "scan_type", "created_at")
    search_fields = ("name", "description")
    list_filter = ("scan_type", "created_at")


@admin.register(ScanningSession)
class ScanningSessionAdmin(admin.ModelAdmin):
    list_display = ("get_session_info", "session_type", "status", "started_at", "completed_at", "get_product_count")
    search_fields = ("batch__batch_id", "hospital__name", "to_hospital__name", "session_key", "scanned_by__username")
    list_filter = ("session_type", "status", "started_at", "hospital")
    readonly_fields = ("session_key", "started_at", "completed_at", "last_scan_time")

    fieldsets = (
        (
            "Session Details",
            {"fields": ("session_type", "batch", "hospital", "to_hospital", "scanned_by", "session_key", "status")},
        ),
        ("Timing", {"fields": ("started_at", "completed_at", "last_scan_time")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_session_info(self, obj):
        user_name = obj.scanned_by.get_full_name() or obj.scanned_by.username if obj.scanned_by else "Unknown"
        if obj.session_type == "transfer" and obj.to_hospital:
            return f"{user_name} - {obj.hospital.name} → {obj.to_hospital.name}"
        return f"{user_name} - {obj.hospital.name}"

    get_session_info.short_description = "Session"

    def get_product_count(self, obj):
        return obj.scanned_products.count()

    get_product_count.short_description = "Products Scanned"


@admin.register(ScannedProduct)
class ScannedProductAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "gtin_code",
        "material_code",
        "product_name",
        "get_hospital",
        "expiry_date",
        "scanned_at",
    )
    search_fields = ("serial_number", "gtin_code", "material_code", "product_name")
    list_filter = ("scanned_at", "expiry_date", "scanning_session__hospital")
    readonly_fields = ("serial_number", "scanned_at", "updated_at")

    fieldsets = (
        ("Product Identifiers", {"fields": ("serial_number", "gtin_code", "material_code", "product_name")}),
        ("Product Details", {"fields": ("batch_number", "received_date", "expiry_date", "quantity")}),
        ("Scanning Session", {"fields": ("scanning_session",)}),
        ("QR Data", {"fields": ("qr_data",), "classes": ("collapse",)}),
        ("Metadata", {"fields": ("scanned_at", "updated_at", "notes"), "classes": ("collapse",)}),
    )

    def get_hospital(self, obj):
        return obj.scanning_session.hospital.name

    get_hospital.short_description = "Hospital"


@admin.register(SessionAttachment)
class SessionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("session", "original_filename", "content_type", "file_size", "uploaded_by", "uploaded_at")
    search_fields = ("original_filename", "session__session_key")
    list_filter = ("uploaded_at", "content_type")
    readonly_fields = ("uploaded_at", "file_size", "content_type")
    exclude = ("file_data",)  # Don't show binary blob in admin


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "category", "is_read", "created_at")
    list_filter = ("is_read", "category", "created_at")
    search_fields = ("recipient__username", "title", "message")
    readonly_fields = ("created_at",)
    actions = ["mark_as_read", "mark_as_unread"]

    @admin.action(description="Mark selected notifications as read")
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)

    @admin.action(description="Mark selected notifications as unread")
    def mark_as_unread(self, request, queryset):
        queryset.update(is_read=False)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "get_session_info", "performed_by", "timestamp")
    search_fields = ("session__session_key", "action", "performed_by__username", "description")
    list_filter = ("action", "timestamp", "performed_by")
    readonly_fields = (
        "timestamp",
        "session",
        "performed_by",
        "action",
        "description",
        "old_status",
        "new_status",
        "ip_address",
        "user_agent",
    )

    fieldsets = (
        ("Action Details", {"fields": ("action", "description", "performed_by", "session")}),
        ("Status Change", {"fields": ("old_status", "new_status"), "classes": ("collapse",)}),
        ("Client Information", {"fields": ("ip_address", "user_agent"), "classes": ("collapse",)}),
        ("Timestamp", {"fields": ("timestamp",)}),
    )

    def get_session_info(self, obj):
        if obj.session:
            return f"{obj.session.hospital.name} - {obj.session.session_key[:8]}"
        return "N/A"

    get_session_info.short_description = "Session"

    def has_add_permission(self, request):
        return False  # Prevent manual creation of audit logs

    def has_delete_permission(self, request, obj=None):
        return False  # Prevent deletion of audit logs


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCT REPORT ADMIN
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ExpiredProductReport)
class ExpiredProductReportAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "product_name",
        "hospital",
        "expiry_date",
        "days_expired",
        "acknowledged",
        "created_at",
    )
    search_fields = ("serial_number", "gtin_code", "product_name", "hospital__name", "session_batch_id")
    list_filter = ("acknowledged", "expiry_date", "hospital", "created_at")
    readonly_fields = ("created_at", "scanned_at")
    actions = ["mark_acknowledged"]

    fieldsets = (
        (
            "Product Information",
            {"fields": ("serial_number", "gtin_code", "product_name", "material_code", "batch_number")},
        ),
        ("Expiry Details", {"fields": ("expiry_date", "days_expired")}),
        ("Session Context", {"fields": ("hospital", "session", "session_batch_id", "scanned_by", "scanned_at")}),
        ("Acknowledgement", {"fields": ("acknowledged", "acknowledged_by", "acknowledged_at", "notes")}),
    )

    @admin.action(description="Mark selected as acknowledged")
    def mark_acknowledged(self, request, queryset):
        from django.utils import timezone

        queryset.update(acknowledged=True, acknowledged_by=request.user, acknowledged_at=timezone.now())


# ─────────────────────────────────────────────────────────────────────────────
# H2H TRANSFER ADMIN
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(TransferSession)
class TransferSessionAdmin(admin.ModelAdmin):
    list_display = ("get_route", "transfer_id", "status", "initiated_by", "started_at", "get_product_count")
    search_fields = ("transfer_id", "session_key", "from_hospital__name", "to_hospital__name", "initiated_by__username")
    list_filter = ("status", "started_at", "from_hospital", "to_hospital")
    readonly_fields = ("session_key", "started_at", "completed_at", "last_scan_time")

    fieldsets = (
        (
            "Transfer Details",
            {"fields": ("transfer_id", "session_key", "from_hospital", "to_hospital", "initiated_by", "status")},
        ),
        ("Timing", {"fields": ("started_at", "completed_at", "last_scan_time")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_route(self, obj):
        return f"{obj.from_hospital.name} → {obj.to_hospital.name}"

    get_route.short_description = "Transfer Route"

    def get_product_count(self, obj):
        return obj.transferred_products.count()

    get_product_count.short_description = "Products"


@admin.register(TransferredProduct)
class TransferredProductAdmin(admin.ModelAdmin):
    list_display = ("serial_number", "product_name", "get_route", "expiry_date", "scanned_at")
    search_fields = ("serial_number", "gtin_code", "product_name", "transfer_session__transfer_id")
    list_filter = ("scanned_at", "expiry_date", "transfer_session__from_hospital")
    readonly_fields = ("scanned_at", "updated_at")

    def get_route(self, obj):
        return f"{obj.transfer_session.from_hospital.name} → {obj.transfer_session.to_hospital.name}"

    get_route.short_description = "Transfer Route"


@admin.register(TransferAttachment)
class TransferAttachmentAdmin(admin.ModelAdmin):
    list_display = ("get_transfer_id", "original_filename", "uploaded_by", "uploaded_at")
    search_fields = ("original_filename", "transfer_session__transfer_id")
    list_filter = ("uploaded_at",)
    readonly_fields = ("uploaded_at", "file_size", "content_type")
    exclude = ("file_data",)

    def get_transfer_id(self, obj):
        return obj.transfer_session.transfer_id

    get_transfer_id.short_description = "Transfer"


@admin.register(MonthlyReminderLog)
class MonthlyReminderLogAdmin(admin.ModelAdmin):
    list_display = ("salesperson_email", "salesperson_name", "year", "month", "pending_hospitals_count", "sent_at")
    list_filter = ("year", "month")
    search_fields = ("salesperson_email", "salesperson_name")
    readonly_fields = (
        "sent_at",
        "salesperson_email",
        "salesperson_name",
        "year",
        "month",
        "pending_hospitals_count",
        "pending_hospital_names",
    )
    ordering = ("-year", "-month", "salesperson_email")
