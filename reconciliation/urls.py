"""
reconciliation/urls.py — mounted by core/urls.py at /reconciliation/.

Three pages and the endpoints behind them. Reads are GET, writes are POST; the
grid, the upload and the edit drawer are the only callers, so the shapes are
kept close to what those screens need rather than generalised into a REST API
nothing would consume.
"""
from django.urls import path

from . import views

app_name = "reconciliation"

urlpatterns = [
    # ── pages ────────────────────────────────────────────────────────────────
    path("", views.records, name="records"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("billing-types/", views.billing_types, name="billing_types"),
    path("sales-types/", views.sales_types, name="sales_types"),

    # ── the grid ─────────────────────────────────────────────────────────────
    path("api/records/", views.records_data, name="records_data"),
    path("api/filters/", views.filter_options, name="filter_options"),

    # ── one record ───────────────────────────────────────────────────────────
    path("api/record/new/", views.record_create, name="record_create"),
    path("api/record/<int:pk>/", views.record_detail, name="record_detail"),
    path("api/record/<int:pk>/document/", views.record_document, name="record_document"),
    path("api/record/<int:pk>/save/", views.record_save, name="record_save"),
    path("api/record/<int:pk>/delete/", views.record_delete, name="record_delete"),
    path("api/record/<int:pk>/restore/", views.record_restore, name="record_restore"),
    path("api/records/remove-filtered/", views.remove_filtered, name="remove_filtered"),

    # ── upload and import ────────────────────────────────────────────────────
    path("api/upload/", views.upload, name="upload"),
    path("api/job/<int:pk>/", views.job_status, name="job_status"),
    path("api/job/<int:pk>/preview/", views.preview, name="preview"),
    path("api/job/<int:pk>/preview/full/", views.preview_full, name="preview_full"),
    path("api/job/<int:pk>/confirm/", views.confirm_import, name="confirm_import"),
    path("api/job/<int:pk>/cancel/", views.cancel_upload, name="cancel_upload"),
    path("api/rerun-rules/", views.rerun_rules, name="rerun_rules"),

    # ── exports, each with a look-before-you-download preview ────────────────
    path("export/records/preview/", views.export_records_preview,
         name="export_records_preview"),
    path("export/records/", views.export_records, name="export_records"),
    path("export/summary/preview/", views.export_summary_preview,
         name="export_summary_preview"),
    path("export/summary/", views.export_summary, name="export_summary"),

    # ── masters ──────────────────────────────────────────────────────────────
    path("api/billing-type/<int:pk>/", views.billing_type_save, name="billing_type_save"),
    path("api/sales-type/new/", views.sales_type_save, {"pk": None},
         name="sales_type_create"),
    path("api/sales-type/<int:pk>/", views.sales_type_save, name="sales_type_save"),
]
