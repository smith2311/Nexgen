"""
shipment_notifications/urls.py
==============================
URL routing for the Shipment Notification System.

All URLs are under the /shipment/ prefix (configured in core/urls.py).

Routes:
  POST /shipment/api/sync/           → ShipmentSyncAPIView
       Manually trigger sync + notifications.
       Requires X-SHIPMENT-POLL-SECRET header.

  GET  /shipment/track/<token>/      → ShipmentDashboardByTokenView
       Salesperson dashboard. Token is a signed string containing the phone number.
       No login required — the token IS the authentication.
"""

from django.urls import path

from .views import ShipmentDashboardByTokenView, ShipmentSyncAPIView

app_name = "shipment_notifications"

urlpatterns = [
    # Internal API — protected by shared secret
    path("api/sync/", ShipmentSyncAPIView.as_view(), name="shipment_sync"),
    # Public dashboard — token via query param (?token=...) or path segment
    path("track/", ShipmentDashboardByTokenView.as_view(), name="shipment_dashboard"),
    path("track/<path:token>/", ShipmentDashboardByTokenView.as_view(), name="shipment_dashboard_legacy"),
]
