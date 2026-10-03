# VendorLink — Geo Command Centre

The hierarchical world map on the VendorLink launcher, directly under the Admin
Console banner. Everything it needs is in this folder; three lines elsewhere
connect it to the project.

```
vendorlink/UI/
├── geo_places.py                           countries -> states -> cities, and address resolution
├── geo_endpoints.py                        the four map tiers + the reference list
├── geo_routes.py                           urlpatterns
├── geo_dataset.py                          UNUSED — the mock the DB-backed version replaced
├── data/admin1_names.json                  3,865 states/provinces (GeoNames admin1)
├── test_geo_command.py                     borders, wiring, CSS scope
├── test_geo_places.py                      the master and what the map does with it
├── templates/vendorlink/geo/
│   └── geo_command_panel.html              the section, one {% include %}
└── static/vendorlink/geo/
    ├── geo-command.css                     "Midnight UI" — all of it scoped .vlgeo
    ├── geo-command.js                      state machine + Leaflet controller
    └── world-countries.geojson             175 borders, ~165KB (Natural Earth)
```

## How it is wired in

| Where | What | Why it is needed |
|---|---|---|
| `templates/vendorlink/index.html` | `{% include "vendorlink/geo/geo_command_panel.html" %}` | puts the section under the Admin Console banner |
| `vendorlink/urls.py` | `path("geo/", include("vendorlink.UI.geo_routes"))` | mounts the three endpoints |
| `vendorlink/management/commands/export_places.py` | a shim over `geo_places` | Django discovers management commands in `<app>/management/commands` and nowhere else |
| `core/settings.py` | this folder's `templates/` in `TEMPLATES[0]["DIRS"]` and `static/` in `STATICFILES_DIRS` | `APP_DIRS` and the app-directories static finder only look in `<app>/templates` and `<app>/static`, which these are not |

Remove those three and the feature is gone cleanly; there is nothing else of it
anywhere in the project.

## The four map tiers

| Tier | Endpoint | Frames | Click does |
|---|---|---|---|
| 1 Global | `/vendorlink/geo/api/overview/` | every country holding vendors | drill to that continent |
| 2 Regional | `/vendorlink/geo/api/continent/<CODE>/` | that continent | drill to that country |
| 3 Country | `/vendorlink/geo/api/country/<ISO2>/` | that country's pins | drill to that state |
| 4 Local | `/vendorlink/geo/api/country/<ISO2>/state/<SLUG>/` | that state's pins | — |

**Every tier ships `pins`**, not just the last one. A pin is one city holding
registered vendors, carrying the whole chain above it (continent, country,
state, city) plus each vendor's name, contact details, capabilities and
warehouse address — which is exactly what the hover card shows, top to bottom.

Vendor presence is deliberately *not* a country tint any more. A tinted
Australia does not say whether the warehouse is in Melbourne or Perth, and at
tier 1 it lit up half the world while the locations themselves stayed
invisible. The polygons now only say where the camera is; the pins say where
the vendors are. A pin whose city the master could not resolve sits on the
country centroid and is drawn amber with a dashed edge and no pulse — an
approximate position that says it is approximate.

### The four dependent selects

`/vendorlink/geo/api/hierarchy/` returns the whole continent → country → state
→ city tree of **vendor locations** in one request, and the selects above the
map are built from it. Deliberately the vendor tree, not the geographic master:
the selects exist to find a vendor, so offering all 3,856 states on earth when
eleven hold a warehouse would make the common case a scrolling exercise and
every other choice would lead to an empty map. The master stays at
`/api/places/` for the forms, where the opposite is true.

They are a second route to the state a map click sets, not a filter beside it:
picking Asia and clicking Asia land in the same place, and drilling by click
moves the selects to match. Clearing one goes up to its parent. The city select
is not a tier — it flies to that pin and opens its card.

Back and the breadcrumb both step up a tier; Escape does the same.

Every response uses one envelope — `level`, `scope`, `trail`, `kpis`,
`children`, `focus` — which is what lets the front end have one render path
rather than three. `geo_endpoints.py` documents it in full.

## The geographic master

