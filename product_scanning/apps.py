from django.apps import AppConfig


class ProductScanningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "product_scanning"
    verbose_name = "SA Product Scanning"

    def ready(self):
        from product_scanning.feedback_registry import register_module

        # ── Stock Audit modules ──────────────────────────────────────────────
        register_module("stock_audit",  "Stock Audit / Scanner",    icon="📦",
                        groups={"PS Salesperson", "PS Warehouse", "Product Scanner", "Sales"}, order=10)
        register_module("transfers",    "H2H Transfers",             icon="🔄",
                        groups={"PS Salesperson", "PS Warehouse", "Product Scanner", "Sales"}, order=20)
        register_module("dashboard",    "Dashboard",                 icon="📊",
                        groups={"PS Warehouse", "Product Scanner", "Sales"}, order=30)
        register_module("master_data",  "Master Data / Imports",     icon="📂",
                        groups={"PS Warehouse", "Product Scanner", "Sales"}, order=40)
        register_module("notifications", "Notifications",             icon="🔔",
                        groups=set(), order=80)  # all users get notifications

        # ── Platform-wide catch-alls (visible to everyone) ───────────────────
        register_module("login", "Login / Access",  icon="🔐", groups=set(), order=90)
        register_module("other", "Other / General", icon="💬", groups=set(), order=99)
