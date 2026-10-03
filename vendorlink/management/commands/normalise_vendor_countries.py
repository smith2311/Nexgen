"""Fold Vendor Master's duplicate country spellings down to one each.

``Vendor.country`` is free text. The add/edit form has long been a strict select
off the Country master, but rows also arrive by spreadsheet import and by shell,
and plenty predate that select — so the column holds "India", "india", "INDIA"
and " India " at once. The screens now canonicalise on read and ``Vendor.save``
canonicalises on write, so nothing new duplicates and nothing displays twice;
this command is what cleans the rows that are already stored.

It is a command rather than a data migration on purpose. Rewriting a column of
live production data is a decision someone should make deliberately, after
seeing exactly what would change — so it reports first and only writes when
told to.

    python manage.py normalise_vendor_countries              # report only
    python manage.py normalise_vendor_countries --apply      # write the changes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from vendorlink.models import Vendor
from vendorlink.utils import canonical_country


class Command(BaseCommand):
    help = "Report (or fix) Vendor Master rows whose country is a duplicate spelling."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without this the command only reports them.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        # canonical name -> {raw spelling: how many vendors hold it}
        groups = {}
        for raw in Vendor.objects.values_list("country", flat=True):
            name = canonical_country(raw)
            groups.setdefault(name, {})
            groups[name][raw] = groups[name].get(raw, 0) + 1

        pending = [
            (name, spellings) for name, spellings in sorted(groups.items())
            if any(raw != name for raw in spellings)
        ]

        if not pending:
            self.stdout.write(self.style.SUCCESS(
                f"Nothing to do — {len(groups)} country name"
                f"{'' if len(groups) == 1 else 's'}, each already stored one way."
            ))
            return

        affected = 0
        self.stdout.write("")
        for name, spellings in pending:
            variants = sorted(spellings)
            shown = ", ".join(f'"{raw}" ×{spellings[raw]}' for raw in variants)
            rows = sum(count for raw, count in spellings.items() if raw != name)
            affected += rows
            label = name or "(blank)"
            self.stdout.write(f"  {label}")
            self.stdout.write(f"      stored as: {shown}")
            self.stdout.write(f"      {rows} row{'' if rows == 1 else 's'} would change")

        self.stdout.write("")
        summary = (
            f"{len(pending)} country name{'' if len(pending) == 1 else 's'} "
            f"split across spellings, {affected} vendor row"
            f"{'' if affected == 1 else 's'} affected."
        )

        if not apply_changes:
            self.stdout.write(self.style.WARNING(summary))
            self.stdout.write("Re-run with --apply to write these changes.")
            return

        # update() rather than save() per row: the point here is a bulk column
        # fix, and going through save() would also bump updated_at on every
        # vendor, making an audit trail full of edits nobody made.
        with transaction.atomic():
            written = 0
            for name, spellings in pending:
                stale = [raw for raw in spellings if raw != name]
                written += Vendor.objects.filter(country__in=stale).update(country=name)

        self.stdout.write(self.style.SUCCESS(f"{summary} {written} row(s) updated."))
