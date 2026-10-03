"""One command to bring a fresh Meril-V environment's data up to date.

Applying migrations only guarantees the *tables* exist — it says nothing about
whether they hold anything. A fresh database migrated clean still has zero
entities and zero master rows, which is exactly what was reported as
"the merilv migrations aren't present": the schema was fine, nothing was
populated, and an empty Master Data screen read as a missing migration.

This command is the other half of that step. It wraps the module's existing
data-loading commands into one call.

Only the seeding half belongs in an unattended startup chain, and that is how
the compose files call it::

    python manage.py migrate && python manage.py transfer_merilv_data --skip-master-data && ...

The workbook half is run deliberately, by hand, when someone has decided which
extract to load — see "What it does only when you ask for it" below.

What it always does
--------------------
Seeds the Meril-V business units (MHPL, MLSIPL, CARDIO, MICRO, MDPL) and carton
profiles via ``seed_merilv`` — no external file needed, safe to run on every
deploy, and required before anything else in the module resolves.

What it does only when you ask for it
--------------------------------------
"Master Data.xlsx" is real SAP-derived customer/material data and is
git-ignored on purpose (see .gitignore) — it will not exist on a fresh clone,
in CI, or on most teammates' machines. Its absence is deliberately NOT an
error: ``load_master_data`` (which this delegates to) already seeds entities
itself, so failing over a spreadsheet nobody expects to have would be worse
than skipping that part and saying so.

It is also no longer loaded on every container start. That file is not a fixed
artefact — it is whatever spreadsheet is sitting in the working directory,
replaced by hand whenever someone has a newer extract — so an unattended load
re-applied a file nobody had chosen at that moment, and reverted master rows
edited since. Loading it is now an explicit act::

    python manage.py transfer_merilv_data
    python manage.py transfer_merilv_data --file "/app/Master Data 2026-09.xlsx"

Usage (from inside the running container):
    python manage.py transfer_merilv_data
    python manage.py transfer_merilv_data --file "/app/Master Data.xlsx"
    python manage.py transfer_merilv_data --skip-master-data
"""

from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

DEFAULT_MASTER_DATA_FILE = "Master Data.xlsx"


class Command(BaseCommand):
    help = "Seed Meril-V entities/cartons, then load Master Data.xlsx if it is present."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_MASTER_DATA_FILE,
            help=f'Path to the master-data workbook (default: "{DEFAULT_MASTER_DATA_FILE}" in the current directory).',
        )
        parser.add_argument(
            "--skip-master-data",
            action="store_true",
            help="Seed entities/cartons only — never attempt the workbook load even if the file is present.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])

        # Seeded directly and unconditionally, rather than relying on
        # load_master_data's own internal seed_merilv call: that call happens
        # AFTER load_master_data has already opened the workbook, so a missing
        # or corrupted file returns before it is ever reached — leaving even
        # this most basic step undone. Calling it here first guarantees entities
        # and cartons exist no matter what happens to the workbook below.
        self.stdout.write("Seeding Meril-V entities and carton profiles…")
        call_command("seed_merilv")

        if options["skip_master_data"]:
            self.stdout.write(self.style.SUCCESS("Done (--skip-master-data: workbook load not attempted)."))
            return

        if not path.exists():
            self.stdout.write(self.style.WARNING(
                f'"{path}" not found — master data load skipped. This is expected on a fresh '
                "checkout, in CI, or on a machine that never received the file (it is git-ignored "
                "real customer data, never committed). Materials, customers, plants, storage and "
                "the billing/sales types will stay empty until it's placed here and this command "
                "(or `load_master_data --file <path>`) is re-run."
            ))
            return

        self.stdout.write(f'Loading Meril-V master data from "{path}"…')
        try:
            call_command("load_master_data", file=str(path))
        except CommandError as exc:
            # Kept non-fatal even though this half no longer runs unattended:
            # it is still one link in an "&&" chain when someone runs the bare
            # command, and a corrupted or half-uploaded workbook should report
            # itself rather than abort whatever follows. load_master_data is
            # idempotent, so re-running it once the file is fixed is safe.
            self.stdout.write(self.style.ERROR(
                f'Master data load failed and was skipped: {exc}\n'
                "Entities and cartons are seeded; materials/customers/plants/storage/types "
                "were not loaded. Fix the workbook and re-run this command."
            ))
            return
        self.stdout.write(self.style.SUCCESS("Meril-V data transfer complete."))
