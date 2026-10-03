"""Permanently remove billing records that were previously *soft*-deleted.

Deleting an upload from the UI is now a hard delete, but rows removed before
that change are still sitting in the database with ``is_deleted=True`` — hidden
from every screen yet occupying space. This command clears that backlog.

It reports first and only writes when ``--yes`` is passed:

    python manage.py purge_deleted_billing            # counts only
    python manage.py purge_deleted_billing --yes      # actually delete
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from billing_dashboard.models import BillingUpload, ExportBillingDetails


class Command(BaseCommand):
    help = "Permanently delete soft-deleted billing uploads and billing rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Actually delete. Without it the command only reports what would go.",
        )

    def handle(self, *args, **opts):
        uploads = BillingUpload.all_objects.filter(is_deleted=True)
        # Rows flagged deleted themselves, plus every row still attached to a
        # soft-deleted upload (those are unreachable once the batch is gone).
        rows = ExportBillingDetails.all_objects.filter(is_deleted=True)

        upload_count = uploads.count()
        row_count = rows.count()
        cascade_count = ExportBillingDetails.all_objects.filter(
            upload__in=uploads, is_deleted=False,
        ).count()

        self.stdout.write(f"Soft-deleted uploads      : {upload_count}")
        self.stdout.write(f"Soft-deleted billing rows : {row_count}")
        self.stdout.write(f"Live rows on those uploads: {cascade_count} (removed with their upload)")

        if not (upload_count or row_count or cascade_count):
            self.stdout.write(self.style.SUCCESS("Nothing to purge."))
            return

        if not opts["yes"]:
            self.stdout.write(self.style.WARNING("\nDry run — re-run with --yes to delete permanently."))
            return

        with transaction.atomic():
            deleted_rows, _ = rows.delete()
            # Cascades to any remaining rows via ExportBillingDetails.upload.
            deleted_uploads, _ = uploads.delete()

        self.stdout.write(self.style.SUCCESS(
            f"Purged {deleted_rows} row object(s) and {deleted_uploads} upload object(s)."
        ))
