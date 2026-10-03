"""Import public Meril-V rows from a legacy Supabase/Postgres SQL dump."""

import json

from django.core.management.base import BaseCommand, CommandError

from merilv.services.legacy_import import LegacyImportError, import_legacy_dump


class Command(BaseCommand):
    help = "Safely import public Meril-V legacy dump rows without executing Supabase SQL."

    def add_arguments(self, parser):
        parser.add_argument("--schema", help="Path to schema_backup.sql.")
        parser.add_argument("--data", required=True, help="Path to data_backup.sql.")
        parser.add_argument("--report", help="Path to write the JSON import report.")
        parser.add_argument("--dry-run", action="store_true", help="Parse and validate without committing writes.")
        parser.add_argument(
            "--truncate-merilv",
            action="store_true",
            help="Clear import-owned Meril-V legacy compatibility tables before importing.",
        )
        parser.add_argument("--strict", action="store_true", help="Fail if supported rows are skipped or unknown public tables appear.")

    def handle(self, *args, **options):
        try:
            report = import_legacy_dump(
                schema_path=options.get("schema"),
                data_path=options["data"],
                report_path=options.get("report"),
                dry_run=options["dry_run"],
                truncate_merilv=options["truncate_merilv"],
                strict=options["strict"],
            )
        except LegacyImportError as exc:
            raise CommandError(str(exc))

        totals = {
            "seen": sum(table["seen"] for table in report["tables"].values()),
            "imported": sum(table["imported"] for table in report["tables"].values()),
            "updated": sum(table["updated"] for table in report["tables"].values()),
            "skipped": sum(table["skipped"] for table in report["tables"].values()),
            "missing": sum(1 for table in report["tables"].values() if table["missing"]),
        }
        self.stdout.write(json.dumps(totals, indent=2))

        if options.get("report"):
            self.stdout.write(self.style.SUCCESS(f"Import report written to {options['report']}"))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run complete; no database changes were committed."))
        else:
            self.stdout.write(self.style.SUCCESS("Legacy Meril-V import complete."))
