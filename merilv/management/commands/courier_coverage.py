"""Which dispatching plants can auto-fetch a courier, and which cannot.

Courier auto-fetch on Finish Delivery resolves in three steps — a courier
mapped to the exact customer, the courier on the customer's master record, then
the dispatching plant's default carrier. On live data only the third can fire:
the SAP customer extract has no forwarding-agent column (87 columns, none of
them a carrier), so the first two have nothing to read.

That makes Courier Master's ``plant`` the one thing standing between a delivery
and an auto-filled courier, and a plant with no courier row fails silently —
the modal simply fills in nothing, which reads as a broken feature rather than
as missing data.

This command makes the gap visible, and writes the template to close it:

    python manage.py courier_coverage
    python manage.py courier_coverage --template couriers.xlsx

The template's headers and sheet name match what the Courier Master upload
screen expects (``MASTER_SHEETS["couriers"]``), so a filled-in copy uploads
without further shaping. It is pre-filled with every plant that real deliveries
actually dispatch from, so whoever owns the data fills in carrier names rather
than hunting for plant codes.
"""

from django.core.management.base import BaseCommand

from merilv.models import CourierMaster, DeliveryLine, FinishedDelivery, PlantMaster

#: Must match MASTER_SHEETS["couriers"] in merilv/masters.py, or a filled-in
#: template will not parse on upload.
HEADERS = (
    "Courier Number", "Courier Name", "Plant", "Vehicle Number",
    "Vehicle Type", "Mode of Transit", "GSTIN", "Active",
)
SHEET_NAME = "Courier Master"


def plants_in_use():
    """Every plant real deliveries dispatch from, with how many lines each.

    Read from delivery lines and finished deliveries rather than from Plant
    Master: of 71 plants on file only a handful ever ship, and a template
    listing all 71 buries the two that matter.
    """
    counts = {}
    for plant in DeliveryLine.objects.exclude(plant="").values_list("plant", flat=True):
        plant = (plant or "").strip()
        if plant:
            counts[plant] = counts.get(plant, 0) + 1
    for plant in FinishedDelivery.objects.exclude(plant="").values_list("plant", flat=True):
        plant = (plant or "").strip()
        if plant:
            counts[plant] = counts.get(plant, 0) + 1
    return counts


class Command(BaseCommand):
    help = "Report which dispatching plants have a default courier, and write the template to fill the gaps."

    def add_arguments(self, parser):
        parser.add_argument(
            "--template",
            metavar="PATH",
            help="Write an upload-ready Courier Master workbook, pre-filled with the plants in use.",
        )
        parser.add_argument(
            "--all-plants",
            action="store_true",
            help="Include every plant in Plant Master, not only those that have dispatched.",
        )

    def handle(self, *args, **options):
        covered = {
            (courier.plant or "").strip(): courier
            for courier in CourierMaster.objects.filter(is_active=True).exclude(plant="")
        }
        names = dict(PlantMaster.objects.values_list("plant_code", "plant_name"))
        in_use = plants_in_use()

        wanted = set(names) if options["all_plants"] else set(in_use)
        if not wanted:
            self.stdout.write(self.style.WARNING(
                "No delivery has dispatched from any plant yet, so there is nothing to "
                "measure. Upload a delivery dump first, or re-run with --all-plants."
            ))
            return

        rows = sorted(wanted, key=lambda code: (-in_use.get(code, 0), code))
        gaps = [code for code in rows if code not in covered]

        self.stdout.write("")
        self.stdout.write(f"{'PLANT':<10}{'DELIVERY LINES':<16}{'COURIER':<28}NAME")
        self.stdout.write("-" * 86)
        for code in rows:
            courier = covered.get(code)
            label = courier.courier_name if courier else "— none —"
            style = self.style.SUCCESS if courier else self.style.ERROR
            self.stdout.write(
                f"{code:<10}{in_use.get(code, 0):<16}"
                + style(f"{label:<28}")
                + (names.get(code) or "")
            )

        self.stdout.write("")
        self.stdout.write(
            f"{len(rows) - len(gaps)} of {len(rows)} plant(s) can auto-fetch a courier."
        )
        if gaps:
            self.stdout.write(self.style.ERROR(
                "Deliveries from these plants auto-fill nothing: " + ", ".join(gaps)
            ))
            self.stdout.write(
                "Add a Courier Master row for each (Meril-V → Masters → Courier), "
                "or fill in and upload the template: --template <path>"
            )

        if options["template"]:
            path = self._write_template(options["template"], rows, covered, names, in_use)
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Template written to {path}"))
            self.stdout.write(
                "Fill in Courier Name (and Number / Vehicle / Type / GSTIN / Mode where "
                "known), then upload it on Meril-V → Masters → Courier."
            )

    def _write_template(self, path, rows, covered, names, in_use):
        import openpyxl

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = SHEET_NAME
        sheet.append(list(HEADERS))
        for code in rows:
            courier = covered.get(code)
            # An already-covered plant is written with its current values so the
            # sheet round-trips: uploading it back changes nothing, rather than
            # blanking couriers that were already set.
            sheet.append([
                courier.courier_number if courier else "",
                courier.courier_name if courier else "",
                code,
                courier.vehicle_number if courier else "",
                courier.vehicle_type if courier else "",
                courier.mode_of_transit if courier else "",
                courier.gstin if courier else "",
                "Yes" if (courier is None or courier.is_active) else "No",
            ])
        widths = (18, 28, 10, 20, 18, 18, 22, 8)
        for column, width in zip(sheet.iter_cols(min_row=1, max_row=1), widths):
            sheet.column_dimensions[column[0].column_letter].width = width

        notes = book.create_sheet("How to fill this in")
        for line in (
            ["Courier Master template — Meril-V outbound courier auto-fetch"],
            [],
            ["One row per dispatching plant. Plant is pre-filled from the plants your"],
            ["deliveries actually ship from; you supply the carrier."],
            [],
            ["Column", "Required?", "What it does"],
            ["Courier Number", "YES", "The partner code (24BD, 07BY). Half the key"],
            ["Courier Name", "YES", "The carrier, as the customer links spell it"],
            ["Plant", "YES", "Pre-filled. The other half of the key"],
            ["Vehicle Number", "No", "Auto-fills the Vehicle Number box"],
            ["Vehicle Type", "No", "Auto-fills the Vehicle Type box"],
            ["GSTIN", "No", "Shown read-only on the modal"],
            ["Active", "No", "Yes/No. Blank counts as Yes"],
            [],
            ["A courier is its CODE within a PLANT. The same carrier can appear"],
            ["under several plants, each row with its own code — they stay separate."],
            ["Re-uploading updates rows in place; it never duplicates them."],
            [],
            ["Leave Courier Number blank and the row falls back to matching on the"],
            ["name, which will update whichever plant's row was found first. Fill it in."],
            [],
            ["Plants still needing a carrier:"],
            *[[code, names.get(code) or "", f"{in_use.get(code, 0)} delivery line(s)"]
              for code in rows if code not in covered],
        ):
            notes.append(line)
        notes.column_dimensions["A"].width = 30
        notes.column_dimensions["B"].width = 26
        notes.column_dimensions["C"].width = 50

        book.save(path)
        return path
