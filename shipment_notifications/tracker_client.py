"""
shipment_notifications/tracker_client.py
========================================
HTTP client for the merigate.e-meril.in shipment data API.

This module handles:
  1. Authentication: POST /handler/GenerateSID.ashx → returns a session ID (SID).
     SID is cached for 55 minutes (server-side expiry is ~60 min).
  2. Data fetching: GET /handler/GetShipmentData.ashx?page=N with sid header.
     Paginates until empty response (max 200 pages safety cap).
  3. Field mapping: map_api_row() converts camelCase API field names
     (soNo, verticalName, awbNo, etc.) to Django model fields (so_no, vertical, awb_no, etc.).
  4. Sync key generation: _sync_key() creates a stable MD5 hash from
     (soNo, vertical, awbNo, country, invoiceDate) used as ExportShipment.source_eid.

Configuration (.env):
  SHIPMENT_TRACKER_API_URL=https://merigate.e-meril.in
  SHIPMENT_TRACKER_USERNAME=...
  SHIPMENT_TRACKER_PASSWORD=...
  SHIPMENT_TRACKER_DAYS_BACK=1

Used by: ShipmentSyncService.sync() in services.py
"""

import hashlib
import logging
from datetime import datetime, timedelta

from django.core.cache import cache

import requests

logger = logging.getLogger(__name__)

# Maximum pages to fetch per sync run (safety cap to prevent infinite loops)
MAX_PAGES = 200


class MerigateClient:
    """
    Client for merigate.e-meril.in shipment data API.

    Authentication flow:
        POST /handler/GenerateSID.ashx  (form: username, password)  → plain-text SID
    Data fetch (paginated):
        GET  /handler/GetShipmentData.ashx?page=N  header: sid: <SID>
        Repeat until the response is an empty list or non-200.
    """

    # SID is cached for 55 minutes (server-side expiry is ~60 min)
    _CACHE_KEY_SID = "merigate_sid"
    _SID_TTL = 55 * 60  # seconds

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    # ── Authentication ────────────────────────────────────────────────────────

    def _generate_sid(self) -> str | None:
        """POST to GenerateSID.ashx with username/password to get a new session ID.
        The SID is cached for 55 minutes to avoid re-auth on every API call."""
        url = f"{self.base_url}/handler/GenerateSID.ashx"
        try:
            response = requests.post(
                url,
                data={"username": self.username, "password": self.password},
                timeout=15,
            )
            if response.status_code == 200:
                # Response may be plain-text SID or JSON {"success":true,"sid":"..."}
                try:
                    data = response.json()
                    sid = data.get("sid") or data.get("SID") or data.get("SessionId")
                except ValueError:
                    sid = response.text.strip()
                if sid:
                    cache.set(self._CACHE_KEY_SID, sid, self._SID_TTL)
                    logger.info("Merigate SID generated: %s...", sid[:20])
                    return sid
                logger.error("Could not extract SID from response: %s", response.text[:200])
            else:
                logger.error("GenerateSID failed: %s %s", response.status_code, response.text[:200])
        except requests.RequestException:
            logger.exception("Error calling GenerateSID.ashx")
        return None

    def _get_sid(self) -> str | None:
        """Return a valid SID from cache, or generate a new one if expired."""
        sid = cache.get(self._CACHE_KEY_SID)
        if sid:
            logger.debug("Using cached Merigate SID")
            return sid
        return self._generate_sid()

    # ── Data fetch ────────────────────────────────────────────────────────────

    def fetch_all_shipments(self, days_back: int = 60) -> list[dict]:
        """
        Fetch shipments from GetShipmentData.ashx filtered to the last `days_back` days
        (by invoice date). Paginates until all pages are consumed.
        """
        sid = self._get_sid()
        if not sid:
            logger.error("Cannot fetch shipments — no valid SID")
            return []

        now = datetime.now()
        start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d 00:00:00.000")
        end = now.strftime("%Y-%m-%d 23:59:59.999")
        base_params = {"searchby": "INVOICEDATE", "startdate": start, "enddate": end}
        logger.info("Fetching shipments from %s to %s (last %d days)", start[:10], end[:10], days_back)

        all_rows: list[dict] = []
        url = f"{self.base_url}/handler/GetShipmentData.ashx"

        for page in range(1, MAX_PAGES + 1):
            try:
                response = requests.get(
                    url,
                    params={"page": page, **base_params},
                    headers={"sid": sid},
                    timeout=30,
                )
            except requests.RequestException:
                logger.exception("Network error fetching page %d", page)
                break

            if response.status_code == 401:
                # SID expired mid-run — regenerate once and retry this page
                logger.warning("SID expired on page %d, regenerating...", page)
                cache.delete(self._CACHE_KEY_SID)
                sid = self._generate_sid()
                if not sid:
                    break
                try:
                    response = requests.get(
                        url,
                        params={"page": page},
                        headers={"sid": sid},
                        timeout=30,
                    )
                except requests.RequestException:
                    logger.exception("Retry error on page %d", page)
                    break

            if response.status_code != 200:
                logger.error("GetShipmentData page %d returned %s", page, response.status_code)
                break

            try:
                data = response.json()
            except ValueError:
                logger.error("Invalid JSON on page %d: %s", page, response.text[:200])
                break

            # Normalise: API may return a list, a dict with a list, or an empty sentinel
            if isinstance(data, dict):
                rows = data.get("data") or data.get("shipments") or data.get("Data") or []
                if not rows and not data:
                    break
            elif isinstance(data, list):
                rows = data
            else:
                break

            if not rows:
                logger.info("Empty page at page %d — stopping pagination", page)
                break

            all_rows.extend(rows)
            logger.info("Fetched page %d: %d rows (total so far: %d)", page, len(rows), len(all_rows))

        logger.info("Total shipment rows fetched: %d", len(all_rows))
        return all_rows


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------
# These functions convert raw API response values into Python types suitable
# for the ExportShipment Django model.


