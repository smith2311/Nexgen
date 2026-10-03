from django.contrib.auth.models import User

from rest_framework import serializers

from .models import (
    AuditLog,
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    ExpiredProductReport,
    ExpiryAlert,
    Hospital,
    InventoryReconciliation,
    Notification,
    ProductCatalog,
    ReconciliationItem,
    ScanLog,
    ScannedProductRemark,
    ScanningBatch,
    ScanningSession,
    ScanSessionRemark,
    SessionAttachment,
    StockOrder,
    StockOrderItem,
    TransferAttachment,
    TransferredProduct,
    TransferSession,
    UsageRecord,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email"]


# ─────────────────────────────────────────────────────────────────────────────
# HOSPITAL & DEPARTMENT
# ─────────────────────────────────────────────────────────────────────────────
class DepartmentSerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)

    class Meta:
        model = Department
        fields = [
            "id",
            "hospital",
            "hospital_name",
            "name",
            "code",
            "floor",
            "contact_person",
            "phone",
            "is_active",
            "product_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "hospital_name"]

    def get_product_count(self, obj):
        return obj.consigned_products.filter(status="on_shelf").count()


class HospitalSerializer(serializers.ModelSerializer):
    departments = DepartmentSerializer(many=True, read_only=True)
    updated_by_name = serializers.SerializerMethodField()
    total_on_shelf = serializers.SerializerMethodField()
    expired_count = serializers.SerializerMethodField()
    salespersons_detail = serializers.SerializerMethodField()
    salesperson_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.all(),
        source="salespersons",
        write_only=True,
        required=False,
    )

    class Meta:
        model = Hospital
        fields = [
            "id",
            "name",
            "sap_code",
            "plant",
            "country",
            "province",
            "city",
            "address",
            "contact_person",
            "phone",
            "email",
            "salesperson_email",
            "salespersons_detail",
            "salesperson_ids",
            "is_active",
            "departments",
            "total_on_shelf",
            "expired_count",
            "updated_by",
            "updated_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "updated_by", "updated_by_name"]

    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return obj.updated_by.get_full_name() or obj.updated_by.username
        return None

    def get_total_on_shelf(self, obj):
        return obj.total_consigned

    def get_expired_count(self, obj):
        return obj.expired_count

    def get_salespersons_detail(self, obj):
        return [
            {"id": u.id, "name": u.get_full_name() or u.username, "email": u.email}
            for u in obj.salespersons.all()
        ]

    def update(self, instance, validated_data):
        salespersons = validated_data.pop("salespersons", None)
        instance = super().update(instance, validated_data)
        if salespersons is not None:
            instance.salespersons.set(salespersons)
        return instance

    def create(self, validated_data):
        salespersons = validated_data.pop("salespersons", [])
        instance = super().create(validated_data)
        if salespersons:
            instance.salespersons.set(salespersons)
        return instance


class HospitalListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for dropdowns and lists."""

    department_count = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = ["id", "name", "sap_code", "plant", "country", "province", "city", "is_active", "department_count"]

    def get_department_count(self, obj):
        return obj.departments.filter(is_active=True).count()


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────────────────────────────────────────────
class ProductCatalogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCatalog
        fields = [
            "id",
            "material_code",
            "gtin_code",
            "name",
            "brand",
            "category",
            "description",
            "size",
            "unit_price",
            "shelf_life_months",
            "requires_cold_chain",
            "material_type",
            "material_type_description",
            "material_group",
            "material_group_description",
            "base_uom",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNED PRODUCT
# ─────────────────────────────────────────────────────────────────────────────
class ConsignedProductSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="department.hospital.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    catalog_product_name = serializers.CharField(source="catalog_product.name", read_only=True, default=None)
    expiry_urgency = serializers.CharField(read_only=True)
    days_until_expiry = serializers.IntegerField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    placed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ConsignedProduct
        fields = [
            "id",
            "department",
            "hospital_name",
            "department_name",
            "serial_number",
            "gtin_code",
            "material_code",
            "catalog_product",
            "catalog_product_name",
            "product_name",
            "batch_number",
            "size",
            "quantity",
            "production_date",
            "expiry_date",
            "status",
            "expiry_urgency",
            "days_until_expiry",
            "is_expired",
            "shelf_location",
            "reference_number",
            "placed_by",
            "placed_by_name",
            "placed_on_shelf_at",
            "removed_at",
            "removal_reason",
            "qr_data",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "placed_on_shelf_at", "created_at", "updated_at"]

    def get_placed_by_name(self, obj):
        if obj.placed_by:
            return obj.placed_by.get_full_name() or obj.placed_by.username
        return None


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY ALERT
# ─────────────────────────────────────────────────────────────────────────────
class ExpiryAlertSerializer(serializers.ModelSerializer):
    product_serial = serializers.CharField(source="product.serial_number", read_only=True)
    product_name = serializers.CharField(source="product.product_name", read_only=True)
    hospital_name = serializers.CharField(source="product.department.hospital.name", read_only=True)
    department_name = serializers.CharField(source="product.department.name", read_only=True)
    expiry_date = serializers.DateField(source="product.expiry_date", read_only=True)

    class Meta:
        model = ExpiryAlert
        fields = [
            "id",
            "product",
            "product_serial",
            "product_name",
            "hospital_name",
            "department_name",
            "expiry_date",
            "tier",
            "status",
            "days_until_expiry",
            "notified_at",
            "acknowledged_by",
            "acknowledged_at",
            "action_notes",
        ]
        read_only_fields = ["id", "notified_at"]


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING SESSION & SCAN LOG
# ─────────────────────────────────────────────────────────────────────────────
class ScanLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScanLog
        fields = [
            "id",
            "session",
            "product",
            "action",
            "serial_number",
            "gtin_code",
            "material_code",
            "product_name",
            "batch_number",
            "expiry_date",
            "production_date",
            "quantity",
            "reference_number",
            "qr_data",
            "scanned_at",
            "notes",
        ]
        read_only_fields = ["id", "scanned_at"]


class SessionAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = SessionAttachment
        fields = [
            "id",
            "session",
            "original_filename",
            "content_type",
            "file_size",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
            "notes",
            "download_url",
        ]
        read_only_fields = ["id", "uploaded_at", "uploaded_by", "uploaded_by_name"]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return "Unknown"

    def get_download_url(self, obj):
        return f"/meritrack/api/sessions/download_attachment/?session_key={obj.session.session_key}"


class ScanningSessionSerializer(serializers.ModelSerializer):
    hospital_details = HospitalListSerializer(source="hospital", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    to_hospital_name = serializers.CharField(source="to_hospital.name", read_only=True, default=None)
    rep_details = UserSerializer(source="rep", read_only=True)
    scan_logs = serializers.SerializerMethodField()
    attachment_details = serializers.SerializerMethodField()
    total_scans = serializers.SerializerMethodField()

    class Meta:
        model = ScanningSession
        fields = [
            "id",
            "session_key",
            "session_type",
            "hospital",
            "department",
            "hospital_details",
            "department_name",
            "to_hospital",
            "to_hospital_name",
            "to_department",
            "rep",
            "rep_details",
            "status",
            "notes",
            "started_at",
            "completed_at",
            "last_scan_time",
            "scan_logs",
            "total_scans",
            "attachment_details",
        ]
        read_only_fields = ["id", "session_key", "started_at", "completed_at"]

    def _visible_scan_logs(self, obj):
        """Scans this response should report for the session.

        Terminating a session writes the visit off, so its scans must stop
        counting as activity — the same rule `active_scan_logs()` applies to
        scan lists, exports and rep performance. Collection responses set
        `include_written_off_scans` False so a terminated visit reports zero;
        detail responses leave it on, keeping the session auditable.

        Reads the prefetched relation rather than issuing a COUNT per row.
        """
        if obj.status == "cancelled" and not self.context.get("include_written_off_scans", True):
            return []
        return list(obj.scan_logs.all())

    def get_scan_logs(self, obj):
        return ScanLogSerializer(self._visible_scan_logs(obj), many=True).data

    def get_total_scans(self, obj):
        return len(self._visible_scan_logs(obj))

    def get_attachment_details(self, obj):
        try:
            return SessionAttachmentSerializer(obj.attachment).data
        except SessionAttachment.DoesNotExist:
            return None


class TransferredProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransferredProduct
        fields = [
            "id",
            "transfer_session",
            "consigned_product",
            "serial_number",
            "gtin_code",
            "material_code",
            "product_name",
            "batch_number",
            "expiry_date",
            "production_date",
            "quantity",
            "qr_data",
            "scanned_at",
            "notes",
        ]
        read_only_fields = ["id", "scanned_at"]


class TransferAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TransferAttachment
        fields = [
            "id",
            "transfer_session",
            "original_filename",
            "content_type",
            "file_size",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
            "notes",
        ]
        read_only_fields = ["id", "uploaded_at", "uploaded_by", "uploaded_by_name"]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return "Unknown"


class TransferSessionSerializer(serializers.ModelSerializer):
    from_hospital_details = HospitalListSerializer(source="from_hospital", read_only=True)
    to_hospital_details = HospitalListSerializer(source="to_hospital", read_only=True)
    initiated_by_details = UserSerializer(source="initiated_by", read_only=True)
    transferred_products = TransferredProductSerializer(many=True, read_only=True)
    total_products = serializers.SerializerMethodField()
    attachment_details = serializers.SerializerMethodField()

    class Meta:
        model = TransferSession
        fields = [
            "id",
            "session_key",
            "transfer_id",
            "from_hospital",
            "from_department",
            "to_hospital",
            "to_department",
            "initiated_by",
            "from_hospital_details",
            "to_hospital_details",
            "initiated_by_details",
            "started_at",
            "completed_at",
            "last_scan_time",
            "status",
            "notes",
            "transferred_products",
            "total_products",
            "attachment_details",
        ]
        read_only_fields = ["id", "session_key", "started_at", "completed_at"]

    def get_total_products(self, obj):
        return obj.transferred_products.count()

    def get_attachment_details(self, obj):
        try:
            return TransferAttachmentSerializer(obj.attachment).data
        except TransferAttachment.DoesNotExist:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# STOCK ORDERS
# ─────────────────────────────────────────────────────────────────────────────
class StockOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="catalog_product.name", read_only=True)
    product_size = serializers.CharField(source="catalog_product.size", read_only=True)

    class Meta:
        model = StockOrderItem
        fields = ["id", "order", "catalog_product", "product_name", "product_size", "quantity", "notes"]
        read_only_fields = ["id"]


class StockOrderSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    ordered_by_name = serializers.SerializerMethodField()
    source_session_key = serializers.CharField(source="source_session.session_key", read_only=True, default=None)
    items = StockOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = StockOrder
        fields = [
            "id",
            "order_id",
            "hospital",
            "hospital_name",
            "department",
            "department_name",
            "ordered_by",
            "ordered_by_name",
            "source_session_key",
            "urgency",
            "status",
            "notes",
            "items",
            "created_at",
            "updated_at",
            "confirmed_at",
            "shipped_at",
            "delivered_at",
        ]
        read_only_fields = ["id", "order_id", "created_at", "updated_at"]

    def get_ordered_by_name(self, obj):
        if obj.ordered_by:
            return obj.ordered_by.get_full_name() or obj.ordered_by.username
        return None


# ─────────────────────────────────────────────────────────────────────────────
# USAGE RECORD
# ─────────────────────────────────────────────────────────────────────────────
class UsageRecordSerializer(serializers.ModelSerializer):
    product_serial = serializers.CharField(source="product.serial_number", read_only=True)
    product_name = serializers.CharField(source="product.product_name", read_only=True)
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = UsageRecord
        fields = [
            "id",
            "product",
            "product_serial",
            "product_name",
            "hospital",
            "hospital_name",
            "department",
            "department_name",
            "used_date",
            "recorded_by",
            "surgeon_name",
            "procedure_type",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────
class NotificationSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True, default=None)
    session_key = serializers.CharField(source="session.session_key", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = ["id", "category", "title", "message", "hospital_name", "session_key", "is_read", "created_at"]
        read_only_fields = ["id", "category", "title", "message", "created_at"]


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────
class AuditLogSerializer(serializers.ModelSerializer):
    performed_by_name = serializers.SerializerMethodField()
    session_key = serializers.CharField(source="session.session_key", read_only=True, default=None)
    hospital_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "description",
            "performed_by",
            "performed_by_name",
            "session",
            "session_key",
            "hospital_name",
            "product",
            "old_status",
            "new_status",
            "ip_address",
            "timestamp",
        ]
        read_only_fields = fields

    def get_performed_by_name(self, obj):
        if obj.performed_by:
            return obj.performed_by.get_full_name() or obj.performed_by.username
        return "System"

    def get_hospital_name(self, obj):
        if obj.session and obj.session.hospital:
            return obj.session.hospital.name
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNMENT POSTING (SAP movement history)
# ─────────────────────────────────────────────────────────────────────────────
class ConsignmentPostingSerializer(serializers.ModelSerializer):
    hospital_name = serializers.SerializerMethodField()
    catalog_product_name = serializers.SerializerMethodField()
    movement_type_display = serializers.CharField(read_only=True)

    class Meta:
        model = ConsignmentPosting
        fields = [
            "id",
            "plant",
            "storage_location",
            "posting_date",
            "entry_date",
            "entry_time",
            "movement_type",
            "movement_type_text",
            "movement_type_display",
            "material_document",
            "material_doc_item",
            "material_doc_year",
            "material",
            "material_description",
            "batch",
            "quantity",
            "base_uom",
            "customer",
            "customer_name",
            "material_type",
            "material_type_description",
            "material_group",
            "material_group_description",
            "delivery",
            "user_name",
            "document_header_text",
            "receiving_plant",
            "sending_plant",
            "amount_lc",
            "currency",
            "hospital",
            "hospital_name",
            "catalog_product",
            "catalog_product_name",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "movement_type_display"]

    def get_hospital_name(self, obj):
        return obj.hospital.name if obj.hospital else None

    def get_catalog_product_name(self, obj):
        return str(obj.catalog_product) if obj.catalog_product else None


class ExpiredProductReportSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    removed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ExpiredProductReport
        fields = [
            "id",
            "report_id",
            "hospital",
            "hospital_name",
            "department",
            "department_name",
            "scanning_session",
            "serial_number",
            "gtin_code",
            "material_code",
            "product_name",
            "batch_number",
            "expiry_date",
            "reason",
            "status",
            "removed_at",
            "removed_by",
            "removed_by_name",
            "removal_notes",
            "regulatory_action_taken",
            "sap_transaction_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "report_id", "created_at", "updated_at"]

    def get_removed_by_name(self, obj):
        if obj.removed_by:
            return obj.removed_by.get_full_name() or obj.removed_by.username
        return None


class ScanningBatchSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ScanningBatch
        fields = [
            "id",
            "batch_id",
            "hospital",
            "hospital_name",
            "batch_type",
            "description",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


class ScannedProductRemarkSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ScannedProductRemark
        fields = ["id", "scan_log", "remark", "role", "created_by", "created_by_name", "created_at"]
        read_only_fields = ["id", "created_by", "created_by_name", "created_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return "Unknown"


class ScanSessionRemarkSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ScanSessionRemark
        fields = ["id", "session", "remark", "role", "created_by", "created_by_name", "created_at"]
        read_only_fields = ["id", "created_by", "created_by_name", "created_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────
class ReconciliationItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationItem
        fields = [
            "id", "product_name", "gtin_code", "material_code", "batch_number",
            "serial_number", "barcode_data", "match_status", "expected_quantity",
            "found_quantity", "expiry_date", "notes",
        ]


class InventoryReconciliationSerializer(serializers.ModelSerializer):
    """List/summary representation (no items) for the dashboard."""

    hospital_name = serializers.CharField(read_only=True)
    performed_by_name = serializers.CharField(read_only=True)
    session_key = serializers.CharField(source="session.session_key", read_only=True, default=None)

    class Meta:
        model = InventoryReconciliation
        fields = [
            "id", "recon_id", "hospital", "hospital_name", "department",
            "session", "session_key", "recon_type", "status",
            "total_expected", "total_scanned", "matched", "missing", "extra",
            "expired_found", "accuracy_rate", "discrepancy_rate",
            "performed_by_name", "notes", "created_at", "completed_at",
        ]


class InventoryReconciliationDetailSerializer(InventoryReconciliationSerializer):
    """Detail representation including per-item breakdown."""

    items = ReconciliationItemSerializer(many=True, read_only=True)

    class Meta(InventoryReconciliationSerializer.Meta):
        fields = InventoryReconciliationSerializer.Meta.fields + ["items"]
