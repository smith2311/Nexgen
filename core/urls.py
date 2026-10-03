from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView
from django.views.static import serve as _serve_media

from product_scanning.views_frontend import feedback_inbox, feedback_portal

# Admin branding — picked up by templates/admin/base_site.html and the browser tab.
admin.site.site_header = "NexGen Administration"
admin.site.site_title = "NexGen Admin"
admin.site.index_title = "Data administration"

urlpatterns = [
    path("admin/", admin.site.urls),

    # Browsers auto-request /favicon.ico — point it at the Meril logo so it
    # doesn't 404 in the console.
    path(
        "favicon.ico",
        RedirectView.as_view(url=f"{settings.STATIC_URL}img/Meril-logo-bluebg.jpg", permanent=True),
    ),

    # ── Platform-wide Feedback (accessible from all apps) ──────────────────
    path("feedback/", feedback_portal, name="feedback_portal_global"),
    path("feedback/inbox/", feedback_inbox, name="feedback_inbox_global"),

    path("product_scanning/", include("product_scanning.urls")),
    path("meritrack/", include("meritrack.urls")),
    path("shipment/", include("shipment_notifications.urls", namespace="shipment_notifications")),
    path("ai-sales-agent/", include("ai_sales_agent_dashboard.urls", namespace="ai_sales_agent_dashboard")),
    path("logistics/", include("logistics_performance.urls", namespace="logistics")),
    path("vat/", include("vat_reconciliation.urls", namespace="vat")),
    path("warehouse-inspection/", include("warehouse_inspection.urls", namespace="warehouse_inspection")),
    path("material-rework/", include("material_rework.urls", namespace="material_rework")),
    path("vendorlink/", include("vendorlink.urls", namespace="vendorlink")),
    path("meril-v/", include("merilv.urls", namespace="merilv")),
    path("billing/", include("billing_dashboard.urls", namespace="billing")),
    path("visa/", include("visa_management.urls", namespace="visa")),
    path("pr/", include("pr_tracker.urls", namespace="pr_tracker")),
    path("sub/", include("subsidiary_logistic.urls", namespace="sub")),
    path("grocery/", include("grocery_stock.urls", namespace="grocery_stock")),
    path("reconciliation/", include("reconciliation.urls", namespace="reconciliation")),
    path("", include("main.urls")),  # Catch-all - must be last
]

# Static files — WhiteNoise middleware handles this in production (DEBUG=False).
# The static() call below is a no-op in production but keeps dev working too.
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Media files (user uploads) — django.conf.urls.static.static() returns [] when
# DEBUG=False, so we use re_path + serve directly to ensure /media/ is always routed.
# There is no nginx in this deployment; Django serves uploads itself.
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        _serve_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
