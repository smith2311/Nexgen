"""
shipment_notifications/models.py
================================
Database models for the Shipment Notification System.

This module defines three core models:

1. ShipmentAccessToken
   - Generates secure, time-limited dashboard URLs for salespersons.
   - Uses Django's TimestampSigner to create deterministic encrypted tokens
     from phone numbers. No login required — the signed URL is the auth.
   - Tokens auto-expire after 30 days.

2. ExportShipment
   - Local mirror of shipment data synced from merigate.e-meril.in.
   - Each row is identified by a stable `source_eid` = MD5(soNo + vertical + awbNo + country + invoiceDate).
   - Implements hash-based change detection: SHA-256 of 11 tracked fields
     (status, remarks, AWB, dates, airline, forwarder) is compared against
     `last_notified_hash` to determine if a WhatsApp notification should fire.

3. SalespersonMapping
   - Maps (Country, Vertical) → salesperson contact details.
   - The `whatsapp_no` field determines WHO receives WhatsApp notifications.
   - Also used by the dashboard view to filter which shipments a salesperson sees.
   - unique_together on (country, vertical) ensures one salesperson per segment.

Data Flow:
  merigate API → tracker_client.py → ShipmentSyncService → ExportShipment (upsert)
  ExportShipment (changed?) → SalespersonMapping (lookup) → WhatsApp notification
"""

import hashlib
import json

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import models
from django.utils.translation import gettext_lazy as _

# ═══════════════════════════════════════════════════════════════════════════════
# Model 1: ShipmentAccessToken — Token-based dashboard authentication
# ═══════════════════════════════════════════════════════════════════════════════


class ShipmentAccessToken(models.Model):
    """
    Secure encrypted token for salesperson dashboard access — bypasses SSO.

    How it works:
      1. A record is created per phone number (one-time).
      2. The `token` property generates a signed string using Django's
         TimestampSigner: "<phone>:<timestamp>:<signature>".
      3. This token is embedded in the dashboard URL sent via WhatsApp.
      4. When the salesperson clicks the link, `verify_token()` decrypts it,
         checks the signature + age (max 30 days), and returns the phone number.
      5. The phone number is then used to look up SalespersonMapping and
         filter the dashboard to show only that person's shipments.

    Security:
      - Tokens are cryptographically signed with Django's SECRET_KEY.
      - They expire after 30 days (configurable).
      - Each phone number has exactly one token record (unique constraint).
    """

    phone_number = models.CharField(max_length=32, db_index=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Shipment access token")
        verbose_name_plural = _("Shipment access tokens")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number}"

    @property
    def token(self) -> str:
        """Generate encrypted token from phone_number (deterministic)."""
        signer = TimestampSigner(salt="shipment_token")
        return signer.sign(self.phone_number)

    @classmethod
    def verify_token(cls, token: str, max_age_days: int = 30) -> str | None:
        """
        Decrypt and verify token. Returns phone_number if valid, None otherwise.
        max_age_days: token valid for this many days from creation.
        """
        try:
            signer = TimestampSigner(salt="shipment_token")
            max_age_seconds = max_age_days * 86400
            phone = signer.unsign(token, max_age=max_age_seconds)
            return phone
        except (SignatureExpired, BadSignature):
            return None

    @classmethod
    def get_or_create_for_phone(cls, phone_number: str, expires_days: int = 30):
        """Get or create token record for phone."""
        obj, created = cls.objects.get_or_create(phone_number=phone_number)
        return obj


