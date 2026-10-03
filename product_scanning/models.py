from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Hospital(models.Model):
    """Model to store hospital information"""

    name = models.CharField(max_length=255, unique=True)
    sap_code = models.CharField(max_length=50, blank=True, null=True, unique=True)  # Customer Code from SAP
    province = models.CharField(max_length=100, blank=True, null=True)  # Province/State
    location = models.CharField(max_length=255, blank=True, null=True)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    salesperson_email = models.EmailField(
        blank=True, null=True, help_text="Email of the Meril salesperson responsible for this hospital"
    )
    updated_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hospital_updates",
        help_text="Last user to update this record",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Hospitals"
        indexes = [
            models.Index(fields=["sap_code"]),
            models.Index(fields=["province"]),
        ]

    def __str__(self):
        return self.name


class GTINMaterialMapping(models.Model):
    """
    Maps a GTIN (barcode) to a Meril SAP material code.
    Used during scanning to auto-populate material_code when the barcode
    carries a GTIN but no material code.
    Upload columns: Material, Brand, Product Desc, Material Description,
                    Control code, GTIN, Material type descr.
    """

    gtin_code = models.CharField(
        max_length=50,
        unique=True,
        help_text="GTIN as printed on the barcode",
    )
    material_code = models.CharField(
        max_length=100,
        help_text="Meril SAP material code",
    )
    brand = models.CharField(max_length=255, blank=True, null=True)
    product_desc = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Product description",
    )
    material_description = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Material description",
    )
    control_code = models.CharField(max_length=100, blank=True, null=True)
    material_type_desc = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Material type description",
    )
    # Legacy field kept for backward compat; new imports write to product_desc
    product_name = models.CharField(max_length=255, blank=True, null=True)
    updated_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gtin_mapping_updates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["gtin_code"]
        verbose_name = "GTIN Material Mapping"
        verbose_name_plural = "GTIN Material Mappings"
        indexes = [
            models.Index(fields=["gtin_code"]),
            models.Index(fields=["material_code"]),
        ]

    def __str__(self):
        return f"{self.gtin_code} → {self.material_code}"


class Batch(models.Model):
    """Model to store batch information - groups multiple scanning sessions together"""

    batch_id = models.CharField(max_length=100, unique=True)  # Unique batch identifier
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="batches")
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_batches"
    )
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["batch_id", "hospital"]

    def __str__(self):
        return f"Batch {self.batch_id} - {self.hospital.name}"


class ScanningSession(models.Model):
    """
    Model to track a scanning session at a hospital for a specific batch.
    This allows scanning multiple products without re-entering info.
    """

    STATUS_CHOICES = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
        ("on_hold", "On Hold"),
    ]

    SESSION_TYPE_CHOICES = [
        ("normal", "Normal Scan"),
        ("transfer", "Hospital-to-Hospital Transfer"),
    ]

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="scanning_sessions")
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="scanning_sessions")
    # For transfer sessions: hospital = source (From Hospital), to_hospital = destination (To Hospital)
    to_hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_transfer_sessions",
        help_text="Destination hospital for H2H transfer sessions",
    )
    session_type = models.CharField(
        max_length=20,
        choices=SESSION_TYPE_CHOICES,
        default="normal",
        help_text="normal = stock audit at hospital; transfer = H2H stock movement",
    )
    scanned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="scanning_sessions"
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    on_hold_at = models.DateTimeField(null=True, blank=True)
    notify_overdue_from = models.DateTimeField(null=True, blank=True)
    submission_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set once when the 'batch submitted' notification fires; blocks duplicate sends.",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    notes = models.TextField(blank=True, null=True)
    session_key = models.CharField(max_length=100, unique=True)  # For frontend identification
    last_scan_time = models.DateTimeField(null=True, blank=True)  # Track last scan time for duplicate prevention

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        user_name = self.scanned_by.get_full_name() or self.scanned_by.username if self.scanned_by else "Unknown"
        return (
            f"{user_name} - {self.hospital.name} - {self.batch.batch_id} ({self.started_at.strftime('%Y-%m-%d %H:%M')})"
        )

    def mark_completed(self):
        """Mark session as completed"""
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save()

    def mark_closed(self):
        """Mark session as closed (attachment uploaded)"""
        self.status = "closed"
        self.save()

    def mark_on_hold(self):
        """Put session on hold — pauses overdue notifications."""
        self.status = "on_hold"
        self.on_hold_at = timezone.now()
        self.save(update_fields=["status", "on_hold_at"])

    def resume_from_hold(self):
        """Resume a held session — notifications restart from the very next cron run."""
        from datetime import timedelta
        self.status = "completed"
        self.on_hold_at = None
        # Back-date the overdue reference so the next daily cron fires immediately.
        self.notify_overdue_from = timezone.now() - timedelta(days=6)
        self.save(update_fields=["status", "on_hold_at", "notify_overdue_from"])
        # Clear today's log entry so dedup doesn't block tomorrow's send.
        self.overdue_logs.filter(notified_on=timezone.localdate()).delete()


