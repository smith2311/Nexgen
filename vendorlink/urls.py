from django.urls import include, path

from . import (
    admin_console, assessment, awards, backup, bidviews, commercial, performance,
    reports, tier3views, tier4views, views,
)

app_name = "vendorlink"

urlpatterns = [
    # ── VendorLink platform launcher ───────────────────────────────────
    path("", views.index, name="index"),

    # ── Geo Command Centre ─────────────────────────────
    # The map under the Admin Console banner. Its data, endpoints, routes,
    # template and assets all live in vendorlink/UI/ — this include is the
    # only line about it outside that folder.
    path("geo/", include("vendorlink.UI.geo_routes")),

    # ── Admin Console ──────────────────────────────────────────────────
    path("admin/", admin_console.console, name="admin_console"),
    path("admin/users/", admin_console.users, name="admin_users"),
    path("admin/users/new/", admin_console.user_create, name="admin_user_create"),
    path("admin/users/<int:pk>/", admin_console.user_detail, name="admin_user_detail"),
    path("admin/users/<int:pk>/action/", admin_console.user_action, name="admin_user_action"),
    path("admin/users/<int:pk>/role/", admin_console.user_role, name="admin_user_role"),
    # Admin-only per-user analytics. Nested under admin/ so the URL says what
    # gates it — everything below that prefix needs is_vendorlink_admin.
    path("admin/insights/", admin_console.insights, name="admin_insights"),
    path("admin/insights/<int:pk>/", admin_console.insights_user, name="admin_insights_user"),
    path("admin/masters/", admin_console.masters, name="admin_masters"),
    path("admin/masters/capabilities/", admin_console.capabilities, name="admin_capabilities"),
    path("admin/masters/capabilities/<int:pk>/edit/", admin_console.capability_edit, name="capability_edit"),
    path("admin/masters/capabilities/<int:pk>/toggle/", admin_console.capability_toggle, name="capability_toggle"),
    # Historical Data replaces the year purge: the old data stays in the
    # database and is read over a date range here instead of being deleted.
    path("admin/historical-data/", backup.historical_data, name="historical_data"),
    path("admin/historical-data/excel/", backup.historical_data_excel, name="historical_data_excel"),
    path("admin/historical-data/pdf/", backup.historical_data_pdf, name="historical_data_pdf"),

    # ── Vendor Master module ───────────────────────────────────────────
    path("vendor-master/", views.vm_home, name="vm_home"),
    path("vendor-master/vendors/", views.vendor_list, name="vendor_list"),
    path("vendor-master/vendors/new/", views.vendor_create, name="vendor_create"),
    path("vendor-master/vendors/<int:pk>/", views.vendor_detail, name="vendor_detail"),
    path("vendor-master/vendors/<int:pk>/edit/", views.vendor_edit, name="vendor_edit"),
    path("vendor-master/vendors/<int:pk>/delete/", views.vendor_delete, name="vendor_delete"),
    path("vendor-master/vendors/<int:pk>/grant-access/", views.vendor_grant_access, name="vendor_grant_access"),
    path("vendor-master/capability-matrix/", views.capability_matrix, name="capability_matrix"),
    path("vendor-master/export/", views.export_excel, name="export_excel"),
    # The price-premium heatmap as a workbook. Honours the Bid Analytics
    # filters, so the query string it is called with decides what is exported.
    path("bids/heatmap/export/", views.quote_heatmap_excel, name="quote_heatmap_excel"),
    path("vendor-master/import/", views.import_excel, name="import_excel"),
    path("vendor-master/import/template/", views.import_template, name="import_template"),

    # ── Commercial Database (Phase 2) — vendor rate cards ──────────────
    path("commercial/", commercial.commercial_list, name="commercial_list"),
    path("commercial/new/", commercial.commercial_create, name="commercial_create"),
    path("commercial/export/", commercial.commercial_export, name="commercial_export"),
    path("commercial/<int:pk>/", commercial.commercial_detail, name="commercial_detail"),
    path("commercial/<int:pk>/edit/", commercial.commercial_edit, name="commercial_edit"),
    path("commercial/<int:pk>/delete/", commercial.commercial_delete, name="commercial_delete"),
    path("commercial/<int:pk>/pdf/", commercial.commercial_export_pdf, name="commercial_export_pdf"),

    # ── 3PL Capability Assessment (Phase 3) ─────────────────────────────
    path("assessment/", assessment.assessment_list, name="assessment_list"),
    path("assessment/invite/", assessment.assessment_invite, name="assessment_invite"),
    path("assessment/fill/<str:token>/", assessment.assessment_fill, name="assessment_fill"),
    path("assessment/<int:pk>/", assessment.assessment_review, name="assessment_review"),
    path("assessment/<int:pk>/approve/", assessment.assessment_approve, name="assessment_approve"),
    path("assessment/<int:pk>/reject/", assessment.assessment_reject, name="assessment_reject"),
    path("assessment/<int:pk>/promote/", assessment.assessment_promote, name="assessment_promote"),

    # ── KPI Dashboard (Phase 4) ──────────────────────────────────────────
    path("kpi/", performance.kpi_dashboard, name="kpi_dashboard"),
    path("kpi/new/", performance.kpi_record_create, name="kpi_record_create"),
    path("kpi/export/", performance.kpi_export, name="kpi_export"),
    path("kpi/<int:pk>/edit/", performance.kpi_record_edit, name="kpi_record_edit"),
    path("kpi/<int:pk>/delete/", performance.kpi_record_delete, name="kpi_record_delete"),

    # ── Vendor Scorecard (Phase 4) ────────────────────────────────────────
    path("scorecard/", performance.scorecard_list, name="scorecard_list"),
    path("scorecard/new/", performance.scorecard_create, name="scorecard_create"),
    path("scorecard/vendor/<int:vendor_pk>/", performance.scorecard_vendor_history, name="scorecard_vendor_history"),
    path("scorecard/<int:pk>/delete/", performance.scorecard_delete, name="scorecard_delete"),
    # ── Geo API ────────────────────────────────────────────────────────
    path("api/cities/", views.api_cities, name="api_cities"),
    path("api/vendors-by-country/", views.api_vendors_by_country, name="api_vendors_by_country"),

    # ── Requester module — raise a request to all vendors ──────────────
    path("requests/", views.request_list, name="request_list"),
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/bid/", views.bid_submit, name="bid_submit"),
    path("bids/<int:pk>/select/", views.bid_select, name="bid_select"),
    path("requests/attachments/<int:pk>/", views.attachment_download, name="attachment_download"),

    # ── Vendor self-registration (public — no auth) ────────────────────
    path("register/", views.vendor_register, name="vendor_register"),
    path("register/success/", views.vendor_register_success, name="register_success"),
    path("register/send-otp/", views.send_email_otp, name="send_otp"),
    path("register/verify-otp/", views.verify_email_otp, name="verify_otp"),
    path("register/captcha/", views.captcha_image, name="captcha_image"),
    path("register/check-captcha/", views.check_captcha, name="check_captcha"),

    # ── Registration admin queue ───────────────────────────────────────
    path("registrations/", views.registration_list, name="registration_list"),
    path("registrations/<int:pk>/approve/", views.approve_registration, name="approve_registration"),
    path("registrations/<int:pk>/reject/", views.reject_registration, name="reject_registration"),
    path("registrations/bulk-action/", views.bulk_registration_action, name="bulk_registration_action"),

    # ── Vendor portal (external vendor accounts) ───────────────────────
    path("vendor/change-password/", views.vendor_change_password, name="vendor_change_password"),

    # ── Bid Analytics & AI Evaluation ─────────────────────────────────────────
    path("bids/", views.bid_analytics, name="bid_analytics"),
    # Tier 1 redesign — per-request evaluation and comparison screens. Both
    # read existing data; running an evaluation still goes through
    # views.request_evaluate.
    # ── Commercial reports: server-computed, not LLM-generated ───────────
    # report_monthly takes the same ?country=&year= Bid Analytics uses, so the
    # Create Report button can hand its current filters straight over.
    path("reports/monthly/", reports.monthly_report, name="report_monthly"),
    path("reports/monthly/export/<str:file_format>/", reports.monthly_report_export, name="report_monthly_export"),
    path("reports/bulk/", reports.bulk_report, name="report_bulk"),
    path("reports/request/<int:pk>/", reports.request_report, name="report_request"),
    path("reports/request/<int:pk>/export/<str:file_format>/", reports.request_report_export, name="report_request_export"),

    # ── Award: the requester nominates a vendor and can withdraw/renominate ──
    # No vendor-facing route here any more -- the vendor is told by mail, not
    # asked to answer. See the module docstring in awards.py.
    path("bids/<int:pk>/award/", awards.award_create, name="award_create"),
    path("awards/<int:pk>/withdraw/", awards.award_withdraw, name="award_withdraw"),
    # Request-scoped, not bid-scoped: closing/reopening nominations is a
    # decision about the RFI as a whole, independent of any one bid.
    path("requests/<int:pk>/nominations/close/", awards.close_nominations, name="close_nominations"),
    path("requests/<int:pk>/nominations/reopen/", awards.reopen_nominations, name="reopen_nominations"),

    path("bids/<int:pk>/evaluation/", bidviews.evaluation_detail, name="evaluation_detail"),
    path("bids/<int:pk>/compare/", bidviews.bid_heatmap, name="bid_heatmap"),
    # Tier 2 — the dashboard those two hang off, and the single-bid view.
    path("bids/analytics/", bidviews.analytics_dashboard, name="analytics_dashboard"),
    path("bids/detail/<int:pk>/", bidviews.bid_detail, name="bid_detail"),
    path("bids/detail/<int:pk>/status/", bidviews.bid_set_status, name="bid_set_status"),

    # ── Tier 3 redesign — core masters, on new routes beside the originals
    #    until the cutover (see tier3views' module docstring).
    path("v2/", tier3views.dashboard, name="t3_dashboard"),
    path("v2/vendors/", tier3views.vendor_master, name="t3_vendor_master"),
    path("v2/vendors/<int:pk>/drawer/", tier3views.vendor_drawer, name="t3_vendor_drawer"),
    path("v2/commercial/", tier3views.commercial, name="t3_commercial"),
    path("v2/scorecard/", tier3views.scorecard, name="t3_scorecard"),
    path("v2/kpi/", tier3views.kpi, name="t3_kpi"),

    # ── Tier 4 redesign — workflows and forms. All render-only; the writes
    #    stay on the existing endpoints above.
    path("v2/requests/", tier4views.request_list, name="t4_request_list"),
    path("v2/requests/<int:pk>/", tier4views.request_detail, name="t4_request_detail"),
    path("v2/register/", tier4views.registration_wizard, name="t4_register"),
    path("v2/assessment/<int:pk>/", tier4views.assessment_review, name="t4_assessment_review"),
    path("v2/admin/", tier4views.admin_console, name="t4_admin"),
    path("bids/ask-gpt/", views.merilgpt_ask, name="merilgpt_ask"),
    # Recent-chats history for the MerilGPT panel. Each is scoped to the
    # signed-in user in the view — a thread is not shared between buyers.
    path("bids/gpt/conversations/", views.merilgpt_conversations, name="merilgpt_conversations"),
    path("bids/gpt/conversations/<int:pk>/", views.merilgpt_conversation, name="merilgpt_conversation"),
    path("bids/gpt/conversations/<int:pk>/delete/", views.merilgpt_conversation_delete,
         name="merilgpt_conversation_delete"),
    path("requests/<int:pk>/evaluate/", views.request_evaluate, name="request_evaluate"),
    path("requests/<int:pk>/evaluation-status/", views.api_evaluation_status, name="api_evaluation_status"),
    path("api/evaluation-data/<int:pk>/", views.api_evaluation_data, name="api_evaluation_data"),

    # ── Vendor Score ────────────────────────────────────────────────────────────
]
