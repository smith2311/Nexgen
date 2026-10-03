"""
Management command: dedupe_session_events
=========================================
Cleans up the duplicate AuditLog / Notification rows left behind when a scanning
session was submitted many times in a row.

Background: complete_session used to have no status guard, so every POST re-ran the
full submission side effects. On 19 Aug 2026 session 4d79cfd9-2268-444d-8a62-c6a2929e5e41
(batch 190826-DH-11200154) was submitted 883 times in 81 seconds — a held Enter key on the
focused "Complete & Submit" button — leaving 883 audit rows and 3,532 notification rows.
The views are now idempotent; this command repairs the historical damage.

For each session it:
  • keeps the EARLIEST AuditLog row per (session, action) and deletes the rest
  • keeps the EARLIEST Notification row per (session, recipient) and deletes the rest
  • rewinds completed_at to the earliest session_completed audit timestamp, since the
    storm overwrote it with the timestamp of the LAST duplicate call
  • aligns submission_notified_at with that same first-submission timestamp

Safe to re-run: a clean session reports zero deletions.

Usage:
    python manage.py dedupe_session_events --session-key <KEY> --dry-run
    python manage.py dedupe_session_events --session-key <KEY>
    python manage.py dedupe_session_events --all-duplicates --dry-run
"""

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from product_scanning.models import AuditLog, Notification, ScanningSession


class Command(BaseCommand):
    help = "Remove duplicate audit/notification rows created by repeated session submissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--session-key",
            action="append",
            dest="session_keys",
            default=[],
            help="Session key to clean. Repeatable.",
        )
        parser.add_argument(
            "--all-duplicates",
            action="store_true",
            default=False,
            help="Clean every session that has more than one audit row for the same action.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report what would be deleted without touching anything.",
        )

    def handle(self, *args, **options):
        session_keys = options["session_keys"]
        scan_all = options["all_duplicates"]
        dry_run = options["dry_run"]

        if not session_keys and not scan_all:
            raise CommandError("Pass --session-key <KEY> (repeatable) or --all-duplicates.")

        if scan_all:
            dupe_session_ids = (
                AuditLog.objects.values("session_id", "action")
                .annotate(n=Count("id"))
                .filter(n__gt=1, session_id__isnull=False)
                .values_list("session_id", flat=True)
                .distinct()
            )
            sessions = list(ScanningSession.objects.filter(pk__in=list(dupe_session_ids)))
            if session_keys:
                sessions += [s for s in ScanningSession.objects.filter(session_key__in=session_keys)]
            # De-duplicate the session list itself.
            sessions = list({s.pk: s for s in sessions}.values())
        else:
            sessions = list(ScanningSession.objects.filter(session_key__in=session_keys))
            missing = set(session_keys) - {s.session_key for s in sessions}
            if missing:
                raise CommandError(f"Unknown session key(s): {', '.join(sorted(missing))}")

        if not sessions:
            self.stdout.write(self.style.SUCCESS("No sessions with duplicate events found. Nothing to do."))
            return

        label = "DRY RUN — " if dry_run else ""
        self.stdout.write(self.style.NOTICE(f"{label}{len(sessions)} session(s) to inspect."))

        total_audit = 0
        total_notif = 0

        for session in sessions:
            audit_ids = self._stale_audit_ids(session)
            notif_ids = self._stale_notification_ids(session)
            first_completed = (
                AuditLog.objects.filter(session=session, action="session_completed")
                .order_by("timestamp")
                .values_list("timestamp", flat=True)
                .first()
            )

            batch_id = session.batch.batch_id if session.batch else "N/A"
            if not audit_ids and not notif_ids:
                self.stdout.write(f"  {session.session_key} (batch {batch_id}) — already clean, skipped.")
                continue

            self.stdout.write(
                f"  {session.session_key} (batch {batch_id}) — "
                f"{len(audit_ids)} audit row(s), {len(notif_ids)} notification row(s) to delete"
                + (
                    f"; completed_at {session.completed_at} → {first_completed}"
                    if first_completed and session.completed_at != first_completed
                    else ""
                )
            )

            total_audit += len(audit_ids)
            total_notif += len(notif_ids)

            if dry_run:
                continue

            with transaction.atomic():
                # Delete in chunks — a single IN (...) with 3,500 ids is needlessly large.
                for chunk in self._chunks(audit_ids, 500):
                    AuditLog.objects.filter(pk__in=chunk).delete()
                for chunk in self._chunks(notif_ids, 500):
                    Notification.objects.filter(pk__in=chunk).delete()

                if first_completed:
                    ScanningSession.objects.filter(pk=session.pk).update(
                        completed_at=first_completed,
                        submission_notified_at=first_completed,
                    )

        verb = "would delete" if dry_run else "deleted"
        self.stdout.write(
            self.style.SUCCESS(f"Done — {verb} {total_audit} audit row(s) and {total_notif} notification row(s).")
        )

    def _stale_audit_ids(self, session):
        """Ids of every AuditLog row for this session except the earliest per action."""
        rows = AuditLog.objects.filter(session=session).order_by("timestamp", "id").values_list("id", "action")
        seen_actions = set()
        stale = []
        for row_id, action in rows:
            if action in seen_actions:
                stale.append(row_id)
            else:
                seen_actions.add(action)
        return stale

    def _stale_notification_ids(self, session):
        """Ids of every Notification row for this session except the earliest per recipient."""
        rows = (
            Notification.objects.filter(session=session)
            .order_by("created_at", "id")
            .values_list("id", "recipient_id")
        )
        seen = defaultdict(bool)
        stale = []
        for row_id, recipient_id in rows:
            if seen[recipient_id]:
                stale.append(row_id)
            else:
                seen[recipient_id] = True
        return stale

    @staticmethod
    def _chunks(items, size):
        for i in range(0, len(items), size):
            yield items[i : i + size]