class SessionAttachment(models.Model):
    """Confirmation document attached to a completed session to close it.
    The file content is stored directly in the database (BinaryField)."""

    session = models.OneToOneField(
        ScanningSession,
        on_delete=models.CASCADE,
        related_name="attachment",
    )
    file_data = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=100, default="application/octet-stream")  # e.g. application/pdf
    file_size = models.PositiveIntegerField(default=0)  # bytes
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Attachment for {self.session} — {self.original_filename}"


class QRTemplate(models.Model):
    """
    Model to define QR code templates/patterns for scanning.
    Kept for backward compatibility - not actively used in new workflow.
    """

    SCAN_TYPE_CHOICES = [
        ("single", "Single QR Code"),
        ("dual", "Dual QR Code (Serial + Details)"),
    ]

    name = models.CharField(max_length=255)
    scan_type = models.CharField(max_length=20, choices=SCAN_TYPE_CHOICES, default="single")
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.scan_type})"


class ScannedProduct(models.Model):
    """
    Model to store scanned product information from QR codes.
    Tracks what products are with hospital and expiry dates for inventory purposes.
    """

    scanning_session = models.ForeignKey(ScanningSession, on_delete=models.CASCADE, related_name="scanned_products")

    # Primary identifiers
    serial_number = models.CharField(max_length=255)  # Unique per session (not globally unique)
    gtin_code = models.CharField(max_length=100, blank=True, null=True)  # GTIN/EAN code
    material_code = models.CharField(max_length=255, blank=True, null=True)

    # Product details (for tracking only, not for billing)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)  # GS1 AI 10 — Lot/Batch No.
    received_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)  # GS1 AI 17
    production_date = models.DateField(blank=True, null=True)  # GS1 AI 11 — Manufactured date
    best_before_date = models.DateField(blank=True, null=True)  # GS1 AI 15
    packaging_date = models.DateField(blank=True, null=True)  # GS1 AI 13
    reference_number = models.CharField(
        max_length=255, blank=True, null=True, help_text="Reference/additional ID from QR (GS1 AI 240/22)"
    )
    quantity = models.IntegerField(default=1, blank=True, null=True)

    # QR Scanning details
    qr_data = models.TextField(blank=True, null=True)  # Raw QR data
    gs1_parsed_data = models.JSONField(blank=True, null=True)  # Full GS1 parsed fields (all AIs)

    # Metadata
    scanned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-scanned_at"]
        # Composite identity: same serial is allowed under a different GTIN/material
        # (serials are scoped to a GTIN). NULL gtin/material aren't enforced by the
        # DB, so the app-level composite check is the primary guard.
        unique_together = [("scanning_session", "serial_number", "gtin_code", "material_code")]
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["gtin_code"]),
            models.Index(fields=["scanning_session"]),
        ]

    def __str__(self):
        return f"{self.serial_number} - {self.product_name or 'Product'}"


