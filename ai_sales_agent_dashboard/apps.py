from django.apps import AppConfig


class AiSalesAgentDashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_sales_agent_dashboard"
    verbose_name = "AI Sales Agent Dashboard"

    def ready(self):
        from product_scanning.feedback_registry import register_module

        register_module("ai_sales", "AI Sales Hub", icon="🤖",
                        groups={"AI Sales Dashboard"}, order=65)
