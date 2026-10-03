"""VendorLink → Geo Command Centre.

Everything that draws the hierarchical world map on the VendorLink launcher
lives in this one folder — data, endpoints, routes, template and assets — so a
change to the map never means hunting through the app.

    geo_dataset.py    the relational mock data + the roll-up arithmetic
    geo_endpoints.py  three JSON endpoints, one per drill-down tier
    geo_routes.py     urlpatterns, included from vendorlink/urls.py
    templates/        the section injected under the Admin Console banner
    static/           the stylesheet, the controller and the world borders

See README.md in this folder for how it is wired into the page.
"""
