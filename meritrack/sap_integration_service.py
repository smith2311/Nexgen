"""
MeriTrack SAP Integration Service
──────────────────────────────────────────────────────────────────
Provides SAP integration readiness layer:
 • Data extraction from media/Meritrack Excel files
 • Reconciliation between app data and SAP postings
 • Export-ready data formatting for SAP RFC/OData (future)
 • Movement type mapping and validation
 • Integration logging and audit trail
"""

import logging
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


class SAPIntegrationService:
    """Service layer for SAP data integration and reconciliation."""

    # SAP movement type mapping
    MOVEMENT_TYPE_MAP = {
        "631": {"description": "Consignment Fill Up", "direction": "in", "sap_posting": "GR"},
        "632": {"description": "Consignment Pickup/Return", "direction": "out", "sap_posting": "GI"},
        "633": {"description": "Consignment Issue (Used)", "direction": "out", "sap_posting": "GI"},
        "634": {"description": "Customer Return Receipt", "direction": "in", "sap_posting": "GR"},
        "561": {"description": "Initial Stock Balance", "direction": "in", "sap_posting": "GR"},
        "551": {"description": "Scrap", "direction": "out", "sap_posting": "GI"},
        "309": {"description": "Transfer Posting", "direction": "transfer", "sap_posting": "TR"},
        "310": {"description": "Transfer Posting (Rev)", "direction": "transfer", "sap_posting": "TR"},
        "301": {"description": "Plant-to-Plant Transfer", "direction": "transfer", "sap_posting": "TR"},
    }

    @staticmethod
    def reconcile_sap_vs_app(hospital_id=None):
        """
        Compare SAP posting data with app inventory to find discrepancies.
        Designed for future real-time SAP sync.
        """
        from .models import ConsignedProduct, ConsignmentPosting, Hospital

        results = []
        hospitals = Hospital.objects.filter(is_active=True)
        if hospital_id:
            hospitals = hospitals.filter(pk=hospital_id)

        for hospital in hospitals:
            # App data: products on shelf
            app_on_shelf = ConsignedProduct.objects.filter(department__hospital=hospital, status="on_shelf").count()

            # SAP data: net stock from postings
            sap_fillups = ConsignmentPosting.objects.filter(hospital=hospital, movement_type="631").aggregate(
                total=Sum("quantity")
            )["total"] or Decimal("0")

            sap_issues = ConsignmentPosting.objects.filter(hospital=hospital, movement_type="633").aggregate(
                total=Sum("quantity")
            )["total"] or Decimal("0")

            sap_returns = ConsignmentPosting.objects.filter(hospital=hospital, movement_type="632").aggregate(
                total=Sum("quantity")
            )["total"] or Decimal("0")

            sap_scrapped = ConsignmentPosting.objects.filter(hospital=hospital, movement_type="551").aggregate(
                total=Sum("quantity")
            )["total"] or Decimal("0")

            sap_net = float(sap_fillups - abs(sap_issues) - abs(sap_returns) - abs(sap_scrapped))

            discrepancy = app_on_shelf - sap_net

            results.append(
                {
                    "hospital_id": hospital.id,
                    "hospital_name": hospital.name,
                    "sap_code": hospital.sap_code,
                    "app_on_shelf": app_on_shelf,
                    "sap_fillups": float(sap_fillups),
                    "sap_issues": float(abs(sap_issues)),
                    "sap_returns": float(abs(sap_returns)),
                    "sap_scrapped": float(abs(sap_scrapped)),
                    "sap_net_stock": sap_net,
                    "discrepancy": discrepancy,
                    "status": (
                        "match"
                        if abs(discrepancy) < 2
                        else ("minor_discrepancy" if abs(discrepancy) < 10 else "major_discrepancy")
                    ),
                }
            )

        return {
            "reconciliation": results,
            "summary": {
                "total_hospitals": len(results),
                "matching": sum(1 for r in results if r["status"] == "match"),
                "minor_discrepancy": sum(1 for r in results if r["status"] == "minor_discrepancy"),
                "major_discrepancy": sum(1 for r in results if r["status"] == "major_discrepancy"),
            },
        }

    @staticmethod
    def get_usage_export_data(hospital_id=None, start_date=None, end_date=None):
        """
        Generate SAP-ready usage data export.
        Format suitable for SAP material document creation (movement type 633).
        """
        from .models import UsageRecord

        qs = UsageRecord.objects.select_related("product", "hospital", "department", "product__catalog_product")
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)
        if start_date:
            qs = qs.filter(used_date__gte=start_date)
        if end_date:
            qs = qs.filter(used_date__lte=end_date)

        export_rows = []
        for record in qs.order_by("used_date"):
            product = record.product
            catalog = product.catalog_product if product else None

            export_rows.append(
                {
                    "posting_date": str(record.used_date),
                    "movement_type": "633",
                    "material": product.material_code or "",
                    "plant": record.hospital.plant or "",
                    "storage_location": "",
                    "customer": record.hospital.sap_code or "",
                    "customer_name": record.hospital.name,
                    "batch": product.batch_number or "",
                    "serial_number": product.serial_number,
                    "quantity": 1,
                    "base_uom": catalog.base_uom if catalog else "EA",
                    "unit_price": float(catalog.unit_price) if catalog and catalog.unit_price else 0,
                    "surgeon": record.surgeon_name or "",
                    "procedure": record.procedure_type or "",
                    "department": record.department.name if record.department else "",
                }
            )

        return {
            "rows": export_rows,
            "total_records": len(export_rows),
            "movement_type": "633",
            "description": "Consignment Issue (Used on Patient)",
        }

    @staticmethod
    def get_sap_sync_status():
        """Get current SAP synchronization status summary."""
        from .models import ConsignmentPosting, SAPIntegrationLog

        # Latest sync for each operation type
        operations = ["import_stock", "import_postings", "import_serial", "export_usage", "sync_master"]
        sync_status = {}
        for op in operations:
            latest = SAPIntegrationLog.objects.filter(operation=op).order_by("-started_at").first()

            sync_status[op] = {
                "last_sync": latest.started_at.isoformat() if latest else None,
                "last_status": latest.get_status_display() if latest else "Never synced",
                "records": latest.records_processed if latest else 0,
            }

        # Data freshness
        latest_posting = ConsignmentPosting.objects.order_by("-posting_date").first()

        return {
            "operations": sync_status,
            "data_freshness": {
                "latest_posting_date": str(latest_posting.posting_date) if latest_posting else None,
                "total_postings": ConsignmentPosting.objects.count(),
            },
        }

    @staticmethod
    def log_integration(operation, user=None, **kwargs):
        """Log an SAP integration operation."""
        from .models import SAPIntegrationLog

        log = SAPIntegrationLog.objects.create(
            operation=operation,
            status="started",
            plant=kwargs.get("plant", ""),
            source_file=kwargs.get("source_file", ""),
            sap_system=kwargs.get("sap_system", ""),
            initiated_by=user,
            notes=kwargs.get("notes", ""),
        )
        return log

    @staticmethod
    def complete_integration_log(
        log,
        records_processed=0,
        records_created=0,
        records_updated=0,
        records_failed=0,
        error_details=None,
        success=True,
    ):
        """Mark an integration log as complete."""
        log.status = "completed" if success else "failed"
        if records_failed > 0 and records_processed > records_failed:
            log.status = "partial"
        log.records_processed = records_processed
        log.records_created = records_created
        log.records_updated = records_updated
        log.records_failed = records_failed
        if error_details:
            log.error_details = error_details
        log.completed_at = timezone.now()
        log.save()
        return log
