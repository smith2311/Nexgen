"""Translate OCR text to English via the internal translation webhook.

POST body: {"text": "<raw text>", "targetLanguage": "en"}
Configured by settings.VAT_TRANSLATE_WEBHOOK_URL.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def to_english(text, source_language=None):
    """Returns {'translated': str, 'source_language': str|None, 'provider': str}."""
    if not text or not text.strip():
        return {"translated": "", "source_language": source_language, "provider": "none"}
    return _webhook(text, source_language)


def _webhook(text, source_language):
    url = getattr(settings, "VAT_TRANSLATE_WEBHOOK_URL", "")
    if not url:
        logger.warning("VAT_TRANSLATE_WEBHOOK_URL not configured — passing text through")
        return {"translated": text, "source_language": source_language, "provider": "none"}
    try:
        resp = requests.post(
            url,
            json={"text": text[:9000], "targetLanguage": "en"},
            timeout=60,
        )
        resp.raise_for_status()
        return {"translated": _read(resp), "source_language": source_language, "provider": "webhook"}
    except Exception as exc:  # noqa: BLE001 — never break the pipeline on translation
        logger.error("Translate webhook failed: %s", exc)
        # Fall back to the original text so downstream extraction still runs.
        return {"translated": text, "source_language": source_language, "provider": "error"}


def _read(resp):
    """Tolerant of several response shapes until the exact one is confirmed."""
    if "application/json" in resp.headers.get("content-type", ""):
        data = resp.json()
        if isinstance(data, str):
            return data
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            for k in ("translatedText", "translation", "translated", "text",
                      "result", "output", "data"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v
        return resp.text
    return resp.text
