"""
Background tasks for the Billing Dashboard.

``process_upload_task`` runs the (heavy) SAP dump ingestion off the request
thread when ``BILLING_ASYNC_UPLOAD`` is on and a Celery worker is available.
"""
import logging

from celery import shared_task

logger = logging.getLogger("billing_dashboard")


@shared_task(name="billing_dashboard.process_upload")
def process_upload_task(upload_id, username=None):
    # Imported lazily so the worker picks up Django app state correctly.
    from .models import BillingUpload
    from .services.ingest import process_upload

    try:
        upload = BillingUpload.all_objects.get(pk=upload_id)
    except BillingUpload.DoesNotExist:
        logger.warning("process_upload_task: upload %s no longer exists", upload_id)
        return None

    process_upload(upload, username=username)
    return upload.status
