"""
shipment_notifications/views.py
===============================
Django views for the Shipment Notification System.

Two views:

1. ShipmentDashboardByTokenView (GET /shipment/track/<token>/)
   - Public-facing dashboard for salespersons.
   - No login required — authentication is via the signed token in the URL.
   - Token → phone number → SalespersonMapping lookup → filtered shipments.
   - Each salesperson only sees shipments for their assigned country/vertical.

2. ShipmentSyncAPIView (POST /shipment/api/sync/)
   - Internal API to manually trigger sync + notifications.
   - Protected by X-SHIPMENT-POLL-SECRET header (shared secret from .env).
   - Runs the same flow as the cron job: sync from merigate, then send WhatsApp.
"""

import logging

from django.conf import settings
from django.shortcuts import render
from django.views.generic import TemplateView

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ExportShipment, ShipmentAccessToken

logger = logging.getLogger(__name__)


class ShipmentDashboardByTokenView(TemplateView):
    """
    Salesperson dashboard — accessed via signed token URL in WhatsApp message.

    Data Isolation Flow:
      1. Extract phone number from signed token.
      2. Query SalespersonMapping WHERE whatsapp_no = phone.
      3. Build a Q filter: (country=X, vertical=Y) OR (country=A, vertical=B)...
      4. Show ONLY ExportShipments matching those country/vertical pairs.
      5. If no mapping found → fallback: show all shipments (initial setup only).

    URL: /shipment/track/<token>/
    Template: shipment_notifications/dashboard.html
    """

    template_name = "shipment_notifications/dashboard.html"

    def get(self, request, token=None, *args, **kwargs):
        # Accept token from query param (?token=) or path segment
        if token is None:
            token = request.GET.get("token") or kwargs.get("token") or self.kwargs.get("token")

        # Step 1: Verify the signed token and extract the phone number.
        # If token is expired (>30 days) or tampered, phone_number will be None.
        phone_number = ShipmentAccessToken.verify_token(token, max_age_days=30)
        if not phone_number:
            return render(
                request, "shipment_notifications/inactive_token.html", {"error": "Token expired or invalid"}, status=403
            )

        # Step 2: Find which country/vertical this salesperson is responsible for.
        # A single phone can be mapped to multiple country/vertical combinations.
        from django.db.models import Q

        from .models import SalespersonMapping

        mappings = SalespersonMapping.objects.filter(whatsapp_no=phone_number)

        if mappings.exists():
            # Step 3: Build OR filter for all the salesperson's country/vertical pairs.
            # This ensures data isolation — each salesperson sees ONLY their shipments.
            q = Q()
            for m in mappings:
                q |= Q(country__iexact=m.country, vertical__iexact=m.vertical)
            shipments = ExportShipment.objects.filter(q).order_by("-updated_at")
        else:
            # Fallback: no mapping exists yet — show all shipments.
            # This only happens during initial setup before SalespersonMapping is populated.
            shipments = ExportShipment.objects.order_by("-updated_at")

        # Collect distinct values for filter dropdowns (before applying filters)
        all_statuses = sorted(shipments.values_list("status", flat=True).distinct())
        all_consignees = sorted(shipments.values_list("consignee_name", flat=True).distinct())

        # Apply query-string filters
        status_filter = request.GET.get("status", "").strip()
        search_query = request.GET.get("q", "").strip()
        date_from = request.GET.get("date_from", "").strip()
        date_to = request.GET.get("date_to", "").strip()
        consignee_filter = request.GET.get("consignee", "").strip()

        if status_filter:
            shipments = shipments.filter(status__iexact=status_filter)
        if consignee_filter:
            shipments = shipments.filter(consignee_name__iexact=consignee_filter)
        if search_query:
            shipments = shipments.filter(
                Q(awb_no__icontains=search_query)
                | Q(invoice_no__icontains=search_query)
                | Q(consignee_name__icontains=search_query)
                | Q(so_no__icontains=search_query)
                | Q(remarks__icontains=search_query)
                | Q(airline__icontains=search_query)
                | Q(forwarder__icontains=search_query)
            )
        if date_from:
            shipments = shipments.filter(invoice_date__gte=date_from)
        if date_to:
            shipments = shipments.filter(invoice_date__lte=date_to)

        context = {
            "shipments": shipments,
            "phone_number": phone_number,
            "base_url": getattr(settings, "SHIPMENT_NOTIFICATION_BASE_URL", ""),
            "all_statuses": all_statuses,
            "all_consignees": all_consignees,
            "current_status": status_filter,
            "current_consignee": consignee_filter,
            "current_q": search_query,
            "current_date_from": date_from,
            "current_date_to": date_to,
        }
        return render(request, self.template_name, context)


class ShipmentSyncAPIView(APIView):
    """
    Manual trigger for sync + notification.

    Usage:
      curl -X POST https://gpt.e-meril.in/shipment/api/sync/ \
           -H 'X-SHIPMENT-POLL-SECRET: <secret>'

    This runs the exact same logic as the daily cron job:
      1. ShipmentSyncService.sync() — fetch data from merigate
      2. WhatsAppNotificationService.send_grouped_notifications() — send WhatsApp

    Protected by SHIPMENT_POLL_SECRET (configured in .env).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        # Verify the shared secret before allowing sync
        poll_secret = request.headers.get("X-SHIPMENT-POLL-SECRET") or request.data.get("poll_secret")
        configured_secret = getattr(settings, "SHIPMENT_POLL_SECRET", "")

        if not configured_secret or poll_secret != configured_secret:
            logger.warning("Sync API request rejected — invalid secret")
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_403_FORBIDDEN)

        # Run the full sync + notify pipeline
        from .services import ShipmentSyncService, WhatsAppNotificationService

        sync_result = ShipmentSyncService.sync()
        notify_result = WhatsAppNotificationService.send_grouped_notifications()
        return Response({"sync": sync_result, "notifications": notify_result}, status=status.HTTP_200_OK)
