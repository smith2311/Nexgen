"""
MeriTrack API Views
─────────────────────────────────────────────────────────────────
Complete REST API for the MeriTrack consignment management system.
"""

import csv
import io
import logging
import uuid
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _parse_upload_file(file_obj):
    """Parse an uploaded CSV or Excel file and return a list of dicts.
    Supports .csv, .xlsx, .xls.  Raises ValueError on unsupported format."""
    name = (file_obj.name or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        import openpyxl

        data = file_obj.read()
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return []
        headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
        result = []
        for row in rows[1:]:
            if all(v is None for v in row):
                continue  # skip blank rows
            result.append({h: (str(v).strip() if v is not None else "") for h, v in zip(headers, row)})
        return result
    else:
        # Treat as CSV (handles .csv and any other extension)
        try:
            raw = file_obj.read()
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(decoded))
        return [dict(row) for row in reader]


from .models import (
    AuditLog,
    ConsignedProduct,
    ConsignmentPosting,
    Department,
    ExpiredProductReport,
    ExpiryAlert,
    Hospital,
    InventoryReconciliation,
    Notification,
    ProductCatalog,
    ScanLog,
    ScannedProductRemark,
    ScanningBatch,
    ScanningSession,
    ScanSessionRemark,
    SessionAttachment,
    StockOrder,
    StockOrderItem,
    TransferAttachment,
    TransferredProduct,
    TransferSession,
    UsageRecord,
)
from .notification_service import (
    notify_batch_submitted,
    notify_expired_acknowledged,
    notify_session_closed,
    notify_session_completed,
    notify_session_terminated,
    notify_transfer_closed,
    notify_warehouse_remark,
)
from .rbac import is_mt_manager, is_mt_rep
from .serializers import (
    AuditLogSerializer,
    ConsignedProductSerializer,
    ConsignmentPostingSerializer,
    DepartmentSerializer,
    ExpiredProductReportSerializer,
    ExpiryAlertSerializer,
    HospitalListSerializer,
    HospitalSerializer,
    InventoryReconciliationDetailSerializer,
    InventoryReconciliationSerializer,
    NotificationSerializer,
    ProductCatalogSerializer,
    ScanLogSerializer,
    ScannedProductRemarkSerializer,
    ScanningBatchSerializer,
    ScanningSessionSerializer,
    ScanSessionRemarkSerializer,
    SessionAttachmentSerializer,
    StockOrderSerializer,
    TransferAttachmentSerializer,
    TransferredProductSerializer,
    TransferSessionSerializer,
    UsageRecordSerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def active_scan_logs():
    """ScanLog rows that still count as real activity.

    Terminating a session (`terminate_session`) sets its status to "cancelled",
    which means the visit is being written off. Its scans must therefore drop
    out of expiry alerts, scan lists, exports and rep performance counts —
    reporting that reads ScanLog directly would otherwise keep showing them.

    Only "cancelled" is excluded: completed / closed / on_hold sessions are all
    legitimate work. Per-record lookups by pk deliberately do not use this, so
    an individual terminated scan can still be opened and audited.
    """
    return ScanLog.objects.exclude(session__status="cancelled")


# A session scan action -> the ConsignedProduct status it leaves behind.
# These are the shelf-affecting actions; "verify" only reads, and the
# transfer_* pair belongs to TransferSession / terminate_transfer.
_SESSION_REMOVAL_STATUS = {
    "remove_expired": "expired_removed",
    "remove_recalled": "recalled",
    "remove_return": "returned",
    "use": "used",
}


@transaction.atomic
def _rollback_session_stock(session):
    """Undo the shelf changes a session made. Called when it is terminated.

    Deliberately conservative: a row is only touched while it is still in the
    exact state this session left it in. If anything happened to it since — a
    later visit used it, removed it, transferred it — the row is left alone and
    counted as `skipped`, because blindly reverting would resurrect stock that
    has genuinely left the shelf, or delete a unit someone else is relying on.

    Order matters: units are restored before created units are deleted, so a
    unit both added and consumed inside the same session ends up deleted rather
    than left on the shelf.
    """
    restored = deleted = skipped = usage_cleared = 0

    # 1. Units this session took off the shelf -> put them back.
    logs = list(
        session.scan_logs.filter(
            action__in=_SESSION_REMOVAL_STATUS, product__isnull=False
        ).select_related("product")
    )
    for log in logs:
        product = log.product
        if product is None:
            continue
        if product.status != _SESSION_REMOVAL_STATUS[log.action]:
            skipped += 1
            continue
        usage = UsageRecord.objects.filter(product=product).first()
        if usage is not None:
            usage.delete()
            usage_cleared += 1
        product.status = "on_shelf"
        product.removed_at = None
        product.removed_by = None
        product.removal_reason = None
        product.save(update_fields=["status", "removed_at", "removed_by", "removal_reason"])
        restored += 1

    # 2. Units this session put on the shelf -> remove them. get_or_create only
    #    writes scan_session on insert, so this is exactly the set it created
    #    (never a pre-existing unit it merely re-scanned). ScanLog.product is
    #    SET_NULL, so the scan history survives the delete.
    for product in ConsignedProduct.objects.filter(scan_session=session):
        if product.status == "on_shelf":
            product.delete()
            deleted += 1
        else:
            skipped += 1

    return {
        "restored_to_shelf": restored,
        "placed_units_deleted": deleted,
        "usage_records_cleared": usage_cleared,
        "skipped_changed_since": skipped,
    }


def _create_replenishment_order(session, user):
    """Auto-create a StockOrder from a completed replenishment scanning session.

    Tries to resolve each scan log to a ProductCatalog entry via (in order):
      1. material_code direct match
      2. gtin_code match on ProductCatalog
      3. serial_number → ConsignedProduct → material_code lookup
    Groups resolved items by catalog product, sums quantities.
    Creates a StockOrder in 'submitted' status even if some items are unmatched.
    Returns the order_id string, or None if there are no scan logs at all.
    """
    from collections import defaultdict

    logs = list(session.scan_logs.all())
    if not logs:
        return None

    # Build lookup dicts for bulk resolution
    all_gtins = [log.gtin_code for log in logs if (log.gtin_code or "").strip()]
    all_serials = [log.serial_number for log in logs if (log.serial_number or "").strip()]

    gtin_catalog_map = {}
    if all_gtins:
        for p in ProductCatalog.objects.filter(gtin_code__in=all_gtins):
            gtin_catalog_map[p.gtin_code] = p

    serial_material_map = {}
    if all_serials:
        for cp in (
            ConsignedProduct.objects.filter(serial_number__in=all_serials)
            .exclude(material_code="")
            .exclude(material_code__isnull=True)
        ):
            serial_material_map[cp.serial_number] = cp.material_code

    # Aggregate quantities per catalog product
    qty_by_catalog_id = defaultdict(int)
    catalog_by_id = {}
    unmatched_desc = []

    for log in logs:
        qty = max(int(log.quantity or 1), 1)
        mat = (log.material_code or "").strip()
        gtin = (log.gtin_code or "").strip()
        serial = (log.serial_number or "").strip()

        # Attempt resolution chain
        resolved_cat = None

        if mat:
            try:
                resolved_cat = ProductCatalog.objects.get(material_code=mat)
            except ProductCatalog.DoesNotExist:
                pass

        if not resolved_cat and gtin and gtin in gtin_catalog_map:
            resolved_cat = gtin_catalog_map[gtin]

        if not resolved_cat and serial and serial in serial_material_map:
            try:
                resolved_cat = ProductCatalog.objects.get(material_code=serial_material_map[serial])
            except ProductCatalog.DoesNotExist:
                pass

        if resolved_cat:
            qty_by_catalog_id[resolved_cat.pk] += qty
            catalog_by_id[resolved_cat.pk] = resolved_cat
        else:
            label = mat or gtin or log.product_name or serial or "unknown"
            unmatched_desc.append(f"{label} x{qty}")

    order_id = f"REPL-{session.session_key[:8].upper()}-{uuid.uuid4().hex[:6].upper()}"
    notes_parts = [f"Auto-generated from replenishment session {session.session_key}"]
    if unmatched_desc:
        notes_parts.append(f"Items not in catalog (manual review needed): {', '.join(unmatched_desc)}")

    # Department is required; fall back to hospital's first active department
    department = session.department or (
        session.hospital.departments.filter(is_active=True).first() if session.hospital else None
    )
    if not department:
        logger.warning(f"Cannot create replenishment order: no department for session {session.session_key}")
        return None

    with transaction.atomic():
        order = StockOrder.objects.create(
            order_id=order_id,
            hospital=session.hospital,
            department=department,
            ordered_by=user,
            source_session=session,
            urgency="normal",
            status="submitted",
            notes="\n".join(notes_parts),
        )
        if qty_by_catalog_id:
            StockOrderItem.objects.bulk_create(
                [
                    StockOrderItem(order=order, catalog_product=catalog_by_id[cat_id], quantity=qty)
                    for cat_id, qty in qty_by_catalog_id.items()
                ]
            )

    logger.info(
        f"Replenishment order {order_id} created from session {session.session_key} "
        f"with {len(qty_by_catalog_id)} catalog items, {len(unmatched_desc)} unmatched"
    )
    return order_id


def _reconcile_session_safe(session, user):
    """Auto-reconcile an audit session against expected consignment inventory.

    Runs only for 'audit' sessions. Never raises — reconciliation must never
    block session completion. Returns the reconciliation id, or None.
    """
    if session.session_type != "audit":
        return None
    try:
        from .reconciliation_service import run_session_reconciliation

        recon = run_session_reconciliation(session, user)
        return recon.id if recon else None
    except Exception as exc:
        logger.error(f"Auto-reconciliation failed for {session.session_key}: {exc}")
        return None


def log_audit(
    session=None,
    product=None,
    action_name="",
    user=None,
    description="",
    old_status=None,
    new_status=None,
    request=None,
    metadata=None,
):
    AuditLog.objects.create(
        session=session,
        product=product,
        action=action_name,
        performed_by=user,
        description=description,
        old_status=old_status,
        new_status=new_status,
        ip_address=_get_client_ip(request) if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500] if request else "",
        metadata=metadata or {},
    )


def _get_client_ip(request):
    if not request:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0] if xff else request.META.get("REMOTE_ADDR")


def _parse_gs1(raw_data):
    """Lightweight GS1 barcode parser for MeriTrack."""
    from .gs1_parser_openai import GS1Parser

    try:
        return GS1Parser.parse(raw_data)
    except Exception as exc:
        logger.warning(f"GS1 parse failed: {exc}")
        return {"serial_number": raw_data}


def ensure_batch_number(batch_number, serial_number):
    """Derive a batch number from serial when batch is missing."""
    batch = (batch_number or "").strip()
    if batch:
        return batch
    serial = (serial_number or "").strip()
    if len(serial) > 7:
        derived = serial[4:-3].strip()
        if derived:
            return derived
    return serial or "UNKNOWN-BATCH"


def _user_initials(user):
    """Two-letter initials, matching getUserInitials() in meritrack_scanner.js
    so the scanner's Step 1 preview and the saved key agree."""
    name = (user.get_full_name() or user.get_username() or "").strip()
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    letters = "".join(c for c in name if c.isalpha())[:2].upper()
    return letters or "XX"


def build_session_key(user, hospital, when=None):
    """Readable session key: DDMMYY-<initials>-<SAP>-HHMMSS.

    The scanner previews exactly this on its setup step, so it has to be built
    here too — the client never sends a key, and until Aug 2026 the server
    minted an unrelated `MT-<uuid hex>` that made the preview a lie.

    The SAP code is stripped to alphanumerics: session_key is a URL segment
    (/meritrack/sessions/<key>/), so a stray slash or space in the hospital
    master would otherwise produce an unreachable session.
    """
    now = timezone.localtime(when or timezone.now())
    sap = "".join(c for c in (hospital.sap_code or "") if c.isalnum()).upper()

    stem = f"{now:%d%m%y}-{_user_initials(user)}"
    if sap:
        stem = f"{stem}-{sap}"
    base = f"{stem}-{now:%H%M%S}"

    # Same rep, same hospital, same second is effectively impossible, but the
    # column is unique=True and a clash would 500 the scanner mid-visit.
    key, n = base, 2
    while ScanningSession.objects.filter(session_key=key).exists():
        key = f"{base}-{n}"
        n += 1
    return key


def _is_salesperson_only(user):
    """Check if user is salesperson-only (limited hospital visibility)."""
    if user.is_superuser:
        return False
    if user.groups.filter(name__in={"MT Manager", "MT Logistics"}).exists():
        return False
    return user.groups.filter(name="MT Rep").exists()


def _get_user_hospitals(user):
    """Return hospitals visible to user based on role."""
    if _is_salesperson_only(user):
        return Hospital.objects.filter(
            Q(salesperson_email__iexact=user.email) | Q(salespersons=user),
            is_active=True,
        ).distinct().order_by("name")
    return Hospital.objects.filter(is_active=True).order_by("name")


