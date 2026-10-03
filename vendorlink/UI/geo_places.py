"""Countries → states → cities: the geographic master the map and the forms read.

WHY THIS EXISTS
---------------
VendorLink records a vendor's country as a proper value, but ``state`` and
``city`` are free-text columns that are, on the live data today, empty on every
row. What the rows *do* carry is a warehouse address::

    "530 Collins St, Melbourne VIC 3000, Australia"
    "Mumbai, Maharashtra"
    "Łowicka 33, 99-120 Piątek"

So the state and the city are in there — they are just not in a column. This
module is the reference data that gets them out: every country, every state or
province inside it, and every city inside that, with coordinates. Given a
country and a scrap of address text, :func:`resolve` returns which city and
which state it names.

WHERE THE DATA COMES FROM
-------------------------
Two sources, both already trusted by this project:

* ``geonamescache`` (a dependency of the app, used by the registration form's
  country and city pickers) — 32,000+ cities with country, ``admin1code``,
  coordinates and population.
* ``data/admin1_names.json`` — 3,865 first-level divisions from GeoNames'
  ``admin1CodesASCII`` export, vendored here because geonamescache ships the
  admin1 *code* on each city but only names the divisions of the United States.
  Without it a city resolves to "IN.16" rather than "Maharashtra".

The join is ``countrycode`` + ``admin1code``, which is why the two files can be
refreshed independently.

MATCHING IS DELIBERATELY CONSERVATIVE
-------------------------------------
An address is scanned right to left, because that is where locality lives —
"530 Collins St, Melbourne VIC 3000" has a street name at the front that a
left-to-right scan would happily match against some other country's town. A
match must be a whole run of words, never a substring, so "Mumbai Road" does
not become Mumbai. Anything unmatched stays visibly unmatched rather than being
guessed at: the map groups it under "Unspecified", which is information, while
a wrong state is a lie that looks like data.
"""

from __future__ import annotations

import functools
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

import geonamescache
from django.utils.text import slugify

DATA = Path(__file__).resolve().parent / "data"

#: Longest multi-word place name worth testing ("Sri Jayawardenepura Kotte").
MAX_PHRASE_WORDS = 5

#: Segments that are only a postcode, a house number or a PO box carry no
#: locality and are skipped before matching.
NOISE_RE = re.compile(r"^[\d\s\-/#.]+$")

#: Postal-style abbreviations that appear in addresses but are not the country's
#: GeoNames admin1 code.
#:
#: The United States needs no entry — its admin1 code *is* the postal
#: abbreviation ("US.CA" → California), so the generic code lookup already
#: matches "CA". Australia and Canada number their divisions instead, and
#: Australian addresses are written with the abbreviation almost without
#: exception, which is precisely the case on the live vendor rows.
ABBREVIATIONS: Dict[str, Dict[str, str]] = {
    "AU": {
        "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland",
        "SA": "South Australia", "WA": "Western Australia", "TAS": "Tasmania",
        "NT": "Northern Territory", "ACT": "Australian Capital Territory",
    },
    "CA": {
        "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia",
        "AB": "Alberta", "MB": "Manitoba", "SK": "Saskatchewan",
        "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
        "PE": "Prince Edward Island", "YT": "Yukon", "NT": "Northwest Territories",
        "NU": "Nunavut",
    },
}


# ── Normalisation ───────────────────────────────────────────────────────────

def normalise(value: str) -> str:
    """One spelling for comparison: unaccented, lower case, single-spaced.

    Accents have to go or "Piątek" never matches the address that spells it
    "Piatek", and "Yucatán" never matches "Yucatan". Punctuation becomes space
    rather than nothing, so "St.Louis" and "St Louis" land on the same key.
    """
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return " ".join(text.split()).casefold()


# ── The master, loaded once ─────────────────────────────────────────────────