`geo_places.py` answers "what places exist", as opposed to the map tiers, which
answer "where are our vendors". It is built from two sources:

* **geonamescache** — already a dependency, and what the registration form's
  city picker uses. 32,444 cities at the default population floor, each with a
  country, an `admin1code`, coordinates and a population.
* **`data/admin1_names.json`** — 3,865 first-level divisions from GeoNames'
  `admin1CodesASCII` export, vendored because geonamescache ships the admin1
  *code* on a city but names only the divisions of the United States. Without
  it a city resolves to "IN.16" rather than "Maharashtra".

| Endpoint | Returns |
|---|---|
| `/vendorlink/geo/api/places/` | every country, with its state and city counts |
| `/vendorlink/geo/api/places/<ISO2>/` | that country's states / provinces / regions |
| `/vendorlink/geo/api/places/<ISO2>/cities/` | every city in the country |
| `/vendorlink/geo/api/places/<ISO2>/<state>/cities/` | the cities of one state |

`state` accepts the GeoNames code, the full name or the slug, because the map
holds a slug, a form holds a name and an import holds a code.

For the whole list as a file: `python manage.py export_places` (Excel, one row
per city) or `--format json` (nested, the shape a dependent select wants).
`--country IN` limits it.

### Why the map needs it

Every live vendor row has a blank `state` and most have a blank `city`. The
location is in the free-text warehouse address — "530 Collins St, Melbourne VIC
3000" — so `resolve()` is what turns those rows into a state tier at all. It
reads an address right to left, because that is where locality lives and the
front of an address is a street name that would otherwise match some other
country's town, and it matches whole runs of words so "Mumbai Road" does not
become Mumbai.

Nothing is guessed. The code this replaced took the last comma-separated chunk,
which filed "Plot 7, Industrial Area" under a state called "Industrial Area" —
a value indistinguishable on screen from a real one. An address the master
cannot place now stays visibly under "Unspecified".

### The population floor

`VENDORLINK_GEO_MIN_CITY_POPULATION` (setting or environment) picks which
geonamescache extract to load. Measured against the live vendor rows:

| Floor | Cities | Resident memory | Live rows placed |
|---|---|---|---|
| 15,000 (default) | 32,444 | ~80 MB | 5 city / 7 state |
| 5,000 | 65,849 | ~130 MB | 6 city / 7 state |
| 1,000 | 161,662 | ~257 MB | 8 city / 9 state |

The default matches `vendorlink.utils.get_cities_for_country`, which feeds the
registration form's city picker — a master offering cities the form refuses
would be its own bug report. Lower it when small-town addresses matter more
than the memory: at 1,000 the Polish village rows and the Australian suburbs
resolve.

## Conventions this folder keeps

* **`.vlgeo` scopes everything**, including the Leaflet overrides. The launcher
  is a light `.vlx` page and Leaflet's stylesheet is global; one unscoped
  `.leaflet-*` rule here would repaint the continent picker on the registration
  form. A test enforces this.
* **`--gc-*` tokens only.** `--vlx-*` belongs to the launcher's design system and
  `--vl-*` to the redesign; a third file writing either would be redefining
  someone else's system.
* **Borders are served from here, not a tile API.** Leaflet is only the free CDN
  renderer; the map itself is the local `world-countries.geojson` vector layer,
  so a blocked or paid tile service cannot break the basemap.

## The failure modes worth knowing

* **The ISO2 join is silent.** Metrics and polygons meet on a two-letter code. A
  country with no matching polygon simply never lights up — no error anywhere.
  It has already happened once: Singapore is absent from Natural Earth's 110m
  set (too small for that generalisation), and its outline was added from the
  50m set at finer precision. `BorderJoinTests` is what caught it.
* **Leaflet needs telling its box changed.** A map in a grid cell that resizes —
  the panel stacking under it at 900px, the sidebar collapsing — renders grey
  tiles until `invalidateSize()`. A `ResizeObserver` on the stage handles it; a
  window resize listener alone does not.
* **Static assets need collecting.** `collectstatic` after any change here, or
  the container serves the previous copy.