class Notification(models.Model):
    """
    In-app + email notification for warehouse managers when a salesperson
    submits (completes) a scanning session.
    """

    CATEGORY_BATCH_SUBMITTED = "batch_submitted"
    CATEGORY_CHOICES = [
        (CATEGORY_BATCH_SUBMITTED, "Batch Submitted"),
    ]

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="ps_notifications",
    )
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_BATCH_SUBMITTED)
    title = models.CharField(max_length=255)
    message = models.TextField()
    session = models.ForeignKey(
        ScanningSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"[{self.category}] → {self.recipient.username}: {self.title}"


class AuditLog(models.Model):
    """
    Model to track all scanning session activities for audit trail.
    Records session terminations, rejections, force submissions, and other important actions.
    """

    ACTION_CHOICES = [
        ("session_started", "Session Started"),
        ("session_completed", "Session Completed"),
        ("session_force_completed", "Session Force Completed"),
        ("session_terminated", "Session Terminated"),
        ("session_rejected", "Session Rejected"),
        ("product_scanned", "Product Scanned"),
        ("product_removed", "Product Removed"),
        ("attachment_uploaded", "Attachment Uploaded"),
        ("session_exported", "Session Exported"),
        ("scan_deleted", "Scan Deleted"),
        ("session_on_hold", "Session Put On Hold"),
        ("session_resumed", "Session Resumed from Hold"),
    ]

    session = models.ForeignKey(
        ScanningSession,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
    )
    description = models.TextField(blank=True, null=True)  # Additional details about the action
    old_status = models.CharField(max_length=20, blank=True, null=True)  # Previous status if applicable
    new_status = models.CharField(max_length=20, blank=True, null=True)  # New status if applicable
    ip_address = models.GenericIPAddressField(null=True, blank=True)  # Client IP for security audit
    user_agent = models.TextField(blank=True, null=True)  # Browser/client info
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name_plural = "Audit Logs"
        indexes = [
            models.Index(fields=["session", "action"]),
            models.Index(fields=["performed_by"]),
            models.Index(fields=["timestamp"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.performed_by.username if self.performed_by else 'Unknown'} on {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCTS REPORT
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductReport(models.Model):
    """
    Consolidated report of expired products from submitted (completed/closed)
    scanning sessions. Populated automatically when a session is completed.
    """

    # Link to original scanned product
    scanned_product = models.OneToOneField(
        ScannedProduct,
        on_delete=models.CASCADE,
        related_name="expired_report",
    )
    # Denormalized fields for reporting convenience
    serial_number = models.CharField(max_length=255)
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    expiry_date = models.DateField()
    days_expired = models.IntegerField(default=0)  # How many days past expiry

    # Hospital & session context
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="expired_products")
    session = models.ForeignKey(ScanningSession, on_delete=models.CASCADE, related_name="expired_products")
    session_batch_id = models.CharField(max_length=100)

    # Who reported / scanned it
    scanned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expired_product_reports",
    )
    scanned_at = models.DateTimeField()

    # Report metadata
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged = models.BooleanField(default=False)  # warehouse can acknowledge/action
    acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_expired_products",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-expiry_date", "-created_at"]
        verbose_name = "Expired Product Report"
        verbose_name_plural = "Expired Product Reports"
        indexes = [
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["hospital"]),
            models.Index(fields=["acknowledged"]),
            models.Index(fields=["serial_number"]),
        ]

    def __str__(self):
        return f"EXPIRED: {self.serial_number} @ {self.hospital.name} (exp: {self.expiry_date})"


# ─────────────────────────────────────────────────────────────────────────────
# HOSPITAL-TO-HOSPITAL (H2H) STOCK TRANSFER — SEPARATE MODULE
# ─────────────────────────────────────────────────────────────────────────────
class TransferSession(models.Model):
    """
    A stock transfer session between two hospitals.
    Separate from ScanningSession to keep the transfer workflow isolated.
    """

    STATUS_CHOICES = [
        ("active", "Active"),  # Transfer in progress
        ("completed", "Completed"),  # Submitted by salesperson, awaiting confirmation
        ("closed", "Closed"),  # Confirmed with attachment
        ("cancelled", "Cancelled"),
    ]

    session_key = models.CharField(max_length=100, unique=True)
    transfer_id = models.CharField(max_length=100, unique=True)  # e.g. DDMMYY-Initials-FROM-TO-TOCODE

    from_hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name="outgoing_transfers",
        help_text="Source hospital (stock leaving)",
    )
    to_hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name="incoming_transfers",
        help_text="Destination hospital (stock arriving)",
    )

    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_transfers",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    notes = models.TextField(blank=True, null=True)
    last_scan_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Stock Transfer Session"
        verbose_name_plural = "Stock Transfer Sessions"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["from_hospital"]),
            models.Index(fields=["to_hospital"]),
        ]

    def __str__(self):
        user = self.initiated_by.get_full_name() if self.initiated_by else "Unknown"
        return f"{user}: {self.from_hospital.name} → {self.to_hospital.name} ({self.transfer_id})"

    def mark_completed(self):
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save()

    def mark_closed(self):
        self.status = "closed"
        self.save()