# ── Change-detection fields tracked for WhatsApp notification ─────────────────
# When ANY of these 11 fields change between syncs, a WhatsApp notification fires.
# Adding/removing fields here changes what triggers a notification.
TRACKED_FIELDS = [
    "status",
    "remarks",
    "awb_no",
    "etd",
    "eta",
    "revised_etd",
    "revised_eta",
    "actual_eta",
    "actual_arrival",
    "airline",
    "forwarder",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Model 2: ExportShipment — Local mirror of shipment data from merigate
# ═══════════════════════════════════════════════════════════════════════════════


class ExportShipment(models.Model):
    """
    Mirrors ExportShipmentDeliveryReport from merigate.e-meril.in.
    Synced periodically via ShipmentSyncService; WhatsApp notifications
    fire when any of the TRACKED_FIELDS change.

    Change Detection:
      - compute_change_hash() → SHA-256 of the 11 TRACKED_FIELDS.
      - last_notified_hash stores the hash at the time of last notification.
      - has_changed_since_last_notification compares current vs stored hash.
      - If different → notification fires. After sending → hash is updated.
      - This prevents duplicate notifications for the same data.

    Sync Key:
      - source_eid = MD5(soNo|vertical|awbNo|country|invoiceDate)
      - This is the stable identifier used for upsert during sync.
      - If the same row comes in from the API, it updates rather than duplicates.
    """

    # Source identifier — stable hash of (soNo, vertical, awbNo, country, invoiceDate)
    source_eid = models.CharField(
        max_length=64, unique=True, db_index=True, help_text="Stable sync key derived from source row key fields"
    )

    # Shipment master fields
    vertical = models.CharField(max_length=255, blank=True, db_index=True)
    country = models.CharField(max_length=255, blank=True, db_index=True)
    consignee_name = models.CharField(max_length=512, blank=True)
    so_no = models.CharField("SO No", max_length=128, blank=True)
    invoice_no = models.CharField(max_length=128, blank=True, db_index=True)
    invoice_date = models.DateField(blank=True, null=True)
    invoice_qty = models.DecimalField(max_digits=18, decimal_places=0, blank=True, null=True)
    invoice_value = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    currency = models.CharField(max_length=10, blank=True)
    incoterms = models.CharField(max_length=32, blank=True)

    # Forwarding / shipping fields
    forwarder = models.CharField(max_length=255, blank=True)
    awb_no = models.CharField("AWB No", max_length=128, blank=True, db_index=True)
    awb_date = models.DateField(blank=True, null=True)
    airline = models.CharField(max_length=128, blank=True)
    dispatch_date = models.DateField(blank=True, null=True)

    # Dates
    etd = models.DateField("ETD", blank=True, null=True)
    eta = models.DateField("ETA", blank=True, null=True)
    revised_etd = models.DateField(blank=True, null=True)
    revised_eta = models.DateField(blank=True, null=True)
    actual_eta = models.DateField(blank=True, null=True)
    actual_arrival = models.DateField(blank=True, null=True)

    # Status
    status = models.CharField(max_length=128, blank=True, db_index=True)
    remarks = models.TextField(blank=True)

    # Notification tracking
    whatsapp_sent_status = models.BooleanField(default=False)
    whatsapp_sent_at = models.DateTimeField(blank=True, null=True)
    last_notified_hash = models.CharField(
        max_length=64, blank=True, help_text="SHA-256 of tracked fields at last notification"
    )

    # Sync metadata
    last_synced_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Export shipment")
        verbose_name_plural = _("Export shipments")
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["country", "vertical"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.invoice_no} | {self.country} | {self.status}"

    def compute_change_hash(self) -> str:
        """SHA-256 of the tracked fields — used for change detection."""
        data = {f: str(getattr(self, f, "") or "") for f in TRACKED_FIELDS}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    @property
    def has_changed_since_last_notification(self) -> bool:
        return self.compute_change_hash() != self.last_notified_hash


# ═══════════════════════════════════════════════════════════════════════════════
# Model 3: SalespersonMapping — Who gets notified for which country/vertical
# ═══════════════════════════════════════════════════════════════════════════════


class SalespersonMapping(models.Model):
    """
    Maps (Country, Vertical) → salesperson contact details.

    Used in two places:
      1. WhatsAppNotificationService: looks up by (country, vertical) to find
         the whatsapp_no to send notifications to.
      2. Dashboard view: looks up by whatsapp_no to find which (country, vertical)
         pairs belong to this salesperson, then filters ExportShipment accordingly.

    Example:
      country='USA', vertical='MHPL' → whatsapp_no='918128644751'
      → Phone 918128644751 receives notifications for all USA/MHPL shipments
      → Dashboard for this phone shows only USA/MHPL shipments.

    In production, populate via Django admin or bulk import.
    unique_together on (vertical, country) prevents duplicate mappings.
    """

    vertical = models.CharField(max_length=255, blank=True, db_index=True)
    country = models.CharField(max_length=255, db_index=True)
    to_email = models.TextField(blank=True)
    cc_email = models.TextField(blank=True)
    bcc_email = models.TextField(blank=True)
    tat = models.IntegerField("TAT (days)", blank=True, null=True)
    # Phone number may not be populated yet; leave blank until provided
    whatsapp_no = models.CharField(
        max_length=64, blank=True, help_text="WhatsApp number with country code, e.g. 918128644751"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Salesperson mapping")
        verbose_name_plural = _("Salesperson mappings")
        unique_together = [("vertical", "country")]
        ordering = ["country", "vertical"]

    def __str__(self):
        return f"{self.country} / {self.vertical} → {self.to_email}"
