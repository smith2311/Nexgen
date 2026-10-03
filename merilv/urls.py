from django.urls import path

from . import btscan, inbound, layouts, masters, monitoring, roles, views, zva05

app_name = "merilv"

urlpatterns = [
    # ── Module launcher ────────────────────────────────────────────────
    path("", views.index, name="index"),

    # ── Stock validator ────────────────────────────────────────────────
    path("validator/", views.validator, name="validator"),
    path("validator/run/", views.validator_run, name="validator_run"),
    path("validator/<int:pk>/", views.validation_detail, name="validation_detail"),
    path("validator/<int:pk>/export/", views.validation_export, name="validation_export"),
    path("validator/history/", views.validation_history, name="validation_history"),

    # ── Outbound scanner ───────────────────────────────────────────────
    path("outbound/", views.outbound, name="outbound"),
    path("outbound/upload/", views.outbound_upload, name="outbound_upload"),
    path("outbound/<int:pk>/", views.outbound_session, name="outbound_session"),
    path("outbound/<int:pk>/delete/", views.outbound_session_delete, name="outbound_session_delete"),
    path("outbound/<int:pk>/scan/", views.outbound_scan, name="outbound_scan"),
    path("outbound/<int:pk>/undo/", views.outbound_undo, name="outbound_undo"),
    path("outbound/<int:pk>/focus/", views.outbound_focus, name="outbound_focus"),
    path("outbound/<int:pk>/state/", views.outbound_state, name="outbound_state"),
    path("outbound/<int:pk>/finish/", views.outbound_finish, name="outbound_finish"),
    path("outbound/<int:pk>/bulk-finish/", views.outbound_bulk_finish, name="outbound_bulk_finish"),
    path("outbound/<int:pk>/export/", views.outbound_export, name="outbound_export"),
    path("outbound/<int:pk>/line-batch/", views.outbound_line_batch, name="outbound_line_batch"),
    path("outbound/<int:pk>/invoice/", views.outbound_invoice_export, name="outbound_invoice_export"),
    path("outbound/<int:pk>/edit-scan/", views.outbound_edit_scan, name="outbound_edit_scan"),
    path("outbound/<int:pk>/delete-scan/", views.outbound_scan_delete, name="outbound_scan_delete"),
    path("outbound/<int:pk>/clear-scans/", views.outbound_clear_scans, name="outbound_clear_scans"),
    path("outbound/<int:pk>/delete-delivery/", views.outbound_delivery_delete, name="outbound_delivery_delete"),
    path("outbound/<int:pk>/bulk-scan/", views.outbound_bulk_scan, name="outbound_bulk_scan"),

    # ── Live monitoring ────────────────────────────────────────────────
    path("monitoring/", monitoring.board, name="monitoring"),
    path("monitoring/state/", monitoring.state, name="monitoring_state"),

    # ── ZVA05 auto-assign ──────────────────────────────────────────────
    # No longer a module of its own: ZVA05 is a mode of the Outbound Scanner
    # (see views.OUTBOUND_MODES). ``zva05`` now redirects there; the console
    # routes below are unchanged and are reached from that mode's dump list.
    path("zva05/", zva05.index, name="zva05"),
    path("zva05/upload/", zva05.upload, name="zva05_upload"),
    path("zva05/<int:pk>/", zva05.detail, name="zva05_detail"),
    path("zva05/<int:pk>/scan/", zva05.scan, name="zva05_scan"),
    path("zva05/<int:pk>/undo/", zva05.undo, name="zva05_undo"),
    path("zva05/<int:pk>/focus/", zva05.focus, name="zva05_focus"),
    path("zva05/<int:pk>/finish/", zva05.finish, name="zva05_finish"),
    path("zva05/<int:pk>/export/", zva05.export_xlsx, name="zva05_export"),

    # ── Delivery history & analytics ───────────────────────────────────
    path("deliveries/", views.delivery_history, name="delivery_history"),
    path("deliveries/export/", views.delivery_export, name="delivery_export"),
    path("deliveries/invoice/", views.delivery_invoice_export, name="delivery_invoice_export"),
    path("deliveries/packing-list/", views.delivery_packing_list, name="delivery_packing_list"),
    path("deliveries/<int:pk>/", views.delivery_detail, name="delivery_detail"),
    path("deliveries/<int:pk>/courier/", views.delivery_courier_update, name="delivery_courier_update"),
    path("deliveries/<int:pk>/delete/", views.delivery_delete, name="delivery_delete"),
    path("reports/", views.reports, name="reports"),
    # Admin-only user analytics. Nested under reports/ so the URL says what
    # gates it — everything below this prefix needs is_merilv_admin.
    path("reports/users/", views.user_activity, name="user_activity"),
    path("reports/users/export/", views.user_activity_export, name="user_activity_export"),
    path("reports/users/<int:pk>/", views.user_activity_detail, name="user_activity_detail"),

    # ── Barcode extractor ──────────────────────────────────────────────
    path("extractor/", views.extractor, name="extractor"),
    path("extractor/parse/", views.extractor_parse, name="extractor_parse"),
    path("extractor/export/", views.extractor_export, name="extractor_export"),
    path("extractor/session/new/", views.scan_session_create, name="scan_session_create"),
    path("extractor/session/<int:pk>/", views.scan_session, name="scan_session"),
    path("extractor/session/<int:pk>/scan/", views.scan_session_scan, name="scan_session_scan"),
    path(
        "extractor/session/<int:pk>/record/<int:record_pk>/condition/",
        views.scan_session_record_condition, name="scan_session_record_condition",
    ),
    path("extractor/session/<int:pk>/columns/", views.scan_session_columns, name="scan_session_columns"),
    path("extractor/session/<int:pk>/finish/", views.scan_session_finish, name="scan_session_finish"),
    path("extractor/session/<int:pk>/export/", views.scan_session_export, name="scan_session_export"),
    path("extractor/history/", views.scan_history, name="scan_history"),
    path("extractor/history/export/", views.scan_history_export, name="scan_history_export"),

    # ── Branch transfer ────────────────────────────────────────────────
    path("branch-transfer/", views.branch_transfer, name="branch_transfer"),
    path("branch-transfer/<int:pk>/", views.branch_transfer_plan, name="branch_transfer_plan"),
    path("branch-transfer/<int:pk>/state/", views.branch_transfer_state, name="branch_transfer_state"),
    path("branch-transfer/<int:pk>/scan/", views.branch_transfer_scan, name="branch_transfer_scan"),
    path("branch-transfer/<int:pk>/bulk-scan/", views.branch_transfer_bulk_scan, name="branch_transfer_bulk_scan"),
    path("branch-transfer/<int:pk>/auto-assign/", views.branch_transfer_auto_assign, name="branch_transfer_auto_assign"),
    path("branch-transfer/<int:pk>/line-batch/", views.branch_transfer_line_batch, name="branch_transfer_line_batch"),
    path("branch-transfer/<int:pk>/export/", views.branch_transfer_export, name="branch_transfer_export"),

    # ── BT Scanning (the floor console for a branch transfer) ──────────
    # No longer a module of its own: BT Scanning is a mode of the Outbound
    # Scanner now (see views.OUTBOUND_MODES), the same way ZVA05 is. ``btscan``
    # redirects there; the console routes below are unchanged and are reached
    # from that mode's open-sessions list. Branch Transfer (a separate screen)
    # still sets the plan up; this is what a picker uses to fill cartons
    # against it.
    path("bt-scan/", btscan.index, name="btscan"),
    path("bt-scan/open/", btscan.open_session, name="btscan_open"),
    path("bt-scan/<int:pk>/", btscan.detail, name="btscan_detail"),
    path("bt-scan/<int:pk>/state/", btscan.state, name="btscan_state"),
    path("bt-scan/<int:pk>/scan/", btscan.scan, name="btscan_scan"),
    path("bt-scan/<int:pk>/undo/", btscan.undo, name="btscan_undo"),
    path("bt-scan/<int:pk>/box/new/", btscan.box_new, name="btscan_box_new"),
    path("bt-scan/<int:pk>/box/<int:box_pk>/zone/", btscan.box_zone, name="btscan_box_zone"),
    path("bt-scan/<int:pk>/focus/", btscan.focus, name="btscan_focus"),
    path("bt-scan/<int:pk>/priority/", btscan.priority, name="btscan_priority"),
    path("bt-scan/<int:pk>/finish/", btscan.finish, name="btscan_finish"),
    path("bt-scan/<int:pk>/export/", btscan.export_xlsx, name="btscan_export"),

    # ── Inbound scanner (the receiving side of a branch transfer) ──────
    path("inbound/", inbound.index, name="inbound"),
    path("inbound/<int:pk>/", inbound.detail, name="inbound_detail"),
    path("inbound/<int:pk>/scan/", inbound.scan, name="inbound_scan"),
    path("inbound/<int:pk>/bulk-scan/", inbound.bulk_scan, name="inbound_bulk_scan"),
    path(
        "inbound/<int:pk>/scan/<int:scan_pk>/condition/",
        inbound.scan_condition,
        name="inbound_scan_condition",
    ),
    path("inbound/<int:pk>/undo/", inbound.undo, name="inbound_undo"),
    path("inbound/<int:pk>/focus/", inbound.focus, name="inbound_focus"),
    path("inbound/<int:pk>/finish/", inbound.finish, name="inbound_finish"),
    path("inbound/<int:pk>/export/", inbound.export_xlsx, name="inbound_export"),

    # ── Invoice consolidator ───────────────────────────────────────────
    path("consolidator/", views.consolidator, name="consolidator"),
    path("consolidator/<int:pk>/", views.consolidation_detail, name="consolidation_detail"),
    path("consolidator/<int:pk>/export/", views.consolidation_export, name="consolidation_export"),

    # ── Master data console ────────────────────────────────────────────
    path("masters/", masters.console, name="masters"),

    # One set of registry-driven endpoints serves every master; <key> is a
    # MASTER_SHEETS key ("materials", "plant", "storage", …). Adding a master
    # means one registry entry, not four more routes.
    path("masters/sheet/<str:key>/template/", masters.master_template, name="masters_template"),
    path("masters/sheet/<str:key>/export/", masters.master_export, name="masters_export"),
    path("masters/sheet/<str:key>/import/", masters.master_import, name="masters_import"),
    path("masters/sheet/<str:key>/clear/", masters.master_clear, name="masters_clear"),
    path("masters/sheet/<str:key>/delete-row/", masters.master_row_delete, name="masters_row_delete"),
    path("masters/export/", masters.masters_export_all, name="masters_export_all"),

    path("masters/materials/", masters.materials, name="masters_materials"),
    path("masters/materials/new/", masters.material_create, name="masters_material_create"),
    path("masters/materials/save/", masters.material_save, name="masters_material_save"),
    path("masters/customers/", masters.customers, name="masters_customers"),
    path("masters/customers/new/", masters.customer_create, name="masters_customer_create"),
    path("masters/customers/save/", masters.customer_save, name="masters_customer_save"),
    path(
        "masters/customers/bulk-courier/",
        masters.customer_bulk_courier,
        name="masters_customer_bulk_courier",
    ),
    path("masters/couriers/", masters.couriers, name="masters_couriers"),
    path("masters/couriers/save/", masters.courier_save, name="masters_courier_save"),
    path("masters/boxes/", masters.boxes, name="masters_boxes"),
    path("masters/boxes/save/", masters.box_save, name="masters_box_save"),
    path("masters/organisation/", masters.org, name="masters_org"),
    path("masters/organisation/tree/", masters.org_tree, name="masters_org_tree"),
    path("masters/organisation/<str:table>/save/", masters.org_save, name="masters_org_save"),
    path("masters/organisation/plant/<str:plant_code>/orgs/", masters.plant_org_map, name="masters_plant_org_map"),
    path("masters/entities/", masters.entities, name="masters_entities"),
    path("masters/entities/new/", masters.entity_create, name="masters_entity_create"),
    path("masters/entities/<int:pk>/", masters.entity_edit, name="masters_entity_edit"),
    path("masters/roles/", roles.index, name="masters_roles"),
    path("masters/roles/new/", roles.create, name="masters_roles_create"),
    path("masters/roles/update/", roles.update, name="masters_roles_update"),
    path("masters/roles/assign/", roles.assign, name="masters_roles_assign"),
    path("masters/roles/remove/", roles.remove, name="masters_roles_remove"),
    path("masters/roles/plants/", roles.set_plants, name="masters_roles_set_plants"),
    path("masters/roles/entities/", roles.set_entities, name="masters_roles_set_entities"),
    path("masters/roles/scopes/", roles.set_scopes, name="masters_roles_set_scopes"),
    path("masters/roles/manager/", roles.set_manager, name="masters_roles_set_manager"),
    path("masters/roles/capabilities/", roles.set_permissions, name="masters_roles_capabilities"),
    path("masters/roles/save-scope/", roles.save_scope, name="masters_roles_save_scope"),
    path("masters/roles/template/", roles.import_template, name="masters_roles_template"),
    path("masters/roles/import/", roles.bulk_import, name="masters_roles_import"),
    path("masters/activity/", masters.activity, name="masters_activity"),
    path("masters/activity/export/", masters.activity_export, name="masters_activity_export"),

    # ── Saved layouts (Row 3.8 / 17.1 "Layout feature") ────────────────
    path("layouts/<str:screen_key>/", layouts.state, name="layouts_state"),
    path("layouts/<str:screen_key>/save/", layouts.save, name="layouts_save"),
    path("layouts/<str:screen_key>/clear/", layouts.clear_active, name="layouts_clear"),
    path("layouts/<str:screen_key>/<int:pk>/activate/", layouts.activate, name="layouts_activate"),
    path("layouts/<str:screen_key>/<int:pk>/delete/", layouts.delete, name="layouts_delete"),
]