# ─────────────────────────────────────────────────────────────────────────────
# HOSPITAL & DEPARTMENT
# ─────────────────────────────────────────────────────────────────────────────
class HospitalViewSet(viewsets.ModelViewSet):
    queryset = Hospital.objects.all()
    serializer_class = HospitalSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # return all records; client handles display via DataTables

    def get_queryset(self):
        qs = Hospital.objects.all()
        if self.request.query_params.get("include_inactive") != "1":
            qs = qs.filter(is_active=True)
        return qs.order_by("name")

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def _require_manager(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
        return None

    @action(detail=False, methods=["get"], url_path="rep-users")
    def rep_users(self, request):
        """Return all active MT Rep users for the salesperson picker."""
        from django.contrib.auth.models import Group
        try:
            group = Group.objects.get(name="MT Rep")
            users = group.user_set.filter(is_active=True).order_by("first_name", "last_name")
        except Group.DoesNotExist:
            users = []
        data = [
            {"id": u.id, "name": u.get_full_name() or u.username, "email": u.email}
            for u in users
        ]
        return Response(data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        err = self._require_manager(request)
        if err:
            return err
        hospital = self.get_object()
        hospital.is_active = False
        hospital.updated_by = request.user
        hospital.save()
        return Response({"id": hospital.id, "is_active": False})

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        err = self._require_manager(request)
        if err:
            return err
        hospital = Hospital.objects.get(pk=pk)
        hospital.is_active = True
        hospital.updated_by = request.user
        hospital.save()
        return Response({"id": hospital.id, "is_active": True})

    @action(detail=False, methods=["get"])
    def list_hospitals(self, request):
        hospitals = _get_user_hospitals(request.user)
        serializer = HospitalListSerializer(hospitals, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="inventory_tree")
    def inventory_tree(self, request):
        """
        Fast hospital summary list — summary counts only, no product data.
        Products are loaded lazily per-hospital via the hospital_products endpoint.
        Uses a single aggregated query instead of N+1 loops.
        """
        today = timezone.now().date()
        soon_90 = today + timedelta(days=90)
        soon_30 = today + timedelta(days=30)
        base_q = Q(departments__is_active=True)

        def cp(extra_q):
            return Count("departments__consigned_products", filter=base_q & extra_q)

        hospitals = (
            Hospital.objects.filter(is_active=True)
            .annotate(
                total_on_shelf=cp(Q(departments__consigned_products__status="on_shelf")),
                total_expired=cp(
                    Q(departments__consigned_products__status="on_shelf")
                    & Q(departments__consigned_products__expiry_date__lt=today)
                ),
                total_expiring_soon=cp(
                    Q(departments__consigned_products__status="on_shelf")
                    & Q(departments__consigned_products__expiry_date__gte=today)
                    & Q(departments__consigned_products__expiry_date__lte=soon_90)
                ),
                total_expiring_30=cp(
                    Q(departments__consigned_products__status="on_shelf")
                    & Q(departments__consigned_products__expiry_date__gte=today)
                    & Q(departments__consigned_products__expiry_date__lte=soon_30)
                ),
                total_used=cp(Q(departments__consigned_products__status="used")),
                total_returned=cp(Q(departments__consigned_products__status="returned")),
                total_expired_removed=cp(Q(departments__consigned_products__status="expired_removed")),
                total_recalled=cp(Q(departments__consigned_products__status="recalled")),
                total_transferred=cp(Q(departments__consigned_products__status="transferred")),
            )
            .order_by("name")
        )

        result = [
            {
                "id": h.id,
                "name": h.name,
                "city": h.city,
                "hospital": HospitalListSerializer(h).data,
                "total_on_shelf": h.total_on_shelf,
                "total_expired": h.total_expired,
                "total_expiring_soon": h.total_expiring_soon,
                "total_expiring_30": h.total_expiring_30,
                "total_used": h.total_used,
                "total_returned": h.total_returned,
                "total_expired_removed": h.total_expired_removed,
                "total_recalled": h.total_recalled,
                "total_transferred": h.total_transferred,
            }
            for h in hospitals
        ]
        return Response(result)

    @action(detail=True, methods=["get"], url_path="products")
    def hospital_products(self, request, pk=None):
        """
        Lazy-load endpoint: all departments + products for a single hospital.
        Called when the user expands a hospital in the inventory tree.
        Supports ?status= and ?search= query params for server-side filtering.
        """
        hospital = self.get_object()
        today = timezone.now().date()

        status_filter = request.query_params.get("status", "")
        search = request.query_params.get("search", "").strip().lower()

        departments = hospital.departments.filter(is_active=True)
        depts = []

        for dept in departments:
            qs = dept.consigned_products.select_related("catalog_product", "placed_by").all()

            if status_filter:
                qs = qs.filter(status=status_filter)

            if search:
                qs = qs.filter(
                    Q(serial_number__icontains=search)
                    | Q(product_name__icontains=search)
                    | Q(batch_number__icontains=search)
                    | Q(material_code__icontains=search)
                    | Q(shelf_location__icontains=search)
                    | Q(catalog_product__name__icontains=search)
                )

            on_shelf_qs = dept.consigned_products.filter(status="on_shelf")
            depts.append(
                {
                    "department": DepartmentSerializer(dept).data,
                    "products": ConsignedProductSerializer(qs, many=True).data,
                    "total_on_shelf": on_shelf_qs.count(),
                    "expired": on_shelf_qs.filter(expiry_date__lt=today).count(),
                    "expiring_soon": on_shelf_qs.filter(
                        expiry_date__gte=today,
                        expiry_date__lte=today + timedelta(days=90),
                    ).count(),
                }
            )

        return Response({"departments": depts})

    @action(detail=False, methods=["post"], url_path="import-master", parser_classes=[MultiPartParser, FormParser])
    def import_master(self, request):
        err = self._require_manager(request)
        if err:
            return err

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = _parse_upload_file(file_obj)
        except Exception as exc:
            return Response({"error": f"Cannot read file: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = 0, 0, []
        for i, row in enumerate(rows, 1):
            # Accept both 'hospital_name' and 'name' as the name column
            name = (row.get("hospital_name") or row.get("name") or "").strip()
            if not name:
                errors.append(f"Row {i}: Missing hospital_name")
                continue

            # Resolve sap_code — accept 'customer_code' or 'sap_code'
            sap_code = (row.get("customer_code") or row.get("sap_code") or "").strip() or None

            try:
                defaults = {
                    "sap_code": sap_code,
                    "country": (row.get("country") or "Belgium").strip() or "Belgium",
                    "province": (row.get("province") or "").strip() or None,
                    "city": (row.get("city") or row.get("location") or "").strip() or None,
                    "address": (row.get("address") or "").strip() or None,
                    "contact_person": (row.get("contact_person") or "").strip() or None,
                    "phone": (row.get("phone") or "").strip() or None,
                    "email": (row.get("hospital_email") or row.get("email") or "").strip() or None,
                    "salesperson_email": (row.get("salesperson_email") or "").strip() or None,
                    "plant": (row.get("plant") or row.get("sap_plant") or "").strip() or None,
                    "updated_by": request.user,
                }
                # Match by sap_code when present, else by name
                if sap_code:
                    obj, was_created = Hospital.objects.update_or_create(
                        sap_code=sap_code,
                        defaults={**defaults, "name": name},
                    )
                else:
                    obj, was_created = Hospital.objects.update_or_create(
                        name=name,
                        defaults=defaults,
                    )
            except Exception as exc:
                errors.append(f"Row {i} ({name}): {exc}")
                continue

            # Rebuild salespersons M2M from salesperson_email so the UI
            # shows the correct rep without requiring a manual re-assignment.
            sp_email = (row.get("salesperson_email") or "").strip()
            if sp_email:
                from django.contrib.auth.models import User as _User
                try:
                    sp_user = _User.objects.get(email__iexact=sp_email)
                    obj.salespersons.add(sp_user)
                except _User.DoesNotExist:
                    pass  # Rep account doesn't exist on this server yet

            if was_created:
                created += 1
                # Auto-create a default department
                Department.objects.get_or_create(
                    hospital=obj,
                    name="Consignment Stock",
                    defaults={"code": "CS", "is_active": True},
                )
            else:
                updated += 1

        return Response(
            {
                "created": created,
                "updated": updated,
                "errors": errors,
                "total_processed": created + updated,
            }
        )

    @action(detail=False, methods=["get"], url_path="export-template")
    def export_template(self, request):
        err = self._require_manager(request)
        if err:
            return err
        hospitals = Hospital.objects.all().order_by("name")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "hospital_name",
                "customer_code",
                "country",
                "province",
                "city",
                "address",
                "contact_person",
                "phone",
                "hospital_email",
                "salesperson_email",
            ]
        )
        for h in hospitals:
            writer.writerow(
                [
                    h.name,
                    h.sap_code or "",
                    h.country,
                    h.province or "",
                    h.city or "",
                    h.address or "",
                    h.contact_person or "",
                    h.phone or "",
                    h.email or "",
                    h.salesperson_email or "",
                ]
            )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="meritrack_hospital_template.csv"'
        return response


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.filter(is_active=True)
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    @action(detail=False, methods=["get"], url_path="by-hospital/(?P<hospital_id>[^/.]+)")
    def by_hospital(self, request, hospital_id=None):
        departments = Department.objects.filter(hospital_id=hospital_id, is_active=True)
        return Response(DepartmentSerializer(departments, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT CATALOG
# ─────────────────────────────────────────────────────────────────────────────
class ProductCatalogViewSet(viewsets.ModelViewSet):
    serializer_class = ProductCatalogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # return all records; client handles display via DataTables

    def get_queryset(self):
        qs = ProductCatalog.objects.all()
        if not self.request.query_params.get("include_inactive"):
            qs = qs.filter(is_active=True)
        return qs.order_by("material_code")

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    @action(detail=False, methods=["get"])
    def search(self, request):
        q = request.query_params.get("q", "").strip()
        if len(q) < 2:
            return Response([])
        products = ProductCatalog.objects.filter(
            Q(name__icontains=q) | Q(material_code__icontains=q) | Q(gtin_code__icontains=q) | Q(brand__icontains=q),
            is_active=True,
        )[:20]
        return Response(ProductCatalogSerializer(products, many=True).data)

    @action(detail=False, methods=["post"], url_path="import-catalog", parser_classes=[MultiPartParser, FormParser])
    def import_catalog(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = _parse_upload_file(file_obj)
        except Exception as exc:
            return Response({"error": f"Cannot read file: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        VALID_CATEGORIES = {c[0] for c in ProductCatalog.CATEGORY_CHOICES}

        created, updated, errors = 0, 0, []
        for i, row in enumerate(rows, 1):
            # Accept 'material_code' or 'Material' (SAP export header)
            material_code = (row.get("material_code") or row.get("Material") or "").strip()
            if not material_code:
                errors.append(f"Row {i}: Missing material_code")
                continue

            # Category: normalise, fall back to heuristic
            raw_cat = (row.get("category") or "").strip().lower()
            if raw_cat not in VALID_CATEGORIES:
                # Heuristic from product name
                pname = (row.get("name") or row.get("product_name") or row.get("Material Description") or "").lower()
                if any(k in pname for k in ("stent", "biomime", "coronary")):
                    raw_cat = "stent"
                elif any(k in pname for k in ("balloon", "catheter")):
                    raw_cat = "balloon"
                elif any(k in pname for k in ("guidewire", "guide wire")):
                    raw_cat = "guidewire"
                elif any(k in pname for k in ("valve", "myval", "aortic")):
                    raw_cat = "valve"
                elif any(k in pname for k in ("graft", "endograft")):
                    raw_cat = "graft"
                elif any(k in pname for k in ("closure", "occluder")):
                    raw_cat = "closure"
                elif any(k in pname for k in ("accessory", "sheath", "introducer")):
                    raw_cat = "accessory"
                else:
                    raw_cat = "other"

            # Price: coerce to Decimal safely
            unit_price = None
            raw_price = row.get("unit_price") or row.get("Unit Price") or ""
            if raw_price:
                try:
                    from decimal import Decimal, InvalidOperation

                    unit_price = Decimal(str(raw_price).replace(",", "."))
                except InvalidOperation:
                    pass

            # Shelf life
            shelf_life = None
            raw_sl = row.get("shelf_life_months") or row.get("Shelf Life") or ""
            if raw_sl:
                try:
                    shelf_life = int(float(str(raw_sl)))
                except (ValueError, TypeError):
                    pass

            try:
                obj, was_created = ProductCatalog.objects.update_or_create(
                    material_code=material_code,
                    defaults={
                        "gtin_code": (row.get("gtin_code") or row.get("GTIN") or "").strip() or None,
                        "name": (
                            row.get("name")
                            or row.get("product_name")
                            or row.get("Material Description")
                            or material_code
                        ).strip(),
                        "brand": (row.get("brand") or row.get("Brand") or "").strip() or None,
                        "category": raw_cat,
                        "description": (row.get("description") or row.get("Description") or "").strip() or None,
                        "size": (row.get("size") or row.get("Size") or "").strip() or None,
                        "base_uom": (row.get("base_uom") or row.get("Base Unit of Measure") or "EA").strip() or "EA",
                        "material_type": (row.get("material_type") or row.get("Material Type") or "").strip() or None,
                        "material_type_description": (
                            row.get("material_type_description") or row.get("Material Type Description") or ""
                        ).strip()
                        or None,
                        "material_group": (row.get("material_group") or row.get("Material Group") or "").strip()
                        or None,
                        "material_group_description": (
                            row.get("material_group_description") or row.get("Material Group Description") or ""
                        ).strip()
                        or None,
                        **({"unit_price": unit_price} if unit_price is not None else {}),
                        **({"shelf_life_months": shelf_life} if shelf_life is not None else {}),
                    },
                )
            except Exception as exc:
                errors.append(f"Row {i} ({material_code}): {exc}")
                continue

            if was_created:
                created += 1
            else:
                updated += 1

        return Response({"created": created, "updated": updated, "errors": errors})

    @action(detail=False, methods=["get"], url_path="export-catalog-template")
    def export_catalog_template(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "material_code",
                "gtin_code",
                "name",
                "brand",
                "category",
                "size",
                "base_uom",
                "unit_price",
                "shelf_life_months",
                "requires_cold_chain",
                "material_type",
                "material_type_description",
                "material_group",
                "material_group_description",
                "description",
            ]
        )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="meritrack_catalog_template.csv"'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNED PRODUCT (inventory)
# ─────────────────────────────────────────────────────────────────────────────
class ConsignedProductViewSet(viewsets.ModelViewSet):
    queryset = ConsignedProduct.objects.select_related(
        "department", "department__hospital", "catalog_product", "placed_by"
    ).all()
    serializer_class = ConsignedProductSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="by-department/(?P<dept_id>[^/.]+)")
    def by_department(self, request, dept_id=None):
        products = ConsignedProduct.objects.filter(department_id=dept_id, status="on_shelf").select_related(
            "department", "department__hospital", "catalog_product"
        )
        return Response(ConsignedProductSerializer(products, many=True).data)

    @action(detail=False, methods=["get"], url_path="by-hospital/(?P<hospital_id>[^/.]+)")
    def by_hospital(self, request, hospital_id=None):
        products = ConsignedProduct.objects.filter(
            department__hospital_id=hospital_id, status="on_shelf"
        ).select_related("department", "department__hospital", "catalog_product")
        return Response(ConsignedProductSerializer(products, many=True).data)

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Products expiring within a given number of days (default 90)."""
        days = int(request.query_params.get("days", 90))
        cutoff = timezone.now().date() + timedelta(days=days)
        products = (
            ConsignedProduct.objects.filter(status="on_shelf", expiry_date__lte=cutoff)
            .select_related("department", "department__hospital")
            .order_by("expiry_date")
        )
        return Response(ConsignedProductSerializer(products, many=True).data)

    @action(detail=False, methods=["get"])
    def expired(self, request):
        """All expired products still on shelf."""
        products = (
            ConsignedProduct.objects.filter(status="on_shelf", expiry_date__lt=timezone.now().date())
            .select_related("department", "department__hospital")
            .order_by("expiry_date")
        )
        return Response(ConsignedProductSerializer(products, many=True).data)

    @action(detail=True, methods=["post"])
    def mark_used(self, request, pk=None):
        """Mark product as used on a patient."""
        product = self.get_object()
        if product.status != "on_shelf":
            return Response(
                {"error": f"Product is {product.get_status_display()}, cannot mark as used."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product.status = "used"
        product.removed_at = timezone.now()
        product.removed_by = request.user
        product.removal_reason = "Used on patient"
        product.save()

        UsageRecord.objects.create(
            product=product,
            hospital=product.department.hospital,
            department=product.department,
            used_date=timezone.now().date(),
            recorded_by=request.user,
            surgeon_name=request.data.get("surgeon_name", ""),
            procedure_type=request.data.get("procedure_type", ""),
            notes=request.data.get("notes", ""),
        )

        log_audit(
            product=product,
            action_name="product_used",
            user=request.user,
            description=f"Product {product.serial_number} used on patient",
            old_status="on_shelf",
            new_status="used",
            request=request,
        )
        return Response({"status": "ok", "message": "Product marked as used."})

    @action(detail=True, methods=["post"])
    def mark_removed(self, request, pk=None):
        """Mark product as expired/removed."""
        product = self.get_object()
        if product.status != "on_shelf":
            return Response({"error": f"Product is {product.get_status_display()}"}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get("reason", "Expired — removed during visit")
        product.status = "expired_removed"
        product.removed_at = timezone.now()
        product.removed_by = request.user
        product.removal_reason = reason
        product.save()

        log_audit(
            product=product,
            action_name="product_removed",
            user=request.user,
            description=f"Product {product.serial_number} removed: {reason}",
            old_status="on_shelf",
            new_status="expired_removed",
            request=request,
        )
        return Response({"status": "ok", "message": "Product removed."})

    @action(detail=False, methods=["post"], url_path="import-inventory", parser_classes=[MultiPartParser, FormParser])
    def import_inventory(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = _parse_upload_file(file_obj)
        except Exception as exc:
            return Response({"error": f"Cannot read file: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        def _parse_date(val):
            if not val:
                return None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    from datetime import datetime

                    return datetime.strptime(val.strip(), fmt).date()
                except ValueError:
                    pass
            return None

        created, updated, errors = 0, 0, []
        for i, row in enumerate(rows, 1):
            serial = (row.get("serial_number") or "").strip()
            if not serial:
                errors.append(f"Row {i}: Missing serial_number")
                continue

            hospital_name = (row.get("hospital_name") or "").strip()
            sap_code = (row.get("sap_code") or row.get("customer_code") or "").strip()
            if not hospital_name and not sap_code:
                errors.append(f"Row {i} ({serial}): hospital_name or sap_code required")
                continue

            try:
                if sap_code:
                    hospital = Hospital.objects.get(sap_code__iexact=sap_code)
                else:
                    hospital = Hospital.objects.get(name__iexact=hospital_name)
            except Hospital.DoesNotExist:
                label = sap_code or hospital_name
                errors.append(f"Row {i} ({serial}): Hospital '{label}' not found")
                continue
            except Hospital.MultipleObjectsReturned:
                errors.append(f"Row {i} ({serial}): Ambiguous hospital name '{hospital_name}'")
                continue

            dept_name = (row.get("department_name") or "").strip() or "Consignment Stock"
            department, _ = Department.objects.get_or_create(
                hospital=hospital,
                name=dept_name,
                defaults={"code": dept_name[:50], "is_active": True},
            )

            gtin = (row.get("gtin_code") or "").strip() or None
            mat_code = (row.get("material_code") or "").strip() or None
            catalog_product = None
            if gtin:
                catalog_product = ProductCatalog.objects.filter(gtin_code=gtin).first()
            if not catalog_product and mat_code:
                catalog_product = ProductCatalog.objects.filter(material_code=mat_code).first()

            defaults = {
                "gtin_code": gtin,
                "material_code": mat_code or (catalog_product.material_code if catalog_product else None),
                "catalog_product": catalog_product,
                "product_name": (row.get("product_name") or "").strip()
                or (catalog_product.name if catalog_product else None),
                "batch_number": (row.get("batch_number") or "").strip() or None,
                "expiry_date": _parse_date(row.get("expiry_date")),
                "production_date": _parse_date(row.get("production_date")),
                "size": (row.get("size") or "").strip() or (catalog_product.size if catalog_product else None),
                "quantity": (
                    max(1, int(row.get("quantity") or 1)) if str(row.get("quantity", "1")).strip().isdigit() else 1
                ),
                "shelf_location": (row.get("shelf_location") or "").strip() or None,
                "reference_number": (row.get("reference_number") or "").strip() or None,
                "placed_by": request.user,
            }
            try:
                obj, was_created = ConsignedProduct.objects.update_or_create(
                    serial_number=serial,
                    department=department,
                    defaults=defaults,
                )
            except Exception as exc:
                errors.append(f"Row {i} ({serial}): {exc}")
                continue

            if was_created:
                created += 1
            else:
                updated += 1

        return Response(
            {
                "created": created,
                "updated": updated,
                "errors": errors,
                "total_processed": created + updated,
            }
        )

    @action(detail=False, methods=["get"], url_path="export-inventory-template")
    def export_inventory_template(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "serial_number",
                "hospital_name",
                "sap_code",
                "department_name",
                "gtin_code",
                "material_code",
                "product_name",
                "batch_number",
                "expiry_date",
                "production_date",
                "size",
                "quantity",
                "shelf_location",
                "reference_number",
            ]
        )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="meritrack_inventory_template.csv"'
        return response

    @action(detail=False, methods=["get"], url_path="expiry-summary")
    def expiry_summary(self, request):
        """Tier counts sourced from ScanLog — only products actually scanned by reps."""
        from django.db.models import Max

        today = timezone.now().date()
        # Per serial number, take the most recent scanned expiry date
        serial_expiries = (
            active_scan_logs().filter(expiry_date__isnull=False)
            .values("serial_number")
            .annotate(latest_expiry=Max("expiry_date"))
            .values_list("latest_expiry", flat=True)
        )
        expired = w1 = m1 = m3 = m6 = upcoming = 0
        for d in serial_expiries:
            days = (d - today).days
            if days < 0:
                expired += 1
            elif days <= 7:
                w1 += 1
            elif days <= 30:
                m1 += 1
            elif days <= 90:
                m3 += 1
            elif days <= 180:
                m6 += 1
            else:
                upcoming += 1
        return Response(
            {
                "expired": expired,
                "1w": w1,
                "1m": m1,
                "3m": m3,
                "6m": m6,
                "upcoming": upcoming,
                "total": expired + w1 + m1 + m3 + m6 + upcoming,
            }
        )

    @action(detail=False, methods=["get"], url_path="expiry-list")
    def expiry_list(self, request):
        """Expiry list sourced from ScanLog — only products actually scanned by reps.
        Deduplicates by serial number (latest scan wins). Tiers: expired/1w/1m/3m/6m/upcoming.
        """
        today = timezone.now().date()
        tier_filter = request.query_params.get("tier", "").strip()
        hospital_filter = request.query_params.get("hospital", "").strip()

        qs = (
            active_scan_logs().filter(
                expiry_date__isnull=False,
            )
            .select_related("session", "session__hospital", "session__department", "product")
            .order_by("serial_number", "-scanned_at")
        )

        if hospital_filter:
            qs = qs.filter(session__hospital_id=hospital_filter)

        # Deduplicate: per serial_number keep the most recently scanned record
        seen = {}
        for log in qs:
            if log.serial_number not in seen:
                seen[log.serial_number] = log

        # Acknowledged ConsignedProduct IDs
        acked_ids = set(
            ExpiryAlert.objects.filter(status__in=["acknowledged", "actioned"]).values_list("product_id", flat=True)
        )

        results = []
        for log in seen.values():
            days = (log.expiry_date - today).days
            if days < 0:
                tier = "expired"
            elif days <= 7:
                tier = "1w"
            elif days <= 30:
                tier = "1m"
            elif days <= 90:
                tier = "3m"
            elif days <= 180:
                tier = "6m"
            else:
                tier = "upcoming"

            if tier_filter and tier != tier_filter:
                continue

            hospital = log.session.hospital if log.session else None
            dept = log.session.department if log.session else None
            results.append(
                {
                    "id": log.product_id if log.product_id else f"sl-{log.id}",
                    "scan_log_id": log.id,
                    "serial_number": log.serial_number,
                    "product_name": log.product_name or "",
                    "gtin_code": log.gtin_code or "",
                    "batch_number": log.batch_number or "",
                    "material_code": log.material_code or "",
                    "expiry_date": str(log.expiry_date),
                    "days_to_expiry": days,
                    "expiry_level": tier,
                    "hospital_name": hospital.name if hospital else "",
                    "hospital_id": hospital.id if hospital else None,
                    "department_name": dept.name if dept else "",
                    "acknowledged": log.product_id in acked_ids if log.product_id else False,
                    "placed_by": "",
                }
            )

        results.sort(key=lambda x: x["days_to_expiry"])
        return Response({"results": results, "count": len(results)})

    @action(detail=True, methods=["post"], url_path="acknowledge-expiry")
    def acknowledge_expiry(self, request, pk=None):
        """Acknowledge an expiry alert for a product (creates/updates ExpiryAlert record)."""
        product = self.get_object()
        if not product.expiry_date:
            return Response({"error": "No expiry date set"}, status=status.HTTP_400_BAD_REQUEST)
        today = timezone.now().date()
        days = (product.expiry_date - today).days
        if days < 0:
            tier = "expired"
        elif days <= 7:
            tier = "1w"
        elif days <= 30:
            tier = "1m"
        elif days <= 90:
            tier = "3m"
        elif days <= 180:
            tier = "6m"
        else:
            return Response({"error": "Product not in expiry window"}, status=status.HTTP_400_BAD_REQUEST)

        alert, created = ExpiryAlert.objects.get_or_create(
            product=product,
            tier=tier,
            defaults={
                "days_until_expiry": days,
                "status": "acknowledged",
                "acknowledged_by": request.user,
                "acknowledged_at": timezone.now(),
            },
        )
        if not created:
            alert.status = "acknowledged"
            alert.acknowledged_by = request.user
            alert.acknowledged_at = timezone.now()
            alert.action_notes = request.data.get("notes", "")
            alert.save()

        log_audit(
            product=product,
            action_name="expiry_alert_actioned",
            user=request.user,
            description=f"Expiry ({tier}) acknowledged for {product.serial_number}",
            request=request,
        )
        return Response({"status": "ok", "acknowledged": True})


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY ALERTS
# ─────────────────────────────────────────────────────────────────────────────
class ExpiryAlertViewSet(viewsets.ModelViewSet):
    queryset = ExpiryAlert.objects.select_related(
        "product", "product__department", "product__department__hospital"
    ).all()
    serializer_class = ExpiryAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ExpiryAlert.objects.select_related("product", "product__department", "product__department__hospital").all()
        status = self.request.query_params.get("status", "").strip()
        tier = self.request.query_params.get("tier", "").strip()
        if status:
            qs = qs.filter(status=status)
        if tier:
            qs = qs.filter(tier=tier)
        return qs.order_by("days_until_expiry")

    @action(detail=False, methods=["get"])
    def pending(self, request):
        alerts = self.get_queryset().filter(status="pending").order_by("days_until_expiry")
        return Response(ExpiryAlertSerializer(alerts, many=True).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Alert summary by tier."""
        qs = ExpiryAlert.objects.filter(status="pending")
        result = {}
        for tier, label in ExpiryAlert.TIER_CHOICES:
            result[tier] = qs.filter(tier=tier).count()
        return Response(result)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.status = "acknowledged"
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.action_notes = request.data.get("notes", "")
        alert.save()

        log_audit(
            product=alert.product,
            action_name="expiry_alert_actioned",
            user=request.user,
            description=f"Alert {alert.tier} acknowledged for {alert.product.serial_number}",
            request=request,
        )
        return Response({"status": "ok"})


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
class ScanningSessionViewSet(viewsets.ModelViewSet):
    queryset = (
        ScanningSession.objects.select_related("hospital", "department", "rep").prefetch_related("scan_logs").all()
    )
    serializer_class = ScanningSessionSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        """Terminated sessions report no scans on the collection endpoint.

        `active_scan_logs()` already keeps written-off scans out of scan lists,
        exports and rep performance; this closes the same gap here, where the
        list embedded every scan_log of a cancelled session. Detail responses
        keep them so a terminated visit can still be opened and audited.
        """
        ctx = super().get_serializer_context()
        ctx["include_written_off_scans"] = self.action != "list"
        return ctx

    @action(detail=False, methods=["post"], url_path="start_session")
    def start_session(self, request):
        hospital_id = request.data.get("hospital_id")
        department_id = request.data.get("department_id")
        session_type = request.data.get("session_type", "audit")

        if not hospital_id:
            return Response({"error": "hospital_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            hospital = Hospital.objects.get(pk=hospital_id)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        department = None
        if department_id:
            try:
                department = Department.objects.get(pk=department_id, hospital=hospital)
            except Department.DoesNotExist:
                return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)

        # Auto-cancel this user's abandoned zero-scan active sessions
        from django.db.models import Count as _Count

        ScanningSession.objects.filter(
            rep=request.user,
            status="active",
        ).annotate(_sc=_Count("scan_logs")).filter(
            _sc=0
        ).update(status="cancelled")

        # build_session_key's uniqueness check is check-then-create, so a
        # concurrent start could still collide on the unique column. Rebuild and
        # retry rather than 500 the rep mid-visit.
        session = None
        for attempt in range(3):
            try:
                with transaction.atomic():
                    session = ScanningSession.objects.create(
                        session_key=build_session_key(request.user, hospital),
                        session_type=session_type,
                        hospital=hospital,
                        department=department,
                        rep=request.user,
                        status="active",
                    )
                break
            except IntegrityError:
                if attempt == 2:
                    raise

        log_audit(
            session=session,
            action_name="session_started",
            user=request.user,
            description=f"{session.get_session_type_display()} session started at {hospital.name}",
            new_status="active",
            request=request,
        )

        return Response(ScanningSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="by_session_key")
    def by_session_key(self, request, session_key=None):
        session_key = request.query_params.get("session_key", "").strip()
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            session = (
                ScanningSession.objects.select_related("hospital", "department", "rep")
                .prefetch_related("scan_logs")
                .get(session_key=session_key)
            )
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(ScanningSessionSerializer(session).data)

    @action(detail=False, methods=["post"], url_path="scan_product")
    def scan_product(self, request):
        """Scan a product in the current session — add/verify/remove.

        Supports 2-scan workflow:
        - serial_number: override parsed serial (from separate SN barcode scan)
        - no_serial: if True, generate NSN-<uuid> placeholder for products without serial
        """
        session_key = request.data.get("session_key")
        raw_data = request.data.get("qr_data", "").strip()
        scan_action = request.data.get("action", "verify")
        shelf_location = request.data.get("shelf_location", "")
        serial_override = request.data.get("serial_number", "").strip() if request.data.get("serial_number") else ""
        no_serial = request.data.get("no_serial", False)

        if not session_key or not raw_data:
            return Response({"error": "session_key and qr_data required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key, status="active")
        except ScanningSession.DoesNotExist:
            return Response({"error": "Active session not found"}, status=status.HTTP_404_NOT_FOUND)

        # Parse GS1/barcode
        parsed = _parse_gs1(raw_data)
        serial = parsed.get("serial_number", "")
        gtin = parsed.get("gtin_code", "")
        material = parsed.get("material_code", "")
        product_name = parsed.get("product_name", "")
        batch_num = parsed.get("batch_number", "")
        expiry = parsed.get("expiry_date")
        production = parsed.get("production_date")
        reference = parsed.get("additional_id", "") or parsed.get("secondary_data", "") or ""
        qty_raw = parsed.get("quantity")
        quantity = 1
        if qty_raw:
            try:
                quantity = max(1, int(qty_raw))
            except (ValueError, TypeError):
                pass

        # 2-scan workflow: apply serial override from separate barcode scan
        if serial_override:
            serial = serial_override

        # No serial detected and no override — check no_serial flag or ask client
        if not serial:
            if no_serial:
                serial = ""  # Intentionally no serial — store blank
            elif gtin or batch_num or material:
                # Has product details but no serial — ask the client to provide one
                return Response(
                    {
                        "error_code": "SERIAL_REQUIRED",
                        "error": 'No serial number detected in barcode. Scan a separate SN barcode, type it manually, or choose "No Serial Number".',
                        "parsed_fields": {
                            "gtin_code": gtin,
                            "material_code": material,
                            "product_name": product_name,
                            "batch_number": batch_num,
                            "expiry_date": str(expiry) if expiry else "",
                        },
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            else:
                # Fallback: use raw data as serial
                serial = raw_data

        # Auto-map from ProductCatalog
        if gtin and not product_name:
            try:
                cat = ProductCatalog.objects.get(gtin_code=gtin, is_active=True)
                product_name = product_name or cat.name
                material = material or cat.material_code
            except ProductCatalog.DoesNotExist:
                pass

        # Parse dates
        expiry_date = None
        if expiry:
            try:
                expiry_date = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        production_date = None
        if production:
            try:
                production_date = datetime.strptime(str(production)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        # Composite duplicate check within the session using whatever identity is
        # captured among {serial, material, GTIN}. A real serial under a *different*
        # material/GTIN is allowed; no-serial items (blank serial) dedupe on
        # material + GTIN, with batch + expiry keeping different lots distinct.
        # If neither a real serial nor a material code is captured → not enough
        # identity to dedupe, so the scan is allowed.
        real_serial = serial or None  # meritrack stores "" (not NSN-) for no-serial
        mc = (material or "").strip() or None
        gt = (gtin or "").strip() or None
        if real_serial is not None or mc is not None:
            dup_qs = ScanLog.objects.filter(session=session)
            dup_key = []
            if real_serial is not None:
                dup_qs = dup_qs.filter(serial_number=real_serial)
                dup_key.append(f"Serial {real_serial}")
            else:
                dup_qs = dup_qs.filter(serial_number="")
            if mc is not None:
                dup_qs = dup_qs.filter(material_code=mc)
                dup_key.append(f"Material {mc}")
            if gt is not None:
                dup_qs = dup_qs.filter(gtin_code=gt)
                dup_key.append(f"GTIN {gt}")
            if real_serial is None:
                if batch_num:
                    dup_qs = dup_qs.filter(batch_number=batch_num)
                    dup_key.append(f"Batch {batch_num}")
                if expiry_date:
                    dup_qs = dup_qs.filter(expiry_date=expiry_date)
            if dup_qs.exists():
                return Response(
                    {
                        "error_code": "DUPLICATE_SERIAL",
                        "error": "duplicate",
                        "message": f"This product ({', '.join(dup_key)}) was already scanned in this session.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        # Create scan log
        try:
            scan_log = ScanLog.objects.create(
                session=session,
                action=scan_action,
                serial_number=serial,
                gtin_code=gtin,
                material_code=material,
                product_name=product_name,
                batch_number=batch_num,
                expiry_date=expiry_date,
                production_date=production_date,
                quantity=quantity,
                reference_number=reference,
                qr_data=raw_data,
            )
        except IntegrityError:
            # Race: two concurrent scans of the same composite key passed the check
            # above. The DB constraint (uniq_scanlog_session_serial_gtin_material)
            # caught the second one.
            return Response(
                {
                    "error_code": "DUPLICATE_SERIAL",
                    "error": "duplicate",
                    "message": "This product was already scanned in this session.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Handle consigned product based on action
        consigned = None
        if scan_action == "add":
            dept = session.department
            if not dept:
                # Fallback: use first active department
                dept = session.hospital.departments.filter(is_active=True).first()

            if dept:
                product_defaults = {
                    "gtin_code": gtin,
                    "material_code": material,
                    "product_name": product_name,
                    "batch_number": batch_num,
                    "expiry_date": expiry_date,
                    "production_date": production_date,
                    "reference_number": reference,
                    "status": "on_shelf",
                    "placed_by": request.user,
                    "shelf_location": shelf_location,
                    "qr_data": raw_data,
                    "scan_session": session,
                }
                if serial:
                    # Serialised product — dedup by serial within department
                    consigned, created = ConsignedProduct.objects.get_or_create(
                        department=dept,
                        serial_number=serial,
                        defaults=product_defaults,
                    )
                else:
                    # No-serial product — always create a new record (no dedup possible)
                    consigned = ConsignedProduct.objects.create(
                        department=dept,
                        serial_number="",
                        **product_defaults,
                    )
                    created = True
                # If product already existed, update expiry/batch from this scan's GS1 data
                if not created:
                    update_fields = []
                    if expiry_date and consigned.expiry_date != expiry_date:
                        consigned.expiry_date = expiry_date
                        update_fields.append("expiry_date")
                    if batch_num and not consigned.batch_number:
                        consigned.batch_number = batch_num
                        update_fields.append("batch_number")
                    if production_date and not consigned.production_date:
                        consigned.production_date = production_date
                        update_fields.append("production_date")
                    if update_fields:
                        consigned.save(update_fields=update_fields)
                scan_log.product = consigned
                scan_log.save()

        elif scan_action in ("remove_expired", "remove_recalled", "remove_return"):
            try:
                consigned = ConsignedProduct.objects.get(
                    serial_number=serial, department__hospital=session.hospital, status="on_shelf"
                )
                status_map = {
                    "remove_expired": "expired_removed",
                    "remove_recalled": "recalled",
                    "remove_return": "returned",
                }
                consigned.status = status_map.get(scan_action, "expired_removed")
                consigned.removed_at = timezone.now()
                consigned.removed_by = request.user
                consigned.removal_reason = f"Removed via scan session: {scan_action}"
                consigned.save()
                scan_log.product = consigned
                scan_log.save()
            except ConsignedProduct.DoesNotExist:
                pass

        elif scan_action == "verify":
            try:
                consigned = ConsignedProduct.objects.get(
                    serial_number=serial, department__hospital=session.hospital, status="on_shelf"
                )
                # Capture real GS1 expiry/batch from verify scan
                update_fields = []
                if expiry_date and consigned.expiry_date != expiry_date:
                    consigned.expiry_date = expiry_date
                    update_fields.append("expiry_date")
                if batch_num and not consigned.batch_number:
                    consigned.batch_number = batch_num
                    update_fields.append("batch_number")
                if update_fields:
                    consigned.save(update_fields=update_fields)
                scan_log.product = consigned
                scan_log.save()
            except ConsignedProduct.DoesNotExist:
                pass

        elif scan_action == "use":
            try:
                consigned = ConsignedProduct.objects.get(
                    serial_number=serial, department__hospital=session.hospital, status="on_shelf"
                )
                consigned.status = "used"
                consigned.removed_at = timezone.now()
                consigned.removed_by = request.user
                consigned.removal_reason = "Used on patient"
                consigned.save()
                scan_log.product = consigned
                scan_log.save()

                UsageRecord.objects.create(
                    product=consigned,
                    hospital=session.hospital,
                    department=consigned.department,
                    used_date=timezone.now().date(),
                    recorded_by=request.user,
                    surgeon_name=request.data.get("surgeon_name", ""),
                    procedure_type=request.data.get("procedure_type", ""),
                )
            except ConsignedProduct.DoesNotExist:
                pass

        session.last_scan_time = timezone.now()
        session.save(update_fields=["last_scan_time"])

        log_audit(
            session=session,
            product=consigned,
            action_name="product_scanned",
            user=request.user,
            description=f"{scan_action}: {serial}",
            request=request,
        )

        return Response(
            {
                "status": "ok",
                "scan_log": ScanLogSerializer(scan_log).data,
                "total_scans": session.scan_logs.count(),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="complete_session")
    def complete_session(self, request):
        session_key = request.data.get("session_key")
        try:
            session = ScanningSession.objects.get(session_key=session_key, status="active")
        except ScanningSession.DoesNotExist:
            return Response({"error": "Active session not found"}, status=status.HTTP_404_NOT_FOUND)

        old_status = session.status
        session.mark_completed()

        notes = (request.data.get("notes") or "").strip()
        if notes:
            session.notes = notes
            session.save(update_fields=["notes"])
            ScanSessionRemark.objects.create(
                session=session,
                remark=notes,
                role="salesperson",
                created_by=request.user,
            )

        log_audit(
            session=session,
            action_name="session_completed",
            user=request.user,
            description=f"Session completed with {session.scan_logs.count()} scans",
            old_status=old_status,
            new_status="completed",
            request=request,
        )

        try:
            notify_session_completed(session)
        except Exception as exc:
            logger.error(f"Notification failed: {exc}")

        replenishment_order_id = None
        if session.session_type == "replenish":
            replenishment_order_id = _create_replenishment_order(session, request.user)

        reconciliation_id = _reconcile_session_safe(session, request.user)

        response_data = ScanningSessionSerializer(session).data
        if replenishment_order_id:
            response_data["replenishment_order_id"] = replenishment_order_id
        if reconciliation_id:
            response_data["reconciliation_id"] = reconciliation_id
        return Response(response_data)

    @action(detail=False, methods=["post"])
    def force_complete_session(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        # Atomic claim — only the first of N concurrent requests wins active → completed,
        # so a double-click cannot re-fire the notification fan-out.
        old_status = session.status
        claimed = ScanningSession.objects.filter(pk=session.pk, status="active").update(
            status="completed", completed_at=timezone.now()
        )
        session.refresh_from_db()

        if not claimed:
            return Response(
                {"error": f'Session is already "{session.status}" — only active sessions can be force-completed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log_audit(
            session=session,
            action_name="session_force_completed",
            user=request.user,
            old_status=old_status,
            new_status="completed",
            request=request,
        )

        try:
            notify_batch_submitted(session, submitted_by=request.user)
        except Exception as exc:
            logger.error(f"force_complete notification failed: {exc}")

        replenishment_order_id = None
        if session.session_type == "replenish":
            replenishment_order_id = _create_replenishment_order(session, request.user)

        reconciliation_id = _reconcile_session_safe(session, request.user)

        response_data = ScanningSessionSerializer(session).data
        if replenishment_order_id:
            response_data["replenishment_order_id"] = replenishment_order_id
        if reconciliation_id:
            response_data["reconciliation_id"] = reconciliation_id
        return Response(response_data)

    @action(detail=False, methods=["post"])
    def terminate_session(self, request):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status not in ("active", "completed", "on_hold"):
            return Response(
                {"error": f"Can only terminate active, completed, or on-hold sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "")
        old_status = session.status
        session.status = "cancelled"
        if reason:
            terminated_note = f"[Terminated by {request.user.get_full_name() or request.user.username}] Reason: {reason}"
            session.notes = f"{terminated_note}\n{session.notes}" if session.notes else terminated_note
        session.save()

        # Terminating writes the visit off, so the shelf must not keep its
        # effects. Reporting already ignores cancelled sessions; this puts the
        # stock records back too.
        rollback = _rollback_session_stock(session)
        rollback_summary = (
            f"Stock rollback — restored {rollback['restored_to_shelf']}, "
            f"deleted {rollback['placed_units_deleted']}, "
            f"skipped {rollback['skipped_changed_since']} changed since."
        )

        log_audit(
            session=session,
            action_name="session_terminated",
            user=request.user,
            description=f"Session terminated. Reason: {reason or '—'}. {rollback_summary}",
            old_status=old_status,
            new_status="cancelled",
            request=request,
        )

        try:
            notify_session_terminated(session, terminated_by=request.user, reason=reason)
        except Exception as exc:
            logger.error(f"terminate notification failed: {exc}")

        return Response({**ScanningSessionSerializer(session).data, "stock_rollback": rollback})

    @action(detail=False, methods=["post"], url_path="upload_attachment", parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request):
        session_key = request.data.get("session_key")
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status not in ("completed", "on_hold"):
            return Response(
                {"error": f"Attachment can only be uploaded to completed or on-hold sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        MAX_SIZE = 50 * 1024 * 1024
        if file_obj.size > MAX_SIZE:
            return Response({"error": "File too large (max 50 MB)"}, status=status.HTTP_400_BAD_REQUEST)

        ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
        ALLOWED_CONTENT_TYPES = {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/bmp",
            "image/tiff",
        }
        ext = ("." + file_obj.name.rsplit(".", 1)[-1].lower()) if "." in file_obj.name else ""
        content_type = (file_obj.content_type or "").lower()
        # Accept by extension OR by MIME type — pasted/dragged screenshots often
        # arrive with no filename extension but a valid image/* content type.
        if ext not in ALLOWED_EXTENSIONS and content_type not in ALLOWED_CONTENT_TYPES:
            return Response(
                {"error": f"File type not allowed. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        SessionAttachment.objects.filter(session=session).delete()
        attachment = SessionAttachment.objects.create(
            session=session,
            file_data=file_obj.read(),
            content_type=file_obj.content_type or "application/octet-stream",
            file_size=file_obj.size,
            original_filename=file_obj.name,
            uploaded_by=request.user,
        )

        session.status = "closed"
        session.on_hold_at = None
        session.save(update_fields=["status", "on_hold_at"])

        try:
            notify_session_closed(session, closed_by=request.user)
        except Exception as exc:
            logger.error(f"session_closed notification failed: {exc}")

        log_audit(
            session=session,
            action_name="attachment_uploaded",
            user=request.user,
            description=f"Attachment: {file_obj.name}",
            request=request,
        )
        return Response(SessionAttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="download_attachment")
    def download_attachment(self, request):
        session_key = request.query_params.get("session_key")
        try:
            session = ScanningSession.objects.get(session_key=session_key)
            att = session.attachment
        except (ScanningSession.DoesNotExist, SessionAttachment.DoesNotExist):
            return Response({"error": "Attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        response = HttpResponse(att.file_data, content_type=att.content_type)
        disposition = "attachment" if request.query_params.get("download") else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{att.original_filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="session-products/(?P<session_key>[^/.]+)")
    def session_products(self, request, session_key=None):
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        logs = session.scan_logs.all().order_by("-scanned_at")
        return Response(ScanLogSerializer(logs, many=True).data)

    @action(detail=False, methods=["post"])
    def hold_session(self, request):
        """Put a completed session on hold — pauses overdue follow-up (manager only)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status != "completed":
            return Response(
                {"error": f"Can only hold completed sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = session.status
        session.mark_on_hold()

        log_audit(
            session=session,
            action_name="session_held",
            user=request.user,
            description="Session placed on hold",
            old_status=old_status,
            new_status="on_hold",
            request=request,
        )
        return Response(ScanningSessionSerializer(session).data)

    @action(detail=False, methods=["post"])
    def resume_session(self, request):
        """Resume an on-hold session back to completed (manager only)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status != "on_hold":
            return Response(
                {"error": f"Can only resume on-hold sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = session.status
        session.resume_from_hold()

        log_audit(
            session=session,
            action_name="session_resumed",
            user=request.user,
            description="Session resumed from on-hold",
            old_status=old_status,
            new_status="completed",
            request=request,
        )
        return Response(ScanningSessionSerializer(session).data)

    @action(detail=False, methods=["post"])
    def reject_session(self, request):
        """Reject a completed session back to active (manager only)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        reason = request.data.get("reason", "No reason provided")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status != "completed":
            return Response(
                {"error": f"Can only reject completed sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if hasattr(session, "attachment") and session.attachment:
            session.attachment.delete()

        old_status = session.status
        session.status = "active"
        session.completed_at = None
        session.notes = f"[Rejected by {request.user.get_full_name() or request.user.username}] {reason}"
        session.save()

        log_audit(
            session=session,
            action_name="session_rejected",
            user=request.user,
            description=f"Session rejected: {reason}",
            old_status=old_status,
            new_status="active",
            request=request,
        )
        return Response(ScanningSessionSerializer(session).data)

    @action(detail=False, methods=["post"])
    def close_session(self, request):
        """Close a completed session — final reviewed state (manager only)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status != "completed":
            return Response(
                {"error": f"Can only close completed sessions. Current: {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = session.status
        session.mark_closed()

        log_audit(
            session=session,
            action_name="session_closed",
            user=request.user,
            description="Session closed",
            old_status=old_status,
            new_status="closed",
            request=request,
        )
        return Response(ScanningSessionSerializer(session).data)

    @action(detail=False, methods=["post"], url_path="delete_scan_log")
    def delete_scan_log(self, request):
        """Delete a scan log from an active session so the item can be re-scanned."""
        scan_log_id = request.data.get("scan_log_id")
        if not scan_log_id:
            return Response({"error": "scan_log_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            scan_log = ScanLog.objects.select_related("session").get(pk=scan_log_id)
        except ScanLog.DoesNotExist:
            return Response({"error": "Scan log not found"}, status=status.HTTP_404_NOT_FOUND)

        session = scan_log.session
        if session.status in ("completed", "closed"):
            return Response(
                {"error": "Cannot delete from completed/closed session"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not request.user.is_superuser and session.rep != request.user:
            return Response({"error": "Only session owner can delete scans"}, status=status.HTTP_403_FORBIDDEN)

        serial = scan_log.serial_number
        scan_log.delete()

        log_audit(
            session=session,
            action_name="scan_deleted",
            user=request.user,
            description=f"Deleted scan: {serial}",
            request=request,
        )
        return Response({"status": "ok", "total_scans": session.scan_logs.count()})

    @action(detail=False, methods=["get"], url_path="resume-check")
    def resume_check(self, request):
        """Return the most recent active session with scans for this rep+hospital this month, if any."""
        hospital_id = request.query_params.get("hospital_id")
        if not hospital_id:
            return Response({"session": None})
        now = timezone.now()
        session = (
            ScanningSession.objects.filter(
                rep=request.user,
                hospital_id=hospital_id,
                status="active",
                started_at__year=now.year,
                started_at__month=now.month,
            )
            .annotate(_sc=Count("scan_logs"))
            .filter(_sc__gt=0)
            .order_by("-started_at")
            .first()
        )
        if not session:
            return Response({"session": None})
        return Response({
            "session": {
                "session_key": session.session_key,
                "session_type": session.session_type,
                "scans_count": session._sc,
                "started_at": session.started_at.strftime("%d %b %Y, %H:%M"),
            }
        })

    @action(detail=False, methods=["get"], url_path="export-expired-csv")
    def export_expired_csv(self, request):
        """Export scan logs for products expiring within 6 months, filtered by scan date range (max 62 days)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        date_from_str = request.query_params.get("date_from", "").strip()
        date_to_str   = request.query_params.get("date_to",   "").strip()
        if not date_from_str or not date_to_str:
            return Response({"error": "date_from and date_to are required (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            date_to   = datetime.strptime(date_to_str,   "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from."}, status=status.HTTP_400_BAD_REQUEST)
        if (date_to - date_from).days > 62:
            return Response({"error": "Date range must not exceed 62 days."}, status=status.HTTP_400_BAD_REQUEST)

        threshold = timezone.now().date() + timedelta(days=180)

        logs = (
            active_scan_logs().filter(
                scanned_at__date__gte=date_from,
                scanned_at__date__lte=date_to,
                expiry_date__isnull=False,
                expiry_date__lte=threshold,
            )
            .select_related("session__hospital", "session__rep")
            .order_by("expiry_date")
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Serial Number", "Product Name", "Material Code", "GTIN",
            "Lot/Batch", "Hospital", "SAP Code", "Expiry Date",
            "Days to Expiry", "Session Key", "Scanned By", "Scanned At",
        ])
        today = timezone.now().date()
        for log in logs:
            hospital = log.session.hospital if log.session else None
            rep      = log.session.rep      if log.session else None
            sn       = log.serial_number if (log.serial_number and not log.serial_number.startswith("NSN-")) else ""
            days     = (log.expiry_date - today).days if log.expiry_date else ""
            writer.writerow([
                sn,
                log.product_name or "",
                log.material_code or "",
                log.gtin_code or "",
                log.batch_number or "",
                hospital.name     if hospital else "",
                hospital.sap_code if hospital else "",
                log.expiry_date.isoformat() if log.expiry_date else "",
                days,
                log.session.session_key if log.session else "",
                (rep.get_full_name() or rep.username) if rep else "",
                log.scanned_at.strftime("%Y-%m-%d") if log.scanned_at else "",
            ])

        filename = f"MT_ExpiredProducts_{date_from_str}_to_{date_to_str}.csv"
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="my_batches")
    def my_batches(self, request):
        """
        Returns all scanning sessions for the current user (rep) as a batch-status
        tracker list. Managers can pass ?rep_email= to view a specific rep's sessions.

        STATUS_STEPS: active=1, completed=2, closed=3, cancelled=0
        """
        STATUS_STEPS = {"active": 1, "completed": 2, "closed": 3, "cancelled": 0}

        user = request.user
        is_manager = is_mt_manager(user) or user.is_superuser

        rep_email = request.query_params.get("rep_email", "").strip()

        if is_manager and rep_email:
            qs = (
                ScanningSession.objects.filter(rep__email__iexact=rep_email)
                .select_related("hospital", "rep")
                .order_by("-started_at")
            )
        elif is_manager:
            qs = ScanningSession.objects.all().select_related("hospital", "rep").order_by("-started_at")
        else:
            qs = ScanningSession.objects.filter(rep=user).select_related("hospital", "rep").order_by("-started_at")

        session_type_filter = request.query_params.get("session_type", "").strip()
        if session_type_filter:
            qs = qs.filter(session_type=session_type_filter)

        from django.db.models import Count as _Count

        qs = qs.annotate(_sc=_Count("scan_logs")).exclude(_sc=0)

        results = []
        for s in qs[:200]:
            rep = s.rep
            rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""
            scan_count = s.scan_logs.count()
            remark_qs = ScanSessionRemark.objects.filter(session=s)
            total_remarks = remark_qs.count()
            has_warehouse_remark = remark_qs.filter(role="warehouse").exists()
            results.append(
                {
                    "session_key": s.session_key,
                    "batch_id": s.session_key,
                    "hospital_name": s.hospital.name if s.hospital else "",
                    "session_type": s.get_session_type_display(),
                    "status": s.status,
                    "status_step": STATUS_STEPS.get(s.status, 0),
                    "scan_count": scan_count,
                    "scans_count": scan_count,
                    "rep_name": rep_name,
                    "rep_email": rep.email if rep else "",
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                    "detail_url": f"/meritrack/session/{s.session_key}/",
                    "has_attachment": hasattr(s, "attachment") and s.attachment is not None,
                    "total_remarks": total_remarks,
                    "has_warehouse_remark": has_warehouse_remark,
                }
            )

        return Response({"sessions": results, "results": results, "count": len(results)})

    @action(detail=False, methods=["get"], url_path="my_scans")
    def my_scans(self, request):
        """Return all scan logs for the current rep across all sessions."""
        user = request.user
        is_manager = is_mt_manager(user) or user.is_superuser

        rep_email = request.query_params.get("rep_email", "").strip()

        if is_manager and rep_email:
            qs = active_scan_logs().filter(session__rep__email__iexact=rep_email)
        elif is_manager:
            qs = active_scan_logs()
        else:
            qs = active_scan_logs().filter(session__rep=user)

        qs = qs.select_related("session", "session__hospital", "session__rep").order_by("-scanned_at")

        today = timezone.now().date()
        results = []
        for log in qs[:500]:
            s = log.session
            rep = s.rep if s else None
            # Compute expiry urgency tier
            exp_date = log.expiry_date
            if exp_date is None:
                expiry_level = "ok"
            else:
                days_left = (exp_date - today).days
                if days_left < 0:
                    expiry_level = "expired"
                elif days_left <= 7:
                    expiry_level = "1w"
                elif days_left <= 30:
                    expiry_level = "1m"
                elif days_left <= 90:
                    expiry_level = "3m"
                elif days_left <= 180:
                    expiry_level = "6m"
                else:
                    expiry_level = "ok"
            results.append(
                {
                    "id": log.id,
                    "session_key": s.session_key if s else "",
                    "batch_id": s.session_key if s else "",
                    "session_status": s.status if s else "",
                    "hospital_name": s.hospital.name if s and s.hospital else "",
                    "rep_name": ((f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""),
                    "serial_number": log.serial_number,
                    "gtin_code": log.gtin_code or "",
                    "material_code": log.material_code or "",
                    "product_name": log.product_name or "",
                    "batch_number": log.batch_number or "",
                    "expiry_date": str(log.expiry_date) if log.expiry_date else "",
                    "expiry_level": expiry_level,
                    "action": log.action,
                    "scanned_at": log.scanned_at.isoformat() if log.scanned_at else None,
                }
            )

        return Response({"scans": results, "results": results, "count": len(results)})

    @action(detail=False, methods=["get"], url_path="export_csv")
    def export_csv(self, request):
        """Export scan logs as CSV for SAP reconciliation (manager only, max 62 days)."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        date_from_str = request.query_params.get("date_from", "")
        date_to_str = request.query_params.get("date_to", "")

        try:
            date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date() if date_from_str else (timezone.now().date() - timedelta(days=30))
            date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date() if date_to_str else timezone.now().date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        if (date_to - date_from).days > 62:
            return Response({"error": "Date range cannot exceed 62 days."}, status=status.HTTP_400_BAD_REQUEST)

        qs = (
            active_scan_logs()
            .filter(scanned_at__date__gte=date_from, scanned_at__date__lte=date_to)
            .exclude(serial_number__startswith="NSN-")
            .select_related("session", "session__hospital", "session__rep")
            .order_by("scanned_at")
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Serial No", "GTIN", "Lot/Batch", "Product Name", "Material Code",
            "Session ID", "Hospital", "SAP Code", "Session Type", "Action",
            "Expiry Date", "Prod. Date", "Qty", "Scanned By", "Scanned Date",
        ])

        for log in qs:
            s = log.session
            rep = s.rep if s else None
            rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""
            writer.writerow([
                log.serial_number,
                log.gtin_code or "",
                log.batch_number or "",
                log.product_name or "",
                log.material_code or "",
                s.session_key if s else "",
                s.hospital.name if s and s.hospital else "",
                s.hospital.sap_code if s and s.hospital else "",
                s.get_session_type_display() if s else "",
                log.action,
                str(log.expiry_date) if log.expiry_date else "",
                str(log.production_date) if log.production_date else "",
                log.quantity,
                rep_name,
                log.scanned_at.strftime("%Y-%m-%d %H:%M") if log.scanned_at else "",
            ])

        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        filename = f"meritrack_reconciliation_{date_from}_{date_to}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="export_my_scans_excel")
    def export_my_scans_excel(self, request):
        """Export scan logs for the current rep (or all if manager) as Excel."""
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        user = request.user
        is_manager = is_mt_manager(user) or user.is_superuser
        today = timezone.now().date()
        soon_90 = today + timedelta(days=90)

        rep_email = request.query_params.get("rep_email", "").strip()

        if is_manager and rep_email:
            qs = active_scan_logs().filter(session__rep__email__iexact=rep_email)
        elif is_manager:
            qs = active_scan_logs()
        else:
            qs = active_scan_logs().filter(session__rep=user)

        qs = qs.select_related("session", "session__hospital", "session__rep").order_by("-scanned_at")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "MT Scans"

        # Header style
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1D4ED8", end_color="1D4ED8", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin = Side(style="thin", color="000000")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        headers = [
            "Session Key",
            "Hospital",
            "Rep",
            "Serial Number",
            "GTIN",
            "Material Code",
            "Product Name",
            "Batch Number",
            "Expiry Date",
            "Days to Expiry",
            "Action",
            "Scanned At",
        ]
        ws.append(headers)
        for col_num, _ in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = border

        # Expiry colour fills
        fill_expired = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        fill_soon = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        fill_ok = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")

        for log in qs:
            s = log.session
            rep = s.rep if s else None
            rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""

            exp_date = log.expiry_date
            days_left = (exp_date - today).days if exp_date else None

            row = [
                s.session_key if s else "",
                s.hospital.name if s and s.hospital else "",
                rep_name,
                log.serial_number or "",
                log.gtin_code or "",
                log.material_code or "",
                log.product_name or "",
                log.batch_number or "",
                str(exp_date) if exp_date else "",
                days_left if days_left is not None else "",
                log.action or "",
                log.scanned_at.strftime("%Y-%m-%d %H:%M") if log.scanned_at else "",
            ]
            ws.append(row)

            # Colour-code expiry column (col 9 = Expiry Date, col 10 = Days)
            row_num = ws.max_row
            if exp_date:
                if exp_date < today:
                    fill = fill_expired
                elif exp_date <= soon_90:
                    fill = fill_soon
                else:
                    fill = fill_ok
                ws.cell(row=row_num, column=9).fill = fill
                ws.cell(row=row_num, column=10).fill = fill

        # Column widths
        col_widths = [18, 25, 20, 22, 16, 16, 30, 16, 14, 14, 12, 18]
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A2"

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"mt_scans_{today}.xlsx"
        response = HttpResponse(
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="dashboard-list")
    def dashboard_list(self, request):
        """Lightweight session list for dashboard — count only, no embedded scan_logs."""
        qs = ScanningSession.objects.select_related("hospital", "to_hospital", "rep", "department").order_by(
            "-started_at"
        )

        status_filter = request.query_params.get("status", "").strip()
        type_filter = request.query_params.get("type", "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        if type_filter:
            qs = qs.filter(session_type=type_filter)

        # Never show active sessions that have no scans yet
        qs = qs.annotate(_sc=Count("scan_logs")).exclude(status="active", _sc=0)

        results = []
        for s in qs[:300]:
            rep = s.rep
            rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""
            scan_count = s.scan_logs.count()
            remark_qs = ScanSessionRemark.objects.filter(session=s)
            total_remarks = remark_qs.count()
            has_wh_remark = remark_qs.filter(role="warehouse").exists()
            try:
                has_att = s.attachment is not None
                att_name = s.attachment.original_filename if has_att else ""
            except Exception:
                has_att = False
                att_name = ""
            results.append(
                {
                    "id": s.id,
                    "session_key": s.session_key,
                    "session_type": s.session_type,
                    "session_type_display": s.get_session_type_display(),
                    "hospital_id": s.hospital.id if s.hospital else None,
                    "hospital_name": s.hospital.name if s.hospital else "",
                    "to_hospital_name": s.to_hospital.name if s.to_hospital else "",
                    "department_name": s.department.name if s.department else "",
                    "rep_name": rep_name,
                    "rep_email": rep.email if rep else "",
                    "status": s.status,
                    "total_scans": scan_count,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                    "total_remarks": total_remarks,
                    "has_warehouse_remark": has_wh_remark,
                    "has_attachment": has_att,
                    "attachment_name": att_name,
                    "detail_url": f"/meritrack/session/{s.session_key}/",
                }
            )

        return Response({"results": results, "count": len(results)})


# ─────────────────────────────────────────────────────────────────────────────
# STOCK ORDERS
# ─────────────────────────────────────────────────────────────────────────────
class StockOrderViewSet(viewsets.ModelViewSet):
    serializer_class = StockOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            StockOrder.objects.select_related("hospital", "department", "ordered_by")
            .prefetch_related("items", "items__catalog_product")
            .all()
        )
        params = self.request.query_params
        status_val = params.get("status", "").strip()
        urgency = params.get("urgency", "").strip()
        hospital = params.get("hospital", "").strip()
        if status_val:
            qs = qs.filter(status=status_val)
        if urgency:
            qs = qs.filter(urgency=urgency)
        if hospital:
            qs = qs.filter(hospital_id=hospital)
        return qs.order_by("-created_at")

    @action(detail=False, methods=["post"], url_path="place_order")
    def place_order(self, request):
        hospital_id = request.data.get("hospital_id")
        department_id = request.data.get("department_id")
        urgency = request.data.get("urgency", "normal")
        items = request.data.get("items", [])
        notes = request.data.get("notes", "")

        # Validate required fields
        if not hospital_id or not items:
            return Response({"error": "hospital_id and items are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            hospital = Hospital.objects.get(pk=hospital_id, is_active=True)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        # Department is optional - use first active department if not specified
        department = None
        if department_id:
            try:
                department = Department.objects.get(pk=department_id, hospital=hospital, is_active=True)
            except Department.DoesNotExist:
                return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            # Use first active department of hospital
            department = hospital.departments.filter(is_active=True).first()
            if not department:
                return Response({"error": "No active departments in this hospital"}, status=status.HTTP_400_BAD_REQUEST)

        # Aggregate items by catalog_product_id so duplicate rows are merged
        items_map = {}  # {catalog_id: {'qty': int, 'notes': str}}
        for item in items:
            catalog_id = item.get("catalog_product_id")
            if not catalog_id:
                continue
            try:
                catalog_id = int(catalog_id)
                qty = int(item.get("quantity") or 1)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            if catalog_id in items_map:
                items_map[catalog_id]["qty"] += qty
            else:
                items_map[catalog_id] = {"qty": qty, "notes": item.get("notes", "") or ""}

        if not items_map:
            return Response({"error": "No valid items provided"}, status=status.HTTP_400_BAD_REQUEST)

        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        order = StockOrder.objects.create(
            order_id=order_id,
            hospital=hospital,
            department=department,
            ordered_by=request.user,
            urgency=urgency,
            status="submitted",
            notes=notes,
        )

        for catalog_id, meta in items_map.items():
            try:
                catalog = ProductCatalog.objects.get(pk=catalog_id, is_active=True)
                StockOrderItem.objects.create(
                    order=order,
                    catalog_product=catalog,
                    quantity=meta["qty"],
                    notes=meta["notes"],
                )
            except ProductCatalog.DoesNotExist:
                continue
            except IntegrityError:
                continue  # shouldn't happen after aggregation, but guard anyway

        log_audit(
            action_name="order_placed",
            user=request.user,
            description=f"Order {order_id} placed for {hospital.name}",
            request=request,
        )

        return Response(StockOrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="update_status")
    def update_status(self, request, pk=None):
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        order = self.get_object()
        new_status = request.data.get("status")
        valid = dict(StockOrder.STATUS_CHOICES)
        if new_status not in valid:
            return Response(
                {"error": f"Invalid status. Choose from: {list(valid.keys())}"}, status=status.HTTP_400_BAD_REQUEST
            )

        old_status = order.status
        order.status = new_status
        now = timezone.now()
        if new_status == "confirmed":
            order.confirmed_at = now
        elif new_status == "shipped":
            order.shipped_at = now
        elif new_status == "delivered":
            order.delivered_at = now
        order.save()

        log_audit(
            action_name="order_updated",
            user=request.user,
            description=f"Order {order.order_id} status: {old_status} → {new_status}",
            old_status=old_status,
            new_status=new_status,
            request=request,
        )

        from .notification_service import notify_order_update

        try:
            notify_order_update(order, old_status, new_status)
        except Exception as exc:
            logger.error(f"Order notification failed: {exc}")

        return Response(StockOrderSerializer(order).data)

    @action(detail=False, methods=["post"], url_path="create_from_session")
    def create_from_session(self, request):
        """Manager-only: retroactively create a replenishment order from an
        already-completed replenishment session that has no order yet."""
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = (request.data.get("session_key") or "").strip()
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.session_type != "replenish":
            return Response({"error": "Session is not a replenishment session"}, status=status.HTTP_400_BAD_REQUEST)

        if session.replenishment_orders.exists():
            existing = session.replenishment_orders.first()
            return Response({"error": f"Order already exists: {existing.order_id}"}, status=status.HTTP_409_CONFLICT)

        order_id = _create_replenishment_order(session, request.user)
        if not order_id:
            return Response({"error": "Session has no scan logs"}, status=status.HTTP_400_BAD_REQUEST)

        order = StockOrder.objects.get(order_id=order_id)
        return Response(StockOrderSerializer(order).data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# USAGE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────
class UsageRecordViewSet(viewsets.ModelViewSet):
    queryset = UsageRecord.objects.select_related("product", "hospital", "department", "recorded_by").all()
    serializer_class = UsageRecordSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def analytics(self, request):
        """Usage analytics — aggregate data for dashboards."""
        hospital_id = request.query_params.get("hospital_id")
        months = int(request.query_params.get("months", 12))
        start_date = timezone.now().date() - timedelta(days=months * 30)

        qs = UsageRecord.objects.filter(used_date__gte=start_date)
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)

        # Usage by month
        monthly = {}
        for rec in qs:
            key = rec.used_date.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0) + 1

        # Usage by hospital
        by_hospital = qs.values("hospital__name").annotate(count=Count("id")).order_by("-count")[:10]

        # Usage by product/size
        by_product = (
            qs.values("product__product_name", "product__size").annotate(count=Count("id")).order_by("-count")[:10]
        )

        # Usage by surgeon
        by_surgeon = (
            qs.exclude(surgeon_name="")
            .exclude(surgeon_name__isnull=True)
            .values("surgeon_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        return Response(
            {
                "monthly": monthly,
                "by_hospital": list(by_hospital),
                "by_product": list(by_product),
                "by_surgeon": list(by_surgeon),
                "total_used": qs.count(),
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG API
# ─────────────────────────────────────────────────────────────────────────────
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for audit logs — managers and superusers only."""

    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not is_mt_manager(self.request.user) and not self.request.user.is_superuser:
            return AuditLog.objects.none()
        qs = AuditLog.objects.select_related("session", "session__hospital", "product", "performed_by").all()
        action = self.request.query_params.get("action")
        if action:
            qs = qs.filter(action=action)
        session_key = self.request.query_params.get("session_key")
        if session_key:
            qs = qs.filter(session__session_key=session_key)
        user_id = self.request.query_params.get("user_id")
        if user_id:
            qs = qs.filter(performed_by_id=user_id)
        days = self.request.query_params.get("days")
        if days:
            try:
                qs = qs.filter(timestamp__gte=timezone.now() - timedelta(days=int(days)))
            except (ValueError, TypeError):
                pass
        return qs[:500]


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────
class InventoryReconciliationViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for inventory reconciliations.

    Reconciliations are generated automatically when an audit ScanningSession
    completes (see reconciliation_service.run_session_reconciliation).
    """

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InventoryReconciliationDetailSerializer
        return InventoryReconciliationSerializer

    def get_queryset(self):
        user = self.request.user
        qs = InventoryReconciliation.objects.select_related(
            "hospital", "department", "session", "performed_by"
        ).prefetch_related("items")
        # Reps see reconciliations for their own sessions; managers see all.
        if not is_mt_manager(user) and not user.is_superuser:
            qs = qs.filter(Q(performed_by=user) | Q(session__rep=user))
        hospital_id = self.request.query_params.get("hospital_id")
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)
        status_f = self.request.query_params.get("status")
        if status_f:
            qs = qs.filter(status=status_f)
        return qs

    @action(detail=True, methods=["get"], url_path="detail-summary")
    def detail_summary(self, request, pk=None):
        recon = self.get_object()
        return Response({
            "id": recon.id,
            "recon_id": recon.recon_id,
            "hospital_name": recon.hospital_name,
            "recon_type": recon.recon_type,
            "status": recon.status,
            "created_at": recon.created_at,
            "completed_at": recon.completed_at,
            "performed_by_name": recon.performed_by_name,
            "accuracy_rate": recon.accuracy_rate,
            "discrepancy_rate": recon.discrepancy_rate,
            "summary": {
                "total_expected": recon.total_expected,
                "total_scanned": recon.total_scanned,
                "matched": recon.matched,
                "missing": recon.missing,
                "extra": recon.extra,
                "expired": recon.expired_found,
            },
        })


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────
class NotificationViewSet(viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    def list(self, request):
        qs = self.get_queryset().order_by("is_read", "-created_at")[:50]
        return Response(NotificationSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"count": count})

    @action(detail=False, methods=["post"])
    def mark_read(self, request):
        notif_id = request.data.get("id")
        if notif_id:
            self.get_queryset().filter(pk=notif_id).update(is_read=True)
        else:
            self.get_queryset().update(is_read=True)
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"status": "ok", "unread_count": count})


# DASHBOARD STATS
# ─────────────────────────────────────────────────────────────────────────────
class DashboardViewSet(viewsets.ViewSet):
    """Aggregate dashboard statistics and analytics from imported data."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        today = timezone.now().date()

        total_hospitals = Hospital.objects.filter(is_active=True).count()
        total_departments = Department.objects.filter(is_active=True).count()
        total_products_catalog = ProductCatalog.objects.filter(is_active=True).count()
        total_on_shelf = ConsignedProduct.objects.filter(status="on_shelf").count()
        total_expired = ConsignedProduct.objects.filter(status="on_shelf", expiry_date__lt=today).count()
        expiring_30d = ConsignedProduct.objects.filter(
            status="on_shelf", expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30)
        ).count()
        expiring_90d = ConsignedProduct.objects.filter(
            status="on_shelf", expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90)
        ).count()
        total_used = ConsignedProduct.objects.filter(status="used").count()
        total_returned = ConsignedProduct.objects.filter(status="returned").count()
        total_consigned = ConsignedProduct.objects.count()
        active_sessions = ScanningSession.objects.filter(status="active").count()
        completed_sessions = ScanningSession.objects.filter(status="completed").count()
        pending_orders = StockOrder.objects.filter(status__in=["submitted", "confirmed"]).count()
        pending_alerts = ExpiryAlert.objects.filter(status="pending").count()
        total_postings = ConsignmentPosting.objects.count()

        return Response(
            {
                "total_hospitals": total_hospitals,
                "total_departments": total_departments,
                "total_products_catalog": total_products_catalog,
                "total_on_shelf": total_on_shelf,
                "total_expired": total_expired,
                "expiring_30d": expiring_30d,
                "expiring_90d": expiring_90d,
                "total_used": total_used,
                "total_returned": total_returned,
                "total_consigned": total_consigned,
                "active_sessions": active_sessions,
                "completed_sessions": completed_sessions,
                "pending_orders": pending_orders,
                "pending_alerts": pending_alerts,
                "total_postings": total_postings,
            }
        )

    @action(detail=False, methods=["get"], url_path="top-hospitals")
    def top_hospitals(self, request):
        """Top hospitals by on-shelf inventory."""
        limit = int(request.query_params.get("limit", 15))
        data = (
            ConsignedProduct.objects.filter(status="on_shelf")
            .values(name=F("department__hospital__name"), hospital_id=F("department__hospital__id"))
            .annotate(count=Count("id"))
            .order_by("-count")[:limit]
        )
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="top-products")
    def top_products(self, request):
        """Top products by unit count on shelf."""
        limit = int(request.query_params.get("limit", 15))
        hospital_id = request.query_params.get("hospital_id")
        qs = ConsignedProduct.objects.filter(status="on_shelf")
        if hospital_id:
            qs = qs.filter(department__hospital_id=hospital_id)
        data = qs.values("material_code", "product_name").annotate(count=Count("id")).order_by("-count")[:limit]
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="status-breakdown")
    def status_breakdown(self, request):
        """ConsignedProduct status distribution."""
        data = ConsignedProduct.objects.values("status").annotate(count=Count("id")).order_by("-count")
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="movement-trends")
    def movement_trends(self, request):
        """Monthly posting volume by movement type from SAP data."""
        months = int(request.query_params.get("months", 12))
        cutoff = timezone.now().date() - timedelta(days=months * 31)
        data = (
            ConsignmentPosting.objects.filter(posting_date__gte=cutoff)
            .annotate(month=TruncMonth("posting_date"))
            .values("month")
            .annotate(
                total=Count("id"),
                consignment_out=Count("id", filter=Q(movement_type="631")),
                used=Count("id", filter=Q(movement_type="633")),
                returned=Count("id", filter=Q(movement_type="632")),
                scrapped=Count("id", filter=Q(movement_type="551")),
            )
            .order_by("month")
        )
        result = []
        for row in data:
            result.append(
                {
                    "month": row["month"].strftime("%Y-%m") if row["month"] else "",
                    "total": row["total"],
                    "consignment_out": row["consignment_out"],
                    "used": row["used"],
                    "returned": row["returned"],
                    "scrapped": row["scrapped"],
                }
            )
        return Response(result)

    @action(detail=False, methods=["get"], url_path="movement-types")
    def movement_types(self, request):
        """Movement type distribution from SAP postings with clean descriptions."""
        # Get movement type choices as dict for clean mapping
        movement_type_map = dict(ConsignmentPosting.MOVEMENT_TYPE_CHOICES)

        data = (
            ConsignmentPosting.objects.values("movement_type", "movement_type_text")
            .annotate(count=Count("id"), total_qty=Sum("quantity"))
            .order_by("-count")
        )

        # Add clean description to each result
        result = []
        for item in data:
            item["description"] = movement_type_map.get(
                item["movement_type"], item["movement_type_text"] or item["movement_type"]
            )
            result.append(item)

        return Response(result)

    @action(detail=False, methods=["get"], url_path="hospital-inventory")
    def hospital_inventory(self, request):
        """Per-hospital inventory summary (on_shelf, used, returned)."""
        data = (
            ConsignedProduct.objects.values(
                hospital_name=F("department__hospital__name"), hospital_id=F("department__hospital__id")
            )
            .annotate(
                on_shelf=Count("id", filter=Q(status="on_shelf")),
                used=Count("id", filter=Q(status="used")),
                returned=Count("id", filter=Q(status="returned")),
                total=Count("id"),
            )
            .order_by("-total")
        )
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="posting-hospitals")
    def posting_hospitals(self, request):
        """Top hospitals by SAP posting volume."""
        limit = int(request.query_params.get("limit", 15))
        data = (
            ConsignmentPosting.objects.filter(customer_name__isnull=False)
            .exclude(customer_name="")
            .values("customer_name")
            .annotate(
                count=Count("id"),
                total_qty=Sum("quantity"),
            )
            .order_by("-count")[:limit]
        )
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="plant-summary")
    def plant_summary(self, request):
        """Posting volume by plant."""
        data = (
            ConsignmentPosting.objects.values("plant")
            .annotate(count=Count("id"), total_qty=Sum("quantity"))
            .order_by("-count")
        )
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="expiry-breakdown")
    def expiry_breakdown(self, request):
        """On-shelf stock bucketed by expiry urgency tier."""
        today = timezone.now().date()
        qs = ConsignedProduct.objects.filter(status="on_shelf")

        # By-hospital filter
        hospital_id = request.query_params.get("hospital_id")
        if hospital_id:
            qs = qs.filter(department__hospital_id=hospital_id)

        expired = qs.filter(expiry_date__lt=today).count()
        critical = qs.filter(expiry_date__gte=today, expiry_date__lt=today + timedelta(days=8)).count()
        warning = qs.filter(
            expiry_date__gte=today + timedelta(days=8), expiry_date__lt=today + timedelta(days=31)
        ).count()
        watch = qs.filter(
            expiry_date__gte=today + timedelta(days=31), expiry_date__lt=today + timedelta(days=91)
        ).count()
        ok = qs.filter(expiry_date__gte=today + timedelta(days=91)).count()
        no_expiry = qs.filter(expiry_date__isnull=True).count()

        return Response(
            [
                {"tier": "expired", "label": "Expired", "count": expired, "color": "#ef4444"},
                {"tier": "critical", "label": "≤ 7 days", "count": critical, "color": "#f97316"},
                {"tier": "warning", "label": "8–30 days", "count": warning, "color": "#eab308"},
                {"tier": "watch", "label": "31–90 days", "count": watch, "color": "#3b82f6"},
                {"tier": "ok", "label": "> 90 days", "count": ok, "color": "#22c55e"},
                {"tier": "no_expiry", "label": "No Expiry Set", "count": no_expiry, "color": "#9ca3af"},
            ]
        )

    @action(detail=False, methods=["get"], url_path="country-breakdown")
    def country_breakdown(self, request):
        """On-shelf stock grouped by hospital country."""
        data = (
            ConsignedProduct.objects.filter(status="on_shelf")
            .values(country=F("department__hospital__country"))
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return Response(list(data))

    @action(detail=False, methods=["get"], url_path="scan-activity")
    def scan_activity(self, request):
        """ScanningSession summary — by type and recent history."""
        days = int(request.query_params.get("days", 30))
        cutoff = timezone.now() - timedelta(days=days)

        # By session type (all time)
        by_type = (
            ScanningSession.objects.values("session_type")
            .annotate(
                total=Count("id"),
                completed=Count("id", filter=Q(status__in=["completed", "closed"])),
            )
            .order_by("-total")
        )

        session_type_labels = {
            "audit": "Stock Audit",
            "replenish": "Replenishment",
            "removal": "Expired Removal",
            "transfer": "Hospital Transfer",
        }

        by_type_result = [
            {
                "session_type": r["session_type"],
                "label": session_type_labels.get(r["session_type"], r["session_type"]),
                "total": r["total"],
                "completed": r["completed"],
            }
            for r in by_type
        ]

        # Recent sessions (last N days)
        recent = (
            ScanningSession.objects.filter(started_at__gte=cutoff)
            .select_related("hospital", "rep")
            .order_by("-started_at")[:20]
        )

        recent_result = [
            {
                "session_key": s.session_key,
                "session_type": s.session_type,
                "type_label": session_type_labels.get(s.session_type, s.session_type),
                "hospital": s.hospital.name if s.hospital else "—",
                "rep": s.rep.get_full_name() or s.rep.username if s.rep else "—",
                "status": s.status,
                "started_at": s.started_at.strftime("%Y-%m-%d %H:%M") if s.started_at else "",
            }
            for s in recent
        ]

        # Totals
        total_sessions = ScanningSession.objects.count()
        active_sessions = ScanningSession.objects.filter(status="active").count()
        completed_30d = ScanningSession.objects.filter(
            status__in=["completed", "closed"], started_at__gte=cutoff
        ).count()

        return Response(
            {
                "by_type": by_type_result,
                "recent": recent_result,
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "completed_30d": completed_30d,
                "days": days,
            }
        )

    @action(detail=False, methods=["get"], url_path="expiry-by-hospital")
    def expiry_by_hospital(self, request):
        """Per-hospital count of products expiring within 90 days or already expired."""
        today = timezone.now().date()
        data = (
            ConsignedProduct.objects.filter(status="on_shelf", expiry_date__isnull=False)
            .filter(expiry_date__lt=today + timedelta(days=91))
            .values(hospital_name=F("department__hospital__name"), hospital_id=F("department__hospital__id"))
            .annotate(
                expired=Count("id", filter=Q(expiry_date__lt=today)),
                expiring_7d=Count("id", filter=Q(expiry_date__gte=today, expiry_date__lt=today + timedelta(days=8))),
                expiring_30d=Count(
                    "id",
                    filter=Q(expiry_date__gte=today + timedelta(days=8), expiry_date__lt=today + timedelta(days=31)),
                ),
                expiring_90d=Count(
                    "id",
                    filter=Q(expiry_date__gte=today + timedelta(days=31), expiry_date__lt=today + timedelta(days=91)),
                ),
                total_at_risk=Count("id"),
            )
            .order_by("-total_at_risk")
        )
        return Response(list(data))


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCT REPORTING
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductViewSet(viewsets.ViewSet):
    """Expired product reporting from consigned inventory."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        """List all expired products currently on shelf."""
        today = timezone.now().date()
        qs = (
            ConsignedProduct.objects.filter(status="on_shelf", expiry_date__lt=today)
            .select_related("department__hospital", "catalog_product")
            .order_by("expiry_date")
        )

        hospital_id = request.query_params.get("hospital_id")
        if hospital_id:
            qs = qs.filter(department__hospital_id=hospital_id)

        results = []
        for p in qs[:500]:
            days_expired = (today - p.expiry_date).days if p.expiry_date else 0
            results.append(
                {
                    "id": p.id,
                    "serial_number": p.serial_number,
                    "material_code": p.material_code,
                    "product_name": p.product_name,
                    "batch_number": p.batch_number,
                    "expiry_date": str(p.expiry_date) if p.expiry_date else None,
                    "days_expired": days_expired,
                    "hospital_name": p.department.hospital.name if p.department else None,
                    "department_name": p.department.name if p.department else None,
                    "shelf_location": p.shelf_location,
                    "status": p.status,
                }
            )
        return Response({"results": results, "count": len(results)})

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Expired product summary by hospital."""
        today = timezone.now().date()
        qs = ConsignedProduct.objects.filter(status="on_shelf", expiry_date__lt=today)
        total = qs.count()
        by_hospital = list(qs.values("department__hospital__name").annotate(count=Count("id")).order_by("-count")[:15])
        return Response(
            {
                "total_expired": total,
                "by_hospital": [
                    {"hospital_name": h["department__hospital__name"], "count": h["count"]} for h in by_hospital
                ],
            }
        )

    @action(detail=False, methods=["post"])
    def acknowledge(self, request):
        """
        Acknowledge a set of expired consigned products — creates
        ExpiredProductReport records and sends notification.

        POST body: { "product_ids": [1, 2, 3], "notes": "optional note" }
        """
        if not is_mt_manager(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        product_ids = request.data.get("product_ids", [])
        notes = request.data.get("notes", "")

        if not product_ids:
            return Response({"error": "product_ids list is required"}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.now().date()
        created_reports = []
        skipped = 0

        for pid in product_ids:
            try:
                product = ConsignedProduct.objects.select_related("department__hospital", "department").get(pk=pid)
            except ConsignedProduct.DoesNotExist:
                skipped += 1
                continue

            report_id = f"EXP-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            days_expired = (today - product.expiry_date).days if product.expiry_date else 0

            report = ExpiredProductReport.objects.create(
                report_id=report_id,
                hospital=product.department.hospital if product.department else None,
                department=product.department,
                scanning_session=None,
                serial_number=product.serial_number,
                gtin_code=product.gtin_code or "",
                material_code=product.material_code or "",
                product_name=product.product_name or "",
                batch_number=product.batch_number or "",
                expiry_date=product.expiry_date,
                days_expired=days_expired,
                reason="expired",
                status="confirmed",
                notes=notes,
                acknowledged_by=request.user,
                acknowledged_at=timezone.now(),
            )
            created_reports.append(report.id)

            log_audit(
                product=product,
                action_name="expired_acknowledged",
                user=request.user,
                description=f"Expired product {product.serial_number} acknowledged",
                request=request,
            )

        count = len(created_reports)
        if count > 0:
            try:
                notify_expired_acknowledged(product_ids, request.user, count)
            except Exception as exc:
                logger.error(f"notify_expired_acknowledged failed: {exc}")

        return Response(
            {
                "acknowledged": count,
                "skipped": skipped,
                "report_ids": created_reports,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# H2H STOCK TRANSFER VIEWSET
# ─────────────────────────────────────────────────────────────────────────────
class TransferSessionViewSet(viewsets.ModelViewSet):
    """Hospital-to-hospital stock transfer session API."""

    queryset = (
        TransferSession.objects.select_related("from_hospital", "to_hospital", "initiated_by")
        .prefetch_related("transferred_products")
        .all()
    )
    serializer_class = TransferSessionSerializer
    permission_classes = [IsAuthenticated]

    # ── Authorization ─────────────────────────────────────────────────────
    @staticmethod
    def _accessible_q(user):
        """Q filter for transfers a non-manager user may see/act on: their own,
        or any tied to a hospital they cover (as named rep or shared rep)."""
        email = (user.email or "").strip()
        q = Q(initiated_by=user) | Q(from_hospital__salespersons=user) | Q(to_hospital__salespersons=user)
        if email:
            q |= Q(from_hospital__salesperson_email__iexact=email) | Q(to_hospital__salesperson_email__iexact=email)
        return q

    def _can_access(self, user, transfer):
        """Object-level access: superuser/manager, the initiator, or a rep tied
        to the source or destination hospital."""
        if user.is_superuser or is_mt_manager(user):
            return True
        if transfer.initiated_by_id == user.id:
            return True
        email = (user.email or "").strip().lower()
        for hosp in (transfer.from_hospital, transfer.to_hospital):
            if not hosp:
                continue
            if email and (hosp.salesperson_email or "").strip().lower() == email:
                return True
            if hosp.salespersons.filter(id=user.id).exists():
                return True
        return False

    def get_queryset(self):
        """Scope the default list/retrieve so reps can't enumerate other
        hospitals' transfers."""
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser or is_mt_manager(user):
            return qs
        return qs.filter(self._accessible_q(user)).distinct()

    # Mutations go through the explicit, guarded custom actions below — disable
    # the default unguarded CRUD writes from ModelViewSet.
    def create(self, request, *args, **kwargs):
        return Response({"error": "Use /start_transfer/"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        return Response({"error": "Not allowed"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        return Response({"error": "Not allowed"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        return Response({"error": "Use /terminate_transfer/"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=False, methods=["post"])
    def start_transfer(self, request):
        if not (is_mt_rep(request.user) or is_mt_manager(request.user) or request.user.is_superuser):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        from_hospital_id = request.data.get("from_hospital_id")
        to_hospital_id = request.data.get("to_hospital_id")
        from_department_id = request.data.get("from_department_id")
        to_department_id = request.data.get("to_department_id")
        transfer_id = request.data.get("transfer_id")

        if not from_hospital_id or not to_hospital_id:
            return Response(
                {"error": "Both from_hospital_id and to_hospital_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if str(from_hospital_id) == str(to_hospital_id):
            return Response(
                {"error": "Source and destination hospitals must be different"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from_hospital = Hospital.objects.get(pk=from_hospital_id)
            to_hospital = Hospital.objects.get(pk=to_hospital_id)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        # Discard abandoned empty starts for the same user so they don't pile up
        # as 0-scan rows on the dashboard.
        TransferSession.objects.annotate(prod_count=Count("transferred_products")).filter(
            initiated_by=request.user, status="active", prod_count=0
        ).delete()

        if not transfer_id:
            now = timezone.now()
            initials = "".join([n[0] for n in request.user.get_full_name().split()][:2]).upper() or "XX"
            transfer_id = (
                f"{now.strftime('%d%m%y')}-{initials}-{from_hospital.sap_code or 'FROM'}"
                f"-TO-{to_hospital.sap_code or 'TO'}"
            )

        from_department = Department.objects.filter(pk=from_department_id).first() if from_department_id else None
        to_department = Department.objects.filter(pk=to_department_id).first() if to_department_id else None

        transfer = TransferSession.objects.create(
            session_key=f"xfer-{uuid.uuid4().hex[:12]}",
            transfer_id=transfer_id,
            from_hospital=from_hospital,
            from_department=from_department,
            to_hospital=to_hospital,
            to_department=to_department,
            initiated_by=request.user,
            status="active",
        )
        return Response(TransferSessionSerializer(transfer).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def scan_product(self, request):
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key, status="active")
        except TransferSession.DoesNotExist:
            return Response({"error": "Active transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if not self._can_access(request.user, transfer):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        # Discrete fields may be supplied directly (parsed client-side), or we
        # parse a raw GS1/barcode string sent as qr_data — same workflow as the
        # main scanner, so the transfer scanner can post raw scans.
        gtin_code = request.data.get("gtin_code")
        material_code = request.data.get("material_code")
        product_name = request.data.get("product_name")
        batch_number = request.data.get("batch_number")
        expiry_date = request.data.get("expiry_date")
        production_date = request.data.get("production_date")
        quantity = request.data.get("quantity")
        raw_qr = (request.data.get("qr_data") or "").strip()
        no_serial = request.data.get("no_serial", False)

        serial_number = ""
        if raw_qr:
            parsed = _parse_gs1(raw_qr)
            serial_number = parsed.get("serial_number", "") or ""
            gtin_code = gtin_code or parsed.get("gtin_code", "")
            material_code = material_code or parsed.get("material_code", "")
            product_name = product_name or parsed.get("product_name", "")
            batch_number = batch_number or parsed.get("batch_number", "")
            expiry_date = expiry_date or parsed.get("expiry_date")
            production_date = production_date or parsed.get("production_date")
            if quantity is None:
                quantity = parsed.get("quantity")

        # A serial scanned/typed separately (2-scan workflow or manual entry) wins.
        serial_override = (request.data.get("serial_number") or "").strip()
        if serial_override:
            serial_number = serial_override

        # Auto-map product details from the catalog by GTIN.
        if gtin_code and not product_name:
            try:
                cat = ProductCatalog.objects.get(gtin_code=gtin_code, is_active=True)
                product_name = product_name or cat.name
                material_code = material_code or cat.material_code
            except ProductCatalog.DoesNotExist:
                pass

        # Resolve a missing serial — mirrors the main scanner: explicit "no serial"
        # gets a placeholder; a parsed product with no serial prompts the client;
        # otherwise fall back to the raw payload.
        if not serial_number:
            if no_serial:
                serial_number = f"NSN-{uuid.uuid4().hex[:12]}"
            elif gtin_code or batch_number or material_code:
                return Response(
                    {
                        "error_code": "SERIAL_REQUIRED",
                        "error": 'No serial number detected in barcode. Scan a separate SN barcode, type it manually, or choose "No Serial Number".',
                        "parsed_fields": {
                            "gtin_code": gtin_code or "",
                            "material_code": material_code or "",
                            "product_name": product_name or "",
                            "batch_number": batch_number or "",
                            "expiry_date": str(expiry_date) if expiry_date else "",
                        },
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            elif raw_qr:
                serial_number = raw_qr
            else:
                return Response({"error": "serial_number is required"}, status=status.HTTP_400_BAD_REQUEST)

        if TransferredProduct.objects.filter(
            transfer_session=transfer,
            serial_number=serial_number,
        ).exists():
            return Response(
                {
                    "error_code": "DUPLICATE_SERIAL",
                    "error": f"Product {serial_number} already scanned in this transfer",
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            quantity = max(1, int(quantity))
        except (ValueError, TypeError):
            quantity = 1

        product = TransferredProduct.objects.create(
            transfer_session=transfer,
            serial_number=serial_number,
            gtin_code=gtin_code or None,
            material_code=material_code or None,
            product_name=product_name or None,
            batch_number=ensure_batch_number(batch_number, serial_number),
            expiry_date=expiry_date or None,
            production_date=production_date or None,
            quantity=quantity,
            qr_data=raw_qr or None,
            notes=request.data.get("notes", ""),
        )

        transfer.last_scan_time = timezone.now()
        transfer.save(update_fields=["last_scan_time"])

        data = TransferredProductSerializer(product).data
        data["total_products"] = transfer.transferred_products.count()
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def by_session_key(self, request):
        """Fetch a single transfer (with products & attachment) by session_key."""
        session_key = request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._can_access(request.user, transfer):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        return Response(TransferSessionSerializer(transfer).data)

    @action(detail=False, methods=["get"])
    def my_transfers(self, request):
        """Flat list of transfers for the dashboard table (optional ?status= filter).

        Abandoned/empty transfers are noise: cancelled sessions and any session
        with zero scanned products are never surfaced on the dashboard.
        """
        qs = (
            TransferSession.objects.select_related("from_hospital", "to_hospital", "initiated_by")
            .annotate(scan_count=Count("transferred_products"))
            .exclude(status="cancelled")
            .filter(scan_count__gt=0)
            .order_by("-started_at")
        )
        # Reps only see transfers they own or cover; managers see all.
        user = request.user
        if not (user.is_superuser or is_mt_manager(user)):
            qs = qs.filter(self._accessible_q(user)).distinct()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        rows = []
        for t in qs:
            rep = t.initiated_by
            rep_name = (rep.get_full_name() or rep.username) if rep else "—"
            rows.append(
                {
                    "session_key": t.session_key,
                    "transfer_id": t.transfer_id,
                    "from_hospital_name": t.from_hospital.name if t.from_hospital else "—",
                    "to_hospital_name": t.to_hospital.name if t.to_hospital else "—",
                    "rep_name": rep_name,
                    "scan_count": t.scan_count,
                    "status": t.status,
                    "started_at": t.started_at,
                }
            )
        return Response({"count": len(rows), "transfers": rows})

    @action(detail=False, methods=["post"])
    def complete_transfer(self, request):
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if not self._can_access(request.user, transfer):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        if transfer.status != "active":
            return Response(
                {"error": f"Cannot complete transfer in {transfer.status} status"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        transfer.mark_completed()
        return Response(TransferSessionSerializer(transfer).data)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request):
        session_key = request.data.get("session_key") or request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if not self._can_access(request.user, transfer):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        if transfer.status not in ("completed", "active"):
            return Response(
                {"error": "Transfer must be active or completed to upload attachment"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        # Attachment save + stock movement + close are one unit: if the movement
        # fails, nothing is persisted (no "closed but stock not moved" divergence).
        try:
            with transaction.atomic():
                attachment, created = TransferAttachment.objects.update_or_create(
                    transfer_session=transfer,
                    defaults={
                        "file_data": uploaded_file.read(),
                        "content_type": uploaded_file.content_type,
                        "file_size": uploaded_file.size,
                        "original_filename": uploaded_file.name,
                        "uploaded_by": request.user,
                        "notes": request.data.get("notes", ""),
                    },
                )
                moved, skipped = self._apply_transfer_inventory(transfer, request.user, request=request)
                transfer.mark_closed()
                log_audit(
                    product=None,
                    action_name="attachment_uploaded",
                    user=request.user,
                    description=(
                        f"Confirmation uploaded for transfer {transfer.transfer_id} "
                        f"({transfer.from_hospital.name} → {transfer.to_hospital.name}); "
                        f"moved {moved}, skipped {skipped}"
                    ),
                    old_status="completed",
                    new_status="closed",
                    request=request,
                    metadata={"transfer_id": transfer.transfer_id, "moved": moved, "skipped": skipped},
                )
        except Exception as exc:
            logger.error(f"Transfer finalize failed for {transfer.transfer_id}: {exc}")
            return Response(
                {"error": "Failed to finalize transfer; no changes were saved. Please retry."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(f"Transfer {transfer.transfer_id}: moved {moved} product(s), skipped {skipped}")

        # Best-effort notification — outside the transaction so a notify failure
        # can never roll back a completed stock movement.
        try:
            notify_transfer_closed(transfer, moved=moved, skipped=skipped)
        except Exception as exc:
            logger.error(f"Transfer notification failed for {transfer.transfer_id}: {exc}")

        data = TransferAttachmentSerializer(attachment).data
        data.update({"moved": moved, "skipped": skipped, "status": transfer.status})
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @staticmethod
    def _apply_transfer_inventory(transfer, user, request=None):
        """Move stock between hospitals when a transfer closes.

        For every product scanned in the transfer: the matching on-shelf unit
        at the source hospital becomes status=transferred, and an equivalent
        on-shelf unit is created at the destination hospital. Each movement
        writes a paired AuditLog (transfer_out / transfer_in).

        Idempotent — products already linked to a moved unit are skipped, so a
        re-run never double-moves stock. Returns (moved_count, skipped_count);
        unmatched products are noted on the transfer for manual review.

        Must run inside a transaction (callers wrap it) — source rows are locked
        with select_for_update to prevent concurrent double-moves.
        """
        dest_dept, _ = Department.objects.get_or_create(
            hospital=transfer.to_hospital,
            name="Consignment Stock",
            defaults={"code": "CS", "is_active": True},
        )

        moved, skipped_serials = 0, []
        with transaction.atomic():
            for tp in transfer.transferred_products.all():
                # Idempotency: this scanned line was already moved on a prior run.
                if tp.consigned_product_id:
                    continue

                source_qs = ConsignedProduct.objects.select_for_update().filter(
                    department__hospital=transfer.from_hospital, status="on_shelf"
                )
                if tp.serial_number:
                    source = source_qs.filter(serial_number=tp.serial_number).first()
                else:
                    # No-serial product — match by GTIN + batch within the source hospital
                    source = source_qs.filter(
                        serial_number="",
                        gtin_code=tp.gtin_code or None,
                        batch_number=tp.batch_number or None,
                    ).first()

                if not source:
                    skipped_serials.append(tp.serial_number or f"(no-serial {tp.gtin_code or '?'})")
                    continue

                source.status = "transferred"
                source.removed_at = timezone.now()
                source.removed_by = user
                source.removal_reason = f"Transferred to {transfer.to_hospital.name} ({transfer.transfer_id})"
                source.save(update_fields=["status", "removed_at", "removed_by", "removal_reason"])

                dest_unit = ConsignedProduct.objects.create(
                    department=dest_dept,
                    serial_number=source.serial_number,
                    gtin_code=source.gtin_code,
                    material_code=source.material_code,
                    catalog_product=source.catalog_product,
                    product_name=source.product_name,
                    batch_number=source.batch_number,
                    size=source.size,
                    quantity=source.quantity,
                    production_date=source.production_date,
                    expiry_date=source.expiry_date,
                    status="on_shelf",
                    placed_by=user,
                    reference_number=source.reference_number,
                    qr_data=source.qr_data,
                    notes=f"Received via transfer {transfer.transfer_id} from {transfer.from_hospital.name}",
                )
                tp.consigned_product = source
                tp.save(update_fields=["consigned_product"])
                moved += 1

                # Audit trail — paired out/in entries for traceability.
                unit_ref = source.serial_number or source.gtin_code or "(no-id)"
                log_audit(
                    product=source,
                    action_name="product_transferred",
                    user=user,
                    description=f"Transfer OUT {unit_ref} → {transfer.to_hospital.name} ({transfer.transfer_id})",
                    old_status="on_shelf",
                    new_status="transferred",
                    request=request,
                    metadata={
                        "transfer_id": transfer.transfer_id,
                        "direction": "out",
                        "from_hospital": transfer.from_hospital.name,
                        "to_hospital": transfer.to_hospital.name,
                        "dest_unit_id": dest_unit.id,
                    },
                )
                log_audit(
                    product=dest_unit,
                    action_name="product_transferred",
                    user=user,
                    description=f"Transfer IN {unit_ref} ← {transfer.from_hospital.name} ({transfer.transfer_id})",
                    old_status=None,
                    new_status="on_shelf",
                    request=request,
                    metadata={
                        "transfer_id": transfer.transfer_id,
                        "direction": "in",
                        "from_hospital": transfer.from_hospital.name,
                        "to_hospital": transfer.to_hospital.name,
                        "source_unit_id": source.id,
                    },
                )

            if skipped_serials:
                skip_note = (
                    f"[Inventory] {len(skipped_serials)} product(s) had no matching on-shelf stock "
                    f"at {transfer.from_hospital.name}: {', '.join(skipped_serials[:20])}"
                )
                transfer.notes = f"{transfer.notes}\n{skip_note}" if transfer.notes else skip_note
                transfer.save(update_fields=["notes"])

        return moved, len(skipped_serials)

    @action(detail=False, methods=["get"])
    def download_attachment(self, request):
        session_key = request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
            attachment = transfer.attachment
        except (TransferSession.DoesNotExist, TransferAttachment.DoesNotExist):
            return Response({"error": "Transfer or attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        if not self._can_access(request.user, transfer):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        response = HttpResponse(attachment.file_data, content_type=attachment.content_type)
        disposition = "attachment" if request.query_params.get("download") else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{attachment.original_filename}"'
        return response

    @action(detail=False, methods=["post"])
    def terminate_transfer(self, request):
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        # Managers/superusers can terminate any transfer; a rep may cancel their own.
        is_owner = transfer.initiated_by_id == request.user.id
        if not (is_mt_manager(request.user) or request.user.is_superuser or is_owner):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        if transfer.status == "closed":
            return Response({"error": "Cannot terminate a closed transfer"}, status=status.HTTP_400_BAD_REQUEST)

        transfer.status = "cancelled"
        transfer.save(update_fields=["status"])
        return Response({"status": "ok", "message": "Transfer terminated"})

    @action(detail=False, methods=["delete"], url_path="products/(?P<product_id>[^/.]+)")
    def delete_transferred_product(self, request, product_id=None):
        try:
            product = TransferredProduct.objects.select_related("transfer_session").get(pk=product_id)
        except TransferredProduct.DoesNotExist:
            return Response({"error": "Transferred product not found"}, status=status.HTTP_404_NOT_FOUND)

        transfer = product.transfer_session
        if transfer.status in ("completed", "closed"):
            return Response(
                {"error": "Cannot delete from completed/closed transfer"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not request.user.is_superuser and transfer.initiated_by != request.user:
            return Response({"error": "Only transfer owner can delete products"}, status=status.HTTP_403_FORBIDDEN)

        product.delete()
        return Response({"status": "ok", "total_products": transfer.transferred_products.count()})


# ─────────────────────────────────────────────────────────────────────────────
# REMARKS — Product-wise & Session-level
# ─────────────────────────────────────────────────────────────────────────────
class ScannedProductRemarkViewSet(viewsets.GenericViewSet):
    """Per-product remarks (mapped to ScanLog entries in MeriTrack)."""

    serializer_class = ScannedProductRemarkSerializer
    permission_classes = [IsAuthenticated]

    def _infer_role(self, user):
        if user.is_superuser:
            return "warehouse"
        if user.groups.filter(name__in={"MT Manager", "MT Logistics"}).exists():
            return "warehouse"
        return "salesperson"

    def list(self, request):
        product_id = request.query_params.get("product")
        session_key = request.query_params.get("session")

        if product_id:
            try:
                scan_log = ScanLog.objects.get(pk=product_id)
            except ScanLog.DoesNotExist:
                return Response({"error": "Product scan not found"}, status=status.HTTP_404_NOT_FOUND)
            remarks = ScannedProductRemark.objects.filter(scan_log=scan_log).select_related("created_by")
        elif session_key:
            remarks = ScannedProductRemark.objects.filter(scan_log__session__session_key=session_key).select_related(
                "created_by", "scan_log"
            )
        else:
            return Response({"error": "product or session query param is required"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ScannedProductRemarkSerializer(remarks, many=True).data)

    def create(self, request):
        product_id = request.data.get("product")
        remark_text = (request.data.get("remark") or "").strip()

        if not product_id:
            return Response({"error": "product is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not remark_text:
            return Response({"error": "remark cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            scan_log = ScanLog.objects.select_related("session__rep", "session__hospital").get(pk=product_id)
        except ScanLog.DoesNotExist:
            return Response({"error": "Product scan not found"}, status=status.HTTP_404_NOT_FOUND)

        role = self._infer_role(request.user)
        remark = ScannedProductRemark.objects.create(
            scan_log=scan_log,
            remark=remark_text,
            role=role,
            created_by=request.user,
        )

        if role == "warehouse":
            try:
                notify_warehouse_remark(
                    session=scan_log.session,
                    remark_text=remark_text,
                    added_by=request.user,
                    remark_type="product",
                    product=scan_log,
                )
            except Exception:
                logger.exception("Failed to send warehouse product remark notification")

        return Response(ScannedProductRemarkSerializer(remark).data, status=status.HTTP_201_CREATED)


class ScanSessionRemarkViewSet(viewsets.GenericViewSet):
    """Session-level remarks for a scan session."""

    serializer_class = ScanSessionRemarkSerializer
    permission_classes = [IsAuthenticated]

    def _infer_role(self, user):
        if user.is_superuser:
            return "warehouse"
        if user.groups.filter(name__in={"MT Manager", "MT Logistics"}).exists():
            return "warehouse"
        return "salesperson"

    def list(self, request):
        session_key = request.query_params.get("session")
        if not session_key:
            return Response({"error": "session query param is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        remarks = ScanSessionRemark.objects.filter(session=session).select_related("created_by")
        return Response(ScanSessionRemarkSerializer(remarks, many=True).data)

    def create(self, request):
        session_key = request.data.get("session")
        remark_text = (request.data.get("remark") or "").strip()

        if not session_key:
            return Response({"error": "session is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not remark_text:
            return Response({"error": "remark cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.select_related("rep", "hospital").get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        role = self._infer_role(request.user)
        remark = ScanSessionRemark.objects.create(
            session=session,
            remark=remark_text,
            role=role,
            created_by=request.user,
        )

        if role == "warehouse":
            try:
                notify_warehouse_remark(
                    session=session,
                    remark_text=remark_text,
                    added_by=request.user,
                    remark_type="session",
                )
            except Exception:
                logger.exception("Failed to send warehouse session remark notification")

        return Response(ScanSessionRemarkSerializer(remark).data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# CONSIGNMENT POSTINGS (read-only SAP journal)
# ─────────────────────────────────────────────────────────────────────────────
class ConsignmentPostingViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only SAP consignment movement journal."""

    queryset = ConsignmentPosting.objects.select_related("hospital", "catalog_product")
    serializer_class = ConsignmentPostingSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = [
        "plant",
        "movement_type",
        "material",
        "customer",
        "batch",
        "posting_date",
    ]
    search_fields = ["material", "material_description", "customer_name", "batch", "material_document", "delivery"]
    ordering_fields = ["posting_date", "entry_date", "quantity", "movement_type"]
    ordering = ["-posting_date", "-entry_time"]


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCT REPORTS
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductReportViewSet(viewsets.ModelViewSet):
    """Reports of expired/recalled products found during scanning sessions."""

    queryset = ExpiredProductReport.objects.select_related("hospital", "department", "scanning_session").order_by(
        "-created_at"
    )
    serializer_class = ExpiredProductReportSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "reason", "hospital", "department"]
    search_fields = ["serial_number", "product_name", "batch_number", "material_code"]
    ordering_fields = ["created_at", "expiry_date", "status"]

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=False, methods=["get"])
    def by_hospital(self, request):
        """Summary of expired products by hospital."""
        hospital_id = request.query_params.get("hospital_id")
        qs = ExpiredProductReport.objects.filter(status__in=["reported", "confirmed"])
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)

        summary = qs.values("hospital__name", "reason").annotate(count=Count("id")).order_by("hospital__name", "reason")

        return Response(list(summary))

    @action(detail=False, methods=["get"])
    def pending_actions(self, request):
        """Get expired products pending removal action."""
        qs = ExpiredProductReport.objects.filter(status="reported").select_related("hospital", "department")
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────────────────────
# SCANNING BATCHES
# ─────────────────────────────────────────────────────────────────────────────
class ScanningBatchViewSet(viewsets.ModelViewSet):
    """Logical grouping of scanning sessions for batch operations."""

    queryset = ScanningBatch.objects.select_related("hospital", "created_by")
    serializer_class = ScanningBatchSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["hospital", "batch_type"]
    search_fields = ["batch_id", "description"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["post"], url_path="auto-generate-id")
    def auto_generate_id(self, request):
        """Generate unique batch ID."""
        batch_id = f"BATCH-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        return Response({"batch_id": batch_id})


# ─────────────────────────────────────────────────────────────────────────────
# SALESPERSON (REP) PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
class SalespersonPerformanceViewSet(viewsets.ViewSet):
    """Performance analytics per MT Rep — session counts, hospitals, trends."""

    permission_classes = [IsAuthenticated]

    def _is_manager(self, user):
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=["MT Manager", "MT Logistics"]).exists()

    def _is_rep_only(self, user):
        if user.is_superuser:
            return False
        in_rep = user.groups.filter(name="MT Rep").exists()
        in_mgr = user.groups.filter(name__in=["MT Manager", "MT Logistics"]).exists()
        return in_rep and not in_mgr

    @staticmethod
    def _discover_rep_emails():
        """Collect unique rep emails from Hospital salesperson_email/salespersons M2M + ScanningSession.rep."""
        from django.contrib.auth.models import User as _User

        from .models import Hospital as _Hosp
        from .models import ScanningSession as _SS

        emails = set()
        # From primary salesperson_email
        for e in (
            _Hosp.objects.exclude(salesperson_email="")
            .exclude(salesperson_email__isnull=True)
            .values_list("salesperson_email", flat=True)
        ):
            emails.add(e.lower())
        # From M2M salespersons
        for e in (
            _Hosp.objects.filter(salespersons__isnull=False)
            .values_list("salespersons__email", flat=True)
            .distinct()
        ):
            if e:
                emails.add(e.lower())
        # From actual session reps
        for uid in _SS.objects.exclude(rep=None).values_list("rep", flat=True).distinct():
            try:
                u = _User.objects.get(pk=uid)
                if u.email:
                    emails.add(u.email.lower())
            except _User.DoesNotExist:
                pass
        return sorted(emails)

    @staticmethod
    def _get_hospitals_for_email(sp_email):
        from django.db.models import Q as _Q

        from .models import Hospital as _Hosp

        return list(
            _Hosp.objects.filter(
                _Q(salesperson_email__iexact=sp_email) | _Q(salespersons__email__iexact=sp_email),
                is_active=True,
            ).distinct().values("id", "name", "city")
        )

    @staticmethod
    def _sessions_for_email_month(sp_email, year, month):
        """
        All sessions at hospitals assigned to this rep in a given month,
        regardless of who scanned (mirrors PS behaviour so warehouse scans
        count toward the rep's hospital visit status).
        """
        from django.db.models import Count as _Count

        return list(
            ScanningSession.objects.filter(
                Q(hospital__salesperson_email__iexact=sp_email)
                | Q(hospital__salespersons__email__iexact=sp_email),
                started_at__year=year,
                started_at__month=month,
            )
            .exclude(status="cancelled")
            .annotate(_sc=_Count("scan_logs"))
            .exclude(_sc=0)
            .distinct()
            .select_related("hospital", "rep")
        )

    @classmethod
    def _build_month_summary_email(cls, sp_email, year, month):
        """Full month summary — mirrors PS's _build_month_summary_email but uses ScanLog."""
        assigned_hospitals = list(
            Hospital.objects.filter(
                Q(salesperson_email__iexact=sp_email) | Q(salespersons__email__iexact=sp_email),
                is_active=True,
            ).distinct()
        )
        sessions = list(cls._sessions_for_email_month(sp_email, year, month))
        session_ids = [s.id for s in sessions]
        visited_hospital_ids = {s.hospital_id for s in sessions if s.hospital_id}
        pending_hospitals = [h for h in assigned_hospitals if h.id not in visited_hospital_ids]
        completed_sessions = [s for s in sessions if s.status in ("completed", "closed")]
        total_scans = active_scan_logs().filter(session_id__in=session_ids).count()

        hospital_details = []
        for h in assigned_hospitals:
            h_sessions = [s for s in sessions if s.hospital_id == h.id]
            h_session_ids = [s.id for s in h_sessions]
            h_completed = [s for s in h_sessions if s.status in ("completed", "closed")]
            h_scans = active_scan_logs().filter(session_id__in=h_session_ids).count() if h_sessions else 0
            hospital_details.append({
                "id": h.id,
                "name": h.name,
                "sap_code": h.sap_code or "",
                "province": h.province or "",
                "city": h.city or "",
                "visited": len(h_sessions) > 0,
                "sessions_count": len(h_sessions),
                "completed_sessions": len(h_completed),
                "scans_count": h_scans,
                "status": (
                    "completed" if len(h_completed) > 0
                    else "in_progress" if len(h_sessions) > 0
                    else "pending"
                ),
                "last_visit": (
                    max((s.started_at for s in h_sessions), default=None).strftime("%d %b %Y")
                    if h_sessions else None
                ),
            })

        return {
            "year": year,
            "month": month,
            "month_label": datetime(year, month, 1).strftime("%B %Y"),
            "assigned_hospitals": len(assigned_hospitals),
            "visited_hospitals": len(visited_hospital_ids),
            "pending_hospitals": len(pending_hospitals),
            "total_sessions": len(sessions),
            "completed_sessions": len(completed_sessions),
            "total_scans": total_scans,
            "visit_rate": (
                round(len(visited_hospital_ids) / len(assigned_hospitals) * 100, 1)
                if assigned_hospitals else 0
            ),
            "hospitals": hospital_details,
            "pending_hospital_names": [h.name for h in pending_hospitals],
        }

    @action(detail=False, methods=["get"])
    def overview(self, request):
        """Full per-rep summary for a given month — mirrors PS overview."""
        if not self._is_manager(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            year  = int(request.query_params.get("year",  timezone.now().year))
            month = int(request.query_params.get("month", timezone.now().month))
        except (ValueError, TypeError):
            year, month = timezone.now().year, timezone.now().month

        from django.contrib.auth.models import User as _User

        mt_reps = list(_User.objects.filter(groups__name="MT Rep", is_active=True).order_by("first_name", "last_name"))
        result = []
        for u in mt_reps:
            if not u.email:
                continue
            summary = self._build_month_summary_email(u.email, year, month)
            result.append({
                "email": u.email,
                "name": u.get_full_name() or u.username,
                "user_id": u.id,
                **summary,
            })

        result.sort(key=lambda x: x.get("total_sessions", 0), reverse=True)
        return Response({"salespersons": result, "year": year, "month": month})

    @action(detail=False, methods=["get"])
    def performance_detail(self, request):
        """Detailed monthly breakdown for a single rep."""
        if not self._is_manager(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        sp_email = request.query_params.get("email", "").strip()
        if not sp_email:
            return Response({"error": "email param is required"}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.now().date()
        months = []
        for i in range(12):
            d = today.replace(day=1) - timedelta(days=i * 30)
            months.append((d.year, d.month))

        monthly = [self._build_month_summary_email(sp_email, y, m) for y, m in months]
        hospitals = self._get_hospitals_for_email(sp_email)

        return Response(
            {
                "email": sp_email,
                "monthly_summary": monthly,
                "assigned_hospitals": hospitals,
            }
        )

    @action(detail=False, methods=["get"])
    def current_month(self, request):
        """Current-month stats for the logged-in rep or a specified email."""
        is_mgr = self._is_manager(request.user)
        email_param = request.query_params.get("email", "").strip()

        if email_param and is_mgr:
            sp_email = email_param
        elif self._is_rep_only(request.user):
            sp_email = request.user.email
        elif is_mgr:
            sp_email = email_param or request.user.email
        else:
            sp_email = request.user.email

        today = timezone.now().date()
        summary = self._build_month_summary_email(sp_email, today.year, today.month)
        hospitals = self._get_hospitals_for_email(sp_email)

        return Response(
            {
                "email": sp_email,
                "summary": summary,
                "assigned_hospitals": hospitals,
            }
        )

    @action(detail=False, methods=["get"])
    def salesperson_list(self, request):
        """List all MT Reps with name, email and hospital count."""
        if not self._is_manager(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        from django.contrib.auth.models import User as _User
        mt_reps = _User.objects.filter(groups__name="MT Rep", is_active=True).order_by("first_name", "last_name")
        result = []
        for u in mt_reps:
            hcount = Hospital.objects.filter(
                Q(salesperson_email__iexact=u.email) | Q(salespersons=u),
                is_active=True,
            ).distinct().count()
            result.append({
                "email": u.email,
                "name": u.get_full_name() or u.username,
                "user_id": u.id,
                "hospital_count": hcount,
            })
        return Response({"salespersons": result})

    @action(detail=False, methods=["get"], url_path="rep-list")
    def rep_list(self, request):
        """List all reps with name and email for the manager dropdown."""
        if not self._is_manager(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        from django.contrib.auth.models import User as _User

        mt_rep_ids = set(
            _User.objects.filter(groups__name="MT Rep", is_active=True).values_list("id", flat=True)
        )
        reps = []
        seen_emails = set()
        # From sessions — only MT Rep users
        rep_ids = ScanningSession.objects.exclude(rep=None).values_list("rep", flat=True).distinct()
        for uid in rep_ids:
            if uid not in mt_rep_ids:
                continue
            try:
                u = _User.objects.get(pk=uid)
                if u.email and u.email.lower() not in seen_emails:
                    seen_emails.add(u.email.lower())
                    reps.append({"email": u.email, "name": f"{u.first_name} {u.last_name}".strip() or u.username})
            except _User.DoesNotExist:
                pass
        # Also include MT Reps assigned to hospitals
        for e in (
            Hospital.objects.exclude(salesperson_email="")
            .exclude(salesperson_email__isnull=True)
            .values_list("salesperson_email", flat=True)
            .distinct()
        ):
            if e.lower() not in seen_emails:
                try:
                    u = _User.objects.get(email__iexact=e)
                    if u.id not in mt_rep_ids:
                        continue
                    seen_emails.add(e.lower())
                    reps.append({"email": u.email, "name": f"{u.first_name} {u.last_name}".strip() or u.username})
                except _User.DoesNotExist:
                    pass
        for e in (
            Hospital.objects.filter(salespersons__isnull=False)
            .values_list("salespersons__email", flat=True)
            .distinct()
        ):
            if e and e.lower() not in seen_emails:
                try:
                    u = _User.objects.get(email__iexact=e)
                    if u.id not in mt_rep_ids:
                        continue
                    seen_emails.add(e.lower())
                    reps.append({"email": u.email, "name": f"{u.first_name} {u.last_name}".strip() or u.username})
                except _User.DoesNotExist:
                    pass
        reps.sort(key=lambda x: x.get("name", "").lower())
        return Response({"reps": reps})

    @action(detail=False, methods=["get"], url_path="current-month")
    def current_month_dashboard(self, request):
        """Per-hospital breakdown for the selected month — mirrors PS salesperson dashboard API."""
        is_mgr = self._is_manager(request.user)

        rep_email = request.query_params.get("rep_email", "").strip()
        if is_mgr and not rep_email:
            # Manager, no rep selected → aggregate all reps
            try:
                year  = int(request.query_params.get("year",  timezone.now().year))
                month = int(request.query_params.get("month", timezone.now().month))
            except (ValueError, TypeError):
                year, month = timezone.now().year, timezone.now().month
            from django.contrib.auth.models import User as _User
            mt_reps = _User.objects.filter(groups__name="MT Rep", is_active=True)
            seen_hosp_ids = set()
            all_hospitals = []
            for u in mt_reps:
                if not u.email:
                    continue
                summary = self._build_month_summary_email(u.email, year, month)
                for h in summary["hospitals"]:
                    if h["id"] not in seen_hosp_ids:
                        seen_hosp_ids.add(h["id"])
                        all_hospitals.append(h)
            all_hospitals.sort(key=lambda h: h["name"])
            assigned  = len(all_hospitals)
            visited   = sum(1 for h in all_hospitals if h["status"] != "pending")
            pending   = assigned - visited
            sessions  = sum(h["sessions_count"] for h in all_hospitals)
            scans     = sum(h["scans_count"]    for h in all_hospitals)
            rate      = round(visited / assigned * 100, 1) if assigned else 0
            return Response({
                "assigned_hospitals": assigned,
                "visited_hospitals":  visited,
                "pending_hospitals":  pending,
                "total_sessions":     sessions,
                "total_scans":        scans,
                "visit_rate":         rate,
                "hospitals":          all_hospitals,
                "year": year, "month": month, "rep_email": "",
            })
        elif is_mgr and rep_email:
            email = rep_email
        else:
            email = request.user.email

        try:
            year = int(request.query_params.get("year", timezone.now().year))
            month = int(request.query_params.get("month", timezone.now().month))
        except (ValueError, TypeError):
            year, month = timezone.now().year, timezone.now().month

        summary = self._build_month_summary_email(email, year, month)
        return Response({**summary, "rep_email": email})

    @action(detail=False, methods=["get"], url_path="hospital-sessions")
    def hospital_sessions_detail(self, request):
        """Sessions for a rep at a specific hospital within a given month."""
        from django.db.models import Count as _Count

        is_mgr = self._is_manager(request.user)

        hospital_id = request.query_params.get("hospital_id", "").strip()
        rep_email = request.query_params.get("rep_email", "").strip()

        if not hospital_id:
            return Response({"error": "hospital_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # rep_email is optional — omitting it (All Reps mode) returns all sessions for the hospital
        filter_by_rep = rep_email if rep_email else (None if is_mgr else request.user.email)

        try:
            year = int(request.query_params.get("year", timezone.now().year))
            month = int(request.query_params.get("month", timezone.now().month))
        except (ValueError, TypeError):
            year, month = timezone.now().year, timezone.now().month

        qs = ScanningSession.objects.filter(
            hospital_id=hospital_id,
            started_at__year=year,
            started_at__month=month,
        )
        if filter_by_rep:
            qs = qs.filter(rep__email__iexact=filter_by_rep)

        sessions = (
            qs
            .exclude(status="cancelled")
            .annotate(_sc=_Count("scan_logs"))
            .exclude(_sc=0)
            .select_related("hospital", "rep")
            .order_by("-started_at")
        )

        result = []
        for s in sessions:
            rep = s.rep
            rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else ""
            result.append(
                {
                    "batch_id": s.session_key,
                    "session_key": s.session_key,
                    "session_type": s.get_session_type_display(),
                    "status": s.status,
                    "started_at": s.started_at.strftime("%d %b %Y, %H:%M") if s.started_at else None,
                    "completed_at": s.completed_at.strftime("%d %b %Y, %H:%M") if s.completed_at else None,
                    "scanned_by": rep_name,
                    "scans_count": s.scan_logs.count(),
                    "detail_url": f"/meritrack/session/{s.session_key}/",
                }
            )
        return Response({"sessions": result})

    @action(detail=False, methods=["get"], url_path="detail")
    def rep_detail(self, request):
        """12-month history for a rep — mirrors PS detail endpoint."""
        is_mgr = self._is_manager(request.user)

        rep_email = request.query_params.get("rep_email", "").strip()

        try:
            months_count = int(request.query_params.get("months", 12))
        except (ValueError, TypeError):
            months_count = 12

        today = timezone.now().date()
        seen = set()
        month_keys = []
        for i in range(months_count - 1, -1, -1):
            target = today.replace(day=1) - timedelta(days=i * 30)
            key = (target.year, target.month)
            if key not in seen:
                seen.add(key)
                month_keys.append((target.year, target.month))

        if is_mgr and not rep_email:
            # All Reps — aggregate each month's totals across all MT Reps
            from django.contrib.auth.models import User as _User
            mt_reps = list(_User.objects.filter(groups__name="MT Rep", is_active=True))
            history = []
            for y, m in month_keys:
                agg = {"year": y, "month": m,
                       "month_label": datetime(y, m, 1).strftime("%B %Y"),
                       "assigned_hospitals": 0, "visited_hospitals": 0, "pending_hospitals": 0,
                       "total_sessions": 0, "completed_sessions": 0, "total_scans": 0,
                       "visit_rate": 0, "hospitals": [], "pending_hospital_names": []}
                seen_h = set()
                for u in mt_reps:
                    if not u.email:
                        continue
                    s = self._build_month_summary_email(u.email, y, m)
                    agg["total_sessions"]     += s["total_sessions"]
                    agg["completed_sessions"] += s["completed_sessions"]
                    agg["total_scans"]        += s["total_scans"]
                    for h in s["hospitals"]:
                        if h["id"] not in seen_h:
                            seen_h.add(h["id"])
                            agg["hospitals"].append(h)
                agg["assigned_hospitals"] = len(agg["hospitals"])
                agg["visited_hospitals"]  = sum(1 for h in agg["hospitals"] if h["status"] != "pending")
                agg["pending_hospitals"]  = agg["assigned_hospitals"] - agg["visited_hospitals"]
                agg["visit_rate"]         = (round(agg["visited_hospitals"] / agg["assigned_hospitals"] * 100, 1)
                                             if agg["assigned_hospitals"] else 0)
                agg["pending_hospital_names"] = [h["name"] for h in agg["hospitals"] if h["status"] == "pending"]
                history.append(agg)
            return Response({"history": history})
        elif is_mgr:
            email = rep_email
        else:
            email = request.user.email

        history = [self._build_month_summary_email(email, y, m) for y, m in month_keys]
        return Response({"history": history})
