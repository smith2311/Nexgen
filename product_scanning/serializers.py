from django.contrib.auth.models import User

from rest_framework import serializers

from .models import (
    AuditLog,
    Batch,
    ExpiredProductReport,
    GTINMaterialMapping,
    Hospital,
    Notification,
    QRTemplate,
    ScannedProduct,
    ScannedProductRemark,
    ScanningSession,
    ScanSessionRemark,
    SessionAttachment,
    TransferAttachment,
    TransferredProduct,
    TransferSession,
)


class HospitalSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = [
            "id",
            "name",
            "sap_code",
            "province",
            "location",
            "contact_person",
            "phone",
            "email",
            "salesperson_email",
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


class GTINMaterialMappingSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = GTINMaterialMapping
        fields = [
            "id",
            "material_code",
            "brand",
            "product_desc",
            "material_description",
            "control_code",
            "gtin_code",
            "material_type_desc",
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


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email"]


class BatchSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Batch
        fields = [
            "id",
            "batch_id",
            "hospital",
            "hospital_name",
            "created_by",
            "created_by_name",
            "description",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return "Unknown"


class QRTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = QRTemplate
        fields = ["id", "name", "scan_type", "description"]


class ScannedProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScannedProduct
        fields = [
            "id",
            "serial_number",
            "gtin_code",
            "material_code",
            "product_name",
            "batch_number",
            "received_date",
            "expiry_date",
            "production_date",
            "best_before_date",
            "packaging_date",
            "reference_number",
            "quantity",
            "qr_data",
            "gs1_parsed_data",
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
        return f"/product_scanning/api/sessions/download_attachment/?session_key={obj.session.session_key}"


class ScanningSessionSerializer(serializers.ModelSerializer):
    batch_details = BatchSerializer(source="batch", read_only=True)
    hospital_details = HospitalSerializer(source="hospital", read_only=True)
    to_hospital_details = HospitalSerializer(source="to_hospital", read_only=True)
    scanned_by_details = UserSerializer(source="scanned_by", read_only=True)
    scanned_products = ScannedProductSerializer(many=True, read_only=True)
    attachment_details = serializers.SerializerMethodField()
    total_scans = serializers.SerializerMethodField()
    has_warehouse_remark = serializers.SerializerMethodField()
    total_remarks = serializers.SerializerMethodField()
    days_awaiting = serializers.SerializerMethodField()

    class Meta:
        model = ScanningSession
        fields = [
            "id",
            "batch",
            "hospital",
            "to_hospital",
            "scanned_by",
            "session_type",
            "batch_details",
            "hospital_details",
            "to_hospital_details",
            "scanned_by_details",
            "started_at",
            "completed_at",
            "on_hold_at",
            "status",
            "session_key",
            "notes",
            "scanned_products",
            "total_scans",
            "attachment_details",
            "has_warehouse_remark",
            "total_remarks",
            "days_awaiting",
        ]
        read_only_fields = ["id", "started_at", "completed_at", "on_hold_at", "session_key"]

    def get_total_scans(self, obj):
        return obj.scanned_products.count()

    def get_attachment_details(self, obj):
        try:
            att = obj.attachment
            return SessionAttachmentSerializer(att).data
        except SessionAttachment.DoesNotExist:
            return None

    def get_has_warehouse_remark(self, obj):
        if obj.session_remarks.filter(role="warehouse").exists():
            return True
        return ScannedProductRemark.objects.filter(scanned_product__scanning_session=obj, role="warehouse").exists()

    def get_total_remarks(self, obj):
        session_count = obj.session_remarks.count()
        product_count = ScannedProductRemark.objects.filter(scanned_product__scanning_session=obj).count()
        return session_count + product_count

    def get_days_awaiting(self, obj):
        """Days the session has been in completed/on_hold status (since completed_at)."""
        if obj.completed_at and obj.status in ("completed", "on_hold"):
            from django.utils import timezone
            return (timezone.now() - obj.completed_at).days
        return None


class NotificationSerializer(serializers.ModelSerializer):
    session_key = serializers.CharField(source="session.session_key", read_only=True)
    batch_id = serializers.CharField(source="session.batch.batch_id", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "category",
            "title",
            "message",
            "session_key",
            "batch_id",
            "is_read",
            "created_at",
        ]
        read_only_fields = ["id", "category", "title", "message", "session_key", "batch_id", "created_at"]


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCT REPORT SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductReportSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    hospital_sap_code = serializers.CharField(source="hospital.sap_code", read_only=True)
    scanned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ExpiredProductReport
        fields = [
            "id",
            "serial_number",
            "gtin_code",
            "product_name",
            "batch_number",
            "material_code",
            "expiry_date",
            "days_expired",
            "hospital",
            "hospital_name",
            "hospital_sap_code",
            "session_batch_id",
            "scanned_by",
            "scanned_by_name",
            "scanned_at",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_scanned_by_name(self, obj):
        if obj.scanned_by:
            return obj.scanned_by.get_full_name() or obj.scanned_by.username
        return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# H2H TRANSFER SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────
class TransferredProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransferredProduct
        fields = [
            "id",
            "serial_number",
            "gtin_code",
            "material_code",
            "product_name",
            "batch_number",
            "expiry_date",
            "production_date",
            "best_before_date",
            "packaging_date",
            "reference_number",
            "quantity",
            "qr_data",
            "scanned_at",
            "notes",
        ]
        read_only_fields = ["id", "scanned_at"]


class TransferAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

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
            "download_url",
        ]
        read_only_fields = ["id", "uploaded_at", "uploaded_by", "uploaded_by_name"]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return "Unknown"

    def get_download_url(self, obj):
        return f"/product_scanning/api/transfers/download_attachment/?session_key={obj.transfer_session.session_key}"


class TransferSessionSerializer(serializers.ModelSerializer):
    from_hospital_details = HospitalSerializer(source="from_hospital", read_only=True)
    to_hospital_details = HospitalSerializer(source="to_hospital", read_only=True)
    initiated_by_details = UserSerializer(source="initiated_by", read_only=True)
    transferred_products = TransferredProductSerializer(many=True, read_only=True)
    attachment_details = serializers.SerializerMethodField()
    total_products = serializers.SerializerMethodField()

    class Meta:
        model = TransferSession
        fields = [
            "id",
            "session_key",
            "transfer_id",
            "from_hospital",
            "to_hospital",
            "initiated_by",
            "from_hospital_details",
            "to_hospital_details",
            "initiated_by_details",
            "started_at",
            "completed_at",
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
            att = obj.attachment
            return TransferAttachmentSerializer(att).data
        except TransferAttachment.DoesNotExist:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG SERIALIZER
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
# REMARK SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────


class ScannedProductRemarkSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    product_info = serializers.SerializerMethodField()

    class Meta:
        model = ScannedProductRemark
        fields = ["id", "scanned_product", "remark", "role", "created_by", "created_by_name", "created_at", "product_info"]
        read_only_fields = ["id", "created_by", "created_by_name", "created_at", "product_info"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return "Unknown"

    def get_product_info(self, obj):
        p = obj.scanned_product
        return {
            "serial_number": p.serial_number,
            "gtin_code": p.gtin_code or "",
            "material_code": p.material_code or "",
            "product_name": p.product_name or "",
        }


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