class TransferredProduct(models.Model):
    """
    A product scanned during a H2H stock transfer session.
    """

    transfer_session = models.ForeignKey(
        TransferSession,
        on_delete=models.CASCADE,
        related_name="transferred_products",
    )

    # Product identification
    serial_number = models.CharField(max_length=255)
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)

    # Dates
    expiry_date = models.DateField(blank=True, null=True)
    production_date = models.DateField(blank=True, null=True)
    best_before_date = models.DateField(blank=True, null=True)
    packaging_date = models.DateField(blank=True, null=True)
    reference_number = models.CharField(
        max_length=255, blank=True, null=True, help_text="Reference/additional ID from QR (GS1 AI 240/22)"
    )

    quantity = models.IntegerField(default=1)
    qr_data = models.TextField(blank=True, null=True)

    scanned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-scanned_at"]
        # Composite identity: same serial allowed under a different GTIN/material.
        unique_together = [("transfer_session", "serial_number", "gtin_code", "material_code")]
        verbose_name = "Transferred Product"
        verbose_name_plural = "Transferred Products"
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["gtin_code"]),
            models.Index(fields=["transfer_session"]),
        ]

    def __str__(self):
        return f"{self.serial_number} ({self.transfer_session.transfer_id})"


class TransferAttachment(models.Model):
    """
    Confirmation document for a completed H2H transfer.
    """

    transfer_session = models.OneToOneField(
        TransferSession,
        on_delete=models.CASCADE,
        related_name="attachment",
    )
    file_data = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    file_size = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Transfer Attachment"
        verbose_name_plural = "Transfer Attachments"

    def __str__(self):
        return f"Attachment: {self.transfer_session.transfer_id}"


# ─────────────────────────────────────────────────────────────────────────────
# SALESPERSON PERFORMANCE DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────


