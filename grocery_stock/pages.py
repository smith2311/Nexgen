"""
Grocery Stock & Cost Management — master page definitions
─────────────────────────────────────────────────────────────────────────────
Every master in this module is the same page: a header with counters, a search
bar, a table, and an add/edit modal. Rather than write that page eight times,
each master is described here as data and rendered by one shared template
(templates/grocery_stock/master_page.html) driven by one shared script
(static/js/grocery_stock/grocery_master.js).

Adding a ninth master is an entry in MASTER_SPECS, not a new template.

Spec keys
─────────
    title / subtitle / icon   page header
    tone                      gs-tone-* class colouring the home-page card
    endpoint                  path segment under /grocery/api/v1/
    columns                   table columns, in order
    fields                    add/edit form fields, in order
    write_roles_label         shown in the "read only" banner

Column ``type``
    text (default) · bool · num · muted

Field ``type``
    text · textarea · number · decimal · email · checkbox · select · fk

A ``fk`` field names the endpoint to populate its dropdown from, plus the
attribute to show as the option label.
"""

# Choices mirror grocery_stock.models.ValuationMethod. LIFO is deliberately
# absent — it is disallowed under Ind AS.
VALUATION_CHOICES = [
    {"value": "WAC", "label": "Weighted Average Cost"},
    {"value": "FIFO", "label": "First In, First Out"},
]

ACTIVE_FIELD = {
    "name": "is_active",
    "label": "Active",
    "type": "checkbox",
    "default": True,
    "help": "Inactive records stay in history but disappear from pickers.",
}

ACTIVE_COLUMN = {"key": "is_active", "label": "Status", "type": "bool", "width": "110px"}


