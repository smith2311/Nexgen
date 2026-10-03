from django.contrib import admin
from django.utils import timezone

from .models import (
    AuditLog,
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    ExpiryAlert,
    Hospital,
    Notification,
    ProductCatalog,
    ScanLog,
    ScanningSession,
    SessionAttachment,
    StockOrder,
    StockOrderItem,
    UsageRecord,
)


# ─────────────────────────────────────────────────────────────────────────────
# INLINE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
class DepartmentInline(admin.TabularInline):
    model = Department
    extra = 1
    fields = ("name", "code", "floor", "contact_person", "phone", "is_active")


class StockOrderItemInline(admin.TabularInline):
    model = StockOrderItem
    extra = 1
    raw_id_fields = ("catalog_product",)


class ScanLogInline(admin.TabularInline):
    model = ScanLog
    extra = 0
    readonly_fields = (
        "action",
        "serial_number",
        "gtin_code",
        "product_name",
        "batch_number",
        "expiry_date",
        "scanned_at",
    )
    fields = ("action", "serial_number", "product_name", "batch_number", "expiry_date", "scanned_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# HOSPITAL & DEPARTMENT
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sap_code",
        "plant",
        "country",
        "province",
        "city",
        "salesperson_email",
        "is_active",
        "total_consigned_display",
    )
    search_fields = ("name", "sap_code", "province", "city", "email", "salesperson_email")
    list_filter = ("country", "province", "plant", "is_active", "created_at")
    readonly_fields = ("created_at", "updated_at")
    inlines = [DepartmentInline]
    fieldsets = (
        (
            "Hospital Information",
            {"fields": ("name", "sap_code", "plant", "country", "province", "city", "address", "is_active")},
        ),
        ("Contact Details", {"fields": ("contact_person", "phone", "email", "salesperson_email", "salespersons")}),
        ("Metadata", {"fields": ("updated_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def total_consigned_display(self, obj):
        return obj.total_consigned

    total_consigned_display.short_description = "On Shelf"

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("country", "province", "name")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "code", "floor", "is_active")
    search_fields = ("name", "code", "hospital__name")
    list_filter = ("is_active", "hospital")
    readonly_fields = ("created_at", "updated_at")


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ProductCatalog)
class ProductCatalogAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "material_code",
        "gtin_code",
        "brand",
        "category",
        "size",
        "material_group_description",
        "base_uom",
        "unit_price",
        "is_active",
    )
    search_fields = ("name", "material_code", "gtin_code", "brand", "description", "material_group_description")
    list_filter = (
        "category",
        "brand",
        "material_type",
        "material_group_description",
        "base_uom",
        "is_active",
        "requires_cold_chain",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Product Identification", {"fields": ("material_code", "gtin_code", "name", "brand", "category")}),
        (
            "Details",
            {
                "fields": (
                    "description",
                    "size",
                    "unit_price",
                    "shelf_life_months",
                    "requires_cold_chain",
                    "base_uom",
                    "is_active",
                )
            },
        ),
        (
            "SAP Classification",
            {
                "fields": (
                    "material_type",
                    "material_type_description",
                    "material_group",
                    "material_group_description",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNED PRODUCT
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ConsignedProduct)
class ConsignedProductAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "product_name",
        "get_hospital",
        "get_department",
        "status",
        "expiry_date",
        "expiry_urgency_display",
        "shelf_location",
    )
    search_fields = ("serial_number", "gtin_code", "material_code", "product_name", "batch_number")
    list_filter = ("status", "department__hospital", "expiry_date")
    readonly_fields = ("placed_on_shelf_at", "created_at", "updated_at")
    raw_id_fields = ("department", "catalog_product", "placed_by", "removed_by", "scan_session")
    fieldsets = (
        (
            "Product Identifiers",
            {"fields": ("serial_number", "gtin_code", "material_code", "catalog_product", "product_name")},
        ),
        ("Location", {"fields": ("department", "shelf_location")}),
        ("Product Details", {"fields": ("batch_number", "size", "quantity", "production_date", "expiry_date")}),
        (
            "Status",
            {"fields": ("status", "placed_by", "placed_on_shelf_at", "removed_by", "removed_at", "removal_reason")},
        ),
        ("Raw Data", {"fields": ("qr_data", "notes"), "classes": ("collapse",)}),
    )
    actions = ["mark_expired_removed"]

    def get_hospital(self, obj):
        return obj.department.hospital.name

    get_hospital.short_description = "Hospital"

    def get_department(self, obj):
        return obj.department.name

    get_department.short_description = "Department"

    def expiry_urgency_display(self, obj):
        return obj.expiry_urgency.upper()

    expiry_urgency_display.short_description = "Urgency"

    @admin.action(description="Mark selected as Expired & Removed")
    def mark_expired_removed(self, request, queryset):
        queryset.update(
            status="expired_removed",
            removed_by=request.user,
            removed_at=timezone.now(),
            removal_reason="Marked via admin bulk action",
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY ALERTS
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ExpiryAlert)
class ExpiryAlertAdmin(admin.ModelAdmin):
    list_display = ("product", "tier", "status", "days_until_expiry", "notified_at")
    search_fields = ("product__serial_number", "product__product_name")
    list_filter = ("tier", "status", "notified_at")
    readonly_fields = ("notified_at",)
    raw_id_fields = ("product", "acknowledged_by")
    actions = ["mark_acknowledged"]

    @admin.action(description="Mark selected alerts as acknowledged")
    def mark_acknowledged(self, request, queryset):
        queryset.update(status="acknowledged", acknowledged_by=request.user, acknowledged_at=timezone.now())


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ScanningSession)
class ScanningSessionAdmin(admin.ModelAdmin):
    list_display = (
        "get_session_info",
        "session_type",
        "hospital",
        "department",
        "status",
        "started_at",
        "get_scan_count",
    )
    search_fields = ("session_key", "hospital__name", "rep__username", "rep__first_name")
    list_filter = ("session_type", "status", "started_at", "hospital")
    readonly_fields = ("session_key", "started_at", "completed_at", "last_scan_time")
    raw_id_fields = ("hospital", "department", "to_hospital", "to_department", "rep")
    inlines = [ScanLogInline]
    fieldsets = (
        (
            "Session Details",
            {
                "fields": (
                    "session_key",
                    "session_type",
                    "hospital",
                    "department",
                    "to_hospital",
                    "to_department",
                    "rep",
                    "status",
                )
            },
        ),
        ("Timing", {"fields": ("started_at", "completed_at", "last_scan_time")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_session_info(self, obj):
        rep = obj.rep.get_full_name() or obj.rep.username if obj.rep else "Unknown"
        return f"{rep} — {obj.hospital.name}"

    get_session_info.short_description = "Session"

    def get_scan_count(self, obj):
        return obj.scan_logs.count()

    get_scan_count.short_description = "Scans"


@admin.register(SessionAttachment)
class SessionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("session", "original_filename", "content_type", "file_size", "uploaded_by", "uploaded_at")
    search_fields = ("original_filename", "session__session_key")
    list_filter = ("uploaded_at", "content_type")
    readonly_fields = ("uploaded_at", "file_size", "content_type")
    exclude = ("file_data",)


@admin.register(ScanLog)
class ScanLogAdmin(admin.ModelAdmin):
    list_display = ("action", "serial_number", "product_name", "get_hospital", "expiry_date", "scanned_at")
    search_fields = ("serial_number", "gtin_code", "product_name", "batch_number")
    list_filter = ("action", "scanned_at", "session__hospital")
    readonly_fields = ("scanned_at",)

    def get_hospital(self, obj):
        return obj.session.hospital.name if obj.session else "N/A"

    get_hospital.short_description = "Hospital"

    def has_add_permission(self, request):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STOCK ORDERS
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(StockOrder)
class StockOrderAdmin(admin.ModelAdmin):
    list_display = ("order_id", "hospital", "department", "ordered_by", "urgency", "status", "created_at")
    search_fields = ("order_id", "hospital__name", "ordered_by__username")
    list_filter = ("status", "urgency", "created_at", "hospital")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("hospital", "department", "ordered_by")
    inlines = [StockOrderItemInline]
    fieldsets = (
        ("Order Details", {"fields": ("order_id", "hospital", "department", "ordered_by", "urgency", "status")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "confirmed_at", "shipped_at", "delivered_at")}),
        ("Notes", {"fields": ("notes",)}),
    )
    actions = ["mark_confirmed", "mark_shipped", "mark_delivered"]

    @admin.action(description="Mark selected as Confirmed")
    def mark_confirmed(self, request, queryset):
        queryset.update(status="confirmed", confirmed_at=timezone.now())

    @admin.action(description="Mark selected as Shipped")
    def mark_shipped(self, request, queryset):
        queryset.update(status="shipped", shipped_at=timezone.now())

    @admin.action(description="Mark selected as Delivered")
    def mark_delivered(self, request, queryset):
        queryset.update(status="delivered", delivered_at=timezone.now())


# ─────────────────────────────────────────────────────────────────────────────
# USAGE RECORDS
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("product", "hospital", "department", "used_date", "surgeon_name", "procedure_type")
    search_fields = ("product__serial_number", "surgeon_name", "procedure_type", "hospital__name")
    list_filter = ("used_date", "hospital", "department")
    readonly_fields = ("created_at",)
    raw_id_fields = ("product", "hospital", "department", "recorded_by")


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "category", "hospital", "is_read", "created_at")
    search_fields = ("recipient__username", "title", "message")
    list_filter = ("category", "is_read", "created_at")
    readonly_fields = ("created_at",)
    actions = ["mark_as_read", "mark_as_unread"]

    @admin.action(description="Mark selected as read")
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)

    @admin.action(description="Mark selected as unread")
    def mark_as_unread(self, request, queryset):
        queryset.update(is_read=False)


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "get_session_info", "performed_by", "timestamp")
    search_fields = ("action", "performed_by__username", "description", "session__session_key")
    list_filter = ("action", "timestamp", "performed_by")
    readonly_fields = (
        "timestamp",
        "session",
        "product",
        "performed_by",
        "action",
        "description",
        "old_status",
        "new_status",
        "ip_address",
        "user_agent",
        "metadata",
    )

    def get_session_info(self, obj):
        if obj.session:
            return f"{obj.session.hospital.name} — {obj.session.session_key[:8]}"
        return "N/A"

    get_session_info.short_description = "Session"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNMENT POSTING (SAP movement history)
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(ConsignmentPosting)
class ConsignmentPostingAdmin(admin.ModelAdmin):
    list_display = (
        "posting_date",
        "movement_type",
        "movement_type_text",
        "material",
        "material_description",
        "batch",
        "quantity",
        "base_uom",
        "customer",
        "customer_name",
        "plant",
    )
    search_fields = ("material", "material_description", "customer", "customer_name", "batch", "material_document")
    list_filter = ("movement_type", "plant", "posting_date", "material_type")
    readonly_fields = ("created_at",)
    raw_id_fields = ("hospital", "catalog_product")
    date_hierarchy = "posting_date"
    fieldsets = (
        (
            "Posting Detail",
            {
                "fields": (
                    "plant",
                    "storage_location",
                    "posting_date",
                    "entry_date",
                    "entry_time",
                    "movement_type",
                    "movement_type_text",
                )
            },
        ),
        (
            "Material",
            {
                "fields": (
                    "material",
                    "material_description",
                    "batch",
                    "quantity",
                    "base_uom",
                    "material_type",
                    "material_type_description",
                    "material_group",
                    "material_group_description",
                )
            },
        ),
        (
            "SAP Document",
            {
                "fields": (
                    "material_document",
                    "material_doc_item",
                    "material_doc_year",
                    "document_header_text",
                    "delivery",
                    "user_name",
                )
            },
        ),
        (
            "Customer & Transfer",
            {"fields": ("customer", "customer_name", "receiving_plant", "sending_plant", "amount_lc", "currency")},
        ),
        ("Links", {"fields": ("hospital", "catalog_product", "created_at"), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
