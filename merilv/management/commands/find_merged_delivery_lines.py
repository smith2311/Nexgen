"""Report delivery lines that were merged across DIFFERENT line items.

``ingest._aggregate_delivery_lines`` used to key on delivery + material +
batch, so two distinct line items shipping the same material from the same
batch were folded into one row with their quantities added together. The
scanner then filled that single row with the combined serial count, and SCP
rejected the upload for putting (say) four serials on an item that wants two.

The key now includes the item number, so new uploads are correct. Dumps that
were already loaded still carry the merged row: the damage was done at upload
time, before anything this fix touches.

They cannot be repaired in place. A merged row records the SUM and the list of
item numbers it came from, never the per-item split — 4 could have been 2+2,
3+1 or 1+3, and guessing would put the wrong serial count on a real shipment.
The remedy is to re-upload the YVL06, which is why this command reports rather
than rewrites.

    python manage.py find_merged_delivery_lines
"""

from django.core.management.base import BaseCommand

from merilv.models import DeliveryLine


class Command(BaseCommand):
    help = "List delivery lines merged from more than one source line item."

    def handle(self, *args, **options):
        # source_items holds every item number folded into the row. More than
        # one means distinct line items were combined — the bug's fingerprint.
        suspect = [
            line for line in DeliveryLine.objects.select_related("dump", "entity")
            if isinstance(line.source_items, list) and len(set(line.source_items)) > 1
        ]

        if not suspect:
            self.stdout.write(self.style.SUCCESS(
                "No merged delivery lines found — every line came from a single line item."
            ))
            return

        by_dump = {}
        for line in suspect:
            by_dump.setdefault(line.dump, []).append(line)

        self.stdout.write(self.style.WARNING(
            f"{len(suspect)} merged line(s) across {len(by_dump)} dump(s). "
            "Each needs the YVL06 re-uploading."
        ))
        for dump, lines in by_dump.items():
            entity = dump.entity.code if dump.entity_id else "?"
            self.stdout.write(f"\n  dump #{dump.pk} [{entity}] {dump.original_filename}")
            for line in lines:
                scanned = float(line.scanned_qty or 0)
                self.stdout.write(
                    f"    delivery {line.delivery_no} item {line.item_no}: "
                    f"required {float(line.required_qty):g}, scanned {scanned:g}, "
                    f"merged from items {', '.join(line.source_items)}"
                )
                if scanned:
                    self.stdout.write(self.style.ERROR(
                        "      ^ already scanned — clear these scans before re-uploading, "
                        "or the serials stay attached to the merged row."
                    ))
