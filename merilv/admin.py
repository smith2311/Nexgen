from django.contrib import admin

from .models import (
    ActivityLog,
    BillingTypeMaster,
    BoxProfile,
    BranchTransferLine,
    BranchTransferPlan,
    BranchTransferReceipt,
    BranchTransferReceiptScan,
    BtScan,
    BtScanBox,
    BtScanSession,
    CompanyMaster,
    ConsolidationRow,
    ConsolidationRun,
    CourierMaster,
    CustomerCourierMapping,
    CustomerMaster,
    DeliveryDump,
    DeliveryLine,
    DispatchedSerial,
    Entity,
    FinishedDelivery,
    LegacyAuditLog,
    LegacyBranchTransferHistory,
    LegacyMessage,
    LegacyMessageReaction,
    LegacyOutboundDraft,
    LegacyOutboundHistory,
    LegacyPlantOrgMapping,
    LegacyProfile,
    LegacyScanHistory,
    LegacyUserData,
    LegacyUserPlantMapping,
    LegacyVehicle,
    MaterialMaster,
    OrganizationMaster,
    OutboundScan,
    OutboundSession,
    PlantMaster,
    SalesTypeMaster,
    SavedLayout,
    ScanLog,
    ScanRecord,
    ScanSession,
    StorageMaster,
    ToolSetting,
    UserPlantMapping,
    ValidationRow,
    ValidationRun,
)


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "supports_serial", "is_active", "sort_order")
    list_filter = ("is_active", "supports_serial")
    search_fields = ("code", "name", "company_name")
    ordering = ("sort_order", "code")
    fieldsets = (
        (None, {"fields": ("code", "name", "company_name", "is_active", "sort_order")}),
        (
            "Barcode parsing",
            {
                "fields": (
                    "strip_whitespace",
                    "supports_serial",
                    "ai21_is_material",
                    "sanitise_batch",
                    "allow_envelope",
                    "positional_walker",
                    "compact_gs1",
                    "positional_rules",
                    "trauma_legacy",
                    "pseudo_serial_digits",
                    "parse_rules_csv",
                ),
                "description": "Each option corresponds to a barcode format one business unit prints.",
            },
        ),
        (
            "Expiry classification",
            {"fields": ("near_expiry_months", "short_expiry_months", "month_divisor")},
        ),
        (
            "Validation engine",
            {
                "fields": (
                    "match_concat",
                    "match_batch",
                    "match_material",
                    "concat_delimiter",
                    "triple_sort",
                    "multi_row_consume",
                    "smart_zero",
                    "use_scan_qty",
                    "strict_batch",
                    "plant_priority",
                    "good_storage_location",
                    "damage_storage_location",
                )
            },
        ),
        (
            "Outbound",
            {
                "fields": (
                    "allow_customer_focus",
                    "aggregate_delivery_lines",
                    "enforce_box_capacity",
                    "allow_partial_export",
                )
            },
        ),
    )


@admin.register(MaterialMaster)
class MaterialMasterAdmin(admin.ModelAdmin):
    list_display = ("material_code", "scan_code", "material_description", "gtin", "entity")
    list_filter = ("entity",)
    search_fields = ("material_code", "scan_code", "material_description", "gtin")
    list_select_related = ("entity",)


@admin.register(CustomerMaster)
class CustomerMasterAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "customer_name", "city", "courier_partner", "is_blocked")
    list_filter = ("is_blocked", "entity", "country")
    search_fields = ("customer_code", "customer_name", "gstin", "city")


@admin.register(CourierMaster)
class CourierMasterAdmin(admin.ModelAdmin):
    list_display = ("courier_name", "plant", "courier_number", "vehicle_number", "is_active")
    list_filter = ("is_active", "plant")
    search_fields = ("courier_name", "courier_number", "vehicle_number")


@admin.register(CustomerCourierMapping)
class CustomerCourierMappingAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "entity", "plant", "courier_name", "courier_number")
    list_filter = ("entity", "plant")
    search_fields = ("customer_code", "courier_name")


@admin.register(BoxProfile)
class BoxProfileAdmin(admin.ModelAdmin):
    list_display = ("code", "length_cm", "width_cm", "height_cm", "max_qty", "volumetric_weight", "is_active")
    list_filter = ("is_active",)
    ordering = ("sort_order", "code")


class DeliveryLineInline(admin.TabularInline):
    model = DeliveryLine
    extra = 0
    fields = ("delivery_no", "item_no", "material", "batch", "required_qty", "scanned_qty", "plant")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DeliveryDump)
class DeliveryDumpAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entity", "row_count", "skipped_count", "uploaded_by", "created_at")
    list_filter = ("entity", "created_at")
    readonly_fields = ("row_count", "skipped_count", "warnings", "created_at")
    inlines = [DeliveryLineInline]


