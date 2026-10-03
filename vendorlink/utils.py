import functools
import json as _json

import geonamescache as _gnc


# ── Geonamescache helpers ──────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _gc():
    return _gnc.GeonamesCache()


@functools.lru_cache(maxsize=1)
def get_sorted_countries():
    """Sorted list of country dicts from geonamescache (by name)."""
    return sorted(_gc().get_countries().values(), key=lambda c: c["name"])


@functools.lru_cache(maxsize=None)
def get_cities_for_country(iso_code):
    """Sorted, deduplicated city names for a 2-letter ISO country code."""
    cities = [
        c["name"] for c in _gc().get_cities().values()
        if c["countrycode"] == iso_code.upper()
    ]
    return sorted(set(cities))


def geonames_country_choices(blank_label="Select a country…"):
    """Django ChoiceField choices list — (country_name, country_name)."""
    return [("", blank_label)] + [(c["name"], c["name"]) for c in get_sorted_countries()]


def geonames_valid_names():
    """Set of all valid country names from geonamescache."""
    return {c["name"] for c in get_sorted_countries()}


@functools.lru_cache(maxsize=1)
def _canonical_country_index():
    """casefolded country name -> the master's official spelling."""
    return {c["name"].casefold(): c["name"] for c in get_sorted_countries()}


def canonical_country(value):
    """One country string reduced to the single spelling the app should show.

    ``Vendor.country`` is a free-text CharField. The add/edit form has always
    been a strict select off the Country master, but rows also arrive by
    spreadsheet import, by shell, and from years of earlier free-text entry —
    so the column really holds ``"India"``, ``"india"``, ``"INDIA"`` and
    ``" India "`` at the same time. Anything that did ``.values("country")
    .distinct()`` over that column therefore listed one country four times, and
    an exact-match filter on any one of them silently hid the vendors stored
    under the other three.

    Whitespace is collapsed first, then the name is matched case-insensitively
    against the master and replaced with the master's own spelling, so the
    result is the same string the form would have produced. A name the master
    does not know (a disputed territory, a typo, an internal label) is kept
    rather than discarded — losing a vendor's country because geonames has not
    heard of it would be worse than showing it as typed — and is only tidied to
    title case so it at least stops duplicating itself by capitalisation.

    Returns "" for anything blank, which callers use to drop empty options.
    """
    if not value:
        return ""
    collapsed = " ".join(str(value).split())
    if not collapsed:
        return ""
    known = _canonical_country_index().get(collapsed.casefold())
    return known if known else collapsed.title()


def canonical_countries(values):
    """De-duplicated, blank-free, case-insensitively sorted canonical names."""
    unique = {canonical_country(value) for value in values}
    unique.discard("")
    return sorted(unique, key=str.casefold)


def geonames_iso_map_json():
    """JSON string: { "India": "IN", "United Arab Emirates": "AE", ... }"""
    m = {c["name"]: c["iso"] for c in get_sorted_countries()}
    return _json.dumps(m)


def geonames_dial_map_json():
    """JSON string: { "India": "+91", "United Arab Emirates": "+971", ... }
    Used to default the phone field's ISD code when a vendor picks a country.
    """
    m = {c["name"]: f"+{c['phone']}" for c in get_sorted_countries() if c.get("phone")}
    return _json.dumps(m)


# ── Continent → country ──────────────────────────────────────────────────────
# Every country row in geonamescache carries a ``continentcode`` (all 252 of
# them do — checked, not assumed), so the two-level picker reads the same master
# the one-level picker always did. A separate continent→country table would be a
# second source of truth that could disagree with the country dropdown next to
# it, which is exactly the failure the Country Master note at the top of
# ``constants.py`` exists to prevent.

#: Display order for the continent dropdown. Meril's vendor base is Asia and
#: Europe first, so alphabetical would bury the two continents that carry almost
#: every RFI. Anything geonames adds later lands after these, alphabetically.
CONTINENT_ORDER = ["AS", "EU", "AF", "NA", "SA", "OC", "AN"]


@functools.lru_cache(maxsize=1)
def _continents():
    """[(code, name), …] in CONTINENT_ORDER, then anything unlisted."""
    raw = _gc().get_continents()
    names = {code: row["name"] for code, row in raw.items()}
    ordered = [code for code in CONTINENT_ORDER if code in names]
    ordered += [code for code in sorted(names) if code not in CONTINENT_ORDER]
    return [(code, names[code]) for code in ordered]


def continent_choices(blank_label="All continents"):
    """Django ChoiceField choices — (continent code, continent name)."""
    return [("", blank_label)] + list(_continents())


def continent_name(code):
    """"AS" -> "Asia". "" for a code the master does not know."""
    return dict(_continents()).get((code or "").upper(), "")


def continent_codes():
    """Set of valid continent codes, for validating a submitted value."""
    return {code for code, _name in _continents()}


@functools.lru_cache(maxsize=1)
def _countries_by_continent():
    """continent code -> sorted list of that continent's country names."""
    out = {}
    for country in get_sorted_countries():
        code = country.get("continentcode")
        if code:
            out.setdefault(code, []).append(country["name"])
    return out


@functools.lru_cache(maxsize=1)
def _continent_by_country():
    """canonical country name -> its continent code."""
    return {
        country["name"]: country["continentcode"]
        for country in get_sorted_countries()
        if country.get("continentcode")
    }


def continent_of(country):
    """The continent code a country sits in, or "" if unknown.

    Goes through :func:`canonical_country` so a stored variant spelling
    ("india", " India ") still resolves — the same reason every other reader of
    that column does.
    """
    return _continent_by_country().get(canonical_country(country), "")


def countries_in_continent(code):
    """Country names in one continent. Empty list for an unknown code."""
    return list(_countries_by_continent().get((code or "").upper(), []))


def continent_country_map_json():
    """JSON string: { "AS": ["Afghanistan", …], "EU": […], … }.

    Embedded once per page and read by ``vl-continent-country.js`` to repopulate
    the country select. Doing the cascade in the browser rather than over a
    round-trip is what keeps the picker usable on the RFI form, where the
    country choice sits mid-form and a reload would lose the rest of it.
    """
    return _json.dumps(_countries_by_continent())


def filter_countries_by_continent(names, code):
    """Subset of ``names`` inside continent ``code``; all of them if code is "".

    Used to narrow the country options a *filter bar* offers, which are the
    countries actually present in the data rather than the whole master — so
    picking Europe cannot offer a country no vendor is registered in.
    """
    if not code:
        return list(names)
    allowed = set(countries_in_continent(code))
    return [name for name in names if canonical_country(name) in allowed]
