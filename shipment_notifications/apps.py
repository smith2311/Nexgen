from django.apps import AppConfig


class ShipmentNotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shipment_notifications"
    verbose_name = "Shipment Notifications"

    def ready(self):
        from product_scanning.feedback_registry import register_module

        register_module("shipments", "Shipment Tracking", icon="🚚",
                        groups=set(), order=60)  # all users may track shipments
