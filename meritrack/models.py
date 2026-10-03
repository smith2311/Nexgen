"""MeriTrack — Smart Consignment Management for Medical Devices.

Models covering:
 • Hospital & Department hierarchy
 • Product Catalog (Meril master data)
 • ConsignedProduct (stock on shelves)
 • ExpiryAlert (tiered 6M/3M/1M/1W alerts)
 • ScanningSession & ScanLog (audit/replenish/removal/transfer visits)
 • StockOrder & OrderItem (replenishment orders)
 • UsageRecord (patient-use tracking)
 • TransferSession & TransferredProduct (H2H transfers)
 • Notification, AuditLog, Attachments
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# HOSPITAL & DEPARTMENT
# ─────────────────────────────────────────────────────────────────────────────
class Hospital(models.Model):
    """Hospital / clinic where consigned stock is stored."""

    name = models.CharField(max_length=255)
    sap_code = models.CharField(max_length=50, blank=True, null=True, unique=True, help_text="SAP Customer Code")
    country = models.CharField(max_length=100, default="Belgium")
    province = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    salesperson_email = models.EmailField(
        blank=True, null=True, help_text="Email of the Meril rep responsible for this hospital"
    )
    salespersons = models.ManyToManyField(
        User,
        blank=True,
        related_name="mt_hospitals",
        help_text="All reps who can access this hospital (supports shared coverage)",
    )
    plant = models.CharField(max_length=20, blank=True, null=True, help_text="SAP plant code (e.g. GE01, GE02)")
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_hospital_updates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Hospitals"
        indexes = [
            models.Index(fields=["sap_code"]),
            models.Index(fields=["country", "province"]),
        ]

    def __str__(self):
        return self.name

    @property
    def total_consigned(self):
        return ConsignedProduct.objects.filter(department__hospital=self, status="on_shelf").count()

    @property
    def expired_count(self):
        return ConsignedProduct.objects.filter(
            department__hospital=self, status="on_shelf", expiry_date__lt=timezone.now().date()
        ).count()


class Department(models.Model):
    """Department / ward within a hospital (e.g. Interventional Cardiology)."""

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True, null=True)
    floor = models.CharField(max_length=50, blank=True, null=True)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hospital", "name"]
        unique_together = [("hospital", "name")]
        verbose_name_plural = "Departments"

    def __str__(self):
        return f"{self.hospital.name} — {self.name}"


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────────────────────────────────────────────
class ProductCatalog(models.Model):
    """Meril master product catalog — reference, description, GTIN, sizes."""

    CATEGORY_CHOICES = [
        ("stent", "Coronary Stent"),
        ("balloon", "Balloon Catheter"),
        ("guidewire", "Guidewire"),
        ("valve", "Heart Valve"),
        ("graft", "Vascular Graft"),
        ("closure", "Closure Device"),
        ("accessory", "Accessory"),
        ("other", "Other"),
    ]
    material_code = models.CharField(max_length=100, unique=True, help_text="SAP material code")
    gtin_code = models.CharField(max_length=50, blank=True, null=True, help_text="GTIN barcode")
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, blank=True, null=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="other")
    description = models.TextField(blank=True, null=True)
    size = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. 3.0 x 28 mm")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shelf_life_months = models.PositiveIntegerField(default=36)
    requires_cold_chain = models.BooleanField(default=False)
    material_type = models.CharField(max_length=50, blank=True, null=True, help_text="SAP material type (e.g. ZFIN)")
    material_type_description = models.CharField(
        max_length=255, blank=True, null=True, help_text="e.g. Finished /Trading"
    )
    material_group = models.CharField(max_length=50, blank=True, null=True, help_text="SAP material group code")
    material_group_description = models.CharField(
        max_length=255, blank=True, null=True, help_text="e.g. Myval Accessories"
    )
    base_uom = models.CharField(max_length=10, default="EA", help_text="Base unit of measure")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Product Catalog Entry"
        verbose_name_plural = "Product Catalog"
        indexes = [
            models.Index(fields=["material_code"]),
            models.Index(fields=["gtin_code"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        size_str = f" ({self.size})" if self.size else ""
        return f"{self.name}{size_str} [{self.material_code}]"


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNED PRODUCT (inventory on hospital shelves)
# ─────────────────────────────────────────────────────────────────────────────
class ConsignedProduct(models.Model):
    """Individual consigned unit on a hospital department shelf."""

    STATUS_CHOICES = [
        ("on_shelf", "On Shelf"),
        ("used", "Used on Patient"),
        ("expired_removed", "Expired & Removed"),
        ("recalled", "Recalled"),
        ("transferred", "Transferred"),
        ("returned", "Returned to Meril"),
    ]

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="consigned_products")
    serial_number = models.CharField(max_length=255, db_index=True, blank=True, default="")
    gtin_code = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    catalog_product = models.ForeignKey(
        ProductCatalog, on_delete=models.SET_NULL, null=True, blank=True, related_name="consigned_units"
    )
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    size = models.CharField(max_length=100, blank=True, null=True)
    quantity = models.PositiveIntegerField(default=1)
    production_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="on_shelf")
    placed_on_shelf_at = models.DateTimeField(auto_now_add=True)
    placed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_placed_products"
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_removed_products"
    )
    removal_reason = models.TextField(blank=True, null=True)
    shelf_location = models.CharField(max_length=100, blank=True, null=True, help_text="Physical shelf/bin location")
    reference_number = models.CharField(
        max_length=255, blank=True, null=True, help_text="Reference/additional ID from QR (GS1 AI 240/22)"
    )
    qr_data = models.TextField(blank=True, null=True)
    scan_session = models.ForeignKey(
        "ScanningSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="products_added"
    )
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-placed_on_shelf_at"]
        verbose_name = "Consigned Product"
        verbose_name_plural = "Consigned Products"
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["gtin_code"]),
            models.Index(fields=["status"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["department", "status"]),
        ]

    def __str__(self):
        return f"{self.serial_number} — {self.product_name or 'Product'} ({self.get_status_display()})"

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date < timezone.now().date()

    @property
    def days_until_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - timezone.now().date()).days

    @property
    def expiry_urgency(self):
        """Return urgency tier: 'expired', '1w', '1m', '3m', '6m', 'ok'."""
        days = self.days_until_expiry
        if days is None:
            return "ok"
        if days < 0:
            return "expired"
        if days <= 7:
            return "1w"
        if days <= 30:
            return "1m"
        if days <= 90:
            return "3m"
        if days <= 180:
            return "6m"
        return "ok"


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY ALERTS (tiered: 6M / 3M / 1M / 1W / Expired)
# ─────────────────────────────────────────────────────────────────────────────
class ExpiryAlert(models.Model):
    """Tracks which expiry-alert tier has been triggered for a product."""

    TIER_CHOICES = [
        ("6m", "6 Months"),
        ("3m", "3 Months"),
        ("1m", "1 Month"),
        ("1w", "1 Week"),
        ("expired", "Expired"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("acknowledged", "Acknowledged"),
        ("actioned", "Actioned"),
        ("dismissed", "Dismissed"),
    ]

    product = models.ForeignKey(ConsignedProduct, on_delete=models.CASCADE, related_name="expiry_alerts")
    tier = models.CharField(max_length=10, choices=TIER_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    days_until_expiry = models.IntegerField()
    notified_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_acknowledged_alerts"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    action_notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-notified_at"]
        unique_together = [("product", "tier")]
        verbose_name = "Expiry Alert"
        verbose_name_plural = "Expiry Alerts"
        indexes = [
            models.Index(fields=["tier", "status"]),
            models.Index(fields=["product", "tier"]),
        ]

    def __str__(self):
        return f"[{self.get_tier_display()}] {self.product.serial_number} — {self.get_status_display()}"


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING SESSION & SCAN LOG
# ─────────────────────────────────────────────────────────────────────────────
class ScanningSession(models.Model):
    """A rep visit to a hospital — audit / replenish / remove / transfer."""

    SESSION_TYPE_CHOICES = [
        ("audit", "Stock Audit"),
        ("replenish", "Replenishment"),
        ("removal", "Expired Removal"),
        ("transfer", "Hospital Transfer"),
    ]
    STATUS_CHOICES = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("on_hold", "On Hold"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]

    session_key = models.CharField(max_length=100, unique=True)
    session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES, default="audit")
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_sessions")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_sessions"
    )
    to_hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mt_incoming_sessions",
        help_text="Destination hospital (transfer sessions only)",
    )
    to_department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_incoming_dept_sessions"
    )
    rep = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_sessions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    notes = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_scan_time = models.DateTimeField(null=True, blank=True)
    on_hold_at = models.DateTimeField(null=True, blank=True)
    notify_overdue_from = models.DateTimeField(
        null=True, blank=True, help_text="Overdue reminders count from this time instead of completed_at (set on resume)"
    )
    submission_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set once when the 'batch submitted' notification fires; blocks duplicate sends.",
    )

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Scanning Session"
        verbose_name_plural = "Scanning Sessions"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["session_type"]),
            models.Index(fields=["hospital", "status"]),
        ]

    def __str__(self):
        rep_name = self.rep.get_full_name() or self.rep.username if self.rep else "Unknown"
        return f"{rep_name} — {self.hospital.name} ({self.get_session_type_display()})"

    def mark_completed(self):
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save()

    def mark_closed(self):
        self.status = "closed"
        self.save()

    def mark_on_hold(self):
        """Put session on hold — pauses overdue reminders."""
        self.status = "on_hold"
        self.on_hold_at = timezone.now()
        self.save(update_fields=["status", "on_hold_at"])

    def resume_from_hold(self):
        """Resume a held session — reminders restart from the very next cron run."""
        from datetime import timedelta

        self.status = "completed"
        self.on_hold_at = None
        # Back-date the overdue reference so the next daily cron fires immediately.
        self.notify_overdue_from = timezone.now() - timedelta(days=6)
        self.save(update_fields=["status", "on_hold_at", "notify_overdue_from"])
        # Clear today's log entry so dedup doesn't block tomorrow's send.
        self.overdue_logs.filter(notified_on=timezone.localdate()).delete()


class SessionAttachment(models.Model):
    """Confirmation document attached to a completed session."""

    session = models.OneToOneField(ScanningSession, on_delete=models.CASCADE, related_name="attachment")
    file_data = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    file_size = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_uploaded_attachments"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Attachment: {self.session} — {self.original_filename}"


class ScanLog(models.Model):
    """Individual scan action within a session."""

    ACTION_CHOICES = [
        ("verify", "Verify on Shelf"),
        ("add", "Add to Shelf"),
        ("remove_expired", "Remove Expired"),
        ("remove_recalled", "Remove Recalled"),
        ("remove_return", "Return to Meril"),
        ("use", "Used on Patient"),
        ("transfer_out", "Transfer Out"),
        ("transfer_in", "Transfer In"),
    ]

    session = models.ForeignKey(ScanningSession, on_delete=models.CASCADE, related_name="scan_logs")
    product = models.ForeignKey(
        ConsignedProduct, on_delete=models.SET_NULL, null=True, blank=True, related_name="scan_history"
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    serial_number = models.CharField(max_length=255, blank=True, default="")
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    production_date = models.DateField(blank=True, null=True)
    quantity = models.PositiveIntegerField(default=1, null=True, blank=True)
    reference_number = models.CharField(
        max_length=255, blank=True, null=True, help_text="Reference/additional ID from QR (GS1 AI 240/22)"
    )
    qr_data = models.TextField(blank=True, null=True)
    scanned_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-scanned_at"]
        verbose_name = "Scan Log"
        verbose_name_plural = "Scan Logs"
        indexes = [
            models.Index(fields=["session", "action"]),
            models.Index(fields=["serial_number"]),
        ]
        constraints = [
            # Composite identity: the same serial may repeat under a different
            # GTIN/material (serials are scoped to a GTIN). Blank serials
            # (no-serial products) are exempt and may repeat. NULL gtin/material
            # aren't enforced by the DB, so the app-level composite check is the
            # primary guard.
            models.UniqueConstraint(
                fields=["session", "serial_number", "gtin_code", "material_code"],
                condition=~models.Q(serial_number=""),
                name="uniq_scanlog_session_serial_gtin_material",
            ),
        ]

    def __str__(self):
        return f"[{self.get_action_display()}] {self.serial_number}"


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


# ─────────────────────────────────────────────────────────────────────────────
# STOCK ORDERS (replenishment)
# ─────────────────────────────────────────────────────────────────────────────
class StockOrder(models.Model):
    """Replenishment order placed by a rep."""

    URGENCY_CHOICES = [
        ("normal", "Normal"),
        ("urgent", "Urgent"),
        ("critical", "Critical — Immediate"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("confirmed", "Confirmed by Logistics"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    order_id = models.CharField(max_length=100, unique=True)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_orders")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="mt_orders")
    ordered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_orders_placed"
    )
    source_session = models.ForeignKey(
        "ScanningSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replenishment_orders",
        help_text="Scanning session that auto-generated this order",
    )
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Stock Order"
        verbose_name_plural = "Stock Orders"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["hospital", "status"]),
        ]

    def __str__(self):
        return f"Order {self.order_id} — {self.hospital.name} ({self.get_status_display()})"


class StockOrderItem(models.Model):
    """Line item on a stock order."""

    order = models.ForeignKey(StockOrder, on_delete=models.CASCADE, related_name="items")
    catalog_product = models.ForeignKey(ProductCatalog, on_delete=models.CASCADE, related_name="order_items")
    quantity = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = [("order", "catalog_product")]
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self):
        return f"{self.catalog_product.name} x{self.quantity}"


# ─────────────────────────────────────────────────────────────────────────────
# USAGE RECORD (product used on patient)
# ─────────────────────────────────────────────────────────────────────────────
class UsageRecord(models.Model):
    """Records when a consigned product is used on a patient."""

    product = models.OneToOneField(ConsignedProduct, on_delete=models.CASCADE, related_name="usage_record")
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_usage_records")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="mt_usage_records")
    used_date = models.DateField()
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_usage_records"
    )
    surgeon_name = models.CharField(max_length=255, blank=True, null=True)
    procedure_type = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-used_date"]
        verbose_name = "Usage Record"
        verbose_name_plural = "Usage Records"
        indexes = [
            models.Index(fields=["hospital", "used_date"]),
            models.Index(fields=["department", "used_date"]),
        ]

    def __str__(self):
        return f"{self.product.serial_number} used {self.used_date} @ {self.hospital.name}"


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION
# ─────────────────────────────────────────────────────────────────────────────
class Notification(models.Model):
    """In-app notifications: expiry alerts, session updates, order status."""

    CATEGORY_CHOICES = [
        ("expiry_6m", "6 Month Warning"),
        ("expiry_3m", "3 Month Warning"),
        ("expiry_1m", "1 Month Urgent"),
        ("expiry_1w", "1 Week Critical"),
        ("expiry_now", "Product Expired"),
        ("session_completed", "Session Completed"),
        ("order_update", "Order Status Update"),
        ("stock_low", "Low Stock Alert"),
        ("recall", "Product Recall"),
        ("transfer_closed", "Transfer Closed"),
    ]
    CATEGORY_TRANSFER_CLOSED = "transfer_closed"
    CATEGORY_EXPIRY_6M = "expiry_6m"
    CATEGORY_EXPIRY_3M = "expiry_3m"
    CATEGORY_EXPIRY_1M = "expiry_1m"
    CATEGORY_EXPIRY_1W = "expiry_1w"
    CATEGORY_EXPIRY_NOW = "expiry_now"
    CATEGORY_SESSION_COMPLETED = "session_completed"
    CATEGORY_ORDER_UPDATE = "order_update"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mt_notifications")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=255)
    message = models.TextField()
    hospital = models.ForeignKey(Hospital, on_delete=models.SET_NULL, null=True, blank=True)
    session = models.ForeignKey(ScanningSession, on_delete=models.SET_NULL, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return f"[{self.category}] → {self.recipient.username}: {self.title}"


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────
class AuditLog(models.Model):
    """Immutable audit trail for every MeriTrack action."""

    ACTION_CHOICES = [
        ("session_started", "Session Started"),
        ("session_completed", "Session Completed"),
        ("session_force_completed", "Session Force Completed"),
        ("session_terminated", "Session Terminated"),
        ("session_rejected", "Session Rejected"),
        ("session_closed", "Session Closed"),
        ("session_held", "Session Held"),
        ("session_resumed", "Session Resumed"),
        ("product_scanned", "Product Scanned"),
        ("product_added", "Product Added to Shelf"),
        ("product_removed", "Product Removed"),
        ("product_used", "Product Used"),
        ("product_transferred", "Product Transferred"),
        ("scan_deleted", "Scan Deleted"),
        ("order_placed", "Order Placed"),
        ("order_updated", "Order Updated"),
        ("expiry_alert_sent", "Expiry Alert Sent"),
        ("expiry_alert_actioned", "Expiry Alert Actioned"),
        ("attachment_uploaded", "Attachment Uploaded"),
    ]

    session = models.ForeignKey(
        ScanningSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_audit_logs"
    )
    product = models.ForeignKey(
        ConsignedProduct, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_audit_logs"
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="mt_audit_logs")
    description = models.TextField(blank=True, null=True)
    old_status = models.CharField(max_length=20, blank=True, null=True)
    new_status = models.CharField(max_length=20, blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        indexes = [
            models.Index(fields=["session", "action"]),
            models.Index(fields=["performed_by"]),
            models.Index(fields=["timestamp"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        user = self.performed_by.username if self.performed_by else "System"
        return f"{self.action} by {user} @ {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


# ─────────────────────────────────────────────────────────────────────────────
# H2H TRANSFER
# ─────────────────────────────────────────────────────────────────────────────
class TransferSession(models.Model):
    """Hospital-to-hospital consignment transfer."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]

    session_key = models.CharField(max_length=100, unique=True)
    transfer_id = models.CharField(max_length=100, unique=True)
    from_hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_outgoing_transfers")
    from_department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_outgoing_transfers"
    )
    to_hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_incoming_transfers")
    to_department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_incoming_transfers"
    )
    initiated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_initiated_transfers"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    notes = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_scan_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Transfer Session"
        verbose_name_plural = "Transfer Sessions"
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
    """Product moved in a transfer session."""

    transfer_session = models.ForeignKey(TransferSession, on_delete=models.CASCADE, related_name="transferred_products")
    consigned_product = models.ForeignKey(ConsignedProduct, on_delete=models.SET_NULL, null=True, blank=True)
    serial_number = models.CharField(max_length=255, blank=True, default="")
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    production_date = models.DateField(blank=True, null=True)
    quantity = models.PositiveIntegerField(default=1)
    qr_data = models.TextField(blank=True, null=True)
    scanned_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-scanned_at"]
        unique_together = [("transfer_session", "serial_number")]
        verbose_name = "Transferred Product"
        verbose_name_plural = "Transferred Products"
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["transfer_session"]),
        ]

    def __str__(self):
        return f"{self.serial_number} ({self.transfer_session.transfer_id})"


