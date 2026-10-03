"""DB-backed endpoints for the VendorLink Geo Command Centre.

The map drills through four tiers:

    /vendorlink/geo/api/overview/                      continents
    /vendorlink/geo/api/continent/<CODE>/              countries
    /vendorlink/geo/api/country/<ISO2>/                states / regions
    /vendorlink/geo/api/country/<ISO2>/state/<SLUG>/   cities

Only active vendors already registered in VendorLink are exposed. Geography that
has no vendor rows is intentionally absent from both the panel and the highlight
list, so the map never suggests capacity where the database has none.
"""

from __future__ import annotations

import functools
from collections import defaultdict
from typing import Any, Dict, Iterable, List

import geonamescache
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from vendorlink.models import Vendor
from vendorlink.rbac import can_access_vendorlink
from vendorlink.utils import canonical_country, continent_name, continent_of

from . import geo_places as places

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")

UNSPECIFIED_STATE = "Unspecified state"
UNSPECIFIED_CITY = "Unspecified city"


@functools.lru_cache(maxsize=1)
def _gc():
    return geonamescache.GeonamesCache()


@functools.lru_cache(maxsize=1)
def _country_by_name() -> Dict[str, Dict[str, Any]]:
    return {country["name"]: country for country in _gc().get_countries().values()}


@functools.lru_cache(maxsize=1)
def _country_by_iso() -> Dict[str, Dict[str, Any]]:
    return {
        country["iso"].upper(): country
        for country in _gc().get_countries().values()
    }


def _state_slug(name: str) -> str:
    return slugify(name or UNSPECIFIED_STATE) or "state"


def _country_iso(country_name: str) -> str:
    row = _country_by_name().get(canonical_country(country_name))
    return (row or {}).get("iso", "").upper()


def _country_name(iso2: str) -> str:
    return (_country_by_iso().get((iso2 or "").upper()) or {}).get("name", "")


def _share(part: float, whole: float) -> float:
    return round((part / whole) * 100, 1) if whole else 0.0


def _place_for(vendor, iso2: str) -> Dict[str, str]:
    """The city and state a vendor row is actually in.

    Three sources, in order of how much they can be trusted:

    1. The ``city`` and ``state`` columns, when a human filled them in.
    2. The geographic master (``geo_places``), asked to find a city or a state
       named anywhere in the warehouse address. On the live data this is the
       only source that answers at all — every row has a blank state and most
       have a blank city, while the address holds "Mumbai, Maharashtra" or
       "530 Collins St, Melbourne VIC 3000".
    3. A city known to the master fixes its own state, so a row with a city and
       no state still lands in the right region.

    Nothing is guessed. A row the master cannot place stays under "Unspecified",
    which tells the reader the data is missing; inventing a state would tell
    them something false in exactly the same typeface as the real ones.
    """
    city = " ".join((vendor.city or "").split())
    state = " ".join((vendor.state or "").split())

    if not city or not state:
        found = places.resolve(iso2, vendor.city, vendor.warehouse_location)
        city = city or found["city"]
        state = state or found["state"]

    if city and not state:
        known = places.city(iso2, city)
        if known:
            state = known["state"]

    return {
        "city": city or UNSPECIFIED_CITY,
        "state": state or UNSPECIFIED_STATE,
    }


def _metric_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    vendors = (
        Vendor.objects
        .filter(is_active=True)
        .exclude(country="")
        .only("id", "vendor_name", "country", "state", "city", "warehouse_location",
              "contact_person", "email", "mobile", "website")
        # One extra query for every capability of every vendor, rather than one
        # query per vendor inside the loop below.
        .prefetch_related("capabilities")
    )

    for vendor in vendors:
        country = canonical_country(vendor.country)
        iso2 = _country_iso(country)
        continent = continent_of(country)
        if not iso2 or not continent:
            continue
        place = _place_for(vendor, iso2)
        city, state = place["city"], place["state"]
        rows.append({
            "vendor_id": vendor.id,
            "vendor_name": vendor.vendor_name,
            "country": country,
            "iso2": iso2,
            "continent": continent,
            "state": state,
            "city": city,
            "warehouse_location": vendor.warehouse_location or "",
            "contact_person": vendor.contact_person or "",
            "email": vendor.email or "",
            "mobile": vendor.mobile or "",
            "website": vendor.website or "",
            "capabilities": [c.label for c in vendor.capabilities.all()],
        })
    return rows


