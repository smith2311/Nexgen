import pymysql

pymysql.install_as_MySQLdb()

# Load the Celery app so shared_task-decorated tasks register on startup.
from .celery import app as celery_app  # noqa: E402

__all__ = ("celery_app",)
