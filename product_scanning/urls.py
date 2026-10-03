from django.urls import include, path

from rest_framework.routers import DefaultRouter

from . import views_frontend
from .feedback_views import FeedbackViewSet
from .views import (
    AuditLogViewSet,
    BatchViewSet,
    ExpiredProductViewSet,
    GTINMaterialMappingViewSet,
    HospitalViewSet,
    NotificationViewSet,
    QRTemplateViewSet,
    SalespersonPerformanceViewSet,
    ScannedProductRemarkViewSet,
    ScannedProductViewSet,
    ScanningSessionViewSet,
    ScanSessionRemarkViewSet,
    TransferSessionViewSet,
)

router = DefaultRouter()
router.register(r"hospitals", HospitalViewSet, basename="hospital")
router.register(r"batches", BatchViewSet, basename="batch")
router.register(r"sessions", ScanningSessionViewSet, basename="session")
router.register(r"products", ScannedProductViewSet, basename="product")
router.register(r"qr-templates", QRTemplateViewSet, basename="qr-template")
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"expired", ExpiredProductViewSet, basename="expired")
router.register(r"transfers", TransferSessionViewSet, basename="transfer")
router.register(r"gtin-mappings", GTINMaterialMappingViewSet, basename="gtin-mapping")
router.register(r"audit-logs", AuditLogViewSet, basename="audit-log")
router.register(r"sp-performance", SalespersonPerformanceViewSet, basename="sp-performance")
router.register(r"product-remarks", ScannedProductRemarkViewSet, basename="product-remark")
router.register(r"session-remarks", ScanSessionRemarkViewSet, basename="session-remark")
router.register(r"feedback", FeedbackViewSet, basename="feedback")

app_name = "product_scanning"

urlpatterns = [
    # API endpoints
    path("api/", include(router.urls)),
    # Frontend pages
    path("", views_frontend.qr_scanning_form, name="qr_scanning_form"),
    path("form/", views_frontend.qr_scanning_form, name="qr_form"),
    path("dashboard/", views_frontend.scanning_dashboard, name="dashboard"),
    path("session/<str:session_key>/", views_frontend.session_detail, name="session_detail"),
    # Salesperson performance dashboard
    path("sp-dashboard/", views_frontend.salesperson_dashboard, name="salesperson_dashboard"),
    # Hospital Master management (warehouse/admin only)
    path("hospital-master/", views_frontend.hospital_master, name="hospital_master"),
    # GTIN Material Mapping management (warehouse/admin only)
    path("gtin-material-master/", views_frontend.gtin_material_master, name="gtin_material_master"),
    # H2H Transfers - separate module
    path("transfers/", views_frontend.transfer_dashboard, name="transfer_dashboard"),
    path("transfers/new/", views_frontend.transfer_scanner_form, name="transfer_scanner"),
    path("transfers/<str:session_key>/", views_frontend.transfer_detail, name="transfer_detail"),
    # Feedback / Bug Report Portal
    path("feedback/", views_frontend.feedback_portal, name="feedback_portal"),
    path("feedback/inbox/", views_frontend.feedback_inbox, name="feedback_inbox"),
    # User Guide (role-specific)
    path("guide/", views_frontend.user_guide, name="user_guide"),
]
