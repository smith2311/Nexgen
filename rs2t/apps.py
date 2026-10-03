from django.apps import AppConfig


class TranslatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rs2t"

    def ready(self):
        """Initialize app when Django starts"""
        import logging

        logger = logging.getLogger(__name__)
        logger.info("🚀 Translator app initialized")

        from product_scanning.feedback_registry import register_module

        register_module("translator", "Translator (RS2T)", icon="🌐", groups={"HR"}, order=70)