@admin.register(DeliveryLine)
class DeliveryLineAdmin(admin.ModelAdmin):
    list_display = ("delivery_no", "item_no", "material", "batch", "required_qty", "scanned_qty", "plant")
    list_filter = ("entity", "plant")
    search_fields = ("delivery_no", "material", "batch", "bill_to_code", "customer_name")


class OutboundScanInline(admin.TabularInline):
    model = OutboundScan
    extra = 0
    fields = ("serial_identity", "material", "batch", "box", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OutboundSession)
class OutboundSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entity", "status", "operator", "created_at")
    list_filter = ("entity", "status", "created_at")
    search_fields = ("name",)
    inlines = [OutboundScanInline]


@admin.register(ScanLog)
class ScanLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "status", "reason_code", "message", "session")
    list_filter = ("status", "reason_code", "created_at")
    search_fields = ("raw_scan", "message")
    readonly_fields = ("session", "raw_scan", "status", "reason_code", "message", "parsed", "created_at")


@admin.register(FinishedDelivery)
class FinishedDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "delivery_no", "entity", "customer_name", "courier_partner",
        "box_count", "is_partial", "picker", "created_at",
    )
    list_filter = ("entity", "is_partial", "courier_partner", "created_at")
    search_fields = ("delivery_no", "customer_code", "customer_name", "docket_no")
    date_hierarchy = "created_at"


@admin.register(DispatchedSerial)
class DispatchedSerialAdmin(admin.ModelAdmin):
    """The burned-serial ledger. Searchable, because "why was this carton
    refused?" is answered by looking the serial up here.

    Adding a row by hand is disabled — the only writer is a delivery being
    finished. Deleting one is left available: it is the sole way to release a
    serial that was burned in error (a test dispatch, a delivery finished
    against the wrong dump), and that is a deliberate, audited admin act
    rather than something the scanner can do on its own.
    """

    list_display = ("serial", "delivery_no", "material", "batch", "entity", "dispatched_by", "created_at")
    list_filter = ("entity", "created_at")
    search_fields = ("serial", "delivery_no", "material", "batch")
    date_hierarchy = "created_at"
    readonly_fields = (
        "serial", "entity", "finished_delivery", "delivery_no", "material",
        "batch", "dispatched_by", "created_at",
    )

    def has_add_permission(self, request):
        return False


class ValidationRowInline(admin.TabularInline):
    model = ValidationRow
    extra = 0
    fields = ("material", "physical_batch", "physical_serial", "scan_qty", "status", "stock_status")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ValidationRun)
class ValidationRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entity", "total_scanned", "total_system", "reconciliation_pct", "created_by", "created_at")
    list_filter = ("entity", "created_at")
    readonly_fields = ("settings_snapshot", "warnings", "created_at")
    inlines = [ValidationRowInline]


class ScanRecordInline(admin.TabularInline):
    model = ScanRecord
    extra = 0
    fields = ("material", "batch", "serial", "result", "condition", "box_no", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ScanSession)
class ScanSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entity", "mode", "total_count", "found_count", "is_finished", "created_by", "created_at")
    list_filter = ("entity", "mode", "is_finished", "created_at")
    search_fields = ("name",)
    inlines = [ScanRecordInline]


class BranchTransferLineInline(admin.TabularInline):
    model = BranchTransferLine
    extra = 0
    fields = (
        "sku_code", "quantity", "picked_qty", "received_qty",
        "node_code", "receiving_node_code",
    )
    readonly_fields = ("sku_code", "quantity", "picked_qty", "received_qty")


@admin.register(BranchTransferPlan)
class BranchTransferPlanAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entity", "is_finished", "created_by", "created_at")
    list_filter = ("entity", "is_finished")
    inlines = [BranchTransferLineInline]


class BranchTransferReceiptScanInline(admin.TabularInline):
    model = BranchTransferReceiptScan
    extra = 0
    fields = ("material", "batch", "serial", "line", "scanned_by", "created_at")
    readonly_fields = fields


@admin.register(BranchTransferReceipt)
class BranchTransferReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "__str__", "plan", "entity", "is_finished", "received_by", "created_at",
    )
    list_filter = ("entity", "is_finished")
    search_fields = ("name", "vehicle_no", "docket_no")
    inlines = [BranchTransferReceiptScanInline]


class ConsolidationRowInline(admin.TabularInline):
    model = ConsolidationRow
    extra = 0
    fields = ("invoice_no", "material", "batch", "quantity", "amount", "source_file")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ConsolidationRun)
class ConsolidationRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "source_type", "file_count", "row_count", "created_by", "created_at")
    list_filter = ("source_type", "created_at")
    inlines = [ConsolidationRowInline]


@admin.register(ToolSetting)
class ToolSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "updated_by", "updated_at")
    search_fields = ("key", "description")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "created_at")
    search_fields = ("action", "target_id")
    readonly_fields = ("actor", "action", "target_type", "target_id", "metadata", "created_at")
    date_hierarchy = "created_at"


@admin.register(UserPlantMapping)
class UserPlantMappingAdmin(admin.ModelAdmin):
    list_display = ("user", "plant")
    list_filter = ("plant",)
    search_fields = ("user__username", "user__email")