# ─────────────────────────────────────────────────────────────────────────────
# SAP CONSIGNMENT POSTING (movement history)
# ─────────────────────────────────────────────────────────────────────────────
class ConsignmentPosting(models.Model):
    """SAP material-document posting for consignment movements.

    Movement types:
      631 — Goods receipt into consignment (fill up / lending)
      632 — Goods issue consignment return (pickup)
      633 — Goods issue to customer (used on patient)
      634 — Goods receipt from customer return
      561 — Initial entry stock balance
      551 — Scrap from customer consignment
      309/310 — Transfer postings
      301 — Plant-to-plant transfer
    """

    MOVEMENT_TYPE_CHOICES = [
        ("631", "Consignment Fill Up"),
        ("632", "Consignment Pickup / Return"),
        ("633", "Consignment Issue (Used)"),
        ("634", "Customer Return Receipt"),
        ("561", "Initial Stock Balance"),
        ("551", "Scrap"),
        ("309", "Transfer Posting Material"),
        ("310", "Transfer Posting Material (Rev)"),
        ("301", "Plant-to-Plant Transfer"),
    ]

    plant = models.CharField(max_length=20)
    storage_location = models.CharField(max_length=20, blank=True, null=True)
    posting_date = models.DateField()
    entry_date = models.DateField(blank=True, null=True)
    entry_time = models.TimeField(blank=True, null=True)
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_TYPE_CHOICES)
    movement_type_text = models.CharField(max_length=255, blank=True, null=True)
    material_document = models.CharField(max_length=50, blank=True, null=True, help_text="SAP material document number")
    material_doc_item = models.CharField(max_length=10, blank=True, null=True)
    material_doc_year = models.CharField(max_length=4, blank=True, null=True)
    material = models.CharField(max_length=100, db_index=True, help_text="SAP material code")
    material_description = models.CharField(max_length=255, blank=True, null=True)
    batch = models.CharField(max_length=100, blank=True, null=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, help_text="Qty (negative = goods issue)")
    base_uom = models.CharField(max_length=10, default="EA")
    customer = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text="SAP customer code")
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    material_type = models.CharField(max_length=20, blank=True, null=True)
    material_type_description = models.CharField(max_length=255, blank=True, null=True)
    material_group = models.CharField(max_length=20, blank=True, null=True)
    material_group_description = models.CharField(max_length=255, blank=True, null=True)
    delivery = models.CharField(max_length=50, blank=True, null=True)
    user_name = models.CharField(max_length=100, blank=True, null=True, help_text="SAP user who posted")
    document_header_text = models.CharField(max_length=255, blank=True, null=True)
    receiving_plant = models.CharField(max_length=20, blank=True, null=True)
    sending_plant = models.CharField(max_length=20, blank=True, null=True)
    amount_lc = models.DecimalField(max_digits=14, decimal_places=2, default=0, blank=True, null=True)
    currency = models.CharField(max_length=5, blank=True, null=True)

    # FK links (populated during import where possible)
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consignment_postings",
    )
    catalog_product = models.ForeignKey(
        ProductCatalog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consignment_postings",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-posting_date", "-entry_time"]
        verbose_name = "Consignment Posting"
        verbose_name_plural = "Consignment Postings"
        indexes = [
            models.Index(fields=["posting_date"]),
            models.Index(fields=["movement_type"]),
            models.Index(fields=["material"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["plant", "posting_date"]),
            models.Index(fields=["material_document", "material_doc_item"]),
        ]

    def __str__(self):
        return f"{self.movement_type} | {self.material} | " f"{self.quantity} {self.base_uom} | {self.posting_date}"

    @property
    def movement_type_display(self):
        """Return clean display name from MOVEMENT_TYPE_CHOICES instead of raw SAP text."""
        mt_dict = dict(self.MOVEMENT_TYPE_CHOICES)
        return mt_dict.get(self.movement_type, self.movement_type_text or self.movement_type)


class SAPIntegrationLog(models.Model):
    """Journal of SAP import/export operations (used by SAPIntegrationService)."""

    OPERATION_CHOICES = [
        ("import_stock", "Import Stock Balances"),
        ("import_postings", "Import Consignment Postings"),
        ("import_serial", "Import Serial Numbers"),
        ("export_usage", "Export Usage to SAP"),
        ("sync_master", "Sync Master Data"),
    ]
    STATUS_CHOICES = [
        ("started", "Started"),
        ("completed", "Completed"),
        ("partial", "Partially Completed"),
        ("failed", "Failed"),
    ]

    operation = models.CharField(max_length=30, choices=OPERATION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="started")
    plant = models.CharField(max_length=20, blank=True, default="")
    source_file = models.CharField(max_length=255, blank=True, default="")
    sap_system = models.CharField(max_length=100, blank=True, default="")
    initiated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_sap_integrations"
    )
    records_processed = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_failed = models.PositiveIntegerField(default=0)
    error_details = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "SAP Integration Log"
        verbose_name_plural = "SAP Integration Logs"
        indexes = [
            models.Index(fields=["operation", "-started_at"]),
        ]

    def __str__(self):
        return f"[{self.get_operation_display()}] {self.get_status_display()} @ {self.started_at:%Y-%m-%d %H:%M}"


