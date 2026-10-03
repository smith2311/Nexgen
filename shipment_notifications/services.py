"""
shipment_notifications/services.py
==================================
All business logic for the Shipment Notification System.

This module contains four service classes and helper functions:

1. DashboardService
   - Generates secure token-based dashboard URLs for salespersons.
   - Used when sending WhatsApp messages so the recipient can click through
     to see their full shipment list.

2. EngatiService
   - Sends WhatsApp messages via the Engati chatbot API.
   - Builds the URL with query parameters matching the Engati flow template.
   - Critical param names: sc_shpmnt_no, sc_inv_no, sc_inv_date, sc_etd,
     sc_current_status, sc_remarks, sc_detail_url, sc_consignee_name.

3. ShipmentSyncService
   - Fetches shipment data from merigate.e-meril.in (via tracker_client.py).
   - Upserts into ExportShipment using source_eid as the stable key.
   - Prunes records older than `days_back` days.

4. WhatsAppNotificationService
   - The core notification engine.
   - Detects changed shipments (hash comparison), looks up salesperson mapping,
     groups by phone number, sends one Engati message per salesperson.
   - In test mode, routes to test phones only.

Execution Flow (triggered by cron or API):
  ShipmentSyncService.sync()  →  fetch & upsert data
  WhatsAppNotificationService.send_grouped_notifications()  →  detect changes & send WhatsApp

Cron: 0 11 * * * (daily 11 AM IST) via `manage.py poll_shipment_updates`
API:  POST /shipment/api/sync/ with X-SHIPMENT-POLL-SECRET header
"""

import logging
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import requests

from .models import ExportShipment, SalespersonMapping, ShipmentAccessToken
from .tracker_client import MerigateClient, map_api_row

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — test mode & formatting utilities
# ---------------------------------------------------------------------------


def _test_mode() -> bool:
    """Check if SHIPMENT_NOTIFICATIONS_TEST_MODE is enabled in settings/.env.
    When True, notifications only go to TEST_PHONE_* numbers, never to real salespersons."""
    val = getattr(settings, "SHIPMENT_NOTIFICATIONS_TEST_MODE", "false")
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "on")


def _test_phones() -> list[str]:
    """Collect non-empty TEST_PHONE_1 through TEST_PHONE_4 from settings.
    These are the only recipients when test mode is ON."""
    phones = [
        getattr(settings, "TEST_PHONE_1", ""),
        getattr(settings, "TEST_PHONE_2", ""),
        getattr(settings, "TEST_PHONE_3", ""),
        getattr(settings, "TEST_PHONE_4", ""),
    ]
    return [p.strip() for p in phones if p and p.strip()]


# European country names as they appear in the shipment database.
EUROPEAN_COUNTRIES = frozenset(
    [
        "Albania",
        "Austria",
        "Belgium",
        "Bosnia",
        "Bosnia and Herzegovina",
        "Bulgaria",
        "Croatia",
        "Cyprus",
        "Czech Republic",
        "Czechia",
        "Denmark",
        "Estonia",
        "Finland",
        "France",
        "Germany",
        "Greece",
        "Hungary",
        "Iceland",
        "Ireland",
        "Italy",
        "Kosovo",
        "Latvia",
        "Lithuania",
        "Luxembourg",
        "Malta",
        "Moldova",
        "Montenegro",
        "Netherlands",
        "North Macedonia",
        "Norway",
        "Poland",
        "Portugal",
        "Romania",
        "Serbia",
        "Slovakia",
        "Slovenia",
        "Spain",
        "Sweden",
        "Switzerland",
        "Ukraine",
        "United Kingdom",
        "UK",
        "England",
        "Scotland",
        "Wales",
        "Russia",
        "Belarus",
    ]
)


def _fmt_date(val) -> str:
    """Format a date as DD-MM-YYYY for the Engati WhatsApp template."""
    if val is None:
        return ""
    try:
        return val.strftime("%d-%m-%Y")
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
# Dashboard URL helper
# ---------------------------------------------------------------------------