class BtScanBoxInline(admin.TabularInline):
    model = BtScanBox
    extra = 0
    fields = ("name", "zone", "sequence", "is_closed")


class BtScanInline(admin.TabularInline):
    model = BtScan
    extra = 0
    fields = ("material", "batch", "serial", "location", "box", "scanned_by", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BtScanSession)
class BtScanSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "plan", "entity", "is_finished", "created_by", "created_at")
    list_filter = ("entity", "is_finished")
    search_fields = ("name", "location_priority")
    inlines = [BtScanBoxInline, BtScanInline]


@admin.register(SavedLayout)
class SavedLayoutAdmin(admin.ModelAdmin):
    list_display = ("user", "screen_key", "name", "is_active", "updated_at")
    list_filter = ("screen_key", "is_active")
    search_fields = ("user__username", "user__email", "screen_key", "name")
    readonly_fields = ("created_at", "updated_at")


admin.site.register(CompanyMaster)
admin.site.register(OrganizationMaster)
admin.site.register(PlantMaster)
admin.site.register(StorageMaster)
admin.site.register(BillingTypeMaster)
admin.site.register(SalesTypeMaster)


@admin.register(LegacyProfile)
class LegacyProfileAdmin(admin.ModelAdmin):
    list_display = ("email", "username", "full_name", "role", "imported_user", "last_seen")
    list_filter = ("role",)
    search_fields = ("email", "username", "full_name", "legacy_id")
    readonly_fields = ("raw_data", "permissions", "imported_at")


@admin.register(LegacyAuditLog)
class LegacyAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target_entity", "target_id", "activity")
    list_filter = ("action", "target_entity", "created_at")
    search_fields = ("action", "target_entity", "target_id", "legacy_id")
    readonly_fields = ("metadata", "raw_data", "imported_at")
    date_hierarchy = "created_at"


@admin.register(LegacyOutboundDraft)
class LegacyOutboundDraftAdmin(admin.ModelAdmin):
    list_display = ("user_email", "last_updated")
    search_fields = ("user_email",)
    readonly_fields = ("state", "imported_at")


@admin.register(LegacyOutboundHistory)
class LegacyOutboundHistoryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "legacy_id", "user_email", "mode", "total")
    list_filter = ("mode", "timestamp")
    search_fields = ("legacy_id", "user_email", "mode")
    readonly_fields = ("data", "imported_at")


@admin.register(LegacyScanHistory)
class LegacyScanHistoryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "legacy_id", "user_email", "mode", "total")
    list_filter = ("mode", "timestamp")
    search_fields = ("legacy_id", "user_email", "mode")
    readonly_fields = ("data", "imported_at")


@admin.register(LegacyBranchTransferHistory)
class LegacyBranchTransferHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "scanner_type", "created_by", "receiver_code", "total_materials", "total_boxes")
    list_filter = ("scanner_type", "created_at")
    search_fields = ("created_by", "receiver_code", "plant_name", "legacy_id")
    readonly_fields = ("color_zones", "plan_data", "scan_data", "box_data", "imported_at")


@admin.register(LegacyMessage)
class LegacyMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "context_id", "sender_name", "status", "message_type")
    list_filter = ("status", "message_type", "created_at")
    search_fields = ("context_id", "sender_name", "legacy_id")
    readonly_fields = ("cipher", "read_by", "imported_at")


@admin.register(LegacyMessageReaction)
class LegacyMessageReactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "emoji", "message_legacy_id", "user_legacy_id")
    search_fields = ("emoji", "message_legacy_id", "user_legacy_id", "legacy_id")
    readonly_fields = ("imported_at",)


@admin.register(LegacyUserData)
class LegacyUserDataAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "key", "user_legacy_id", "updated_at")
    list_filter = ("tool_name",)
    search_fields = ("tool_name", "key", "user_legacy_id", "legacy_id")
    readonly_fields = ("file_data", "value", "imported_at")


@admin.register(LegacyVehicle)
class LegacyVehicleAdmin(admin.ModelAdmin):
    list_display = ("vehicle_number", "vehicle_type", "courier_code", "owner_name", "capacity_kg", "is_active")
    list_filter = ("vehicle_type", "is_active")
    search_fields = ("vehicle_number", "courier_code", "owner_name", "legacy_id")


@admin.register(LegacyPlantOrgMapping)
class LegacyPlantOrgMappingAdmin(admin.ModelAdmin):
    list_display = ("plant_code", "org_code", "is_primary", "created_at")
    list_filter = ("is_primary", "org_code")
    search_fields = ("plant_code", "org_code")


@admin.register(LegacyUserPlantMapping)
class LegacyUserPlantMappingAdmin(admin.ModelAdmin):
    list_display = ("plant_code", "user_legacy_id", "created_at")
    list_filter = ("plant_code",)
    search_fields = ("plant_code", "user_legacy_id")
