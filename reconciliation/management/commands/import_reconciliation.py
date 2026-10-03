"""
Import a Reconciliation Export from the command line.

The same two steps the screen uses, and the same code: read and classify into a
staging file, then match and write. ``--dry-run`` stops after the first step and
prints what the file says, which is how to check a new export's shape before
letting it near the table.

    python manage.py import_reconciliation "ReconciliationHubReport.xlsx"
    python manage.py import_reconciliation export.xlsx --dry-run
    python manage.py import_reconciliation export.xlsx --mode append --user smith
"""
import os
from collections import Counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from reconciliation import importer
from reconciliation.models import ReconImportJob


class Command(BaseCommand):
    help = "Read and import a Reconciliation Export workbook (.xlsx or .csv)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The workbook to import.")
        parser.add_argument(
            "--mode", choices=["upsert", "append"], default="upsert",
            help="upsert (default) matches existing documents; append inserts everything.")
        parser.add_argument(
            "--user", default=None,
            help="Username to stamp on the imported records.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Read and classify only. Nothing is written and the stage is discarded.")

    def handle(self, *args, **options):
        path = options["path"]
        if not os.path.exists(path):
            raise CommandError(f"No such file: {path}")

        user = None
        if options["user"]:
            try:
                user = get_user_model().objects.get(username=options["user"])
            except get_user_model().DoesNotExist:
                raise CommandError(f"No user named {options['user']!r}.")

        # Jobs normally run in a worker thread so the browser can poll them. A
        # management command has nobody to poll it and must not return before
        # the work is done, so it runs inline.
        settings.RECON_SYNC_JOBS = True

        read = ReconImportJob.objects.create(
            kind=ReconImportJob.KIND_READ, mode=options["mode"], created_by=user,
            filename=os.path.basename(path), source_path=os.path.abspath(path))

        self.stdout.write("Reading and classifying…")
        importer.run_job(read.pk)
        read.refresh_from_db()

        if read.state == ReconImportJob.STATE_ERROR:
            raise CommandError(read.message)

        self.stdout.write(self.style.SUCCESS(
            f"{read.row_count:,} rows · {read.matched_headers} of "
            f"{len(importer.schema.ALL_COLUMNS)} known columns matched"))
        self._summarise(read.stage_path)

        if options["dry_run"]:
            try:
                os.remove(read.stage_path)
            except OSError:
                pass
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
            return

        confirm = ReconImportJob.objects.create(
            kind=ReconImportJob.KIND_IMPORT, mode=options["mode"], created_by=user,
            filename=read.filename, stage_path=read.stage_path,
            source_path=read.source_path, row_count=read.row_count)
        ReconImportJob.objects.filter(pk=read.pk).update(stage_path="")

        self.stdout.write(f"Importing ({options['mode']})…")
        importer.run_job(confirm.pk)
        confirm.refresh_from_db()

        if confirm.state == ReconImportJob.STATE_ERROR:
            raise CommandError(confirm.message)
        self.stdout.write(self.style.SUCCESS(confirm.message))

    def _summarise(self, stage_path):
        """What the file was classified as, before anything is committed."""
        health, remarks = Counter(), Counter()
        for row in importer.read_stage(stage_path):
            health[row.get("Health")] += 1
            remarks[row.get("ReasonRemark")] += 1

        self.stdout.write(
            f"  clear {health['ok']:,} · watch {health['warn']:,} · "
            f"exception {health['bad']:,}")
        for remark, count in remarks.most_common(12):
            self.stdout.write(f"    {count:>7,}  {remark}")
        if len(remarks) > 12:
            self.stdout.write(f"    … and {len(remarks) - 12} more remark(s)")
