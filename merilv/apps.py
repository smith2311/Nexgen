from django.apps import AppConfig


class MerilVConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "merilv"
    verbose_name = "Meril-V"

    def ready(self):
        # Self-register with the platform-wide feedback portal.
        from product_scanning.feedback_registry import register_module

        register_module(
            "merilv",
            "Meril-V (Validation & Outbound)",
            icon="📦",
            groups={"Meril-V Admin", "Meril-V Picker", "Meril-V User", "Meril-V Viewer"},
            order=60,
        )
