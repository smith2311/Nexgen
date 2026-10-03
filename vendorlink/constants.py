"""Shared constants for the Vendor Master module."""

# NB: Countries are NOT a static list. They are managed exclusively by an admin
# through the Country Master (the `Country` model) and are the single source of
# truth for every country dropdown. There is intentionally no hardcoded fallback
# list — nothing is ever suggested or auto-created from free text.

# Default intro shown (pre-filled) when raising a request. The per-vendor
# "Dear <vendor name>," greeting is prepended automatically at send time.
DEFAULT_REQUEST_INTRO = (
    "Greetings from Meril Life Sciences India.<br><br>"
    "I am Jyoti Verma, writing on behalf of Meril Life Sciences Pvt. Ltd., "
    "a global MedTech organization with a strong presence across the "
    "Cardiovascular, Orthopedics, Endosurgery, and Diagnostics domains."
    "<br><br>Website: "
    '<a href="https://www.merillife.com">www.merillife.com</a>'
)

# File types accepted by the request upload control (pdf / word / excel / image).
ALLOWED_REQUEST_UPLOAD_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png",
}

# The nine Yes/No capability dimensions from the SOW data dictionary.
#
# NOT the live list any more. Capabilities are rows in the `Capability` master
# (see models.Capability) so the SCM team can add a tenth without a migration;
# read them through `vendorlink.capabilities`, never from here. This list is
# purely the seed the initial data migration writes, and the fallback
# `ensure_seeded()` uses on a database that somehow has none — keeping it means
# a fresh install still comes up with the nine the spec names, rather than an
# empty capability master and nine screens with nothing on them.
#
# Tuple = (code, label, font-awesome icon). The codes are the old model field
# names on purpose: saved filter URLs and the circulating Excel exports already
# carry them, so reusing them keeps both meaning the same thing after the move.
DEFAULT_CAPABILITIES = [
    ("gdp_certification",     "GDP Certification",        "fa-certificate"),
    ("iso_certification",     "ISO Certification",        "fa-award"),
    ("temperature_capability", "Temperature Capability",  "fa-temperature-low"),
    ("bonded_warehouse",      "Bonded Warehouse",         "fa-box"),
    ("customs_clearance",     "Customs Clearance",        "fa-passport"),
    ("last_mile_distribution", "Last-Mile Distribution",  "fa-truck-fast"),
    ("regulatory_support",    "Regulatory Support",       "fa-clipboard-check"),
    ("sfdc_integration",      "SFDC Integration",         "fa-cloud"),
    ("edi_capability",        "EDI Capability",           "fa-network-wired"),
]

# ── 3PL Capability Assessment (Phase 3) ─────────────────────────────────────
# The exact 6-section / 34-component field spec from the SOW's "3PL Capability
# Assessment Form" sheet, each with its "Guidelines to Fill" text. This single
# list drives the model fields, the external vendor form, and the internal
# review screen — so the spec only has to be transcribed once.
#
# Each row: (field_name, label, guideline, required, widget_kind)
# widget_kind "short" -> single-line input, "long" -> textarea.
CAPABILITY_ASSESSMENT_SECTIONS = [
    ("Company & Network", [
        ("company_name", "Company Name", "Your official registered company name", True, "short"),
        ("warehouse_location_desc", "Warehouse Location", "Full warehouse address including city, country and postal code", True, "long"),
        ("medtech_experience", "MedTech Experience", "Years of MedTech experience and key customers served", False, "long"),
        ("client_base", "Client Base", "Top 5 most relevant customers", False, "long"),
        ("last_mile_delivery_desc", "Last-Mile Delivery", "Do you offer last mile delivery? Describe capabilities and coverage area", False, "long"),
        ("cha_services", "CHA Services", "Do you offer CHA services? Describe capabilities", False, "long"),
        ("wms_name", "WMS Name & Version", "WMS system name and version (if applicable)", False, "short"),
    ]),
    ("Operational Capability", [
        ("warehouse_size_sqm", "Warehouse Size (sq. m)", "Available warehouse space in square metres", False, "short"),
        ("operating_hours", "Operating Hours", "e.g. Monday–Friday 08:00–18:00", False, "short"),
        ("order_cutoff_time", "Order Cut-off Time", "Last order receipt time for same-day processing", False, "short"),
        ("warehouse_cutoff_time", "Warehouse Dispatch Cut-off", "Last dispatch time from warehouse", False, "short"),
        ("emergency_delivery_cutoff", "Emergency Delivery Cut-off", "Emergency same-day delivery cut-off time and coverage area", False, "long"),
        ("returns_handling", "Returns Handling", "Describe your returns handling process", False, "long"),
        ("temperature_controlled_storage", "Temperature-Controlled Storage", "Temperature range available and monitoring method. 15°C–25°C capability required.", False, "long"),
        ("consignment_handling", "Consignment Handling", "Experience managing hospital consignment inventory, replenishment and reporting", False, "long"),
        ("order_to_cash_service", "Order-to-Cash Service", "Can you support order processing, invoicing and order-to-cash services?", False, "long"),
        ("batch_lot_tracking", "Batch / Lot Tracking", "Is batch/lot tracking available? Describe", False, "long"),
        ("serial_number_tracking", "Serial Number Tracking", "Is serial number tracking available? Describe", False, "long"),
    ]),
    ("Compliance & Quality", [
        ("iso_9001_certified", "ISO 9001 Certification", "Certificate number and validity date", False, "long"),
        ("iso_13485_experience", "ISO 13485 Experience", "Experience / details of ISO 13485 compliance", False, "long"),
        ("gdp_compliance", "GDP Compliance", "Current GDP compliance status and certificate details", False, "long"),
        ("regulatory_support_desc", "Regulatory Support", "Describe regulatory support capabilities", False, "long"),
        ("insurance_coverage", "Insurance Coverage", "Coverage: Warehouse, Goods in Transit, Coverage Limit (EUR/USD)", False, "long"),
    ]),
    ("Technology & Integration", [
        ("wms_platform", "WMS Platform", "WMS platform name and version", False, "short"),
        ("real_time_inventory_visibility", "Real-Time Inventory Visibility", "Real-time inventory visibility for warehouse and consignment stock?", False, "long"),
        ("dashboard_reporting", "Dashboard & Reporting", "Can you provide weekly/monthly reports and custom reports on request?", False, "long"),
        ("order_tracking_visibility", "Order Tracking Visibility", "Describe order tracking and visibility capabilities", False, "long"),
    ]),
    ("Strategic Fit", [
        ("why_choose_us", "Why Choose Us", "Key advantages and differentiators", False, "long"),
        ("cons", "Limitations / Cons", "Honest assessment of limitations or challenges", False, "long"),
        ("local_presence", "Local Presence", "Local office/warehouse locations and support", False, "long"),
        ("global_presence", "Global Presence", "Global locations and support network", False, "long"),
        ("dedicated_account_manager", "Dedicated Account Manager", "Yes/No. If available, provide name and role", False, "short"),
        ("implementation_timeline", "Implementation Timeline", "Expected project go-live timeline if engaged", False, "short"),
    ]),
    ("Additional Notes", [
        ("remarks", "Remarks", "Any additional information you'd like to share", False, "long"),
    ]),
]

