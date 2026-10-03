"""The network the Geo Command Centre draws, and the arithmetic that rolls it up.

WHY THE NUMBERS LIVE HERE RATHER THAN IN THE DATABASE
-----------------------------------------------------
VendorLink stores vendors, bids, KPI records and scorecards — but nothing that
says "this warehouse, in this city, moved this much volume this quarter". Until
a warehouse-throughput model exists, this file is the seam: it hands the map a
realistic dataset in exactly the shape the endpoints return, so when the real
figures arrive only ``CITIES`` and the two lookup helpers change and neither the
endpoints nor a line of front-end code has to move.

THE SHAPE IS RELATIONAL, NOT NESTED
-----------------------------------
Cities are the only place a number is written down. A country is the sum of its
cities and a continent is the sum of its countries, computed on every request by
:func:`roll_up`, because a hand-maintained total is a total that goes stale the
first time someone edits a city and forgets the parent. Rates that cannot be
summed — utilisation, on-time — are weighted by the thing that makes them
comparable (capacity, then shipments), never averaged flat: a 12,000 m² hub at
94% and a 900 m² spoke at 40% is not a network running at 67%.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# ── Continents ──────────────────────────────────────────────────────────────
# `anchor` is a label position, not a centroid: it is where the tier-1 chip for
# the continent sits, nudged off the true centre where that would drop the chip
# in an ocean or on top of a neighbouring continent's chip. The camera itself
# never uses these — it frames the real border geometry, client-side.
CONTINENTS: Dict[str, Dict[str, Any]] = {
    "NA": {"name": "North America", "anchor": [44.0, -99.0]},
    "SA": {"name": "South America", "anchor": [-16.0, -60.0]},
    "EU": {"name": "Europe", "anchor": [52.0, 14.0]},
    "AF": {"name": "Africa", "anchor": [2.0, 21.0]},
    "AS": {"name": "Asia", "anchor": [36.0, 92.0]},
    "OC": {"name": "Oceania", "anchor": [-26.0, 142.0]},
}

# ── Countries ───────────────────────────────────────────────────────────────
# Keyed by ISO 3166-1 alpha-2, which is also the key the border file carries,
# so a country polygon and its metrics join without a translation table.
COUNTRIES: Dict[str, Dict[str, Any]] = {
    "US": {"name": "United States", "continent": "NA"},
    "CA": {"name": "Canada", "continent": "NA"},
    "MX": {"name": "Mexico", "continent": "NA"},
    "BR": {"name": "Brazil", "continent": "SA"},
    "CL": {"name": "Chile", "continent": "SA"},
    "GB": {"name": "United Kingdom", "continent": "EU"},
    "DE": {"name": "Germany", "continent": "EU"},
    "NL": {"name": "Netherlands", "continent": "EU"},
    "FR": {"name": "France", "continent": "EU"},
    "ZA": {"name": "South Africa", "continent": "AF"},
    "KE": {"name": "Kenya", "continent": "AF"},
    "JP": {"name": "Japan", "continent": "AS"},
    "SG": {"name": "Singapore", "continent": "AS"},
    "IN": {"name": "India", "continent": "AS"},
    "AE": {"name": "United Arab Emirates", "continent": "AS"},
    "CN": {"name": "China", "continent": "AS"},
    "AU": {"name": "Australia", "continent": "OC"},
}

# ── Cities — the only rows carrying a number ────────────────────────────────
# sales      quarter-to-date billed throughput, USD
# warehouses live 3PL sites
# vendors    distinct vendors operating at least one of those sites
# capacity   contracted storage, m²
# utilisation / on_time  percentages, weighted on roll-up (see roll_up)
CITIES: List[Dict[str, Any]] = [
    # ── North America ───────────────────────────────────────────────────────
    {"id": "us-nyc", "name": "New York", "country": "US", "lat": 40.7128, "lon": -74.0060,
     "sales": 4_820_000, "warehouses": 6, "vendors": 4, "capacity": 58_400,
     "utilisation": 91, "on_time": 96.4, "lead_days": 1.8},
    {"id": "us-chi", "name": "Chicago", "country": "US", "lat": 41.8781, "lon": -87.6298,
     "sales": 3_150_000, "warehouses": 4, "vendors": 3, "capacity": 71_200,
     "utilisation": 84, "on_time": 94.1, "lead_days": 2.3},
    {"id": "us-lax", "name": "Los Angeles", "country": "US", "lat": 34.0522, "lon": -118.2437,
     "sales": 3_960_000, "warehouses": 5, "vendors": 4, "capacity": 63_800,
     "utilisation": 88, "on_time": 92.7, "lead_days": 2.1},
    {"id": "ca-yyz", "name": "Toronto", "country": "CA", "lat": 43.6532, "lon": -79.3832,
     "sales": 1_740_000, "warehouses": 3, "vendors": 2, "capacity": 29_600,
     "utilisation": 79, "on_time": 95.2, "lead_days": 2.4},
    {"id": "ca-yvr", "name": "Vancouver", "country": "CA", "lat": 49.2827, "lon": -123.1207,
     "sales": 980_000, "warehouses": 2, "vendors": 2, "capacity": 18_900,
     "utilisation": 72, "on_time": 93.8, "lead_days": 2.9},
    {"id": "mx-mex", "name": "Mexico City", "country": "MX", "lat": 19.4326, "lon": -99.1332,
     "sales": 1_260_000, "warehouses": 3, "vendors": 2, "capacity": 34_100,
     "utilisation": 81, "on_time": 89.6, "lead_days": 3.4},

    # ── South America ───────────────────────────────────────────────────────
    {"id": "br-sao", "name": "São Paulo", "country": "BR", "lat": -23.5505, "lon": -46.6333,
     "sales": 2_140_000, "warehouses": 4, "vendors": 3, "capacity": 46_700,
     "utilisation": 86, "on_time": 88.9, "lead_days": 3.8},
    {"id": "cl-scl", "name": "Santiago", "country": "CL", "lat": -33.4489, "lon": -70.6693,
     "sales": 620_000, "warehouses": 1, "vendors": 1, "capacity": 12_300,
     "utilisation": 68, "on_time": 91.2, "lead_days": 4.1},

    # ── Europe ──────────────────────────────────────────────────────────────
    {"id": "gb-lon", "name": "London", "country": "GB", "lat": 51.5074, "lon": -0.1278,
     "sales": 4_410_000, "warehouses": 5, "vendors": 4, "capacity": 49_800,
     "utilisation": 93, "on_time": 97.1, "lead_days": 1.4},
    {"id": "gb-man", "name": "Manchester", "country": "GB", "lat": 53.4808, "lon": -2.2426,
     "sales": 1_180_000, "warehouses": 2, "vendors": 2, "capacity": 26_400,
     "utilisation": 77, "on_time": 95.8, "lead_days": 1.9},
    {"id": "de-fra", "name": "Frankfurt", "country": "DE", "lat": 50.1109, "lon": 8.6821,
     "sales": 5_240_000, "warehouses": 7, "vendors": 5, "capacity": 88_600,
     "utilisation": 95, "on_time": 98.2, "lead_days": 1.2},
    {"id": "de-ham", "name": "Hamburg", "country": "DE", "lat": 53.5511, "lon": 9.9937,
     "sales": 2_070_000, "warehouses": 3, "vendors": 3, "capacity": 41_900,
     "utilisation": 87, "on_time": 96.9, "lead_days": 1.6},
    {"id": "nl-rtm", "name": "Rotterdam", "country": "NL", "lat": 51.9244, "lon": 4.4777,
     "sales": 3_380_000, "warehouses": 4, "vendors": 3, "capacity": 74_500,
     "utilisation": 92, "on_time": 97.6, "lead_days": 1.3},
    {"id": "nl-ams", "name": "Amsterdam", "country": "NL", "lat": 52.3676, "lon": 4.9041,
     "sales": 1_420_000, "warehouses": 2, "vendors": 2, "capacity": 21_700,
     "utilisation": 83, "on_time": 96.2, "lead_days": 1.5},
    {"id": "fr-par", "name": "Paris", "country": "FR", "lat": 48.8566, "lon": 2.3522,
     "sales": 2_660_000, "warehouses": 4, "vendors": 3, "capacity": 38_200,
     "utilisation": 89, "on_time": 94.8, "lead_days": 1.7},
    {"id": "fr-lyo", "name": "Lyon", "country": "FR", "lat": 45.7640, "lon": 4.8357,
     "sales": 890_000, "warehouses": 2, "vendors": 1, "capacity": 17_400,
     "utilisation": 74, "on_time": 93.4, "lead_days": 2.2},

    # ── Africa ──────────────────────────────────────────────────────────────
    {"id": "za-jnb", "name": "Johannesburg", "country": "ZA", "lat": -26.2041, "lon": 28.0473,
     "sales": 1_390_000, "warehouses": 3, "vendors": 2, "capacity": 31_800,
     "utilisation": 80, "on_time": 90.3, "lead_days": 3.6},
    {"id": "za-cpt", "name": "Cape Town", "country": "ZA", "lat": -33.9249, "lon": 18.4241,
     "sales": 720_000, "warehouses": 2, "vendors": 2, "capacity": 15_600,
     "utilisation": 69, "on_time": 89.1, "lead_days": 4.0},
    {"id": "ke-nbo", "name": "Nairobi", "country": "KE", "lat": -1.2921, "lon": 36.8219,
     "sales": 540_000, "warehouses": 1, "vendors": 1, "capacity": 9_800,
     "utilisation": 76, "on_time": 86.7, "lead_days": 4.6},

    # ── Asia ────────────────────────────────────────────────────────────────
    {"id": "jp-tyo", "name": "Tokyo", "country": "JP", "lat": 35.6762, "lon": 139.6503,
     "sales": 4_070_000, "warehouses": 5, "vendors": 4, "capacity": 52_300,
     "utilisation": 94, "on_time": 98.7, "lead_days": 1.1},
    {"id": "jp-osa", "name": "Osaka", "country": "JP", "lat": 34.6937, "lon": 135.5023,
     "sales": 1_610_000, "warehouses": 2, "vendors": 2, "capacity": 24_900,
     "utilisation": 85, "on_time": 97.9, "lead_days": 1.4},
    {"id": "sg-sin", "name": "Singapore", "country": "SG", "lat": 1.3521, "lon": 103.8198,
     "sales": 3_720_000, "warehouses": 4, "vendors": 4, "capacity": 47_600,
     "utilisation": 96, "on_time": 98.4, "lead_days": 1.2},
    {"id": "in-bom", "name": "Mumbai", "country": "IN", "lat": 19.0760, "lon": 72.8777,
     "sales": 2_980_000, "warehouses": 6, "vendors": 5, "capacity": 68_400,
     "utilisation": 90, "on_time": 91.8, "lead_days": 2.6},
    {"id": "in-del", "name": "Delhi", "country": "IN", "lat": 28.6139, "lon": 77.2090,
     "sales": 2_340_000, "warehouses": 5, "vendors": 4, "capacity": 59_100,
     "utilisation": 87, "on_time": 90.4, "lead_days": 2.8},
    {"id": "in-blr", "name": "Bengaluru", "country": "IN", "lat": 12.9716, "lon": 77.5946,
     "sales": 1_870_000, "warehouses": 4, "vendors": 3, "capacity": 42_300,
     "utilisation": 83, "on_time": 92.6, "lead_days": 2.5},
    {"id": "ae-dxb", "name": "Dubai", "country": "AE", "lat": 25.2048, "lon": 55.2708,
     "sales": 3_290_000, "warehouses": 4, "vendors": 3, "capacity": 55_900,
     "utilisation": 92, "on_time": 96.1, "lead_days": 1.9},
    {"id": "cn-sha", "name": "Shanghai", "country": "CN", "lat": 31.2304, "lon": 121.4737,
     "sales": 4_530_000, "warehouses": 6, "vendors": 4, "capacity": 81_700,
     "utilisation": 93, "on_time": 95.3, "lead_days": 2.0},
    {"id": "cn-szx", "name": "Shenzhen", "country": "CN", "lat": 22.5431, "lon": 114.0579,
     "sales": 3_110_000, "warehouses": 4, "vendors": 3, "capacity": 64_200,
     "utilisation": 89, "on_time": 94.7, "lead_days": 2.2},

    # ── Oceania ─────────────────────────────────────────────────────────────
    {"id": "au-syd", "name": "Sydney", "country": "AU", "lat": -33.8688, "lon": 151.2093,
     "sales": 2_260_000, "warehouses": 3, "vendors": 3, "capacity": 36_500,
     "utilisation": 88, "on_time": 95.6, "lead_days": 2.7},
    {"id": "au-mel", "name": "Melbourne", "country": "AU", "lat": -37.8136, "lon": 144.9631,
     "sales": 1_480_000, "warehouses": 2, "vendors": 2, "capacity": 27_800,
     "utilisation": 81, "on_time": 94.9, "lead_days": 2.9},
]


# ── Roll-up ─────────────────────────────────────────────────────────────────

#: Fields that are simply added up the tree.
#:
#: ``vendors`` is the honest exception: adding a per-city vendor count gives
#: vendor *engagements*, not distinct vendors, because one 3PL operating in
#: Frankfurt and Hamburg is counted twice. The endpoint labels it as such. A
#: distinct count needs vendor identities per city, which is exactly what the
#: real model will carry and this stand-in does not.
SUMMED = ("sales", "warehouses", "vendors", "capacity")


def roll_up(cities: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate city rows into one set of metrics.

    Counts add; rates do not. ``utilisation`` is weighted by capacity — the
    only honest way to say how full a mixed estate of hubs and spokes is — and
    ``on_time`` / ``lead_days`` by warehouse count, which stands in for shipment
    volume until the real figure exists. An empty input returns a complete zero
    row rather than ``None``, so a caller never has to branch on emptiness.
    """
    rows = list(cities)
    totals: Dict[str, Any] = {key: 0 for key in SUMMED}
    totals["cities"] = len(rows)

    if not rows:
        totals.update(utilisation=0.0, on_time=0.0, lead_days=0.0)
        return totals

    for key in SUMMED:
        totals[key] = sum(row[key] for row in rows)

    capacity = totals["capacity"] or 1
    warehouses = totals["warehouses"] or 1
    totals["utilisation"] = round(
        sum(row["utilisation"] * row["capacity"] for row in rows) / capacity, 1
    )
    totals["on_time"] = round(
        sum(row["on_time"] * row["warehouses"] for row in rows) / warehouses, 1
    )
    totals["lead_days"] = round(
        sum(row["lead_days"] * row["warehouses"] for row in rows) / warehouses, 1
    )
    return totals


def cities_for_country(iso2: str) -> List[Dict[str, Any]]:
    """Every city in one country, biggest first."""
    code = (iso2 or "").upper()
    rows = [c for c in CITIES if c["country"] == code]
    return sorted(rows, key=lambda c: c["sales"], reverse=True)


def cities_for_continent(code: str) -> List[Dict[str, Any]]:
    """Every city on one continent, across all of its countries."""
    wanted = {
        iso2 for iso2, meta in COUNTRIES.items()
        if meta["continent"] == (code or "").upper()
    }
    return [c for c in CITIES if c["country"] in wanted]


def country_anchor(iso2: str) -> Optional[List[float]]:
    """Where a country's tier-2 label sits: the mean of its own cities.

    Deliberately not the polygon centroid. A label at the centroid of Norway or
    the United States lands hundreds of miles from anything this network
    actually operates, which reads as a data error.
    """
    rows = cities_for_country(iso2)
    if not rows:
        return None
    return [
        round(sum(r["lat"] for r in rows) / len(rows), 4),
        round(sum(r["lon"] for r in rows) / len(rows), 4),
    ]


def countries_with_data() -> List[str]:
    """ISO2 codes that have at least one city, for styling the borders."""
    return sorted({c["country"] for c in CITIES})