def _str(val) -> str:
    """Safely convert a value to a stripped string, or '' if None."""
    return str(val).strip() if val is not None else ""


def _date(val):
    """Parse a date string (YYYY-MM-DD or YYYY-MM-DDThh:mm:ss) → date or None."""
    if not val:
        return None
    s = str(val).strip()
    # Take first 10 chars to get YYYY-MM-DD portion regardless of time suffix
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _decimal(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _sync_key(row: dict) -> str:
    """
    Derive a stable 16-char hex key from the row's natural identifiers.
    Used as ExportShipment.source_eid when the API does not return an integer EId.

    Components: soNo + vertical + awbNo + country + invoiceDate
    This combination uniquely identifies a shipment across syncs.
    MD5 is used for compactness (not security), truncated to 16 chars.
    """
    parts = [
        str(row.get("soNo") or ""),
        str(row.get("vertical") or ""),
        str(row.get("awbNo") or ""),
        str(row.get("country") or ""),
        str(row.get("invoiceDate") or ""),
    ]
    key = "|".join(parts)
    return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:16]


def map_api_row(row: dict) -> dict:
    """
    Convert a raw GetShipmentData row (camelCase keys from the .ashx handler)
    to a dict that maps directly to ExportShipment model fields.

    API field → Model field mapping:
      soNo → so_no, vertical → vertical, country → country,
      consigneeName → consignee_name, invoiceNo → invoice_no,
      etd → etd, eta → eta, awbNo → awb_no, status → status, etc.

    Also generates source_eid via _sync_key() for upsert matching.
    """
    return {
        "source_eid": _sync_key(row),
        "vertical": _str(row.get("vertical")),
        "country": _str(row.get("country")),
        "consignee_name": _str(row.get("consigneeName")),
        "so_no": _str(row.get("soNo")),
        "invoice_no": _str(row.get("invoiceNo")),
        "invoice_date": _date(row.get("invoiceDate")),
        "invoice_qty": _decimal(row.get("invoiceQty")),
        "invoice_value": _decimal(row.get("invoiceValue")),
        "currency": _str(row.get("currency")),
        "incoterms": _str(row.get("incoterms")),
        "forwarder": _str(row.get("forwarder")),
        "awb_no": _str(row.get("awbNo")),
        "awb_date": _date(row.get("awbDate")),
        "airline": _str(row.get("airline")),
        "dispatch_date": _date(row.get("dispatchDate")),
        "etd": _date(row.get("etd")),
        "eta": _date(row.get("eta")),
        "revised_etd": _date(row.get("revisedETD")),
        "revised_eta": _date(row.get("revisedETA")),
        "actual_eta": _date(row.get("actualETA")),
        "actual_arrival": _date(row.get("actualArrival")),
        "status": _str(row.get("status")),
        "remarks": _str(row.get("remarks")),
    }