MASTER_SPECS = {
    # ── Categories (specification section 3) ────────────────────────────────
    "categories": {
        "title": "Category Master",
        "subtitle": "Grocery categories — Cereals, Pulses, Spices, Dairy and the rest",
        "icon": "fas fa-tags",
        "tone": "gs-tone-teal",
        "endpoint": "categories",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "code", "label": "Code", "width": "150px"},
            {"key": "name", "label": "Category"},
            {"key": "description", "label": "Description", "type": "muted"},
            {"key": "item_count", "label": "Items", "type": "num", "width": "90px"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "code", "label": "Category Code", "type": "text", "required": True,
             "placeholder": "CEREALS", "maxlength": 30, "uppercase": True},
            {"name": "name", "label": "Category Name", "type": "text", "required": True,
             "placeholder": "Cereals", "maxlength": 100},
            {"name": "description", "label": "Description", "type": "textarea", "span": 2},
            ACTIVE_FIELD,
        ],
    },

    # ── Brands ──────────────────────────────────────────────────────────────
    "brands": {
        "title": "Brand Master",
        "subtitle": "Manufacturer and house brands used on the item master",
        "icon": "fas fa-copyright",
        "tone": "gs-tone-purple",
        "endpoint": "brands",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "code", "label": "Code", "width": "150px"},
            {"key": "name", "label": "Brand"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "code", "label": "Brand Code", "type": "text", "required": True,
             "placeholder": "TATA", "maxlength": 30, "uppercase": True},
            {"name": "name", "label": "Brand Name", "type": "text", "required": True,
             "placeholder": "Tata", "maxlength": 100},
            ACTIVE_FIELD,
        ],
    },

    # ── Units of measure ────────────────────────────────────────────────────
    "units": {
        "title": "Unit Master",
        "subtitle": "Units of measure. Set a base unit only when this unit converts into another",
        "icon": "fas fa-balance-scale",
        "tone": "gs-tone-amber",
        "endpoint": "units",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "code", "label": "Code", "width": "120px"},
            {"key": "name", "label": "Unit"},
            {"key": "base_unit_code", "label": "Base Unit", "type": "muted", "width": "130px"},
            {"key": "conversion_factor", "label": "Factor", "type": "num", "width": "120px"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "code", "label": "Unit Code", "type": "text", "required": True,
             "placeholder": "KG", "maxlength": 20, "uppercase": True},
            {"name": "name", "label": "Unit Name", "type": "text", "required": True,
             "placeholder": "Kilogram", "maxlength": 50},
            {"name": "base_unit", "label": "Base Unit", "type": "fk",
             "source": "units", "source_label": "code",
             "help": "Leave blank unless this unit converts into another one."},
            {"name": "conversion_factor", "label": "Conversion Factor", "type": "decimal",
             "step": "0.0001", "placeholder": "25.0000",
             "help": "1 BAG = 25 KG → enter 25. Required when a base unit is set."},
            ACTIVE_FIELD,
        ],
    },

    # ── Suppliers (specification section 4) ─────────────────────────────────
    "suppliers": {
        "title": "Supplier Master",
        "subtitle": "Vendors, GST details and payment terms",
        "icon": "fas fa-truck-field",
        "tone": "gs-tone-green",
        "endpoint": "suppliers",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "name", "label": "Supplier"},
            {"key": "phone", "label": "Phone", "type": "muted", "width": "140px"},
            {"key": "email", "label": "Email", "type": "muted"},
            {"key": "credit_days", "label": "Credit", "type": "num", "width": "90px"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "name", "label": "Supplier Name", "type": "text", "required": True,
             "placeholder": "Shree Traders", "maxlength": 200},
            {"name": "phone", "label": "Phone", "type": "text", "maxlength": 30},
            {"name": "email", "label": "Email", "type": "email"},
            {"name": "address", "label": "Address", "type": "textarea", "span": 2},
            {"name": "payment_terms", "label": "Payment Terms", "type": "text",
             "maxlength": 150, "placeholder": "Net 30"},
            {"name": "credit_days", "label": "Credit Days", "type": "number", "default": 0},
            ACTIVE_FIELD,
        ],
    },

    # ── Warehouses ──────────────────────────────────────────────────────────
    "warehouses": {
        "title": "Warehouse Master",
        "subtitle": "Storage sites. Bin locations hang off each warehouse",
        "icon": "fas fa-warehouse",
        "tone": "gs-tone-navy",
        "endpoint": "warehouses",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "code", "label": "Code", "width": "150px"},
            {"key": "name", "label": "Warehouse"},
            {"key": "address", "label": "Address", "type": "muted"},
            {"key": "location_count", "label": "Bins", "type": "num", "width": "90px"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "code", "label": "Warehouse Code", "type": "text", "required": True,
             "placeholder": "WH-MAIN", "maxlength": 30, "uppercase": True},
            {"name": "name", "label": "Warehouse Name", "type": "text", "required": True,
             "placeholder": "Main Store", "maxlength": 150},
            {"name": "address", "label": "Address", "type": "textarea", "span": 2},
            ACTIVE_FIELD,
        ],
    },

    # ── Bin locations (specification section 6) ─────────────────────────────
    "locations": {
        "title": "Bin Location Master",
        "subtitle": "Racks and bins inside each warehouse. Codes are unique per warehouse",
        "icon": "fas fa-map-location-dot",
        "tone": "gs-tone-blue",
        "endpoint": "locations",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "warehouse_name", "label": "Warehouse", "width": "220px"},
            {"key": "code", "label": "Bin Code", "width": "150px"},
            {"key": "name", "label": "Description", "type": "muted"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "warehouse", "label": "Warehouse", "type": "fk", "required": True,
             "source": "warehouses", "source_label": "name"},
            {"name": "code", "label": "Bin Code", "type": "text", "required": True,
             "placeholder": "A-01", "maxlength": 30, "uppercase": True,
             "help": "Must be unique within the selected warehouse."},
            {"name": "name", "label": "Description", "type": "text", "maxlength": 100,
             "placeholder": "Rack A, Shelf 1"},
            ACTIVE_FIELD,
        ],
    },

    # ── Consuming departments (specification section 9) ─────────────────────
    "departments": {
        "title": "Department Master",
        "subtitle": "Consuming departments — every cost-per-department report groups by these",
        "icon": "fas fa-sitemap",
        "tone": "gs-tone-teal",
        "endpoint": "departments",
        "write_roles_label": "Grocery Admin",
        "columns": [
            {"key": "code", "label": "Code", "width": "170px"},
            {"key": "name", "label": "Department"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "code", "label": "Department Code", "type": "text", "required": True,
             "placeholder": "KITCHEN", "maxlength": 30, "uppercase": True},
            {"name": "name", "label": "Department Name", "type": "text", "required": True,
             "placeholder": "Kitchen", "maxlength": 100},
            ACTIVE_FIELD,
        ],
    },

    # ── Item master (specification section 2) ───────────────────────────────
    "items": {
        "title": "Item Master",
        "subtitle": "Every grocery item — classification, tax, stock levels, shelf life and costing",
        "icon": "fas fa-box-open",
        "tone": "gs-tone-blue",
        "endpoint": "items",
        "write_roles_label": "Grocery Admin",
        "wide": True,
        "columns": [
            {"key": "name", "label": "Item"},
            {"key": "category_name", "label": "Category", "width": "140px"},
            {"key": "brand_name", "label": "Brand", "type": "muted", "width": "130px"},
            {"key": "unit_code", "label": "Unit", "type": "muted", "width": "90px"},
            {"key": "gst_percent", "label": "GST %", "type": "num", "width": "90px"},
            ACTIVE_COLUMN,
        ],
        "fields": [
            {"name": "name", "label": "Item Name", "type": "text", "required": True,
             "placeholder": "Basmati Rice 25kg", "maxlength": 200, "span": 2},
            {"name": "local_name", "label": "Local Name", "type": "text", "maxlength": 200},
            {"name": "category", "label": "Category", "type": "fk", "required": True,
             "source": "categories", "source_label": "name"},
            {"name": "brand", "label": "Brand", "type": "fk",
             "source": "brands", "source_label": "name"},
            {"name": "unit", "label": "Unit", "type": "fk", "required": True,
             "source": "units", "source_label": "code"},
            {"name": "gst_percent", "label": "GST %", "type": "decimal", "step": "0.01",
             "default": 0},

            {"name": "reorder_level", "label": "Reorder Level", "type": "decimal",
             "step": "0.001", "default": 0, "section": "Stock control"},
            {"name": "minimum_stock", "label": "Minimum Stock", "type": "decimal",
             "step": "0.001", "default": 0},
            {"name": "maximum_stock", "label": "Maximum Stock", "type": "decimal",
             "step": "0.001", "default": 0,
             "help": "Minimum and reorder level are validated against this when it is above zero."},

            {"name": "shelf_life_days", "label": "Shelf Life (days)", "type": "number",
             "section": "Shelf life", "help": "Leave blank when the item does not perish."},
            {"name": "expiry_required", "label": "Expiry Required", "type": "checkbox",
             "help": "When set, a goods receipt cannot be posted without an expiry date. Needs a shelf life."},
            {"name": "storage_location", "label": "Default Bin", "type": "fk",
             "source": "locations", "source_label": "__label__"},

            {"name": "standard_cost", "label": "Standard Cost", "type": "decimal",
             "step": "0.0001", "default": 0, "section": "Costing",
             "help": "Benchmark cost. The cost-variance report compares actual against this."},
            {"name": "valuation_method", "label": "Valuation Method", "type": "select",
             "choices": VALUATION_CHOICES, "default": "WAC"},
            {"name": "is_gst_creditable", "label": "GST Creditable", "type": "checkbox",
             "default": True,
             "help": "On: GST is claimed as input credit and excluded from inventory cost. "
                     "Off: GST forms part of landed cost."},
            ACTIVE_FIELD,
        ],
    },
}


# Order the module home page lists them in.
MASTER_ORDER = [
    "items", "categories", "brands", "units",
    "suppliers", "warehouses", "locations", "departments",
]
