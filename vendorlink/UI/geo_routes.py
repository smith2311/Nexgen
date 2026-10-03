"""Routes for the Geo Command Centre.

Included from ``vendorlink/urls.py`` under the ``geo/`` prefix rather than
listed there one path at a time, so this folder stays the only place a change
to the map touches::

    path("geo/", include("vendorlink.UI.geo_routes")),

The names are unprefixed here because the include sits inside VendorLink's own
``app_name``: they resolve as ``vendorlink:geo_overview`` and so on.
"""

from django.urls import path

from . import geo_endpoints

urlpatterns = [
    path("api/overview/", geo_endpoints.overview, name="geo_overview"),
    path("api/continent/<str:code>/", geo_endpoints.continent, name="geo_continent"),
    path("api/country/<str:iso2>/", geo_endpoints.country, name="geo_country"),
    path("api/country/<str:iso2>/state/<slug:state>/", geo_endpoints.state, name="geo_state"),

    # The vendor tree behind the four dependent selects above the map.
    path("api/hierarchy/", geo_endpoints.hierarchy, name="geo_hierarchy"),

    # The reference master, not the vendor footprint: what places exist, for a
    # Country -> State -> City cascade and for resolving an address to a state.
    path("api/places/", geo_endpoints.place_countries, name="geo_places"),
    path("api/places/<str:iso2>/", geo_endpoints.place_states, name="geo_place_states"),
    path("api/places/<str:iso2>/cities/", geo_endpoints.place_cities, name="geo_place_cities"),
    path("api/places/<str:iso2>/<str:state>/cities/", geo_endpoints.place_cities,
         name="geo_place_state_cities"),
]