def _city_point(iso2: str, city: str, fallback: List[float]) -> List[float]:
    """Where to drop the pin. The master knows 32,000 cities by name; anything
    it does not know falls back to the centre of whatever is being framed."""
    found = places.city(iso2, city)
    if found:
        return [found["lat"], found["lon"]]
    return fallback


def _aggregate(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    return {
        "warehouses": len(rows),
        "vendors": len({r["vendor_id"] for r in rows}),
        "countries": len({r["iso2"] for r in rows}),
        "states": len({(r["iso2"], r["state"]) for r in rows}),
        "cities": len({(r["iso2"], r["state"], r["city"]) for r in rows}),
    }


def _display_count(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _kpi_cards(totals: Dict[str, Any], scope_label: str) -> List[Dict[str, Any]]:
    return [
        {
            "key": "warehouses",
            "label": "Registered Warehouses",
            "display": f"{totals['warehouses']:,}",
            "value": totals["warehouses"],
            "sub": f"active VendorLink records - {scope_label}",
            "accent": "warehouse",
        },
        {
            "key": "vendors",
            "label": "Registered Vendors",
            "display": f"{totals['vendors']:,}",
            "value": totals["vendors"],
            "sub": "distinct active vendor records",
            "accent": "sales",
        },
        {
            "key": "states",
            "label": "States / Regions",
            "display": f"{totals['states']:,}",
            "value": totals["states"],
            "sub": "derived from warehouse addresses",
            "accent": "capacity",
        },
        {
            "key": "cities",
            "label": "Cities",
            "display": f"{totals['cities']:,}",
            "value": totals["cities"],
            "sub": "registered warehouse cities",
            "accent": "ontime",
        },
    ]


def _row(kind: str, code: str, name: str, rows: List[Dict[str, Any]], total: int,
         lat: float = 0.0, lon: float = 0.0) -> Dict[str, Any]:
    agg = _aggregate(rows)
    return {
        "kind": kind,
        "code": code,
        "name": name,
        "lat": lat,
        "lon": lon,
        "share": _share(agg["warehouses"], total),
        "display": _display_count(agg["warehouses"], "warehouse"),
        **agg,
    }


def _anchor(rows: List[Dict[str, Any]]) -> List[float]:
    points = []
    for row in rows:
        country = _country_by_iso().get(row["iso2"], {})
        fallback = [
            float(country.get("latitude") or 20.0),
            float(country.get("longitude") or 0.0),
        ]
        points.append(_city_point(row["iso2"], row["city"], fallback))
    if not points:
        return [20.0, 0.0]
    return [
        round(sum(p[0] for p in points) / len(points), 4),
        round(sum(p[1] for p in points) / len(points), 4),
    ]


def _pin_code(row: Dict[str, Any]) -> str:
    """One stable id per place, used as the marker key and the select value."""
    return slugify(f"{row['iso2']}-{row['state']}-{row['city']}") or row["iso2"].lower()


def _vendor_card(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the hover card shows about one vendor.

    Contact details and capabilities, not metrics: the question a pin answers is
    "who is here and how do I reach them", which is the whole reason someone
    hovers a warehouse on a map rather than opening the directory.
    """
    return {
        "id": row["vendor_id"],
        "name": row["vendor_name"],
        "contact": row["contact_person"],
        "email": row["email"],
        "mobile": row["mobile"],
        "website": row["website"],
        "warehouse_location": row["warehouse_location"],
        "capabilities": row["capabilities"],
    }


def _pins(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One marker per city that holds registered vendors, with their details.

    Pins are sent at *every* tier, not only the innermost one. The map used to
    say "this country has vendors" by tinting the country, which answers a
    question nobody asked — you cannot tell from a tinted Australia whether the
    warehouse is in Melbourne or Perth. A pin is the same fact placed where the
    fact actually is, and it reads identically whether the camera is on the
    world or on one state.

    A city with several vendors is one pin carrying all of them rather than
    several markers stacked on the same coordinate, where only the top one
    would ever be hoverable.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_pin_code(row)].append(row)

    pins = []
    for code, group in grouped.items():
        first = group[0]
        country = _country_by_iso().get(first["iso2"], {})
        point = _city_point(first["iso2"], first["city"], [
            float(country.get("latitude") or 20.0),
            float(country.get("longitude") or 0.0),
        ])
        pins.append({
            "code": code,
            "kind": "pin",
            "city": first["city"],
            "state": first["state"],
            "country": first["country"],
            "iso2": first["iso2"],
            "continent": first["continent"],
            "continent_name": continent_name(first["continent"]) or first["continent"],
            "state_slug": _state_slug(first["state"]),
            "lat": point[0],
            "lon": point[1],
            # A pin whose coordinates came from the country centroid rather than
            # a known city is flagged, so the front end can show it as
            # approximate instead of implying street-level precision.
            "located": places.city(first["iso2"], first["city"]) is not None,
            "warehouses": len(group),
            "vendors": [_vendor_card(row) for row in group],
        })

    return sorted(pins, key=lambda pin: (-pin["warehouses"], pin["city"].casefold()))


def _group(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def _sort_children(children: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(children, key=lambda child: (-child["warehouses"], child["name"].casefold()))


@login_required
@access_required
@require_GET
def overview(request):
    rows = _metric_rows()
    totals = _aggregate(rows)
    children = []
    for code, group in _group(rows, "continent").items():
        anchor = _anchor(group)
        children.append(_row(
            "continent", code, continent_name(code) or code, group, totals["warehouses"],
            anchor[0], anchor[1],
        ))

    return JsonResponse({
        "level": 1,
        "scope": {"kind": "global", "code": None, "name": "Global network", "parent": None},
        "trail": [{"level": 1, "code": None, "label": "Global"}],
        "kpis": _kpi_cards(totals, "all registered geographies"),
        "children": _sort_children(children),
        "pins": _pins(rows),
        "focus": {"codes": sorted({r["iso2"] for r in rows})},
    })


@login_required
@access_required
@require_GET
def continent(request, code):
    code = (code or "").upper()
    rows = [r for r in _metric_rows() if r["continent"] == code]
    if not rows:
        return JsonResponse({"error": "Unknown continent or no registered vendors"}, status=404)

    totals = _aggregate(rows)
    children = []
    for iso2, group in _group(rows, "iso2").items():
        anchor = _anchor(group)
        children.append(_row(
            "country", iso2, _country_name(iso2) or group[0]["country"], group,
            totals["warehouses"], anchor[0], anchor[1],
        ))

    return JsonResponse({
        "level": 2,
        "scope": {"kind": "continent", "code": code, "name": continent_name(code) or code, "parent": None},
        "trail": [
            {"level": 1, "code": None, "label": "Global"},
            {"level": 2, "code": code, "label": continent_name(code) or code},
        ],
        "kpis": _kpi_cards(totals, continent_name(code) or code),
        "children": _sort_children(children),
        "pins": _pins(rows),
        "focus": {"codes": sorted({r["iso2"] for r in rows})},
    })


@login_required
@access_required
@require_GET
def country(request, iso2):
    iso2 = (iso2 or "").upper()
    rows = [r for r in _metric_rows() if r["iso2"] == iso2]
    if not rows:
        return JsonResponse({"error": "Unknown country or no registered vendors"}, status=404)

    totals = _aggregate(rows)
    parent = rows[0]["continent"]
    country_label = _country_name(iso2) or rows[0]["country"]
    children = []
    for state_name, group in _group(rows, "state").items():
        anchor = _anchor(group)
        children.append(_row(
            "state", _state_slug(state_name), state_name, group,
            totals["warehouses"], anchor[0], anchor[1],
        ))

    return JsonResponse({
        "level": 3,
        "scope": {"kind": "country", "code": iso2, "name": country_label, "parent": parent},
        "trail": [
            {"level": 1, "code": None, "label": "Global"},
            {"level": 2, "code": parent, "label": continent_name(parent) or parent},
            {"level": 3, "code": iso2, "label": country_label},
        ],
        "kpis": _kpi_cards(totals, country_label),
        "children": _sort_children(children),
        "pins": _pins(rows),
        "focus": {"codes": [iso2]},
    })


@login_required
@access_required
@require_GET
def state(request, iso2, state):
    iso2 = (iso2 or "").upper()
    all_rows = [r for r in _metric_rows() if r["iso2"] == iso2]
    rows = [r for r in all_rows if _state_slug(r["state"]) == state]
    if not rows:
        return JsonResponse({"error": "Unknown state or no registered vendors"}, status=404)

    totals = _aggregate(rows)
    parent = rows[0]["continent"]
    country_label = _country_name(iso2) or rows[0]["country"]
    state_label = rows[0]["state"]
    children = []
    for city_name, group in _group(rows, "city").items():
        anchor = _anchor(group)
        children.append(_row(
            "city", slugify(f"{iso2}-{state}-{city_name}") or city_name,
            city_name, group, totals["warehouses"], anchor[0], anchor[1],
        ))

    return JsonResponse({
        "level": 4,
        "scope": {
            "kind": "state",
            "code": state,
            "name": state_label,
            "parent": iso2,
            "grandparent": parent,
        },
        "trail": [
            {"level": 1, "code": None, "label": "Global"},
            {"level": 2, "code": parent, "label": continent_name(parent) or parent},
            {"level": 3, "code": iso2, "label": country_label},
            {"level": 4, "code": state, "label": state_label},
        ],
        "kpis": _kpi_cards(totals, state_label),
        "children": _sort_children(children),
        "pins": _pins(rows),
        "focus": {"codes": [iso2]},
    })


@login_required
@access_required
@require_GET
def hierarchy(request):
    """The whole continent → country → state → city tree, in one request.

    This is what the four dependent selects above the map are built from, and
    it is deliberately the *vendor* tree rather than the geographic master in
    ``geo_places``. The selects exist to find a vendor; offering all 3,856
    states on earth when eleven of them hold a warehouse would make the common
    case — "show me our Maharashtra sites" — a scrolling exercise, and every
    other choice would lead to an empty map.

    One request rather than one per level: the whole tree is the size of the
    vendor table, so a round trip per dropdown change would be three network
    waits to answer a question the browser already has the data for. The
    geographic master stays available at /api/places/ for the forms, where the
    opposite is true — there you are entering an address that does not exist
    in the system yet.
    """
    rows = _metric_rows()

    continents: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        continent_entry = continents.setdefault(row["continent"], {
            "code": row["continent"],
            "name": continent_name(row["continent"]) or row["continent"],
            "countries": {},
        })
        country_entry = continent_entry["countries"].setdefault(row["iso2"], {
            "code": row["iso2"],
            "name": row["country"],
            "states": {},
        })
        state_entry = country_entry["states"].setdefault(_state_slug(row["state"]), {
            "code": _state_slug(row["state"]),
            "name": row["state"],
            "cities": {},
        })
        city_entry = state_entry["cities"].setdefault(_pin_code(row), {
            "code": _pin_code(row),
            "name": row["city"],
            "warehouses": 0,
        })
        city_entry["warehouses"] += 1

    def by_name(values):
        return sorted(values, key=lambda item: item["name"].casefold())

    tree = []
    for continent_entry in by_name(continents.values()):
        countries = []
        for country_entry in by_name(continent_entry["countries"].values()):
            states = []
            for state_entry in by_name(country_entry["states"].values()):
                states.append({**state_entry, "cities": by_name(state_entry["cities"].values())})
            countries.append({**country_entry, "states": states})
        tree.append({**continent_entry, "countries": countries})

    return JsonResponse({"continents": tree, "warehouses": len(rows)})


# ── The reference list: countries → states → cities ─────────────────────────
#
# Separate from the four map tiers above, and deliberately so. Those answer
# "where are our vendors"; these answer "what places exist" — the master a form
# needs to offer a Country → State → City cascade, and the lookup that put the
# state on a vendor row in the first place.
#
# Three endpoints rather than one tree: the full master is 245 countries,
# 3,856 states and 32,444 cities, which is several megabytes of JSON that no
# caller needs in one piece. Each level is a request, the way a dependent
# select actually consumes it. For the whole thing as a file, there is a
# management command — see `export_places`.


@login_required
@access_required
@require_GET
def place_countries(request):
    """Every country in the master, with how many states and cities it holds."""
    rows = places.countries()
    return JsonResponse({
        "count": len(rows),
        "min_city_population": places.min_city_population(),
        "countries": rows,
    })


@login_required
@access_required
@require_GET
def place_states(request, iso2):
    """The states / provinces / regions of one country.

    Divisions with no city in the extract are listed too, with ``cities: 0``.
    A form that offered only the states GeoNames happens to hold a large town
    in would be quietly refusing valid addresses.
    """
    iso2 = (iso2 or "").upper()
    rows = places.states(iso2)
    if not rows and not places.cities(iso2):
        return JsonResponse({"error": "Unknown country"}, status=404)
    return JsonResponse({
        "country": iso2,
        "count": len(rows),
        "states": rows,
    })


@login_required
@access_required
@require_GET
def place_cities(request, iso2, state=None):
    """The cities of one state, or of a whole country when no state is given.

    ``state`` accepts the GeoNames code, the full name or the slug, because the
    three things that call this each hold a different one.
    """
    iso2 = (iso2 or "").upper()
    rows = places.cities(iso2, state)
    if not rows and not places.cities(iso2):
        return JsonResponse({"error": "Unknown country"}, status=404)
    return JsonResponse({
        "country": iso2,
        "state": state or "",
        "count": len(rows),
        "cities": rows,
    })
