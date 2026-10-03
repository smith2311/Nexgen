"""Where "Back" goes on every Meril-V screen.

The module is three levels deep — launcher -> tool -> record — so a single
hardcoded "back to the launcher" link skips a level, and a referrer-based one is
non-deterministic (it depends on how you arrived, so the same page sends you
somewhere different each time, and nowhere at all on a fresh tab or after a
form POST redirect).

This maps each screen to its PARENT instead, so Back always walks one step up
the hierarchy the breadcrumb already shows:

    /meril-v/outbound/4/  ->  /meril-v/outbound/  ->  /meril-v/

Keyed by ``request.resolver_match.url_name``, so adding a screen means adding
one line here rather than editing a template.
"""

from __future__ import annotations

#: url_name -> (parent url_name, label shown on the button, context key holding
#: the object whose pk the parent route needs — None when the parent takes no
#: arguments, which is the usual case).
PARENTS: dict[str, tuple[str, str, str | None]] = {
    # Launcher — one step up leaves the module entirely.
    "index": ("__landing__", "All Modules", None),

    # Stock validator
    "validator": ("index", "Meril-V", None),
    "validation_detail": ("validator", "Stock Validator", None),
    "validation_history": ("validator", "Stock Validator", None),

    # Outbound scanner
    "outbound": ("index", "Meril-V", None),
    "outbound_session": ("outbound", "Outbound Scanner", None),
    # The discrepancy report is reached from a specific session, so its parent
    # needs that session's pk.
    "outbound_discrepancy": ("outbound_session", "Outbound Scanner", "session"),
    "delivery_history": ("index", "Meril-V", None),
    "delivery_detail": ("delivery_history", "Delivery History", None),

    # Barcode extractor
    "extractor": ("index", "Meril-V", None),
    "scan_session": ("extractor", "Barcode Extractor", None),
    "scan_session_columns": ("scan_session", "Session", "session"),
    "scan_history": ("extractor", "Barcode Extractor", None),

    # Invoice consolidator
    "consolidator": ("index", "Meril-V", None),
    "consolidation_detail": ("consolidator", "Invoice Consolidator", None),

    # Branch transfer
    "branch_transfer": ("index", "Meril-V", None),
    "branch_transfer_plan": ("branch_transfer", "Branch Transfer", None),

    # Inbound scanner (the receiving side of a branch transfer)
    "inbound": ("index", "Meril-V", None),
    "inbound_detail": ("inbound", "Inbound Scanner", None),

    # ZVA05 and BT Scanning — both modes of the Outbound Scanner now, not
    # modules of their own, so each console's parent is that screen rather
    # than a landing page of its own.
    "zva05_detail": ("outbound", "Outbound Scanner", None),
    "btscan_detail": ("outbound", "Outbound Scanner", None),

    # Standalone tools
    "monitoring": ("index", "Meril-V", None),
    "reports": ("index", "Meril-V", None),
    "user_activity": ("reports", "Reports", None),
    "user_activity_detail": ("user_activity", "User Activity", None),

    # Master data
    "masters": ("index", "Meril-V", None),
    "masters_materials": ("masters", "Master Data", None),
    "masters_material_create": ("masters_materials", "Materials", None),
    "masters_customers": ("masters", "Master Data", None),
    "masters_customer_create": ("masters_customers", "Customers", None),
    "masters_couriers": ("masters", "Master Data", None),
    "masters_boxes": ("masters", "Master Data", None),
    "masters_org": ("masters", "Master Data", None),
    "masters_entities": ("masters", "Master Data", None),
    "masters_entity_create": ("masters_entities", "Entities", None),
    "masters_entity_edit": ("masters_entities", "Entities", None),
    "masters_roles": ("masters", "Master Data", None),
    "masters_activity": ("masters", "Master Data", None),
}

#: Sentinel meaning "the parent is outside this module".
LANDING = "__landing__"
