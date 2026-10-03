"""
Celery application for the project. Enables background processing (e.g. the
Billing Dashboard dump upload). The worker is started separately, e.g.:

    celery -A core worker -l info

Broker/backend come from CELERY_BROKER_URL / CELERY_RESULT_BACKEND in settings.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

app = Celery("core")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
