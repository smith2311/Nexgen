"""
shipment_notifications/serializers.py
=====================================
DRF serializers for the Shipment Notification System.

These serializers normalize incoming webhook/API payloads from various sources
that may use different field naming conventions (camelCase, snake_case, sc_* prefixes).

Not currently used in the main sync flow (which goes through tracker_client.py),
but available for webhook-based integrations where external systems push updates.
"""

from rest_framework import serializers


class ShipmentItemSerializer(serializers.Serializer):
    salesperson_phone = serializers.CharField(required=True)
    shipment_number = serializers.CharField(required=True)
    consignee_name = serializers.CharField(required=False, allow_blank=True, default="")
    invoice_number = serializers.CharField(required=False, allow_blank=True, default="")
    invoice_date = serializers.CharField(required=False, allow_blank=True, default="")
    etd = serializers.CharField(required=False, allow_blank=True, default="")
    current_shipment_status = serializers.CharField(required=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    tracking_url = serializers.URLField(required=False, allow_blank=True, default="")

    def to_internal_value(self, data):
        normalized = {
            "salesperson_phone": data.get("salesperson_phone")
            or data.get("sc_rcvr_contact")
            or data.get("salespersonContact")
            or data.get("phone_number"),
            "shipment_number": data.get("shipment_number")
            or data.get("sc_shpmnt_no")
            or data.get("shipmentNumber")
            or data.get("awb"),
            "consignee_name": data.get("consignee_name")
            or data.get("sc_consignee_name")
            or data.get("consigneeName")
            or "",
            "invoice_number": data.get("invoice_number") or data.get("sc_inv_no") or data.get("invoiceNumber") or "",
            "invoice_date": data.get("invoice_date") or data.get("sc_inv_date") or data.get("invoiceDate") or "",
            "etd": data.get("etd") or data.get("sc_etd") or data.get("estimatedDeliveryDate") or "",
            "current_shipment_status": data.get("current_shipment_status")
            or data.get("sc_current_status")
            or data.get("current_status")
            or data.get("status"),
            "remarks": data.get("remarks") or data.get("sc_remarks") or data.get("notes") or "",
            "tracking_url": data.get("tracking_url")
            or data.get("sc_detail_url")
            or data.get("trackingUrl")
            or data.get("tracking_url")
            or "",
        }
        return super().to_internal_value(normalized)


class ShipmentWebhookPayloadSerializer(serializers.Serializer):
    shipments = ShipmentItemSerializer(many=True)

    def to_internal_value(self, data):
        if isinstance(data, list):
            return {"shipments": data}
        if "shipments" in data and isinstance(data["shipments"], list):
            return {"shipments": data["shipments"]}
        return super().to_internal_value(data)