#: Smallest city the master carries, overridable in settings.
#:
#: geonamescache ships four extracts — towns above 15,000 people (the default,
#: 32k rows, ~80MB resident), 5,000 (66k rows, ~130MB), 1,000 (162k rows,
#: ~257MB) and 500. 15,000 is the default here because it is what
#: ``vendorlink.utils.get_cities_for_country`` already feeds the registration
#: form's city picker, and a master that offered cities the form refuses would
#: be its own bug report.
#:
#: Lower it when addresses in small towns matter more than the memory: at 5,000
#: the Polish village rows on the live data start resolving. Set
#: ``VENDORLINK_GEO_MIN_CITY_POPULATION`` in settings or the environment.
DEFAULT_MIN_CITY_POPULATION = 15000


def min_city_population() -> int:
    """settings first, then the environment, then the default.

    Both, because this project configures the container from the environment
    and its settings module from Python, and a knob that answered to only one
    of the two would look broken from whichever side did not work.
    """
    from django.conf import settings
    value = getattr(settings, "VENDORLINK_GEO_MIN_CITY_POPULATION", None)
    if value is None:
        value = os.environ.get("VENDORLINK_GEO_MIN_CITY_POPULATION")
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MIN_CITY_POPULATION


@functools.lru_cache(maxsize=1)
def _gc() -> geonamescache.GeonamesCache:
    return geonamescache.GeonamesCache(min_city_population=min_city_population())


