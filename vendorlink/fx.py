"""Exchange rates, for showing every vendor quote in INR.

Vendors bid in their own currency — a German 3PL may quote EUR or, just as
often, USD. Meril reads the results in INR, so every figure on every VendorLink
screen is converted here.

**Rates are stored, not just cached.** A quote converted today and the same
quote converted next month are different numbers, and a procurement record has
to be able to say which rate it used. Each fetch is written to ``FxRate`` with
its timestamp, so a converted figure can always be shown with the rate and the
age behind it.

**Nothing is ever converted at a guessed rate.** If no rate is available the
conversion returns ``None`` and the caller shows the vendor's own currency
untouched. A missing rate must never degrade into a plausible-looking wrong
number: a bad rate on a bid still reads as a price, and would be acted on.

**Providers.** Which service to call is configuration, because an API key does
not identify its provider. Two shapes are supported:

* ``open-er-api`` (the default) — ``https://open.er-api.com/v6/latest/<base>``,
  HTTPS and no key required, so conversion works before anyone signs up.
* ``exchangerate-api`` — the keyed v6 endpoint of the same service, used when
  ``CURRENCY_CONVERSION_API_KEY`` is set.

A third shape, ``http://currencies.apps.grandtrunk.net/getlatest/{from}/{to}``,
was suggested but is deliberately not the default: it is plain HTTP, so the
rate can be altered in transit, and it takes no key at all — which is what
made the presence of a configured key ambiguous in the first place. It is
supported as ``grandtrunk`` for anyone who wants it.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: What everything is reported in.
TARGET = "INR"

#: How long a stored rate is treated as current. Procurement decisions are not
#: made on intraday moves, and refetching per page view would put an external
#: service in the path of every render.
MAX_AGE_HOURS = 12

#: Never let a page hang on the rates service.
TIMEOUT_SECONDS = 6

#: Country -> the currency a vendor there is most likely to quote. Used only to
#: preselect the bid form; the vendor can always choose another, which is the
#: whole point — plenty of German suppliers quote in USD.
COUNTRY_CURRENCY = {
    "India": "INR",
    "Germany": "EUR",
    "France": "EUR",
    "Italy": "EUR",
    "Spain": "EUR",
    "Netherlands": "EUR",
    "Belgium": "EUR",
    "Ireland": "EUR",
    "Austria": "EUR",
    "Portugal": "EUR",
    "United States": "USD",
    "United States of America": "USD",
    "United Kingdom": "GBP",
    "United Arab Emirates": "AED",
    "Saudi Arabia": "SAR",
    "Singapore": "SGD",
    "Japan": "JPY",
    "China": "CNY",
    "Australia": "AUD",
    "Canada": "CAD",
    "Switzerland": "CHF",
}


def default_currency_for(country):
    """The currency to preselect for a vendor in ``country``.

    A default, never a constraint — see COUNTRY_CURRENCY.
    """
    return COUNTRY_CURRENCY.get((country or "").strip(), "USD")


def _provider():
    return (getattr(settings, "CURRENCY_CONVERSION_PROVIDER", "") or "open-er-api").strip()


def _api_key():
    return (getattr(settings, "CURRENCY_CONVERSION_API_KEY", "") or "").strip()


def _endpoint(base):
    """(url, kind) for the configured provider, or (None, None) if unusable."""
    provider = _provider()
    key = _api_key()

    if provider == "exchangerate-api":
        if not key:
            # Falling back rather than failing: the keyless endpoint of the
            # same service answers the same question.
            return f"https://open.er-api.com/v6/latest/{base}", "rates"
        return f"https://v6.exchangerate-api.com/v6/{key}/latest/{base}", "conversion_rates"
    if provider == "fixer":
        return f"https://data.fixer.io/api/latest?access_key={key}&base={base}", "rates"
    if provider == "openexchangerates":
        return f"https://openexchangerates.org/api/latest.json?app_id={key}&base={base}", "rates"
    if provider == "currencylayer":
        return f"https://api.currencylayer.com/live?access_key={key}&source={base}", "quotes"
    if provider == "grandtrunk":
        return f"http://currencies.apps.grandtrunk.net/getlatest/{base}/{TARGET}", "bare"
    return f"https://open.er-api.com/v6/latest/{base}", "rates"


def _parse(payload, kind, base):
    """Pull the base->INR rate out of a provider response."""
    if kind == "bare":
        return Decimal(str(payload).strip())
    if kind == "quotes":
        # currencylayer keys its quotes as e.g. "USDINR".
        value = payload.get("quotes", {}).get(f"{base}{TARGET}")
        return None if value is None else Decimal(str(value))
    value = payload.get(kind, {}).get(TARGET)
    return None if value is None else Decimal(str(value))


def fetch_rate(base):
    """Ask the provider for base->INR. Returns a Decimal, or None on any failure.

    Deliberately swallows every error into None. A rates service being down,
    rate-limited or newly reshaped must not 500 a procurement screen — the
    caller renders the original currency instead.
    """
    base = (base or "").upper()
    if not base or base == TARGET:
        return Decimal("1")

    url, kind = _endpoint(base)
    if not url:
        return None

    try:
        import requests

        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        rate = _parse(response.text if kind == "bare" else response.json(), kind, base)
    except Exception as exc:
        logger.warning("FX lookup failed for %s->%s via %s: %s", base, TARGET, _provider(), exc)
        return None

    if rate is None or rate <= 0:
        logger.warning("FX lookup for %s->%s returned no usable rate", base, TARGET)
        return None
    return rate


def rate_to_inr(base, allow_fetch=True):
    """base->INR, from storage when it is fresh, otherwise fetched and stored.

    A stale stored rate is preferred over no rate at all when a fetch fails:
    "converted at last week's rate, and here is the date" is useful, while
    nothing at all leaves the reader comparing currencies by eye.
    """
    from .models import FxRate

    base = (base or "").upper()
    if not base or base == TARGET:
        return Decimal("1")

    stored = FxRate.objects.filter(base=base).first()
    if stored and not stored.is_stale(MAX_AGE_HOURS):
        return stored.rate
    if not allow_fetch:
        return stored.rate if stored else None

    fresh = fetch_rate(base)
    if fresh is None:
        return stored.rate if stored else None

    FxRate.objects.update_or_create(
        base=base, defaults={"rate": fresh, "fetched_at": timezone.now()},
    )
    return fresh


def to_inr(amount, currency, allow_fetch=True):
    """``amount`` in ``currency`` as INR, or None when no rate is known."""
    if amount is None:
        return None
    rate = rate_to_inr(currency, allow_fetch=allow_fetch)
    if rate is None:
        return None
    try:
        return (Decimal(str(amount)) * rate).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def rates_for(currencies, allow_fetch=True):
    """{currency: rate} for several currencies in one pass.

    Used by the screens that convert a whole table: without this each cell
    would hit storage on its own.
    """
    wanted = {(c or "").upper() for c in currencies if c}
    return {code: rate_to_inr(code, allow_fetch=allow_fetch) for code in wanted}


def describe(currency):
    """How a converted figure should be labelled: the rate and its age.

    A converted number shown bare is indistinguishable from a quoted one, so
    every screen that converts has to be able to say what it used.
    """
    from .models import FxRate

    code = (currency or "").upper()
    if not code or code == TARGET:
        return None
    stored = FxRate.objects.filter(base=code).first()
    if not stored:
        return None
    return {
        "base": code,
        "rate": stored.rate,
        "fetched_at": stored.fetched_at,
        "is_stale": stored.is_stale(MAX_AGE_HOURS),
        "provider": _provider(),
    }
