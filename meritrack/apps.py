from django.apps import AppConfig


class MeriTrackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meritrack"
    verbose_name = "MeriTrack — Consignment Management"

    def ready(self):
        from product_scanning.feedback_registry import register_module

        register_module("meritrack", "MeriTrack", icon="❤️",
                        groups={"MT Rep", "MT Manager", "MT Logistics"}, order=50)