@functools.lru_cache(maxsize=1)
def _admin1() -> Dict[str, Dict[str, str]]:
    """``{"IN": {"16": "Maharashtra", ...}, ...}``"""
    with (DATA / "admin1_names.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@functools.lru_cache(maxsize=1)
def _countries_by_iso() -> Dict[str, Dict[str, Any]]:
    return {c["iso"].upper(): c for c in _gc().get_countries().values()}


@functools.lru_cache(maxsize=1)
def _cities_by_iso() -> Dict[str, List[Dict[str, Any]]]:
    """Every city grouped by country, largest first.

    Population order is what makes an ambiguous name resolve sensibly: three
    countries have a "Springfield", and within one country the biggest is the
    one an address almost always means.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    names = _admin1()

    for city in _gc().get_cities().values():
        iso2 = (city.get("countrycode") or "").upper()
        if not iso2:
            continue
        code = city.get("admin1code") or ""
        grouped.setdefault(iso2, []).append({
            "name": city["name"],
            "state": names.get(iso2, {}).get(code, ""),
            "state_code": code,
            "lat": round(float(city["latitude"]), 5),
            "lon": round(float(city["longitude"]), 5),
            "population": int(city.get("population") or 0),
        })

    for rows in grouped.values():
        rows.sort(key=lambda r: (-r["population"], r["name"]))
    return grouped


@functools.lru_cache(maxsize=None)
def _city_lookup(iso2: str) -> Dict[str, Dict[str, Any]]:
    """Normalised city name → the biggest city of that name in the country."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _cities_by_iso().get(iso2.upper(), []):
        out.setdefault(normalise(row["name"]), row)      # list is already sorted
    return out


@functools.lru_cache(maxsize=None)
def _state_lookup(iso2: str) -> Dict[str, str]:
    """Normalised state name, GeoNames code, or postal abbreviation → state name."""
    iso2 = iso2.upper()
    out: Dict[str, str] = {}
    for code, name in _admin1().get(iso2, {}).items():
        out[normalise(name)] = name
        # "US.CA" → matches a bare "CA" in an address. Purely numeric codes
        # ("IN.16") are skipped: a house number would match them.
        if not code.isdigit():
            out.setdefault(normalise(code), name)
    for abbreviation, name in ABBREVIATIONS.get(iso2, {}).items():
        out.setdefault(normalise(abbreviation), name)
    return out


# ── The list: countries → states → cities ───────────────────────────────────

@functools.lru_cache(maxsize=1)
def countries() -> List[Dict[str, Any]]:
    """Every country that has at least one city in the master, by name."""
    rows = []
    for iso2, cities_in in _cities_by_iso().items():
        meta = _countries_by_iso().get(iso2)
        if not meta:
            continue
        rows.append({
            "iso2": iso2,
            "iso3": meta.get("iso3", ""),
            "name": meta["name"],
            "continent": meta.get("continentcode", ""),
            "states": len(_admin1().get(iso2, {})),
            "cities": len(cities_in),
        })
    return sorted(rows, key=lambda r: r["name"])


@functools.lru_cache(maxsize=None)
def states(iso2: str) -> List[Dict[str, Any]]:
    """Every state / province / region of one country, by name.

    Divisions with no city in the master are still listed — they exist whether
    or not GeoNames' city extract happens to hold a town in them, and a form
    offering a partial list of states is worse than one offering all of them.
    """
    iso2 = (iso2 or "").upper()
    counts: Dict[str, int] = {}
    for row in _cities_by_iso().get(iso2, []):
        counts[row["state_code"]] = counts.get(row["state_code"], 0) + 1

    rows = [
        {
            "code": code,
            "name": name,
            "slug": slugify(name) or code.lower(),
            "cities": counts.get(code, 0),
        }
        for code, name in _admin1().get(iso2, {}).items()
    ]
    return sorted(rows, key=lambda r: r["name"])


@functools.lru_cache(maxsize=None)
def cities(iso2: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """Cities in a country, or in one of its states.

    ``state`` accepts the GeoNames code, the full name or the slug, because the
    three callers of this — the map's drill-down, a form's dependent select and
    a direct API request — each naturally hold a different one.
    """
    iso2 = (iso2 or "").upper()
    rows = _cities_by_iso().get(iso2, [])
    if state:
        wanted = normalise(state)
        rows = [
            row for row in rows
            if wanted in {normalise(row["state_code"]), normalise(row["state"]),
                          normalise(slugify(row["state"]))}
        ]
    return sorted(rows, key=lambda r: r["name"])


def city(iso2: str, name: str) -> Optional[Dict[str, Any]]:
    """One city by name, or None — the lookup behind "where is this city?"."""
    return _city_lookup((iso2 or "").upper()).get(normalise(name))


# ── Resolution: address text → city + state ─────────────────────────────────

def _phrases(segment: str) -> List[str]:
    """Every run of up to five consecutive words, longest first.

    Longest first is what makes "New South Wales" win over "Wales", and
    whole-word runs are what stop "Mumbai Road" from matching "Mumbai".
    """
    words = normalise(segment).split()
    out = []
    for size in range(min(MAX_PHRASE_WORDS, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            out.append(" ".join(words[start:start + size]))
    return out


def resolve(iso2: str, *texts: str) -> Dict[str, Any]:
    """Find the city and state named in some address text.

    Returns ``{"city", "state", "state_code", "lat", "lon", "matched"}`` with
    empty strings for whatever could not be found. ``matched`` says which rule
    fired — "city", "state", or "" — so a caller can tell a resolved value from
    a blank one, and so the tests can assert on the path taken rather than only
    on the answer.

    Segments are tried right to left: an address ends with its locality and
    starts with a street, and a street is exactly where a false positive comes
    from.
    """
    iso2 = (iso2 or "").upper()
    blank = {"city": "", "state": "", "state_code": "", "lat": None, "lon": None, "matched": ""}
    if not iso2:
        return blank

    city_index = _city_lookup(iso2)
    state_index = _state_lookup(iso2)
    state_hit = ""

    for text in texts:
        if not text:
            continue
        segments = [s.strip() for s in re.split(r"[,\n;]+", text) if s.strip()]
        for segment in reversed(segments):
            if NOISE_RE.match(segment):
                continue
            for phrase in _phrases(segment):
                found = city_index.get(phrase)
                if found:
                    # The city knows its own state, which beats anything read
                    # out of the surrounding text.
                    return {
                        "city": found["name"],
                        "state": found["state"] or state_hit,
                        "state_code": found["state_code"],
                        "lat": found["lat"],
                        "lon": found["lon"],
                        "matched": "city",
                    }
                if not state_hit and phrase in state_index:
                    state_hit = state_index[phrase]

    if state_hit:
        return {"city": "", "state": state_hit, "state_code": "",
                "lat": None, "lon": None, "matched": "state"}
    return blank
