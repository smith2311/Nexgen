"""Export the geographic master — every country, state and city — as a file.

    python manage.py export_places                      # places.xlsx
    python manage.py export_places --format json        # places.json
    python manage.py export_places --country IN --out maharashtra.xlsx

A thin shim over ``vendorlink.UI.geo_places``, which is where the data and all
the thinking about it live. It sits here and not in that folder only because
Django discovers management commands in ``<app>/management/commands`` and
nowhere else.

One row per city, because that is the grain the list is actually read at: a
country and a state repeat down the sheet so any column can be filtered or
pivoted without a join. The city count reflects
``VENDORLINK_GEO_MIN_CITY_POPULATION`` — 32,444 cities at the default floor of
15,000 people, 161,662 at 1,000.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from vendorlink.UI import geo_places as places

COLUMNS = ("Country ISO2", "Country", "State code", "State", "City",
           "Latitude", "Longitude", "Population")


class Command(BaseCommand):
    help = "Export every country, state and city to an Excel or JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=("xlsx", "json"), default="xlsx")
        parser.add_argument("--country", help="Limit to one ISO 3166-1 alpha-2 code.")
        parser.add_argument("--out", help="Output path (default places.xlsx / places.json).")

    def handle(self, *args, **options):
        fmt = options["format"]
        only = (options.get("country") or "").upper()
        out = Path(options.get("out") or f"places.{fmt}")

        countries = places.countries()
        if only:
            countries = [c for c in countries if c["iso2"] == only]
            if not countries:
                raise CommandError(f"No country in the master with ISO2 {only!r}.")

        self.stdout.write(
            f"Cities above {places.min_city_population():,} people; "
            f"{len(countries)} countr{'y' if len(countries) == 1 else 'ies'}."
        )

        rows = []
        for country in countries:
            for city in places.cities(country["iso2"]):
                rows.append([
                    country["iso2"], country["name"],
                    city["state_code"], city["state"] or "",
                    city["name"], city["lat"], city["lon"], city["population"],
                ])

        if fmt == "json":
            # Nested for JSON: a consumer of this shape is almost always
            # building a dependent select, and nesting is the shape that
            # feeds one without regrouping.
            tree = []
            for country in countries:
                states = []
                for state in places.states(country["iso2"]):
                    states.append({
                        "code": state["code"], "name": state["name"],
                        "slug": state["slug"],
                        "cities": places.cities(country["iso2"], state["code"]),
                    })
                tree.append({**country, "state_list": states})
            out.write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            from openpyxl import Workbook
            from openpyxl.styles import Font

            book = Workbook()
            sheet = book.active
            sheet.title = "Places"
            sheet.append(list(COLUMNS))
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            sheet.freeze_panes = "A2"
            for row in rows:
                sheet.append(row)
            for column, width in zip("ABCDEFGH", (12, 26, 11, 28, 28, 11, 11, 12)):
                sheet.column_dimensions[column].width = width
            book.save(out)

        self.stdout.write(self.style.SUCCESS(
            f"{len(rows):,} cities written to {out}"
        ))