class TransferAttachment(models.Model):
    """Attachment for a transfer session."""

    transfer_session = models.OneToOneField(TransferSession, on_delete=models.CASCADE, related_name="attachment")
    file_data = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    file_size = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_transfer_attachments"
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
# EXPIRED PRODUCT REPORT
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductReport(models.Model):
    """
    Consolidated report of expired/recalled products found during
    scanning sessions with removal details and regulatory actions.
    """

    report_id = models.CharField(max_length=100, unique=True)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="expired_reports")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="expired_reports"
    )
    scanning_session = models.ForeignKey(
        ScanningSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="expired_reports"
    )

    # Product details
    serial_number = models.CharField(max_length=255, blank=True, default="")
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    product_name = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    expiry_date = models.DateField()

    # Regulatory actions
    REASON_CHOICES = [
        ("expired", "Expired"),
        ("recalled", "Recalled"),
        ("damaged", "Damaged"),
        ("contaminated", "Contaminated"),
        ("other", "Other"),
    ]
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="expired")

    STATUS_CHOICES = [
        ("reported", "Reported"),
        ("confirmed", "Confirmed"),
        ("removed", "Removed from Shelf"),
        ("returned", "Returned to Meril"),
        ("destroyed", "Destroyed"),
        ("other_action", "Other Action"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="reported")

    # Removal details
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_removed_expired"
    )
    removal_notes = models.TextField(blank=True, null=True)

    # Acknowledgment details (set when a manager acknowledges the expired product)
    days_expired = models.IntegerField(default=0, help_text="Days past expiry at acknowledgment time")
    notes = models.TextField(blank=True, null=True)
    acknowledged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_acknowledged_expired"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    # Regulatory/Reference
    regulatory_action_taken = models.TextField(
        blank=True, null=True, help_text="Any regulatory/compliance action (recall notice, destruction cert, etc.)"
    )
    sap_transaction_id = models.CharField(
        max_length=100, blank=True, null=True, help_text="SAP material document for movement"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expiry_date", "-created_at"]
        verbose_name = "Expired Product Report"
        verbose_name_plural = "Expired Product Reports"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["hospital", "status"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"[{self.get_reason_display()}] {self.serial_number} @ {self.hospital.name}"


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING BATCH (Optional logical grouping for multi-session audits)
# ─────────────────────────────────────────────────────────────────────────────
class ScanningBatch(models.Model):
    """
    Logical grouping of multiple scanning sessions at a hospital.
    Allows organizing related sessions (e.g., multi-day audit) together.
    """

    batch_id = models.CharField(max_length=100, unique=True)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mt_scanning_batches")
    batch_type = models.CharField(
        max_length=20,
        choices=[
            ("audit", "Audit"),
            ("replenish", "Replenishment"),
            ("removal", "Removal"),
            ("transfer", "Transfer"),
            ("mixed", "Mixed"),
        ],
        default="audit",
    )
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_batches_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("batch_id", "hospital")]
        verbose_name = "Scanning Batch"
        verbose_name_plural = "Scanning Batches"

    def __str__(self):
        return f"Batch {self.batch_id} - {self.hospital.name}"


# ─────────────────────────────────────────────────────────────────────────────
# REMARKS — Product-wise & Session-level dialogue
# ─────────────────────────────────────────────────────────────────────────────
class ScannedProductRemark(models.Model):
    """Remark attached to an individual scan log entry."""

    ROLE_CHOICES = [
        ("salesperson", "Salesperson"),
        ("warehouse", "Warehouse"),
    ]

    scan_log = models.ForeignKey(
        ScanLog,
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
        related_name="mt_product_remarks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Scanned Product Remark"
        verbose_name_plural = "Scanned Product Remarks"
        indexes = [
            models.Index(fields=["scan_log"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        user = self.created_by.get_full_name() or self.created_by.username if self.created_by else "Unknown"
        return f"[{self.role}] {user} on {self.scan_log.serial_number}: {self.remark[:60]}"


class ScanSessionRemark(models.Model):
    """Batch-level/session-level remark for a scanning session."""

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
        related_name="mt_session_remarks",
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


# ─────────────────────────────────────────────────────────────────────────────
# MONTHLY REMINDER LOG
# ─────────────────────────────────────────────────────────────────────────────
class MonthlyReminderLog(models.Model):
    """Tracks monthly reminder emails sent to salespersons (MT Reps)."""

    salesperson_email = models.EmailField()
    salesperson_name = models.CharField(max_length=255, blank=True, default="")
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)
    sessions_count = models.PositiveIntegerField(default=0)
    pending_hospitals_count = models.PositiveIntegerField(default=0)
    pending_hospital_names = models.TextField(blank=True, default="", help_text="JSON list of hospital names")

    class Meta:
        unique_together = [("salesperson_email", "year", "month")]
        ordering = ["-sent_at"]
        verbose_name = "Monthly Reminder Log"
        verbose_name_plural = "Monthly Reminder Logs"

    def __str__(self):
        return f"{self.salesperson_email} — {self.year}/{self.month:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY RECONCILIATION (auto-generated on audit-session completion)
# ─────────────────────────────────────────────────────────────────────────────
class InventoryReconciliation(models.Model):
    """Reconciliation of a scanning session against expected consignment inventory.

    Auto-created when an *audit* ScanningSession completes: compares the serials
    physically scanned during the session against the ConsignedProduct units the
    system expected to be on the shelf for that hospital/department, recording
    matched / missing / extra counts plus per-item detail.
    """

    RECON_TYPE_CHOICES = [
        ("session_audit", "Session Audit (auto)"),
        ("full", "Full Count"),
        ("cycle", "Cycle Count"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("pending_review", "Pending Review"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    recon_id = models.CharField(max_length=100, unique=True)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="reconciliations")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliations"
    )
    session = models.ForeignKey(
        "ScanningSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliations"
    )
    recon_type = models.CharField(max_length=20, choices=RECON_TYPE_CHOICES, default="session_audit")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")

    total_expected = models.PositiveIntegerField(default=0)
    total_scanned = models.PositiveIntegerField(default=0)
    matched = models.PositiveIntegerField(default=0)
    missing = models.PositiveIntegerField(default=0)
    extra = models.PositiveIntegerField(default=0)
    expired_found = models.PositiveIntegerField(default=0)
    accuracy_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    discrepancy_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mt_reconciliations_conducted"
    )
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Inventory Reconciliation"
        verbose_name_plural = "Inventory Reconciliations"
        indexes = [
            models.Index(fields=["hospital", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.recon_id} — {self.hospital.name} ({self.get_status_display()})"

    @property
    def hospital_name(self):
        return self.hospital.name if self.hospital else None

    @property
    def performed_by_name(self):
        if not self.performed_by:
            return None
        return self.performed_by.get_full_name() or self.performed_by.username


class ReconciliationItem(models.Model):
    """One line of an InventoryReconciliation result."""

    MATCH_STATUS_CHOICES = [
        ("matched", "Matched"),
        ("missing", "Missing"),
        ("extra", "Extra"),
        ("expired_found", "Expired Found"),
        ("error", "Error"),
    ]

    reconciliation = models.ForeignKey(
        InventoryReconciliation, on_delete=models.CASCADE, related_name="items"
    )
    consigned_product = models.ForeignKey(
        ConsignedProduct, on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliation_items"
    )
    scan_log = models.ForeignKey(
        ScanLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliation_items"
    )
    product_name = models.CharField(max_length=255, blank=True, null=True)
    gtin_code = models.CharField(max_length=100, blank=True, null=True)
    material_code = models.CharField(max_length=255, blank=True, null=True)
    batch_number = models.CharField(max_length=255, blank=True, null=True)
    serial_number = models.CharField(max_length=255, blank=True, default="")
    barcode_data = models.TextField(blank=True, null=True)
    match_status = models.CharField(max_length=20, choices=MATCH_STATUS_CHOICES)
    expected_quantity = models.IntegerField(null=True, blank=True)
    found_quantity = models.IntegerField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["match_status", "product_name"]
        verbose_name = "Reconciliation Item"
        verbose_name_plural = "Reconciliation Items"
        indexes = [
            models.Index(fields=["reconciliation", "match_status"]),
        ]

    def __str__(self):
        return f"[{self.match_status}] {self.serial_number or self.product_name or 'item'}"