class MonthlyReminderLog(models.Model):
    """
    Records when a 20th-of-month reminder email was sent to a salesperson
    for pending hospital visits. Prevents duplicate notifications.
    """

    salesperson_email = models.EmailField(max_length=254)
    salesperson_name = models.CharField(max_length=255, blank=True, default="")
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()  # 1-12
    sent_at = models.DateTimeField(auto_now_add=True)
    pending_hospitals_count = models.PositiveIntegerField(default=0)
    pending_hospital_names = models.TextField(blank=True, null=True)  # JSON list

    class Meta:
        unique_together = [("salesperson_email", "year", "month")]
        ordering = ["-year", "-month"]
        verbose_name = "Monthly Reminder Log"
        verbose_name_plural = "Monthly Reminder Logs"
        indexes = [
            models.Index(fields=["salesperson_email", "year", "month"]),
            models.Index(fields=["year", "month"]),
        ]

    def __str__(self):
        return (
            f"Reminder → {self.salesperson_email} "
            f"{self.year}-{self.month:02d} "
            f"({self.pending_hospitals_count} pending)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# REMARKS — Product-wise & Batch-level (salesperson ↔ warehouse dialogue)
# ─────────────────────────────────────────────────────────────────────────────


class ScannedProductRemark(models.Model):
    """
    A remark attached to a single scanned product.

    Salesperson adds a remark while scanning (role='salesperson').
    Warehouse person can reply (role='warehouse') — adding a remark here
    triggers an email notification to the session's salesperson.
    """

    ROLE_CHOICES = [
        ("salesperson", "Salesperson"),
        ("warehouse", "Warehouse"),
    ]

    scanned_product = models.ForeignKey(
        ScannedProduct,
        on_delete=models.CASCADE,
        related_name="remarks",
    )
    remark = models.TextField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_remarks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Scanned Product Remark"
        verbose_name_plural = "Scanned Product Remarks"
        indexes = [
            models.Index(fields=["scanned_product"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        user = self.created_by.get_full_name() or self.created_by.username if self.created_by else "Unknown"
        return f"[{self.role}] {user} on {self.scanned_product.serial_number}: {self.remark[:60]}"


class ScanSessionRemark(models.Model):
    """
    A batch-level remark for an entire scanning session.

    Warehouse person adds a remark for a session/batch.
    Triggers an email notification to the session's salesperson.
    Salesperson can also add session-level notes here.
    """

    ROLE_CHOICES = [
        ("salesperson", "Salesperson"),
        ("warehouse", "Warehouse"),
    ]

    session = models.ForeignKey(
        ScanningSession,
        on_delete=models.CASCADE,
        related_name="session_remarks",
    )
    remark = models.TextField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session_remarks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Scan Session Remark"
        verbose_name_plural = "Scan Session Remarks"
        indexes = [
            models.Index(fields=["session"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        user = self.created_by.get_full_name() or self.created_by.username if self.created_by else "Unknown"
        return f"[{self.role}] {user} on session {self.session.session_key}: {self.remark[:60]}"


class AwaitingOverdueLog(models.Model):
    """
    Tracks which sessions have already received an overdue-awaiting notification
    on a given date, so the daily cron does not double-send.
    """

    session = models.ForeignKey(
        ScanningSession,
        on_delete=models.CASCADE,
        related_name="overdue_logs",
    )
    notified_on = models.DateField()

    class Meta:
        unique_together = [("session", "notified_on")]
        verbose_name = "Awaiting Overdue Log"
        verbose_name_plural = "Awaiting Overdue Logs"

    def __str__(self):
        return f"Overdue log: session {self.session.session_key} on {self.notified_on}"


# ── Feedback / Bug Report ────────────────────────────────────────────────────


class Feedback(models.Model):
    """
    Platform-wide feedback / bug report ticket.

    The 'module' field is intentionally free-form (no hardcoded choices).
    Module options are managed by the FeedbackModuleRegistry — each app
    self-registers in its AppConfig.ready().  Adding a new NexGen app only
    requires 3 lines in that app's AppConfig; no changes here, no migrations.

    See: product_scanning/feedback_registry.py
    """

    CATEGORY_CHOICES = [
        ("bug", "Bug Report"),
        ("feature", "Feature Request"),
        ("ux", "UI / UX Issue"),
        ("performance", "Performance Problem"),
        ("data", "Data / Sync Issue"),
        ("other", "General Feedback"),
    ]
    SEVERITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    feedback_id = models.CharField(max_length=20, unique=True, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="bug")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default="medium")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    # No choices= here — registry drives the options; no migration needed when apps are added
    module = models.CharField(
        max_length=30,
        default="other",
        blank=True,
        help_text="App/module this feedback relates to (populated from feedback_registry)",
    )
    screenshot = models.ImageField(
        upload_to="feedback/screenshots/",
        blank=True,
        null=True,
        help_text="Optional screenshot to illustrate the issue",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="submitted_feedbacks",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_feedbacks",
        help_text="Admin/superuser handling this ticket",
    )
    is_read_by_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Feedback"
        verbose_name_plural = "Feedback"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["category"]),
            models.Index(fields=["submitted_by"]),
        ]

    def save(self, *args, **kwargs):
        if not self.feedback_id:
            import random

            ts = timezone.now().strftime("%y%m")
            rand = random.randint(1000, 9999)
            self.feedback_id = f"FB-{ts}-{rand}"
            # Ensure uniqueness
            while Feedback.objects.filter(feedback_id=self.feedback_id).exists():
                rand = random.randint(1000, 9999)
                self.feedback_id = f"FB-{ts}-{rand}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.feedback_id}] {self.title}"

    @property
    def submitter_name(self):
        if self.submitted_by:
            return self.submitted_by.get_full_name() or self.submitted_by.username
        return "Unknown"

    @property
    def submitter_role_label(self):
        if not self.submitted_by:
            return "Unknown"
        user = self.submitted_by
        if user.is_superuser:
            return "Super Admin"
        groups = list(user.groups.values_list("name", flat=True))
        if "PS Warehouse" in groups:
            return "Warehouse"
        if "PS Salesperson" in groups:
            return "Salesperson"
        if "Product Scanner" in groups:
            return "Scanner"
        return groups[0] if groups else "User"

    @property
    def module_label(self):
        """Human-readable module name looked up from the registry at runtime."""
        try:
            from product_scanning.feedback_registry import get_label
            return get_label(self.module)
        except Exception:
            return (self.module or "").replace("_", " ").title()


class FeedbackReply(models.Model):
    """
    A reply on a Feedback ticket.
    Admins/superusers can reply; the submitter gets an email notification.
    Submitters can also post follow-up messages.
    is_internal=True means only admins see the note (not emailed to submitter).
    """

    feedback = models.ForeignKey(
        Feedback,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    replied_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="feedback_replies",
    )
    message = models.TextField()
    replied_at = models.DateTimeField(auto_now_add=True)
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal admin note — not shown to the submitter",
    )

    class Meta:
        ordering = ["replied_at"]
        verbose_name = "Feedback Reply"
        verbose_name_plural = "Feedback Replies"

    def __str__(self):
        by = self.replied_by.get_full_name() if self.replied_by else "Unknown"
        return f"Reply by {by} on {self.feedback.feedback_id}"
