"""
ui/registry.py
Single source of truth for reusable front-end components.

To add a component later:
  1. Put its files in  static/components/<name>/
  2. Add ONE entry below.

Each entry maps a component name to the static file paths it needs.
Paths are relative to a STATICFILES dir (i.e. what you'd pass to {% static %}).
A component may declare only "css", only "js", or both.
"""

COMPONENTS = {
    "adv_filter": {
        "css": ["components/adv_filter/adv_filter.css"],
        "js":  ["components/adv_filter/adv_filter.js"],
    },
    "data_table": {
        "css": ["components/data_table/data_table.css"],
        "js":  ["components/data_table/data_table.js"],
    },
    "modal": {
        "css": ["components/modal/modal.css"],
        "js":  ["components/modal/modal.js"],
    },
    "master_page": {
        "css": ["components/master_page/master_page.css"],
    },
    # ── add future components here ──
}