#: Flat list of every content field name, in spec order.
CAPABILITY_ASSESSMENT_FIELDS = [
    row[0] for _, rows in CAPABILITY_ASSESSMENT_SECTIONS for row in rows
]

#: The two fields the external form marks mandatory before a vendor can submit.
CAPABILITY_ASSESSMENT_REQUIRED_FIELDS = [
    row[0] for _, rows in CAPABILITY_ASSESSMENT_SECTIONS for row in rows if row[3]
]

# ── KPI Dashboard (Phase 4) ──────────────────────────────────────────────────
# The SOW's "KPI Dashboard" sheet — 8 metrics per vendor, filled monthly by the
# Global SCM team (never vendor self-report). The sheet itself carries no unit
# or target column, so units below are a reasonable reading of each metric's
# name rather than a value transcribed from the source.
#
# Each row: (field_name, label, unit) — unit is "%" | "hrs" | "days" | "count" | "currency".
KPI_METRIC_FIELDS = [
    ("inventory_accuracy", "Inventory Accuracy", "%"),
    ("on_time_delivery", "On-Time Delivery", "%"),
    ("order_fulfilment_accuracy", "Order Fulfilment Accuracy", "%"),
    ("damage_rate", "Damage Rate", "%"),
    ("response_time_hours", "Response Time", "hrs"),
    ("capa_closure_days", "CAPA Closure Time", "days"),
    ("internal_complaints", "Internal Customer Complaints", "count"),
    ("cost_per_shipment", "Cost per Shipment", "currency"),
]

#: Metrics expressed as a percentage — validated to the 0-100 range.
KPI_PERCENT_FIELDS = {
    "inventory_accuracy", "on_time_delivery", "order_fulfilment_accuracy", "damage_rate",
}

# ── Vendor Scorecard (Phase 4) ───────────────────────────────────────────────
# The SOW's "Vendor Scorecard" sheet: 4 weighted attributes, each scored out of
# 10 by Meril stakeholders with a mandatory comment per score, rolling up to a
# Final Score out of 10. Kept as a separate model from the pre-existing
# VendorScore (read by ai_evaluator.py, which must keep working unchanged).
#
# Each row: (field_prefix, label, weight_percent). field_prefix + "_score" /
# field_prefix + "_comment" are the two model fields for that attribute.
# Weight is stored as a whole percentage (20, not 0.20) purely so templates
# can print it directly — VendorScorecard.weights() divides by 100 itself.
SCORECARD_ATTRIBUTES = [
    ("cost", "Cost", 20),
    ("serviceability", "Serviceability", 40),
    ("wms_reports", "WMS / Reports", 20),
    ("tat", "TAT", 20),
]

