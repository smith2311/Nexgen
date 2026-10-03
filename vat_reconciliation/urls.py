from django.urls import path

from . import views

app_name = "vat"

urlpatterns = [
    path("", views.vat_home, name="home"),
    path("dashboard/", views.vat_dashboard, name="dashboard"),
    path("api/dashboard/stats/", views.dashboard_stats_api, name="dashboard_stats_api"),
    path("api/dashboard/fx/", views.dashboard_fx_api, name="dashboard_fx_api"),

    # ── Country Master ──────────────────────────────────────────────────────
    path("country-master/", views.country_master_list, name="country_master_list"),
    path("api/country/list/", views.country_list_api, name="country_list_api"),
    path("api/country/create/", views.country_create_api, name="country_create_api"),
    path("api/country/update/<int:id>/", views.country_update_api, name="country_update_api"),
    path("api/country/delete/<int:id>/", views.country_delete_api, name="country_delete_api"),
    path("api/country/upload/", views.country_upload_api, name="country_upload_api"),
    path("api/country/preview/", views.country_preview_api, name="country_preview_api"),
    path("api/country/confirm/", views.country_confirm_api, name="country_confirm_api"),

    # ── VAT Rate Master ─────────────────────────────────────────────────────
    path("vat-rate-master/", views.rate_master_list, name="rate_master_list"),
    path("api/rate/list/", views.rate_list_api, name="rate_list_api"),
    path("api/rate/create/", views.rate_create_api, name="rate_create_api"),
    path("api/rate/update/<int:id>/", views.rate_update_api, name="rate_update_api"),
    path("api/rate/delete/<int:id>/", views.rate_delete_api, name="rate_delete_api"),
    path("api/rate/upload/", views.rate_upload_api, name="rate_upload_api"),
    path("api/rate/preview/", views.rate_preview_api, name="rate_preview_api"),
    path("api/rate/confirm/", views.rate_confirm_api, name="rate_confirm_api"),

    # ── CHA Email Master ────────────────────────────────────────────────────
    path("cha-email-master/", views.cha_master_list, name="cha_master_list"),
    path("api/cha/list/", views.cha_list_api, name="cha_list_api"),
    path("api/cha/create/", views.cha_create_api, name="cha_create_api"),
    path("api/cha/update/<int:id>/", views.cha_update_api, name="cha_update_api"),
    path("api/cha/delete/<int:id>/", views.cha_delete_api, name="cha_delete_api"),
    path("api/cha/upload/", views.cha_upload_api, name="cha_upload_api"),
    path("api/cha/preview/", views.cha_preview_api, name="cha_preview_api"),
    path("api/cha/confirm/", views.cha_confirm_api, name="cha_confirm_api"),

    # ── Invoice upload / process / output ───────────────────────────────────
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail_page, name="invoice_detail_page"),
    path("api/invoice/list/", views.invoice_list_api, name="invoice_list_api"),
    path("api/invoice/detail/<int:id>/", views.invoice_detail_api, name="invoice_detail_api"),
    path("api/invoice/upload/", views.invoice_upload_api, name="invoice_upload_api"),
    path("api/invoice/process/<int:id>/", views.invoice_process_api, name="invoice_process_api"),
    path("api/invoice/delete/<int:id>/", views.invoice_delete_api, name="invoice_delete_api"),
    path("api/invoice/email-draft/<int:id>/", views.invoice_email_draft_api, name="invoice_email_draft_api"),
    path("api/invoice/cha-contacts/<int:id>/", views.invoice_cha_contacts_api, name="invoice_cha_contacts_api"),
    path("api/invoice/send-cha/<int:id>/", views.invoice_send_cha_api, name="invoice_send_cha_api"),
]
