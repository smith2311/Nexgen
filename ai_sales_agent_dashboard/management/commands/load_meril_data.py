"""
Management command: load_meril_data
Verifies that the live MySQL database (ai_sales_agent_dashboard alias) is
accessible and reports the tables and row counts found there.

Data is read directly from MySQL via the Django DB router — no SQL file
import is required.

Usage:
    python manage.py load_meril_data
"""

from django.core.management.base import BaseCommand
from django.db import OperationalError, connections

DB_ALIAS = "ai_sales_agent_dashboard"


class Command(BaseCommand):
    help = (
        "Verify the live MySQL database connection and report table/row counts. "
        "Data is read directly from MySQL — no SQL file import needed."
    )

    def handle(self, *args, **options):
        self.stdout.write("🔌 Checking MySQL connection…")

        try:
            conn = connections[DB_ALIAS]
            conn.ensure_connection()
        except OperationalError as exc:
            self.stderr.write(self.style.ERROR(f"❌ Cannot connect to MySQL ({DB_ALIAS}): {exc}"))
            raise SystemExit(1)

        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]

        self.stdout.write(self.style.SUCCESS(f"✅ MySQL connected — {len(tables)} tables found in the live database."))

        # Report row counts for the key n8n pipeline tables if present
        key_tables = [
            "Results",
            "AI_Enhanced_Briefs",
            "Business_Leads",
            "ICP_Account_Scores",
            "Scraped_Results",
        ]
        table_set = set(tables)
        with conn.cursor() as cursor:
            for tbl in key_tables:
                if tbl in table_set:
                    # Safe: `tbl` is one of the six literals in key_tables above.
                    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")  # nosec
                    count = cursor.fetchone()[0]
                    self.stdout.write(f"   {tbl}: {count} rows")

        self.stdout.write(self.style.SUCCESS("🎉 AI Sales data is served live from MySQL — no import required."))