class DashboardService:
    """Generates secure dashboard URLs containing encrypted phone-based tokens."""

    @staticmethod
    def get_url_for_phone(phone: str) -> str:
        """
        Generate a full dashboard URL for a salesperson's phone number.

        Flow:
          1. Get/create ShipmentAccessToken record for this phone.
          2. Generate signed token (phone:timestamp:signature).
          3. Build URL: https://gpt.e-meril.in/shipment/track/<token>/

        This URL is included in WhatsApp messages so salespersons can click
        through to see their filtered shipment dashboard.
        """
        token_obj = ShipmentAccessToken.get_or_create_for_phone(phone_number=phone)
        encrypted_token = token_obj.token  # Gets the encrypted token dynamically
        base = getattr(settings, "SHIPMENT_NOTIFICATION_BASE_URL", "").rstrip("/")
        path = reverse("shipment_notifications:shipment_dashboard")
        # Use query param to avoid colons in path breaking WhatsApp URL parsing
        from urllib.parse import quote

        return f'{base}{path}?token={quote(encrypted_token, safe="")}'


# ---------------------------------------------------------------------------
# Engati WhatsApp sender
# ---------------------------------------------------------------------------


class EngatiService:
    """
    Sends WhatsApp messages via the Engati chatbot API.

    The Engati flow is configured with a template that expects these query parameters:
      sc_rcvr_contact   → recipient phone number (with country code, e.g. 918128644751)
      channel           → 'whatsapp' (fixed)
      sc_shpmnt_no      → shipment/AWB number
      sc_consignee_name → hospital/customer name
      sc_inv_no         → invoice number
      sc_inv_date       → invoice date (DD-MM-YYYY)
      sc_etd            → estimated time of departure (DD-MM-YYYY)
      sc_current_status → current shipment status
      sc_remarks        → additional comments
      sc_detail_url     → dashboard link for full details

    API endpoint and auth token are configured in .env:
      ENGATI_API_ENDPOINT=https://api.engati.com/bot-api/v1.0/customer/.../flow/...
      ENGATI_BASIC_TOKEN=<api-key>
    """

    @staticmethod
    def send(phone: str, params: dict) -> dict:
        """
        Send a single WhatsApp message via Engati.

        Args:
            phone: Recipient phone with country code (e.g. '918128644751')
            params: Dict of sc_* parameters for the flow template

        Returns:
            {'success': True, 'status_code': 200} on success
            {'success': False, 'error': '...'}    on failure
        """
        endpoint = getattr(settings, "ENGATI_API_ENDPOINT", "").strip()
        auth_token = getattr(settings, "ENGATI_BASIC_TOKEN", "").strip()
        if not endpoint or not auth_token:
            msg = "Missing ENGATI_API_ENDPOINT or ENGATI_BASIC_TOKEN"
            logger.error(msg)
            return {"success": False, "error": msg}

        full_params = {"sc_rcvr_contact": phone, "channel": "whatsapp", **params}
        url = f"{endpoint}?{urlencode(full_params)}"
        headers = {
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "",  # Match working curl which has Content-Type;
        }

        try:
            resp = requests.post(url, headers=headers, timeout=15)
            logger.info("Engati → %s: status=%s body=%s", phone, resp.status_code, resp.text[:200])
            if resp.ok:
                return {"success": True, "status_code": resp.status_code}
            return {"success": False, "status_code": resp.status_code, "body": resp.text}
        except requests.RequestException as exc:
            logger.exception("Engati request failed for %s", phone)
            return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Sync service — fetch data from merigate → upsert into ExportShipment
# ---------------------------------------------------------------------------


