"""
Re-apply the business enrichment (region, subsidiary, LOB, financial period,
flags) to billing rows already in the database — no re-upload needed.

Run after changing the enrichment rules or updating the Country / LOB masters:

    python manage.py reenrich_billing
"""
from django.core.management.base import BaseCommand

from billing_dashboard.models import ExportBillingDetails
from billing_dashboard.services.enrichment import Enricher

# Fields the enricher reads from each row.
_READ = [
    "payer_country", "sold_to_country", "ship_to_country", "payer_name",
    "sold_to_name", "sold_to_city", "revenue_gl_desc", "speciality",
    "material_group", "material", "ph_level01_desc", "ph_level02_desc",
    "payer_region", "sold_to_region", "plant_region", "export_type",
    "billing_date", "invoice_value_lc", "billing_type", "fi_status",
    "sales_office_description",
]
# Derived fields the enricher writes back.
_WRITE = [
    "country", "country_code", "region", "is_subsidiary", "subsidiary_name",
    "lob", "push_division", "fin_year", "fin_month_seq", "fin_month_label",
    "calendar_month", "value_lakhs", "is_reversal", "is_pending",
    "is_excluded", "is_domestic", "mapping_status", "sales_office_description",
]
_BATCH = 1000


class Command(BaseCommand):
    help = "Recompute region/subsidiary/LOB/period/flags for existing billing rows."

    def handle(self, *args, **options):
        enricher = Enricher()
        qs = ExportBillingDetails.all_objects.all()
        total = qs.count()
        self.stdout.write(f"Re-enriching {total} row(s)…")

        buf, done = [], 0
        for obj in qs.iterator(chunk_size=_BATCH):
            row = {k: getattr(obj, k) for k in _READ}
            # Rows loaded before Sales_Office_Description was promoted keep it in
            # the raw JSON as the 2nd "Description" column. Recover it so Region
            # (= Sales_Office_Description) is correct without a re-upload.
            if not row.get("sales_office_description"):
                raw = obj.raw or {}
                row["sales_office_description"] = (
                    raw.get("description_2") or raw.get("sales_office_description") or None
                )
            enricher.enrich(row)
            for f in _WRITE:
                setattr(obj, f, row[f])
            buf.append(obj)
            if len(buf) >= _BATCH:
                ExportBillingDetails.all_objects.bulk_update(buf, _WRITE)
                done += len(buf)
                buf = []
                self.stdout.write(f"  …{done}/{total}")
        if buf:
            ExportBillingDetails.all_objects.bulk_update(buf, _WRITE)
            done += len(buf)

        self.stdout.write(self.style.SUCCESS(f"Done — re-enriched {done} row(s)."))
