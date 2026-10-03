"""Client for the AI Data Extractor API (http://10.10.30.160:5001).

Handles JWT login + the V3 VAT-extraction endpoint, which performs OCR,
translation (output always English) and structured field extraction in a
single call. Used by services/ocr.py as the 'ai_extractor' provider.
"""
import logging
import threading

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Cached JWT shared across requests; refreshed on expiry / 401.
_token = None
_lock = threading.Lock()


def _base_url():
    return getattr(settings, "VAT_AI_EXTRACTOR_URL", "http://10.10.30.160:5001").rstrip("/")


def _login():
    """POST /api/v1/login -> JWT string. Raises on failure.

    The live API expects a raw JSON body with 'email' + 'password'
    (the PDF reference's form-data/username spec is out of date).
    """
    user = getattr(settings, "VAT_AI_EXTRACTOR_USERNAME", "")
    pwd = getattr(settings, "VAT_AI_EXTRACTOR_PASSWORD", "")
    if not user or not pwd:
        raise RuntimeError("VAT_AI_EXTRACTOR_USERNAME/PASSWORD not configured.")
    resp = requests.post(
        f"{_base_url()}/api/v1/login",
        json={"email": user, "password": pwd},
        timeout=30,
    )
    if not resp.ok:
        # Surface the server's own message (e.g. "Login failed") instead of a bare 500.
        try:
            detail = (resp.json() or {}).get("error") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"AI Extractor login failed ({resp.status_code}): {detail}")
    token = (resp.json() or {}).get("token")
    if not token:
        raise RuntimeError("AI Extractor login returned no token.")
    return token


def _get_token(force_refresh=False):
    global _token
    with _lock:
        if _token is None or force_refresh:
            _token = _login()
        return _token


def extract_v3(file_bytes, mime_type, filename="invoice"):
    """POST /api/v3/extract -> parsed JSON dict (raw_text + English fields).

    Re-authenticates once if the token has expired (401).
    """
    url = f"{_base_url()}/api/v3/extract"
    files = {"file": (filename or "invoice", file_bytes,
                      mime_type or "application/octet-stream")}

    for attempt in (1, 2):
        token = _get_token(force_refresh=(attempt == 2))
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            timeout=120,
        )
        if resp.status_code == 401 and attempt == 1:
            logger.info("AI Extractor token expired — re-authenticating.")
            continue
        if not resp.ok:
            # The API returns 500 for a per-document model/processing error;
            # surface its own message instead of a bare HTTP error string.
            try:
                body = resp.json() or {}
                detail = body.get("error") or body.get("message") or resp.text
            except ValueError:
                detail = resp.text
            raise RuntimeError(
                f"AI Extractor /api/v3/extract failed ({resp.status_code}): "
                f"{str(detail)[:300]}"
            )
        return resp.json() or {}