class ShipmentSyncService:
    """
    Fetches shipment data from merigate.e-meril.in and upserts into ExportShipment.

    Flow:
      1. Authenticate with merigate (SID-based, via tracker_client.py)
      2. Paginate through GetShipmentData.ashx (all pages until empty)
      3. For each row: map_api_row() → get_or_create by source_eid
         - New rows → create ExportShipment
         - Existing rows → update all fields
      4. Prune records with invoice_date older than `days_back` days

    Configuration (.env):
      SHIPMENT_TRACKER_API_URL=https://merigate.e-meril.in
      SHIPMENT_TRACKER_USERNAME=...
      SHIPMENT_TRACKER_PASSWORD=...
      SHIPMENT_TRACKER_DAYS_BACK=1  (default, overridden by --days-back flag)
    """

    @classmethod
    def sync(cls, days_back: int = 60) -> dict:
        """
        Fetch shipments from the last `days_back` days from merigate and upsert
        into ExportShipment.  Records with invoice_date older than `days_back`
        days are deleted from the local DB to keep the table lean.
        Returns {'created': N, 'updated': N, 'skipped': N, 'deleted': N, 'errors': N}
        """
        api_url = getattr(settings, "SHIPMENT_TRACKER_API_URL", "").strip()
        username = getattr(settings, "SHIPMENT_TRACKER_USERNAME", "").strip()
        password = getattr(settings, "SHIPMENT_TRACKER_PASSWORD", "").strip()

        if not api_url or not username or not password:
            logger.error("Merigate API credentials not configured")
            return {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 1}

        client = MerigateClient(api_url, username, password)
        rows = client.fetch_all_shipments(days_back=days_back)

        if not rows:
            logger.warning("Merigate returned no rows")
            return {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}

        created = updated = skipped = errors = 0
        now = timezone.now()

        for row in rows:
            try:
                mapped = map_api_row(row)
                source_eid = mapped.pop("source_eid", None)
                if not source_eid:
                    skipped += 1
                    continue

                obj, is_new = ExportShipment.objects.get_or_create(
                    source_eid=source_eid,
                    defaults={**mapped, "last_synced_at": now},
                )
                if is_new:
                    created += 1
                else:
                    for k, v in mapped.items():
                        setattr(obj, k, v)
                    obj.last_synced_at = now
                    obj.save()
                    updated += 1
            except Exception:
                logger.exception("Error upserting row: %s", row)
                errors += 1

        # ── Prune records older than days_back days or with no invoice date ─────
        cutoff = (timezone.now() - timedelta(days=days_back)).date()
        from django.db.models import Q

        old_qs = ExportShipment.objects.filter(Q(invoice_date__lt=cutoff) | Q(invoice_date__isnull=True))
        deleted = old_qs.count()
        if deleted:
            old_qs.delete()
            logger.info("Pruned %d shipment records (older than %s or no invoice date)", deleted, cutoff)

        logger.info(
            "Sync complete: created=%d updated=%d skipped=%d deleted=%d errors=%d",
            created,
            updated,
            skipped,
            deleted,
            errors,
        )
        return {"created": created, "updated": updated, "skipped": skipped, "deleted": deleted, "errors": errors}


# ---------------------------------------------------------------------------
# WhatsApp notification service — change detection + grouped send
# ---------------------------------------------------------------------------


