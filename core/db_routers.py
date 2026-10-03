from django.conf import settings as django_settings


class AppDatabaseRouter:
    """Route ai_sales_agent_dashboard app models to its dedicated MySQL DB.
    Falls back to 'default' when the MySQL DB alias is not configured.
    """

    app_label = "ai_sales_agent_dashboard"
    db_alias = "ai_sales_agent_dashboard"

    def _target_db(self):
        """Return the db alias to use — dedicated MySQL if available, else default."""
        if self.db_alias in django_settings.DATABASES:
            return self.db_alias
        return "default"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self._target_db()
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self._target_db()
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == self.app_label or obj2._meta.app_label == self.app_label:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        target = self._target_db()
        if app_label == self.app_label:
            return db == target
        if db == self.db_alias and db != "default":
            return False
        return None