# ── Capability icon picker ───────────────────────────────────────────────────
# The Capability Master is used by the SCM team, not by developers, so the icon
# is chosen from a palette rather than typed. Asking a non-technical user to
# know that "fa-snowflake" is a snowflake is asking them to know Font Awesome.
#
# Curated to what a 3PL capability actually is — certification, storage, cold
# chain, transport, customs, systems, handling, service — rather than the full
# 2,000-icon set, which is a worse picker than no picker.
#
# Every name below is verified present in static/css/font-awesome.min.css
# (Font Awesome Free 6.5.1). An icon that is not in that file renders as a blank
# square, so anything added here must be checked against it first.
#
# Each group: (group label, [(icon class, what it depicts), ...]).
CAPABILITY_ICON_GROUPS = [
    ("Certification & compliance", [
        ("fa-certificate", "Certificate"), ("fa-award", "Award"),
        ("fa-medal", "Medal"), ("fa-stamp", "Stamp"),
        ("fa-clipboard-check", "Checked clipboard"), ("fa-file-shield", "Protected document"),
        ("fa-shield-halved", "Shield"), ("fa-scale-balanced", "Regulatory / legal"),
        ("fa-user-shield", "Vetted personnel"), ("fa-file-circle-check", "Approved document"),
        ("fa-ribbon", "Accreditation"),
    ]),
    ("Warehouse & storage", [
        ("fa-warehouse", "Warehouse"), ("fa-boxes-stacked", "Stacked boxes"),
        ("fa-box", "Box"), ("fa-box-open", "Open box"),
        ("fa-pallet", "Pallet"), ("fa-layer-group", "Racking / layers"),
        ("fa-cubes", "Bulk stock"), ("fa-industry", "Facility"),
        ("fa-dolly", "Dolly"), ("fa-cart-flatbed", "Flatbed cart"),
        ("fa-box-archive", "Archive / bonded"),
    ]),
    ("Temperature & cold chain", [
        ("fa-temperature-low", "Low temperature"), ("fa-temperature-half", "Controlled temperature"),
        ("fa-temperature-arrow-down", "Cooling"), ("fa-snowflake", "Frozen"),
        ("fa-icicles", "Deep freeze"), ("fa-thermometer", "Thermometer"),
        ("fa-fire", "Heated"), ("fa-wind", "Ventilated"),
    ]),
    ("Transport & delivery", [
        ("fa-truck", "Truck"), ("fa-truck-fast", "Express delivery"),
        ("fa-truck-ramp-box", "Loading"), ("fa-truck-front", "Fleet"),
        ("fa-van-shuttle", "Van"), ("fa-ship", "Sea freight"),
        ("fa-plane", "Air freight"), ("fa-plane-departure", "Export"),
        ("fa-train", "Rail"), ("fa-route", "Route planning"),
        ("fa-map-location-dot", "Coverage area"), ("fa-motorcycle", "Two-wheeler"),
        ("fa-bicycle", "Cycle courier"), ("fa-road", "Line haul"),
    ]),
    ("Customs & documentation", [
        ("fa-passport", "Customs clearance"), ("fa-file-invoice", "Invoice"),
        ("fa-file-contract", "Contract"), ("fa-file-signature", "Signed document"),
        ("fa-receipt", "Receipt"), ("fa-globe", "International"),
        ("fa-earth-americas", "Cross-border"), ("fa-anchor", "Port"),
        ("fa-building-columns", "Duty / authority"), ("fa-money-check-dollar", "Payment"),
    ]),
    ("Systems & integration", [
        ("fa-cloud", "Cloud platform"), ("fa-network-wired", "EDI / network"),
        ("fa-plug", "Integration"), ("fa-server", "Server"),
        ("fa-database", "Database"), ("fa-barcode", "Barcode"),
        ("fa-qrcode", "QR code"), ("fa-microchip", "RFID / chip"),
        ("fa-gears", "Automation"), ("fa-mobile-screen", "Mobile app"),
        ("fa-laptop-code", "WMS"), ("fa-code-branch", "API"),
        ("fa-satellite-dish", "Tracking"),
    ]),
    ("Handling & quality", [
        ("fa-recycle", "Returns / reverse"), ("fa-arrows-rotate", "Rework"),
        ("fa-magnifying-glass", "Inspection"), ("fa-clipboard-list", "Stock count"),
        ("fa-triangle-exclamation", "Hazardous"), ("fa-circle-check", "Quality passed"),
        ("fa-vials", "Samples"), ("fa-syringe", "Medical devices"),
        ("fa-kit-medical", "Healthcare"), ("fa-heart-pulse", "Patient-critical"),
        ("fa-hand-holding-hand", "Careful handling"),
    ]),
    ("Service & reporting", [
        ("fa-headset", "Support desk"), ("fa-user-tie", "Account manager"),
        ("fa-users", "Dedicated team"), ("fa-clock", "Hours / SLA"),
        ("fa-phone", "Contact"), ("fa-envelope", "Notifications"),
        ("fa-chart-line", "Reporting"), ("fa-gauge-high", "Performance"),
    ]),
]

#: Flat set of every pickable icon — what the form validates against.
CAPABILITY_ICON_CHOICES = {
    icon for _group, icons in CAPABILITY_ICON_GROUPS for icon, _label in icons
}

#: Used when nothing is chosen. In the palette above (Handling & quality).
DEFAULT_CAPABILITY_ICON = "fa-circle-check"