class WhatsAppNotificationService:
    """
    Core notification engine. Detects changed shipments and sends WhatsApp alerts.

    Full Flow:
      1. Query ALL ExportShipments, compute each one's change hash.
      2. Filter to those where current_hash != last_notified_hash (i.e., data changed).
      3. For each changed shipment:
         a. Look up SalespersonMapping by (country, vertical) → get whatsapp_no.
         b. If no mapping exists → skip (logged as warning).
      4. Test mode routing:
         - If the mapped phone IS a test phone → send directly to it.
         - If the mapped phone is NOT a test phone → redirect to test_phones[0].
         - In production (test_mode=False) → send to the real phone.
      5. Group all changed shipments by destination phone number.
      6. For each phone group:
         a. Build Engati params from the FIRST shipment's data.
         b. Include the dashboard URL for that phone.
         c. Send ONE WhatsApp message via EngatiService.send().
         d. On success: update last_notified_hash + whatsapp_sent_status + whatsapp_sent_at
            for ALL shipments in the group (prevents re-notification).

    This runs after every sync (daily 11 AM via cron, or on-demand via API).
    """

    @classmethod
    def send_grouped_notifications(cls, countries: list[str] | None = None) -> dict:
        """
        For every ExportShipment whose tracked fields changed since the last
        notification, find the matching SalespersonMapping (country + vertical),
        group by WhatsApp number, and send ONE message per salesperson listing
        all their changed shipments.

        Args:
            countries: Optional list of country names to restrict notifications to.
                       If None, all countries are processed.
                       Example: ['Germany', 'France', 'United Kingdom']
                       Pass EUROPEAN_COUNTRIES to restrict to European shipments.

        In test mode:
          - All messages are redirected to TEST_PHONE_* numbers.
          - Shipments with no SalespersonMapping fall back to TEST_PHONE_1
            instead of being skipped (useful for European country demo runs
            where production mappings may not yet exist).

        Returns dict with notification statistics.
        """
        # Build queryset — optionally filtered by country list
        qs = ExportShipment.objects.all()
        if countries:
            qs = qs.filter(country__in=countries)

        # Pull shipments that have changed
        changed = [s for s in qs if s.has_changed_since_last_notification]
        if not changed:
            label = f" (countries={countries})" if countries else ""
            logger.info("No changed shipments found%s — nothing to notify", label)
            return {"sent": 0, "skipped": 0, "errors": 0}

        test_mode = _test_mode()
        test_phones = _test_phones()

        # Map (country, vertical) → salesperson WhatsApp #
        mapping_cache: dict[tuple, SalespersonMapping | None] = {}

        def get_mapping(country: str, vertical: str) -> SalespersonMapping | None:
            key = (country.lower(), vertical.lower())
            if key not in mapping_cache:
                mapping_cache[key] = SalespersonMapping.objects.filter(
                    country__iexact=country,
                    vertical__iexact=vertical,
                ).first()
            return mapping_cache[key]

        # Group changed shipments by destination WhatsApp number
        # In test mode: group under test phones instead
        groups: dict[str, list[ExportShipment]] = defaultdict(list)
        skipped = 0

        for shipment in changed:
            mapping = get_mapping(shipment.country, shipment.vertical)
            if not mapping or not mapping.whatsapp_no.strip():
                if test_mode and test_phones:
                    # In test mode: no mapping → fall back to TEST_PHONE_1 so
                    # European/unmapped country shipments still appear in the demo.
                    dest_phone = test_phones[0]
                    logger.info(
                        "Test mode fallback (no mapping) country=%s vertical=%s → %s",
                        shipment.country,
                        shipment.vertical,
                        dest_phone,
                    )
                else:
                    logger.warning(
                        "No salesperson mapping/WhatsApp for country=%s vertical=%s (EId=%s)",
                        shipment.country,
                        shipment.vertical,
                        shipment.source_eid,
                    )
                    skipped += 1
                    continue
            else:
                real_phone = mapping.whatsapp_no.strip()
                if test_mode:
                    if not test_phones:
                        logger.warning("Test mode ON but no TEST_PHONE_* configured; skipping %s", shipment.source_eid)
                        skipped += 1
                        continue
                    # If the mapped phone is already a test phone, send directly to it
                    # Otherwise redirect to the first test phone
                    if real_phone in test_phones:
                        dest_phone = real_phone
                    else:
                        dest_phone = test_phones[0]
                        logger.info("Test mode: redirecting %s → %s", real_phone, dest_phone)
                else:
                    dest_phone = real_phone

            groups[dest_phone].append(shipment)

        sent = errors = 0

        for phone, shipments_for_phone in groups.items():
            # Build summary params for the FIRST (or only) shipment;
            # include a count so the template can say "and N more"
            first = shipments_for_phone[0]
            dashboard_url = DashboardService.get_url_for_phone(phone)

            # We send one Engati message per salesperson phone with the first
            # changed shipment's key details.  Recipients see the dashboard for
            # the full list.
            params = {
                "sc_shpmnt_no": first.awb_no or first.invoice_no or str(first.source_eid),
                "sc_consignee_name": first.consignee_name,
                "sc_inv_no": first.invoice_no,
                "sc_inv_date": _fmt_date(first.invoice_date),
                "sc_etd": _fmt_date(first.etd),
                "sc_current_status": first.status,
                "sc_remarks": first.remarks[:200] if first.remarks else "",
                "sc_detail_url": dashboard_url,
            }

            result = EngatiService.send(phone, params)
            if result["success"]:
                # Mark all shipments in this group as notified
                for shipment in shipments_for_phone:
                    shipment.last_notified_hash = shipment.compute_change_hash()
                    shipment.whatsapp_sent_status = True
                    shipment.whatsapp_sent_at = timezone.now()
                    shipment.save(
                        update_fields=[
                            "last_notified_hash",
                            "whatsapp_sent_status",
                            "whatsapp_sent_at",
                        ]
                    )
                sent += 1
                logger.info("Notification sent to %s for %d shipment(s)", phone, len(shipments_for_phone))
            else:
                errors += 1
                logger.error("Notification failed for %s: %s", phone, result.get("error") or result.get("body"))

        return {"sent": sent, "skipped": skipped, "errors": errors, "changed_shipments": len(changed)}
