"""Currency normalization for VAT invoices.

Fetches a live exchange rate (free, no API key) and caches it for the day.
If the two currencies are the same, returns rate = 1 with no network call.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_TTL = 60 * 60 * 12  # 12 hours
TWO = Decimal("0.01")


def _r2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def get_rate(from_ccy, to_ccy):
    """Return (rate: Decimal, source: str) to convert 1 from_ccy -> to_ccy."""
    f = (from_ccy or "").upper().strip()
    t = (to_ccy or "").upper().strip()
    if not f or not t or f == t:
        return Decimal("1"), "same-currency"

    cache_key = f"vat_fx:{f}:{t}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Decimal(str(cached)), "cache"

    provider = getattr(settings, "VAT_FX_PROVIDER", "open.er-api.com")
    try:
        # open.er-api.com: https://open.er-api.com/v6/latest/USD  -> rates{...}
        url = f"https://{provider}/v6/latest/{f}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        rate = payload.get("rates", {}).get(t)
        if rate is None:
            raise ValueError(f"No rate for {f}->{t} from {provider}")
        rate = Decimal(str(rate))
        cache.set(cache_key, str(rate), _CACHE_TTL)
        return rate, provider
    except Exception as exc:  # noqa: BLE001
        logger.error("FX lookup %s->%s failed: %s", f, t, exc)
        raise RuntimeError(f"Could not fetch exchange rate {f}->{t}: {exc}")


def convert(amount, from_ccy, to_ccy, rate=None):
    """Convert amount from_ccy -> to_ccy.

    Returns {'converted': Decimal, 'rate': Decimal, 'source': str}.
    Pass an explicit rate to override the live lookup (manual override).
    """
    amount = Decimal(str(amount or 0))
    if rate is not None:
        rate = Decimal(str(rate))
        return {"converted": _r2(amount * rate), "rate": rate, "source": "manual"}

    rate, source = get_rate(from_ccy, to_ccy)
    return {"converted": _r2(amount * rate), "rate": rate, "source": source}
