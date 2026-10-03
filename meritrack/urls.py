from django.urls import include, path
from django.views.generic import RedirectView

from rest_framework.routers import DefaultRouter

from . import views_frontend
from .views import (
    AuditLogViewSet,
    ConsignedProductViewSet,
    ConsignmentPostingViewSet,
    DashboardViewSet,
    DepartmentViewSet,
    ExpiredProductReportViewSet,
    ExpiredProductViewSet,
    ExpiryAlertViewSet,
    HospitalViewSet,
    InventoryReconciliationViewSet,
    NotificationViewSet,
    ProductCatalogViewSet,
    SalespersonPerformanceViewSet,
    ScannedProductRemarkViewSet,
    ScanningBatchViewSet,
    ScanningSessionViewSet,
    ScanSessionRemarkViewSet,
    StockOrderViewSet,
    TransferSessionViewSet,
    UsageRecordViewSet,
)

router = DefaultRouter()
router.register(r"hospitals", HospitalViewSet, basename="mt-hospital")
router.register(r"departments", DepartmentViewSet, basename="mt-department")
router.register(r"catalog", ProductCatalogViewSet, basename="mt-catalog")
router.register(r"inventory", ConsignedProductViewSet, basename="mt-inventory")
router.register(r"expiry-alerts", ExpiryAlertViewSet, basename="mt-expiry-alert")
router.register(r"sessions", ScanningSessionViewSet, basename="mt-session")
router.register(r"orders", StockOrderViewSet, basename="mt-order")
router.register(r"usage", UsageRecordViewSet, basename="mt-usage")
router.register(r"notifications", NotificationViewSet, basename="mt-notification")
router.register(r"dashboard", DashboardViewSet, basename="mt-dashboard")
router.register(r"postings", ConsignmentPostingViewSet, basename="mt-posting")
router.register(r"expired", ExpiredProductViewSet, basename="mt-expired")
router.register(r"transfers", TransferSessionViewSet, basename="mt-transfer")
router.register(r"expired-reports", ExpiredProductReportViewSet, basename="mt-expired-report")
router.register(r"batches", ScanningBatchViewSet, basename="mt-batch")
router.register(r"sp-performance", SalespersonPerformanceViewSet, basename="mt-sp-performance")
router.register(r"product-remarks", ScannedProductRemarkViewSet, basename="mt-product-remark")
router.register(r"session-remarks", ScanSessionRemarkViewSet, basename="mt-session-remark")
router.register(r"audit-logs", AuditLogViewSet, basename="mt-audit-log")
router.register(r"reconciliations", InventoryReconciliationViewSet, basename="mt-reconciliation")


app_name = "meritrack"

urlpatterns = [
    # API endpoints
    path("api/", include(router.urls)),
    # Frontend pages
    path("", views_frontend.scanner, name="scanner"),
    path("scanner/", views_frontend.scanner, name="scanner_form"),
    path("dashboard/", views_frontend.dashboard, name="dashboard"),
    path("session/<str:session_key>/", views_frontend.session_detail, name="session_detail"),
    path("inventory/", views_frontend.inventory, name="inventory"),
    path("expiry-alerts/", views_frontend.expiry_alerts, name="expiry_alerts"),
    path("orders/", views_frontend.orders, name="orders"),
    path("analytics/", views_frontend.analytics, name="analytics"),
    path("hospital-master/", views_frontend.hospital_master, name="hospital_master"),
    path("product-catalog/", views_frontend.product_catalog, name="product_catalog"),
    path("sp-dashboard/", views_frontend.salesperson_dashboard, name="salesperson_dashboard"),
    path("guide/", views_frontend.user_guide, name="user_guide"),
    path("ui-kit/", views_frontend.ui_kit, name="ui_kit"),

    path("feedback/", views_frontend.feedback_portal, name="feedback_portal"),
    path("feedback/inbox/", views_frontend.feedback_inbox, name="feedback_inbox"),
    path("reconciliation-export/", views_frontend.reconciliation_export, name="reconciliation_export"),
    path("reconciliations/", views_frontend.reconciliation_dashboard, name="reconciliation_dashboard"),
    path("reconciliation/<int:recon_id>/", views_frontend.reconciliation_detail, name="reconciliation_detail"),
    # Transfer (H2H) pages
    path("transfers/", views_frontend.transfer_dashboard, name="transfer_dashboard"),
    # H2H has no page of its own any more — it is a session type on the scanner.
    # Kept only so old links/bookmarks land somewhere useful instead of being
    # swallowed by the session_key pattern below as a session called "new".
    path(
        "transfers/new/",
        RedirectView.as_view(url="/meritrack/scanner/?type=transfer", permanent=False),
    ),
    path("transfers/<str:session_key>/", views_frontend.transfer_detail, name="transfer_detail"),
]
