"""Purge noise transfer sessions: cancelled ones, and empty (0-scan) ones.

Mirrors what the Transfers dashboard hides. Currently-active sessions are never
touched (someone may be mid-scan). Dry-run by default — pass --apply to delete.

    python manage.py purge_empty_transfers            # preview only
    python manage.py purge_empty_transfers --apply     # actually delete
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from meritrack.models import TransferSession


class Command(BaseCommand):
    help = "Delete cancelled and empty (0-product) transfer sessions (excludes active)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete. Without this flag the command only previews.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]

        qs = (
            TransferSession.objects.annotate(prod_count=Count("transferred_products"))
            .filter(Q(status="cancelled") | Q(prod_count=0))
            .exclude(status="active")
        )

        total = qs.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("Nothing to purge — no cancelled/empty transfers found."))
            return

        # Show what will go.
        self.stdout.write(f"Found {total} transfer session(s) to purge:")
        for t in qs.order_by("-started_at")[:50]:
            self.stdout.write(
                f"  • {t.transfer_id or t.session_key}  "
                f"[{t.status}]  scans={t.prod_count}  started={t.started_at:%d/%m/%Y}"
            )
        if total > 50:
            self.stdout.write(f"  … and {total - 50} more")

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDRY RUN — nothing deleted. Re-run with --apply to delete these {total} session(s)."
                )
            )
            return

        deleted, breakdown = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"\nDeleted {total} transfer session(s)."))
        self.stdout.write(f"Rows removed (incl. cascaded products/attachments): {breakdown}")
