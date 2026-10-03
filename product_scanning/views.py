import csv
import io
import logging
import uuid
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .gs1_parser_openai import GS1Parser
from .models import (
    AuditLog,
    Batch,
    ExpiredProductReport,
    GTINMaterialMapping,
    Hospital,
    Notification,
    QRTemplate,
    ScannedProduct,
    ScannedProductRemark,
    ScanningSession,
    ScanSessionRemark,
    SessionAttachment,
    TransferAttachment,
    TransferredProduct,
    TransferSession,
)
from .notification_service import (
    notify_batch_submitted,
    notify_session_closed,
    notify_session_terminated,
    notify_warehouse_remark,
)
from .rbac import can_use_dashboard, can_use_scanner, is_viewer_only
from .serializers import (
    AuditLogSerializer,
    BatchSerializer,
    GTINMaterialMappingSerializer,
    HospitalSerializer,
    NotificationSerializer,
    QRTemplateSerializer,
    ScannedProductRemarkSerializer,
    ScannedProductSerializer,
    ScanningSessionSerializer,
    ScanSessionRemarkSerializer,
    TransferAttachmentSerializer,
    TransferredProductSerializer,
    TransferSessionSerializer,
)

logger = logging.getLogger(__name__)


def log_audit(session, action, user, description="", old_status=None, new_status=None, request=None):
    """
    Helper function to log audit events for scanning sessions.
    """
    AuditLog.objects.create(
        session=session,
        action=action,
        performed_by=user,
        description=description,
        old_status=old_status,
        new_status=new_status,
        ip_address=get_client_ip(request) if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500] if request else "",
    )


def get_client_ip(request):
    """
    Get client IP address from request.
    """
    if not request:
        return None
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def ensure_batch_number(batch_number, serial_number):
    """
    Ensure batch/lot is never empty.
    Rule: when batch is missing, derive from serial by removing first 4 and last 3 chars.
    """
    batch = (batch_number or "").strip()
    if batch:
        return batch

    serial = (serial_number or "").strip()
    if len(serial) > 7:
        derived = serial[4:-3].strip()
        if derived:
            return derived

    # Fallback for short/edge serials so batch is still never blank
    return serial or "UNKNOWN-BATCH"


class HospitalViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Hospital management — full CRUD.
    Create/Update/Delete restricted to dashboard (warehouse/admin) users.
    """

    queryset = Hospital.objects.all()
    serializer_class = HospitalSerializer
    permission_classes = [IsAuthenticated]

    # ── permission guard for mutating actions ──────────────────────────────
    def _require_dashboard(self, request):
        if not can_use_dashboard(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
        if is_viewer_only(request.user):
            return Response({"error": "You have view-only access."}, status=status.HTTP_403_FORBIDDEN)
        return None

    def create(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _is_salesperson_only(self, user):
        """True if user is in PS Salesperson group but NOT in warehouse/admin groups."""
        if user.is_superuser:
            return False
        if user.groups.filter(name__in={"PS Warehouse", "Product Scanner", "Sales"}).exists():
            return False
        return user.groups.filter(name="PS Salesperson").exists()

    @action(detail=False, methods=["get"])
    def list_hospitals(self, request):
        """
        List hospitals visible to the requesting user.
        - PS Salesperson (pure): only hospitals mapped to their email via salesperson_email.
        - Product Scanner / PS Warehouse / Sales / Superuser: all hospitals.
        The mapping is maintained by the application admin (Product Scanner role)
        via the Hospital Master import/edit. 'scanned_by' always records who scanned.
        """
        user = request.user
        is_salesperson_only = (
            not user.is_superuser
            and user.groups.filter(name="PS Salesperson").exists()
            and not user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists()
        )
        if is_salesperson_only:
            hospitals = Hospital.objects.filter(salesperson_email__iexact=user.email).order_by("name")
        else:
            hospitals = Hospital.objects.all().order_by("name")
        serializer = HospitalSerializer(hospitals, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="export-template")
    def export_template(self, request):
        """
        Download the current Hospital Master as a CSV file (template for import).
        Columns: hospital_name, customer_code, province, location,
                 contact_person, phone, hospital_email, salesperson_email
        Note: hospital_name and customer_code are mandatory; salesperson_email may be blank.
        """
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        hospitals = Hospital.objects.all().order_by("name")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "hospital_name",
                "customer_code",
                "province",
                "location",
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
                    h.province or "",
                    h.location or "",
                    h.contact_person or "",
                    h.phone or "",
                    h.email or "",
                    h.salesperson_email or "",
                ]
            )
        content = output.getvalue()
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="hospital_master_template.csv"'
        return response

    @action(detail=False, methods=["post"], url_path="import-master", parser_classes=[MultiPartParser, FormParser])
    def import_master(self, request):
        """
        Import/update Hospital Master from uploaded CSV file.
        - hospital_name  → REQUIRED (cannot be blank)
        - customer_code  → REQUIRED (cannot be blank)
        - salesperson_email → optional (blank = unassigned)
        Returns a summary of created/updated/skipped/error rows.
        """
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        filename = file_obj.name.lower()
        if not (filename.endswith(".csv") or filename.endswith(".xlsx") or filename.endswith(".xls")):
            return Response(
                {"error": "Only CSV or Excel (.xlsx/.xls) files are accepted."}, status=status.HTTP_400_BAD_REQUEST
            )

        rows = []

        # ── Parse file ────────────────────────────────────────────────────
        try:
            if filename.endswith(".csv"):
                text = file_obj.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                rows = list(reader)
            else:
                try:
                    import openpyxl

                    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
                    ws = wb.active
                    headers = [
                        str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))
                    ]
                    for row in ws.iter_rows(min_row=2, values_only=True):
                        rows.append(dict(zip(headers, [str(v).strip() if v is not None else "" for v in row])))
                except ImportError:
                    return Response(
                        {"error": "openpyxl is required for Excel uploads. Use CSV instead or contact admin."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        if not rows:
            return Response({"error": "File is empty or contains no data rows."}, status=status.HTTP_400_BAD_REQUEST)

        # ── Validate headers ──────────────────────────────────────────────
        required_headers = {"hospital_name", "customer_code"}
        actual_headers = set(rows[0].keys())
        missing = required_headers - actual_headers
        if missing:
            return Response(
                {
                    "error": f'Missing required columns: {", ".join(sorted(missing))}. '
                    f"Please download the template first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_count = 0
        updated_count = 0
        skipped_rows = []
        uploaded_codes = set()  # SAP codes successfully processed from the file

        import re

        email_re = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

        for idx, row in enumerate(rows, start=2):
            row_num = idx
            name = (row.get("hospital_name") or "").strip()
            sap_code = (row.get("customer_code") or "").strip()

            # ── Mandatory field validation ──
            row_errors = []
            if not name:
                row_errors.append("hospital_name is required")
            if not sap_code:
                row_errors.append("customer_code is required")

            # ── Optional field validation ──
            sp_email = (row.get("salesperson_email") or "").strip()
            if sp_email and not email_re.match(sp_email):
                row_errors.append(f'salesperson_email "{sp_email}" is not a valid email')

            h_email = (row.get("hospital_email") or "").strip()
            if h_email and not email_re.match(h_email):
                row_errors.append(f'hospital_email "{h_email}" is not a valid email')

            if row_errors:
                skipped_rows.append({"row": row_num, "hospital_name": name or "(blank)", "reasons": row_errors})
                continue

            # ── Upsert by SAP code (preferred) or name ──
            defaults = {
                "name": name,
                "province": (row.get("province") or "").strip() or None,
                "location": (row.get("location") or "").strip() or None,
                "contact_person": (row.get("contact_person") or "").strip() or None,
                "phone": (row.get("phone") or "").strip() or None,
                "email": h_email or None,
                "salesperson_email": sp_email or None,
            }

            try:
                hospital, created = Hospital.objects.update_or_create(
                    sap_code=sap_code,
                    defaults=defaults,
                )
                uploaded_codes.add(sap_code)
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except IntegrityError as e:
                skipped_rows.append(
                    {"row": row_num, "hospital_name": name, "reasons": [f"Database conflict: {str(e)}"]}
                )

        # ── Remove hospitals that are no longer in the uploaded master ──────
        deleted_count = 0
        protected_rows = []  # hospitals with linked scan data — not safe to delete

        stale_hospitals = Hospital.objects.exclude(sap_code__in=uploaded_codes)
        for h in stale_hospitals:
            has_data = h.scanning_sessions.exists() or h.batches.exists()
            if has_data:
                protected_rows.append(
                    {
                        "hospital_name": h.name,
                        "customer_code": h.sap_code,
                        "reason": "Has linked scan sessions/batches — not deleted",
                    }
                )
            else:
                h.delete()
                deleted_count += 1

        return Response(
            {
                "success": True,
                "summary": {
                    "total_rows": len(rows),
                    "created": created_count,
                    "updated": updated_count,
                    "deleted": deleted_count,
                    "protected": len(protected_rows),
                    "skipped": len(skipped_rows),
                },
                "skipped_details": skipped_rows,
                "protected_details": protected_rows,
            }
        )


class GTINMaterialMappingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for GTIN → Material Code mapping management.
    Create/Update/Delete restricted to dashboard (warehouse/admin) users.
    """

    queryset = GTINMaterialMapping.objects.all()
    serializer_class = GTINMaterialMappingSerializer
    permission_classes = [IsAuthenticated]

    def _require_dashboard(self, request):
        if not can_use_dashboard(request.user) and not request.user.is_superuser:
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
        if is_viewer_only(request.user):
            return Response({"error": "You have view-only access."}, status=status.HTTP_403_FORBIDDEN)
        return None

    def create(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        err = self._require_dashboard(request)
        if err:
            return err
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # Column index → model field name (matches DataTables column order in template)
    _DT_COLUMNS = [
        "material_code",
        "brand",
        "product_desc",
        "material_description",
        "control_code",
        "gtin_code",
        "material_type_desc",
    ]

    @action(detail=False, methods=["get"])
    def list_mappings(self, request):
        """
        List GTIN mappings.
        Supports DataTables server-side protocol (when ?draw= is present)
        and simple ?search= / ?page= pagination otherwise.
        """
        from django.db.models import Q

        qs = GTINMaterialMapping.objects.all()
        draw = request.query_params.get("draw")

        # ── DataTables server-side protocol ───────────────────────────────
        if draw is not None:
            total = qs.count()
            search_val = (request.query_params.get("search[value]") or "").strip()
            if search_val:
                qs = qs.filter(
                    Q(gtin_code__icontains=search_val)
                    | Q(material_code__icontains=search_val)
                    | Q(brand__icontains=search_val)
                    | Q(product_desc__icontains=search_val)
                    | Q(material_description__icontains=search_val)
                    | Q(control_code__icontains=search_val)
                    | Q(material_type_desc__icontains=search_val)
                )
            try:
                col_idx = int(request.query_params.get("order[0][column]", 0))
                order_dir = request.query_params.get("order[0][dir]", "asc")
                col_name = self._DT_COLUMNS[col_idx] if 0 <= col_idx < len(self._DT_COLUMNS) else "material_code"
                if order_dir == "desc":
                    col_name = "-" + col_name
                qs = qs.order_by(col_name)
            except (ValueError, IndexError):
                qs = qs.order_by("material_code")
            filtered = qs.count()
            try:
                start = max(0, int(request.query_params.get("start", 0)))
                length = min(500, max(10, int(request.query_params.get("length", 50))))
            except (ValueError, TypeError):
                start, length = 0, 50
            serializer = GTINMaterialMappingSerializer(qs[start : start + length], many=True)
            return Response(
                {
                    "draw": int(draw),
                    "recordsTotal": total,
                    "recordsFiltered": filtered,
                    "data": serializer.data,
                }
            )

        # ── Simple paginated fallback ──────────────────────────────────────
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(gtin_code__icontains=search)
                | Q(material_code__icontains=search)
                | Q(brand__icontains=search)
                | Q(product_desc__icontains=search)
                | Q(material_description__icontains=search)
                | Q(control_code__icontains=search)
                | Q(material_type_desc__icontains=search)
            )
        qs = qs.order_by("material_code", "gtin_code")
        total = qs.count()
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(200, max(10, int(request.query_params.get("page_size", 50))))
        except (ValueError, TypeError):
            page, page_size = 1, 50
        start = (page - 1) * page_size
        serializer = GTINMaterialMappingSerializer(qs[start : start + page_size], many=True)
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size),
                "results": serializer.data,
            }
        )

    @action(detail=False, methods=["get"], url_path="export-template")
    def export_template(self, request):
        """Download current GTIN mapping as CSV template."""
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        mappings = GTINMaterialMapping.objects.all().order_by("gtin_code")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Material",
                "Brand",
                "Product Desc",
                "Material Description",
                "Control code",
                "GTIN",
                "Material type descr.",
            ]
        )
        for m in mappings:
            writer.writerow(
                [
                    m.material_code or "",
                    m.brand or "",
                    m.product_desc or "",
                    m.material_description or "",
                    m.control_code or "",
                    m.gtin_code,
                    m.material_type_desc or "",
                ]
            )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="gtin_material_mapping_template.csv"'
        return response

    # ── Column-name mapping (file header → model field) ─────────────────────
    HEADER_MAP = {
        "material": "material_code",
        "material_code": "material_code",
        "brand": "brand",
        "product desc": "product_desc",
        "product_desc": "product_desc",
        "material description": "material_description",
        "material_description": "material_description",
        "control code": "control_code",
        "control_code": "control_code",
        "gtin": "gtin_code",
        "gtin_code": "gtin_code",
        "material type descr.": "material_type_desc",
        "material type descr": "material_type_desc",
        "material_type_desc": "material_type_desc",
    }

    def _normalise_row(self, raw_row):
        """Map arbitrary file headers to model field names."""
        out = {}
        for header, value in raw_row.items():
            key = self.HEADER_MAP.get(header.strip().lower())
            if key:
                out[key] = (value or "").strip()
        return out

    @action(detail=False, methods=["post"], url_path="import-master", parser_classes=[MultiPartParser, FormParser])
    def import_master(self, request):
        """
        Import/update GTIN Material Mapping from CSV or Excel.
        Required file columns: Material, GTIN
        Optional: Brand, Product Desc, Material Description, Control code, Material type descr.
        """
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        filename = file_obj.name.lower()
        if not (filename.endswith(".csv") or filename.endswith(".xlsx") or filename.endswith(".xls")):
            return Response(
                {"error": "Only CSV or Excel (.xlsx/.xls) files are accepted."}, status=status.HTTP_400_BAD_REQUEST
            )

        rows = []
        try:
            if filename.endswith(".csv"):
                text = file_obj.read().decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(text)))
            else:
                try:
                    import openpyxl

                    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
                    ws = wb.active
                    headers = [
                        str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))
                    ]
                    for row in ws.iter_rows(min_row=2, values_only=True):
                        rows.append(dict(zip(headers, [str(v).strip() if v is not None else "" for v in row])))
                except ImportError:
                    return Response(
                        {"error": "openpyxl required for Excel. Use CSV instead."}, status=status.HTTP_400_BAD_REQUEST
                    )
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        if not rows:
            return Response({"error": "File is empty or contains no data rows."}, status=status.HTTP_400_BAD_REQUEST)

        # Normalise the first row to check required headers
        sample = self._normalise_row(rows[0])
        required = {"material_code", "gtin_code"}
        missing = required - set(sample.keys())
        if missing:
            return Response(
                {
                    "error": "Missing required columns: Material, GTIN. "
                    "Please use the template (Material, Brand, Product Desc, "
                    "Material Description, Control code, GTIN, Material type descr.)"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_count = 0
        updated_count = 0
        skipped_rows = []
        uploaded_gtins = set()  # GTINs successfully processed from the file

        for idx, raw_row in enumerate(rows, start=2):
            row = self._normalise_row(raw_row)
            gtin = row.get("gtin_code", "")
            matcode = row.get("material_code", "")

            row_errors = []
            if not gtin:
                row_errors.append("GTIN is required")
            if not matcode:
                row_errors.append("Material is required")
            if row_errors:
                skipped_rows.append({"row": idx, "gtin_code": gtin or "(blank)", "reasons": row_errors})
                continue

            try:
                defaults = {
                    "material_code": matcode,
                    "brand": row.get("brand") or None,
                    "product_desc": row.get("product_desc") or None,
                    "material_description": row.get("material_description") or None,
                    "control_code": row.get("control_code") or None,
                    "material_type_desc": row.get("material_type_desc") or None,
                    "updated_by": request.user,
                }
                _, created = GTINMaterialMapping.objects.update_or_create(
                    gtin_code=gtin,
                    defaults=defaults,
                )
                uploaded_gtins.add(gtin)
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as e:
                skipped_rows.append({"row": idx, "gtin_code": gtin, "reasons": [str(e)]})

        # ── Remove GTIN mappings that are no longer in the uploaded master ──
        deleted_qs = GTINMaterialMapping.objects.exclude(gtin_code__in=uploaded_gtins)
        deleted_count = deleted_qs.count()
        deleted_qs.delete()

        return Response(
            {
                "success": True,
                "summary": {
                    "total_rows": len(rows),
                    "created": created_count,
                    "updated": updated_count,
                    "deleted": deleted_count,
                    "skipped": len(skipped_rows),
                },
                "skipped_details": skipped_rows,
            }
        )


class BatchViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Batch management
    """

    queryset = Batch.objects.all()
    serializer_class = BatchSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["post"])
    def create_batch(self, request):
        """Create a new batch with auto-generated ID if not provided"""
        batch_id = request.data.get("batch_id")
        hospital_id = request.data.get("hospital_id")
        description = request.data.get("description", "")

        # Auto-generate batch ID if not provided
        if not batch_id:
            from datetime import datetime

            batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

        if not hospital_id:
            return Response({"error": "hospital_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            hospital = Hospital.objects.get(id=hospital_id)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            batch, created = Batch.objects.get_or_create(
                batch_id=batch_id, hospital=hospital, defaults={"created_by": request.user, "description": description}
            )
            serializer = BatchSerializer(batch)
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        except IntegrityError:
            # Race condition: batch_id already inserted by concurrent request
            try:
                batch = Batch.objects.get(batch_id=batch_id)
                serializer = BatchSerializer(batch)
                return Response(serializer.data, status=status.HTTP_200_OK)
            except Batch.DoesNotExist:
                return Response(
                    {"error": "Failed to create batch (concurrent conflict)"}, status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response({"error": f"Failed to create batch: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"])
    def list_batches(self, request):
        """List all batches for current user or hospital"""
        hospital_id = request.query_params.get("hospital_id")

        if hospital_id:
            batches = Batch.objects.filter(hospital_id=hospital_id)
        else:
            batches = Batch.objects.all()

        serializer = BatchSerializer(batches, many=True)
        return Response(serializer.data)


class ScanningSessionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Scanning Sessions
    """

    queryset = ScanningSession.objects.all()
    serializer_class = ScanningSessionSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"])
    def start_session(self, request):
        """Start a new scanning session — requires scanner access."""
        if not can_use_scanner(request.user):
            return Response(
                {"error": "You do not have permission to start scanning sessions."}, status=status.HTTP_403_FORBIDDEN
            )
        batch_id = request.data.get("batch_id")
        hospital_id = request.data.get("hospital_id")
        session_type = request.data.get("session_type", "normal")
        to_hospital_id = request.data.get("to_hospital_id")

        if not hospital_id:
            return Response({"error": "hospital_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Transfer sessions must specify a destination hospital
        if session_type == "transfer" and not to_hospital_id:
            return Response(
                {"error": "to_hospital_id is required for transfer sessions"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Auto-generate batch ID if not provided
        if not batch_id:
            from datetime import datetime

            batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

        try:
            hospital = Hospital.objects.get(id=hospital_id)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        # Resolve destination hospital for transfers
        to_hospital = None
        if session_type == "transfer" and to_hospital_id:
            try:
                to_hospital = Hospital.objects.get(id=to_hospital_id)
            except Hospital.DoesNotExist:
                return Response({"error": "Destination hospital not found"}, status=status.HTTP_404_NOT_FOUND)
            if to_hospital.id == hospital.id:
                return Response(
                    {"error": "Source and destination hospitals must be different"}, status=status.HTTP_400_BAD_REQUEST
                )

        # Create or get batch — handle race condition where two simultaneous
        # requests both miss the SELECT and both try to INSERT the same batch_id
        try:
            batch, created = Batch.objects.get_or_create(
                batch_id=batch_id, hospital=hospital, defaults={"created_by": request.user}
            )
        except IntegrityError:
            # Another request already inserted this batch_id just now; fetch it
            try:
                batch = Batch.objects.get(batch_id=batch_id)
            except Batch.DoesNotExist:
                return Response(
                    {"error": "Failed to create batch (concurrent conflict)"}, status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response({"error": f"Failed to create batch: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Auto-cancel any of this user's existing active sessions that have 0 products
        # (abandoned setup sessions that were never actually used)
        from django.db.models import Count as _Count

        abandoned = (
            ScanningSession.objects.filter(
                scanned_by=request.user,
                status="active",
            )
            .annotate(pc=_Count("scanned_products"))
            .filter(pc=0)
        )
        abandoned_count = abandoned.count()
        if abandoned_count:
            abandoned.update(status="cancelled")
            log_audit(
                session=None,
                action="terminated",
                user=request.user,
                description=f"Auto-cancelled {abandoned_count} empty active session(s) when new session was started.",
                request=request,
            )

        # Generate unique session key
        session_key = str(uuid.uuid4())

        # Create scanning session
        try:
            session = ScanningSession.objects.create(
                batch=batch,
                hospital=hospital,
                to_hospital=to_hospital,
                session_type=session_type,
                scanned_by=request.user,
                session_key=session_key,
            )

            serializer = ScanningSessionSerializer(session)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": f"Failed to create session: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"])
    def resume_check(self, request):
        """Return the most recent active session with scans for this user+hospital, if any."""
        hospital_id = request.query_params.get("hospital_id")
        if not hospital_id:
            return Response({"session": None})
        from django.db.models import Count as _Count

        now = timezone.now()
        session = (
            ScanningSession.objects.filter(
                scanned_by=request.user,
                hospital_id=hospital_id,
                status="active",
                started_at__year=now.year,
                started_at__month=now.month,
            )
            .annotate(pc=_Count("scanned_products"))
            .filter(pc__gt=0)
            .order_by("-started_at")
            .first()
        )
        if not session:
            return Response({"session": None})
        return Response(
            {
                "session": {
                    "session_key": session.session_key,
                    "batch_id": session.batch.batch_id,
                    "scans_count": session.pc,
                    "started_at": session.started_at.strftime("%d %b %Y, %H:%M"),
                }
            }
        )

    @action(detail=False, methods=["get"])
    def by_session_key(self, request):
        """Retrieve session by session_key"""
        session_key = request.query_params.get("session_key")

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
            serializer = ScanningSessionSerializer(session)
            return Response(serializer.data)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["post"])
    def complete_session(self, request):
        """Complete a scanning session — requires scanner access."""
        if not can_use_scanner(request.user):
            return Response(
                {"error": "You do not have permission to complete sessions."}, status=status.HTTP_403_FORBIDDEN
            )
        session_key = request.data.get("session_key")
        notes = request.data.get("notes", "")

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        # Atomic claim — only the first of N concurrent requests wins active → completed.
        # Without this a double-click / held Enter key re-submits the session on every POST,
        # re-firing the whole notification fan-out each time.
        old_status = session.status
        claimed = ScanningSession.objects.filter(pk=session.pk, status="active").update(
            status="completed", completed_at=timezone.now(), notes=notes
        )
        session.refresh_from_db()

        if not claimed:
            # Duplicate submit: no audit row, no notification, no completed_at overwrite.
            logger.info("complete_session: duplicate submit for %s (status=%s)", session_key, session.status)
            return Response(
                {
                    "status": session.status,
                    "duplicate": True,
                    "session": ScanningSessionSerializer(session).data,
                },
                status=status.HTTP_200_OK,
            )

        # Log the completion
        log_audit(
            session=session,
            action="session_completed",
            user=request.user,
            description=f"Session completed with notes: {notes}",
            old_status=old_status,
            new_status="completed",
            request=request,
        )

        # Fire notifications to warehouse managers (email + in-app)
        try:
            notify_batch_submitted(session, submitted_by=request.user)
        except Exception as exc:
            logger.error(f"Failed to send batch notification: {exc}")

        serializer = ScanningSessionSerializer(session)
        return Response(
            {
                "status": "completed",
                "session": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"])
    def force_complete_session(self, request):
        """Force-complete an active session — requires dashboard access.
        Allows warehouse managers to submit a session on behalf of a salesperson
        who scanned products but forgot to click 'Complete'."""
        if not can_use_dashboard(request.user) or is_viewer_only(request.user):
            return Response(
                {"error": "You do not have permission to force-complete sessions."}, status=status.HTTP_403_FORBIDDEN
            )
        session_key = request.data.get("session_key")
        notes = request.data.get("notes", "")

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        actor = request.user.get_full_name() or request.user.username
        forced_notes = f"[Force-completed by {actor}] {notes}" if notes else f"[Force-completed by {actor}]"

        # Atomic claim — the status check and the write must be one operation, otherwise two
        # concurrent clicks both pass the check and both fire the notification fan-out.
        old_status = session.status
        claimed = ScanningSession.objects.filter(pk=session.pk, status="active").update(
            status="completed", completed_at=timezone.now(), notes=forced_notes
        )
        session.refresh_from_db()

        if not claimed:
            return Response(
                {"error": f'Session is already "{session.status}" — only active sessions can be force-completed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Log the force completion
        log_audit(
            session=session,
            action="session_force_completed",
            user=request.user,
            description=f"Session force-completed with notes: {notes}",
            old_status=old_status,
            new_status="completed",
            request=request,
        )

        try:
            notify_batch_submitted(session, submitted_by=request.user)
        except Exception as exc:
            logger.error(f"Failed to send batch notification on force-complete: {exc}")

        serializer = ScanningSessionSerializer(session)
        return Response(
            {
                "status": "completed",
                "force_completed": True,
                "session": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"])
    def hold_session(self, request):
        """Put a completed (awaiting) session on hold — pauses overdue notifications."""
        if not can_use_dashboard(request.user) or is_viewer_only(request.user):
            return Response({"error": "You do not have permission to hold sessions."}, status=status.HTTP_403_FORBIDDEN)
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        if session.status != "completed":
            return Response(
                {"error": f'Only sessions awaiting confirmation can be put on hold (current: "{session.status}").'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_status = session.status
        session.mark_on_hold()
        log_audit(
            session=session,
            action="session_on_hold",
            user=request.user,
            description=f"Session put on hold by {request.user.get_full_name() or request.user.username}",
            old_status=old_status,
            new_status="on_hold",
            request=request,
        )
        serializer = ScanningSessionSerializer(session)
        return Response({"status": "on_hold", "session": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def resume_session(self, request):
        """Resume an on-hold session back to awaiting confirmation."""
        if not can_use_dashboard(request.user) or is_viewer_only(request.user):
            return Response({"error": "You do not have permission to resume sessions."}, status=status.HTTP_403_FORBIDDEN)
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        if session.status != "on_hold":
            return Response(
                {"error": f'Only on-hold sessions can be resumed (current: "{session.status}").'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_status = session.status
        session.resume_from_hold()
        log_audit(
            session=session,
            action="session_resumed",
            user=request.user,
            description=f"Session resumed from hold by {request.user.get_full_name() or request.user.username}",
            old_status=old_status,
            new_status="completed",
            request=request,
        )
        serializer = ScanningSessionSerializer(session)
        return Response({"status": "completed", "session": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request):
        """Upload confirmation document — requires dashboard access."""
        if not can_use_dashboard(request.user) or is_viewer_only(request.user):
            return Response(
                {"error": "You do not have permission to upload attachments."}, status=status.HTTP_403_FORBIDDEN
            )
        session_key = request.data.get("session_key")
        attachment_file = request.FILES.get("file")
        notes = request.data.get("notes", "")

        if not session_key or not attachment_file:
            return Response({"error": "session_key and file are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status not in ("completed", "on_hold"):
            return Response(
                {"error": f'Session must be in "completed" or "on_hold" status to upload attachment (current: {session.status})'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Delete old attachment if exists (replace)
        if hasattr(session, "attachment"):
            session.attachment.delete()

        # Read file bytes and store in database
        file_bytes = attachment_file.read()
        content_type = attachment_file.content_type or "application/octet-stream"

        SessionAttachment.objects.create(
            session=session,
            file_data=file_bytes,
            content_type=content_type,
            file_size=len(file_bytes),
            original_filename=attachment_file.name,
            uploaded_by=request.user,
            notes=notes,
        )

        # Move session to closed
        session.status = "closed"
        session.save()

        # Notify salesperson + warehouse managers
        try:
            notify_session_closed(session, closed_by=request.user)
        except Exception as exc:
            logger.error(f"Failed to send session-closed notification: {exc}")

        serializer = ScanningSessionSerializer(session)
        return Response(
            {
                "status": "closed",
                "message": "Attachment uploaded and session closed.",
                "session": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @method_decorator(xframe_options_sameorigin)
    @action(detail=False, methods=["get"])
    def download_attachment(self, request):
        """Serve an attachment file from the database."""
        session_key = request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
            attachment = session.attachment
        except (ScanningSession.DoesNotExist, SessionAttachment.DoesNotExist):
            return Response({"error": "Attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        response = HttpResponse(bytes(attachment.file_data), content_type=attachment.content_type)
        # For inline preview (PDF / images) use inline disposition; browser will display it
        response["Content-Disposition"] = f'inline; filename="{attachment.original_filename}"'
        response["Content-Length"] = attachment.file_size
        return response

    @action(detail=False, methods=["post"])
    def terminate_session(self, request):
        """
        Terminate/cancel an active session.
        Requires superuser or product scanner role with dashboard access.
        """
        # Check permissions - superuser or dashboard access required
        if not (request.user.is_superuser or can_use_dashboard(request.user)) or is_viewer_only(request.user):
            return Response(
                {"error": "You do not have permission to terminate sessions."}, status=status.HTTP_403_FORBIDDEN
            )

        session_key = request.data.get("session_key")
        reason = request.data.get("reason", "No reason provided")

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        if session.status not in ("active", "completed"):
            return Response(
                {
                    "error": f'Cannot terminate session with status "{session.status}". Only active or completed sessions can be terminated.'
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = session.status
        session.status = "cancelled"
        session.completed_at = timezone.now()
        session.notes = f"[Terminated by {request.user.get_full_name() or request.user.username}] Reason: {reason}"
        session.save()

        # Log the action
        log_audit(
            session=session,
            action="session_terminated",
            user=request.user,
            description=f"Session terminated with reason: {reason}",
            old_status=old_status,
            new_status="cancelled",
            request=request,
        )

        logger.info(f"Session {session_key} terminated by {request.user.username}: {reason}")

        # Notify warehouse managers about the termination
        try:
            notify_session_terminated(session, terminated_by=request.user, reason=reason)
        except Exception as exc:
            logger.error(f"Failed to send termination notification: {exc}")

        serializer = ScanningSessionSerializer(session)
        return Response(
            {
                "status": "cancelled",
                "message": "Session has been terminated.",
                "session": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"])
    def reject_session(self, request):
        """
        Reject a completed session and move it back to active status.
        Requires superuser or product scanner role with dashboard access.
        """
        # Check permissions - superuser or dashboard access required
        if not (request.user.is_superuser or can_use_dashboard(request.user)) or is_viewer_only(request.user):
            return Response(
                {"error": "You do not have permission to reject sessions."}, status=status.HTTP_403_FORBIDDEN
            )

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
                {"error": f'Can only reject completed sessions. Current status: "{session.status}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Remove attachment if exists (so scanner can resubmit)
        if hasattr(session, "attachment") and session.attachment:
            session.attachment.delete()

        old_status = session.status
        session.status = "active"
        session.completed_at = None
        session.notes = (
            f"[Rejected by {request.user.get_full_name() or request.user.username}] Reason: {reason}. Please resubmit."
        )
        session.save()

        # Log the action
        log_audit(
            session=session,
            action="session_rejected",
            user=request.user,
            description=f"Session rejected with reason: {reason}",
            old_status=old_status,
            new_status="active",
            request=request,
        )

        logger.info(f"Session {session_key} rejected by {request.user.username}: {reason}")

        serializer = ScanningSessionSerializer(session)
        return Response(
            {
                "status": "active",
                "message": "Session has been rejected and moved back to active status.",
                "session": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def my_batches(self, request):
        """
        Return all scanning sessions for the current user as a batch status tracker.
        Each entry has a visual status: active → submitted (completed) → confirmed (closed).
        Warehouse/admin can pass ?sp_email= to view another user's batches.
        """
        user = request.user
        sp_email = request.query_params.get("sp_email", "").strip()

        if user.is_superuser or user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists():
            if sp_email:
                from django.contrib.auth.models import User as AuthUser

                try:
                    target_user = AuthUser.objects.get(email__iexact=sp_email)
                    qs = ScanningSession.objects.filter(scanned_by=target_user)
                except AuthUser.DoesNotExist:
                    qs = ScanningSession.objects.none()
            else:
                qs = ScanningSession.objects.all()
        else:
            qs = ScanningSession.objects.filter(scanned_by=user)

        # Exclude cancelled sessions and sessions with zero scans — they add no value to the tracker
        qs = (
            qs.exclude(status="cancelled")
            .annotate(scans_total=models.Count("scanned_products"))
            .filter(scans_total__gt=0)
            .select_related("hospital", "batch", "scanned_by")
            .order_by("-started_at")
        )

        results = []
        STATUS_STEPS = {
            "active": {"step": 1, "label": "Scanning"},
            "completed": {"step": 2, "label": "Submitted"},
            "closed": {"step": 3, "label": "Confirmed"},
        }
        for s in qs:
            step_info = STATUS_STEPS.get(s.status, {"step": 1, "label": s.status.title()})
            session_remarks = s.session_remarks.all()
            total_session_remarks = session_remarks.count()
            has_warehouse_remark = session_remarks.filter(role="warehouse").exists()
            # Product-level remarks for this session
            total_product_remarks = ScannedProductRemark.objects.filter(scanned_product__scanning_session=s).count()
            has_product_wh_remark = ScannedProductRemark.objects.filter(
                scanned_product__scanning_session=s, role="warehouse"
            ).exists()
            results.append(
                {
                    "id": s.id,
                    "session_key": s.session_key,
                    "batch_id": s.batch.batch_id if s.batch else None,
                    "hospital_name": s.hospital.name if s.hospital else None,
                    "hospital_sap_code": s.hospital.sap_code if s.hospital else None,
                    "session_type": s.session_type,
                    "status": s.status,
                    "status_step": step_info["step"],
                    "status_label": step_info["label"],
                    "scans_count": s.scanned_products.count(),
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                    "scanned_by": (s.scanned_by.get_full_name() or s.scanned_by.username) if s.scanned_by else None,
                    "detail_url": f"/product_scanning/sessions/{s.session_key}/",
                    # Remark flags
                    "has_warehouse_remark": has_warehouse_remark or has_product_wh_remark,
                    "total_remarks": total_session_remarks + total_product_remarks,
                }
            )

        return Response({"results": results, "count": len(results)})

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request):
        """
        Server-side streaming CSV export of all scanned products within a date range.
        Params: date_from (YYYY-MM-DD), date_to (YYYY-MM-DD) — max range 62 days.
        Access: dashboard / warehouse / admin users only.
        """
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        date_from_str = request.query_params.get("date_from", "").strip()
        date_to_str = request.query_params.get("date_to", "").strip()

        if not date_from_str or not date_to_str:
            return Response(
                {"error": "date_from and date_to are required (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from."}, status=status.HTTP_400_BAD_REQUEST)

        if (date_to - date_from).days > 62:
            return Response(
                {"error": "Date range must not exceed 62 days (2 months)."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Query products scanned in range — use select_related to avoid N+1
        from .models import ScannedProduct as SP

        products = (
            SP.objects.filter(scanned_at__date__gte=date_from, scanned_at__date__lte=date_to)
            .select_related("scanning_session__hospital", "scanning_session__batch", "scanning_session__scanned_by")
            .order_by("scanned_at")
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Serial Number",
                "GTIN Code",
                "Lot/Batch No.",
                "Product Name",
                "Material Code",
                "Session ID",
                "Session Type",
                "Hospital",
                "SAP Code",
                "Expiry Date",
                "Prod. Date",
                "Quantity",
                "Scanned Date",
                "Sales Rep",
            ]
        )
        for p in products:
            sess = p.scanning_session
            hospital = sess.hospital if sess else None
            batch = sess.batch if sess else None
            rep = sess.scanned_by if sess else None
            rep_name = (rep.get_full_name() or rep.username) if rep else ""
            sn = p.serial_number if (p.serial_number and not p.serial_number.startswith("NSN-")) else ""
            writer.writerow(
                [
                    sn,
                    p.gtin_code or "",
                    p.batch_number or "",
                    p.product_name or "",
                    p.material_code or "",
                    batch.batch_id if batch else "",
                    sess.session_type if sess else "",
                    hospital.name if hospital else "",
                    hospital.sap_code if hospital else "",
                    p.expiry_date.isoformat() if p.expiry_date else "",
                    p.production_date.isoformat() if p.production_date else "",
                    p.quantity or 1,
                    p.scanned_at.strftime("%Y-%m-%d") if p.scanned_at else "",
                    rep_name,
                ]
            )

        content = output.getvalue()
        filename = f"Meril_StockAudit_{date_from_str}_to_{date_to_str}.csv"
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="export-expired-csv")
    def export_expired_csv(self, request):
        """
        Server-side streaming CSV export of expired / near-expiry products within a date range.
        Params: date_from (YYYY-MM-DD), date_to (YYYY-MM-DD) — max range 62 days.
        Filters on scanned_at date, not expiry_date, so the range means "scanned during this period".
        """
        if (not can_use_dashboard(request.user) and not request.user.is_superuser) or is_viewer_only(request.user):
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        date_from_str = request.query_params.get("date_from", "").strip()
        date_to_str = request.query_params.get("date_to", "").strip()

        if not date_from_str or not date_to_str:
            return Response(
                {"error": "date_from and date_to are required (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from."}, status=status.HTTP_400_BAD_REQUEST)

        if (date_to - date_from).days > 62:
            return Response(
                {"error": "Date range must not exceed 62 days (2 months)."}, status=status.HTTP_400_BAD_REQUEST
            )

        today = timezone.now().date()
        threshold = today + timedelta(days=180)  # include products expiring within 6 months

        from .models import ScannedProduct as SP

        products = (
            SP.objects.filter(
                scanned_at__date__gte=date_from,
                scanned_at__date__lte=date_to,
                expiry_date__isnull=False,
                expiry_date__lte=threshold,
            )
            .select_related("scanning_session__hospital", "scanning_session__batch", "scanning_session__scanned_by")
            .order_by("expiry_date")
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Serial Number",
                "Product Name",
                "GTIN",
                "Lot/Batch",
                "Hospital",
                "SAP Code",
                "Expiry Date",
                "Days to Expiry",
                "Session ID",
                "Scanned By",
                "Scanned At",
                "Acknowledged",
            ]
        )
        for p in products:
            sess = p.scanning_session
            hospital = sess.hospital if sess else None
            batch = sess.batch if sess else None
            rep = sess.scanned_by if sess else None
            rep_name = (rep.get_full_name() or rep.username) if rep else ""
            sn = p.serial_number if (p.serial_number and not p.serial_number.startswith("NSN-")) else ""
            days = (p.expiry_date - today).days if p.expiry_date else ""
            writer.writerow(
                [
                    sn,
                    p.product_name or "",
                    p.gtin_code or "",
                    p.batch_number or "",
                    hospital.name if hospital else "",
                    hospital.sap_code if hospital else "",
                    p.expiry_date.isoformat() if p.expiry_date else "",
                    days,
                    batch.batch_id if batch else "",
                    rep_name,
                    p.scanned_at.strftime("%Y-%m-%d") if p.scanned_at else "",
                    "Yes" if getattr(p, "acknowledged", False) else "No",
                ]
            )

        content = output.getvalue()
        filename = f"Meril_ExpiredProducts_{date_from_str}_to_{date_to_str}.csv"
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ScannedProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Scanned Products
    """

    queryset = ScannedProduct.objects.all()
    serializer_class = ScannedProductSerializer
    permission_classes = [IsAuthenticated]

    def _parse_qr_string(self, qr_data):
        """Parse QR data in format: SERIAL:SN001|MATCODE:MC001|NAME:Product|GTIN:1234567890"""
        parsed = {}
        try:
            parts = qr_data.split("|")
            for part in parts:
                if ":" in part:
                    key, value = part.split(":", 1)
                    key = key.strip().lower()
                    value = value.strip()

                    # Map common key variations
                    if key in ["serial", "serial_number", "sn"]:
                        parsed["serial_number"] = value
                    elif key in ["matcode", "material_code", "mc"]:
                        parsed["material_code"] = value
                    elif key in ["name", "product_name", "pname"]:
                        parsed["product_name"] = value
                    elif key in ["batch", "batch_number", "bn"]:
                        parsed["batch_number"] = value
                    elif key in ["gtin", "ean", "ean13", "gtin_code"]:
                        parsed["gtin_code"] = value
                    elif key in ["expiry", "expiry_date", "exp"]:
                        parsed["expiry_date"] = value
                    elif key in ["received", "received_date", "rec"]:
                        parsed["received_date"] = value
                    elif key in ["qty", "quantity", "q"]:
                        try:
                            parsed["quantity"] = int(value)
                        except ValueError:
                            pass
        except Exception:
            pass

        return parsed

    def _parse_date(self, date_string):
        """Parse date from various formats"""
        if not date_string:
            return None

        formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]
        for fmt in formats:
            try:
                return datetime.strptime(date_string, fmt).date()
            except ValueError:
                continue

        return None

    def _check_duplicate_scan(
        self, session, serial_number, material_code=None, gtin_code=None, batch_number=None, expiry_date=None
    ):
        """
        Check if the same product was scanned in the SAME SESSION using a
        COMPOSITE key of whatever identity is captured among
        {serial number, material code, GTIN}.

        - A "real" serial ignores NSN- placeholders (no-serial items).
        - With a real serial: match on serial + material + GTIN (whichever present),
          so the same serial under a *different* material/GTIN is not a duplicate
          (medical-device serials are scoped to a GTIN).
        - Without a real serial: match on material + GTIN, plus batch + expiry when
          present, so different lots stay distinct — and only against other no-serial
          rows, never against serialized ones.
        - If neither a real serial nor a material code is captured (e.g. a bare GTIN
          or nothing), there is not enough identity to dedupe → allow the scan.
        """
        real_serial = serial_number if (serial_number and not serial_number.startswith("NSN-")) else None
        mc = (material_code or "").strip() or None
        gt = (gtin_code or "").strip() or None

        # Not enough identity to dedupe: neither a real serial nor a material code.
        if real_serial is None and mc is None:
            return False, None

        qs = ScannedProduct.objects.filter(scanning_session=session)
        key = []
        if real_serial is not None:
            qs = qs.filter(serial_number=real_serial)
            key.append(f"Serial {real_serial}")
        else:
            # A no-serial scan only collides with other no-serial rows.
            qs = qs.filter(serial_number__startswith="NSN-")
        if mc is not None:
            qs = qs.filter(material_code=mc)
            key.append(f"Material {mc}")
        if gt is not None:
            qs = qs.filter(gtin_code=gt)
            key.append(f"GTIN {gt}")
        # Keep lots distinct for no-serial items.
        if real_serial is None:
            if batch_number:
                qs = qs.filter(batch_number=batch_number)
                key.append(f"Batch {batch_number}")
            if expiry_date:
                qs = qs.filter(expiry_date=expiry_date)

        if qs.exists():
            logger.info(f"Duplicate product in session {session.session_key}: {', '.join(key)}")
            return True, f"This product ({', '.join(key)}) was already scanned in this session."
        return False, None

    def destroy(self, request, *args, **kwargs):
        """
        Delete a scanned product from the DB so it can be re-scanned without
        triggering the duplicate-check error.
        Only allowed while the session is still active.
        """
        product = self.get_object()
        session = product.scanning_session

        # Only the owner (or superuser) can delete
        if not request.user.is_superuser and session.scanned_by != request.user:
            return Response(
                {"error": "You can only delete products from your own sessions."}, status=status.HTTP_403_FORBIDDEN
            )

        # Disallow deletion from completed/closed sessions
        if session.status in ("completed", "closed"):
            return Response(
                {"error": "Cannot delete products from a completed or closed session."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    def scan_product(self, request):
        """
        Process QR scan for a product — requires scanner access.
        """
        if not can_use_scanner(request.user):
            return Response({"error": "You do not have permission to scan products."}, status=status.HTTP_403_FORBIDDEN)
        session_key = request.data.get("session_key")
        qr_data = request.data.get("qr_data")

        if not session_key or not qr_data:
            return Response({"error": "session_key and qr_data are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        # Parse QR data using GS1 standard parser
        try:
            logger.info(f"Parsing QR data: {qr_data}")

            # GS1 rule-based parse first; OpenAI fills any missing important fields
            qr_parsed = GS1Parser.parse(qr_data, use_openai=True)

            logger.info(f"Parsed data: {qr_parsed}")

            # Check if parsing had errors
            if "error" in qr_parsed and not any(k in qr_parsed for k in ["serial_number", "gtin_code", "batch_number"]):
                logger.warning(f"Parsing error: {qr_parsed.get('error')}")
                # Still continue with whatever data we have

        except Exception as e:
            logger.error(f"Exception during GS1 parsing: {str(e)}")
            # Fallback to regex parsing
            qr_parsed = self._parse_qr_string(qr_data)

        # Serial number — explicit override from client 2-scan workflow takes highest priority
        # (frontend sends 'serial_number' when it captures SN from a separate barcode first)
        serial_override = request.data.get("serial_number")
        no_serial = request.data.get("no_serial", False)  # user confirmed: no SN exists
        # Batch/lot override — sent when salesperson edits lot in queue UI before submitting
        batch_override = (request.data.get("batch_number") or "").strip() or None
        serial_number = serial_override or qr_parsed.get("serial_number")

        if serial_override:
            logger.info(f"Using client-provided serial override: {serial_override}")
        if batch_override:
            logger.info(f"Using client-provided batch override: {batch_override}")

        # Guard: a truncated / over-captured client serial override must not silently
        # replace a serial the parser cleanly extracted from THIS same barcode. The
        # 2-scan override is meant for products whose QR has no serial; when the QR
        # *does* encode one, the parsed value is authoritative. Without this, partial
        # captures (e.g. "CM16EFF9" instead of "CM16EFF93037") or whole-GS1-string
        # captures get stored as the serial — and batch_number is then mis-derived
        # from the broken serial (e.g. "E"/"EF").
        parsed_serial = (qr_parsed.get("serial_number") or "").strip()
        if serial_override and parsed_serial:
            so = serial_override.strip()
            if so != parsed_serial and (
                parsed_serial.startswith(so) or so.startswith(parsed_serial) or parsed_serial in so
            ):
                logger.warning(
                    "Client serial override %r disagrees with parsed serial %r from the "
                    "same barcode (looks truncated/over-captured); using parsed value.",
                    so,
                    parsed_serial,
                )
                serial_number = parsed_serial

        if not serial_number and not no_serial:
            # Ask the frontend to show the "Does this product have a serial number?" dialog
            return Response(
                {
                    "error": "Serial number not found in this barcode.",
                    "error_code": "SERIAL_REQUIRED",
                    "parsed_fields": qr_parsed,  # Return what we found so the dialog can show it
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if not serial_number and no_serial:
            # User confirmed: this product genuinely has no serial number
            # Generate a unique internal placeholder (never shown to user as a meaningful SN)
            serial_number = f"NSN-{str(uuid.uuid4()).replace('-', '')[:14].upper()}"
            logger.info(f"No-serial placeholder generated: {serial_number}")

        # Resolve batch, and auto-fill material_code/product_name from the GTIN
        # mapping BEFORE the duplicate check so they can participate in the key.
        resolved_batch_number = ensure_batch_number(batch_override or qr_parsed.get("batch_number"), serial_number)
        material_code = qr_parsed.get("material_code")
        product_name = qr_parsed.get("product_name")
        gtin_from_scan = qr_parsed.get("gtin_code")
        if gtin_from_scan and (not material_code or not product_name):
            try:
                gtin_map = GTINMaterialMapping.objects.get(gtin_code=gtin_from_scan)
                if not material_code:
                    material_code = gtin_map.material_code
                if not product_name:
                    product_name = (
                        gtin_map.product_name
                        or gtin_map.material_description
                        or gtin_map.product_desc
                    )
                logger.info(f"GTIN mapping applied: {gtin_from_scan} → {material_code}")
            except GTINMaterialMapping.DoesNotExist:
                pass

        # Composite duplicate check (serial + material + GTIN; batch/expiry for no-serial)
        is_duplicate, error_msg = self._check_duplicate_scan(
            session,
            serial_number,
            material_code=material_code,
            gtin_code=gtin_from_scan,
            batch_number=resolved_batch_number,
            expiry_date=self._parse_date(qr_parsed.get("expiry_date")),
        )
        if is_duplicate:
            return Response({"error": error_msg, "error_code": "DUPLICATE_SERIAL"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Create scanned product with all extracted data
            reference = qr_parsed.get("additional_id", "") or qr_parsed.get("secondary_data", "") or ""
            product = ScannedProduct.objects.create(
                scanning_session=session,
                serial_number=serial_number,
                gtin_code=gtin_from_scan,
                material_code=material_code,
                product_name=product_name,
                batch_number=resolved_batch_number,
                received_date=self._parse_date(qr_parsed.get("received_date")),
                expiry_date=self._parse_date(qr_parsed.get("expiry_date")),
                production_date=self._parse_date(qr_parsed.get("production_date")),
                best_before_date=self._parse_date(qr_parsed.get("best_before_date")),
                packaging_date=self._parse_date(qr_parsed.get("packaging_date")),
                reference_number=reference,
                quantity=qr_parsed.get("quantity", 1),
                qr_data=qr_data,  # Store original raw data
                gs1_parsed_data=qr_parsed,  # Store full parsed GS1 fields
            )

            # Update session's last scan time
            session.last_scan_time = timezone.now()
            session.save()

            # Log successful extraction
            logger.info(
                f"Successfully created product: {serial_number} with GTIN: {product.gtin_code} Expiry: {product.expiry_date}"
            )

            serializer = ScannedProductSerializer(product)
            # Build a complete parsed_fields dict: start with everything GS1 returned,
            # then overlay the final resolved values for the key fields.
            all_parsed = {k: v for k, v in qr_parsed.items() if v not in (None, "", [])}
            all_parsed.update(
                {
                    "serial_number": serial_number,
                    "gtin_code": gtin_from_scan,
                    "batch_number": resolved_batch_number,
                    "product_name": product_name,
                    "material_code": material_code,
                }
            )
            # Remove internal parser artefacts before sending to client
            all_parsed.pop("error", None)
            all_parsed.pop("raw_data", None)
            return Response(
                {
                    "status": "success",
                    "message": "Product scanned and parsed successfully",
                    "data": serializer.data,
                    "parsed_fields": all_parsed,
                },
                status=status.HTTP_201_CREATED,
            )

        except IntegrityError:
            # Composite unique_together (session, serial, gtin, material) violated by a
            # concurrent scan — the app-level check above lost a race.
            logger.warning(f"Duplicate product in session {session.session_key}: {serial_number}")
            return Response(
                {
                    "error": "This product was already scanned in this session.",
                    "error_code": "DUPLICATE_SERIAL",
                    "serial_number": serial_number,
                },
                status=status.HTTP_409_CONFLICT,
            )

        except Exception as e:
            logger.error(f"Error creating product: {str(e)}")
            return Response(
                {"error": f"Error scanning product: {str(e)}", "parsed_data": qr_parsed},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=False, methods=["get"])
    def session_products(self, request):
        """Get all products for a session"""
        session_key = request.query_params.get("session_key")

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ScanningSession.objects.get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        products = session.scanned_products.all()
        serializer = ScannedProductSerializer(products, many=True)
        data = [dict(p) for p in serializer.data]

        # Fill in missing product_name (and material_code) from GTIN mapping
        missing = {p["gtin_code"] for p in data if not p.get("product_name") and p.get("gtin_code")}
        if missing:
            gtin_lookup = {
                m.gtin_code: m
                for m in GTINMaterialMapping.objects.filter(gtin_code__in=missing)
            }
            for p in data:
                if not p.get("product_name") and p.get("gtin_code"):
                    gm = gtin_lookup.get(p["gtin_code"])
                    if gm:
                        p["product_name"] = (
                            gm.product_name or gm.material_description or gm.product_desc or ""
                        )
                        if not p.get("material_code"):
                            p["material_code"] = gm.material_code or ""

        return Response(data)

    @action(detail=False, methods=["get"])
    def my_scans(self, request):
        """
        Return all scanned products from sessions owned by the current user.
        Salesperson sees only their own data; warehouse/admin can pass ?sp_email=
        to view a specific salesperson's data.
        """
        user = request.user
        sp_email = request.query_params.get("sp_email", "").strip()

        if user.is_superuser or user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists():
            if sp_email:
                from django.contrib.auth.models import User as AuthUser

                try:
                    target_user = AuthUser.objects.get(email__iexact=sp_email)
                    sessions_qs = ScanningSession.objects.filter(scanned_by=target_user)
                except AuthUser.DoesNotExist:
                    sessions_qs = ScanningSession.objects.none()
            else:
                sessions_qs = ScanningSession.objects.all()
        else:
            sessions_qs = ScanningSession.objects.filter(scanned_by=user)

        today = timezone.now().date()
        products = (
            ScannedProduct.objects.filter(scanning_session__in=sessions_qs)
            .select_related(
                "scanning_session__hospital",
                "scanning_session__batch",
                "scanning_session__scanned_by",
            )
            .order_by("-scanned_at")
        )

        results = []
        for p in products:
            sess = p.scanning_session
            days = (p.expiry_date - today).days if p.expiry_date else None
            if days is None:
                expiry_level = "ok"
            elif days < 0:
                expiry_level = "expired"
            elif days <= 7:
                expiry_level = "1w"
            elif days <= 30:
                expiry_level = "1m"
            elif days <= 90:
                expiry_level = "3m"
            elif days <= 180:
                expiry_level = "6m"
            else:
                expiry_level = "ok"

            sn = p.serial_number or ""
            results.append(
                {
                    "id": p.id,
                    "serial_number": "" if sn.startswith("NSN-") else sn,
                    "gtin_code": p.gtin_code,
                    "product_name": p.product_name,
                    "batch_number": p.batch_number,
                    "material_code": p.material_code,
                    "expiry_date": str(p.expiry_date) if p.expiry_date else None,
                    "days_to_expiry": days,
                    "expiry_level": expiry_level,
                    "quantity": p.quantity,
                    "scanned_at": p.scanned_at.isoformat() if p.scanned_at else None,
                    "hospital_name": sess.hospital.name if sess.hospital else None,
                    "hospital_sap_code": sess.hospital.sap_code if sess.hospital else None,
                    "batch_id": sess.batch.batch_id if sess.batch else None,
                    "session_key": sess.session_key,
                    "session_status": sess.status,
                    "scanned_by": (
                        (sess.scanned_by.get_full_name() or sess.scanned_by.username) if sess.scanned_by else None
                    ),
                }
            )

        return Response({"results": results, "count": len(results)})

    @action(detail=False, methods=["get"])
    def export_my_scans_excel(self, request):
        """
        Download the current user's scanned products as an Excel (.xlsx) file.
        Supports ?sp_email= for warehouse/admin users.
        """
        if is_viewer_only(request.user):
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        # Reuse the same queryset logic as my_scans
        user = request.user
        sp_email = request.query_params.get("sp_email", "").strip()

        if user.is_superuser or user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists():
            if sp_email:
                from django.contrib.auth.models import User as AuthUser

                try:
                    target_user = AuthUser.objects.get(email__iexact=sp_email)
                    sessions_qs = ScanningSession.objects.filter(scanned_by=target_user)
                    sp_name = target_user.get_full_name() or target_user.username
                except AuthUser.DoesNotExist:
                    sessions_qs = ScanningSession.objects.none()
                    sp_name = sp_email
            else:
                sessions_qs = ScanningSession.objects.all()
                sp_name = "All"
        else:
            sessions_qs = ScanningSession.objects.filter(scanned_by=user)
            sp_name = user.get_full_name() or user.username

        today = timezone.now().date()
        products = (
            ScannedProduct.objects.filter(scanning_session__in=sessions_qs)
            .select_related(
                "scanning_session__hospital",
                "scanning_session__batch",
                "scanning_session__scanned_by",
            )
            .order_by("-scanned_at")
        )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Scanned Products"

        # Header row
        headers = [
            "Serial Number",
            "GTIN",
            "Product Name",
            "Material Code",
            "Lot / Batch",
            "Expiry Date",
            "Days to Expiry",
            "Expiry Status",
            "Quantity",
            "Hospital",
            "SAP Code",
            "Batch ID",
            "Session Status",
            "Scanned By",
            "Scanned At",
        ]
        hdr_fill = PatternFill("solid", fgColor="0771F4")
        hdr_font = Font(bold=True, color="FFFFFF", size=11)
        thin = Side(border_style="thin", color="D1D5DB")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_idx, hdr in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=hdr)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
        ws.row_dimensions[1].height = 28

        # Expiry level fill colours (matching meritrack)
        LEVEL_FILLS = {
            "expired": PatternFill("solid", fgColor="FEE2E2"),
            "1w": PatternFill("solid", fgColor="FECDD3"),
            "1m": PatternFill("solid", fgColor="FEF3C7"),
            "3m": PatternFill("solid", fgColor="FEF9C3"),
            "6m": PatternFill("solid", fgColor="DCFCE7"),
            "ok": None,
        }
        LEVEL_LABELS = {
            "expired": "Expired",
            "1w": "≤ 1 Week",
            "1m": "≤ 1 Month",
            "3m": "≤ 3 Months",
            "6m": "≤ 6 Months",
            "ok": "OK",
        }

        for row_idx, p in enumerate(products, 2):
            sess = p.scanning_session
            sn = p.serial_number or ""
            if sn.startswith("NSN-"):
                sn = ""
            days = (p.expiry_date - today).days if p.expiry_date else None
            if days is None:
                level = "ok"
            elif days < 0:
                level = "expired"
            elif days <= 7:
                level = "1w"
            elif days <= 30:
                level = "1m"
            elif days <= 90:
                level = "3m"
            elif days <= 180:
                level = "6m"
            else:
                level = "ok"

            row_data = [
                sn,
                p.gtin_code or "",
                p.product_name or "",
                p.material_code or "",
                p.batch_number or "",
                str(p.expiry_date) if p.expiry_date else "",
                days if days is not None else "",
                LEVEL_LABELS.get(level, ""),
                p.quantity or 1,
                sess.hospital.name if sess.hospital else "",
                sess.hospital.sap_code if sess.hospital else "",
                sess.batch.batch_id if sess.batch else "",
                sess.get_status_display() if hasattr(sess, "get_status_display") else sess.status,
                (sess.scanned_by.get_full_name() or sess.scanned_by.username) if sess.scanned_by else "",
                p.scanned_at.strftime("%Y-%m-%d %H:%M") if p.scanned_at else "",
            ]
            fill = LEVEL_FILLS.get(level)
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = border
                cell.alignment = Alignment(vertical="center")
                if fill:
                    cell.fill = fill

        # Column widths
        col_widths = [20, 16, 28, 16, 16, 14, 14, 14, 10, 28, 12, 18, 14, 20, 18]
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A2"

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_name = sp_name.replace(" ", "_")
        filename = f"scanned_products_{safe_name}_{today}.xlsx"
        response = HttpResponse(
            buf.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class QRTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for QR Templates (read-only)
    """

    queryset = QRTemplate.objects.all()
    serializer_class = QRTemplateSerializer
    permission_classes = [IsAuthenticated]


class NotificationViewSet(viewsets.GenericViewSet):
    """
    API for the in-app notification bell.

    GET  /api/notifications/         → list for current user (unread first)
    GET  /api/notifications/unread_count/ → {"count": N}
    POST /api/notifications/mark_read/    → mark one or all as read
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    def list(self, request):
        qs = self.get_queryset().order_by("is_read", "-created_at")[:50]
        serializer = NotificationSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"count": count})

    @action(detail=False, methods=["post"], url_path="mark_read")
    def mark_read(self, request):
        """
        Body: {"id": <pk>}  → mark single notification read
              {}             → mark all read
        """
        notif_id = request.data.get("id")
        if notif_id:
            self.get_queryset().filter(pk=notif_id).update(is_read=True)
        else:
            self.get_queryset().update(is_read=True)
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"status": "ok", "unread_count": count})


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG VIEWSET
# ─────────────────────────────────────────────────────────────────────────────
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for audit logs — dashboard users and superusers only."""

    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not can_use_dashboard(self.request.user) and not self.request.user.is_superuser:
            return AuditLog.objects.none()
        qs = AuditLog.objects.select_related("session", "session__hospital", "performed_by").all()
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
# EXPIRED PRODUCT REPORT VIEWSET
# ─────────────────────────────────────────────────────────────────────────────
class ExpiredProductViewSet(viewsets.ViewSet):
    """
    ViewSet for viewing expired products from active/completed/closed sessions.

    Directly queries ScannedProduct records where expiry_date < today
    from sessions that are ongoing, awaiting confirmation, or closed.

    GET  /api/expired/                  → list all expired products
    GET  /api/expired/summary/          → stats summary
    POST /api/expired/acknowledge/      → acknowledge an expired product (future use)
    """

    permission_classes = [IsAuthenticated]
    eligible_session_statuses = ("active", "completed", "closed")

    def _expired_products_queryset(self):
        """Base queryset for expired/near-expiry products visible on the stock audit dashboard."""
        today = timezone.now().date()
        threshold = today + timedelta(days=180)
        return ScannedProduct.objects.filter(
            scanning_session__status__in=self.eligible_session_statuses,
            expiry_date__isnull=False,
            expiry_date__lte=threshold,
        )

    def list(self, request):
        """
        List expired and near-expiry products from ongoing, awaiting, and closed sessions.
        Each product includes an expiry_level: expired / 1w / 1m / 3m / 6m.
        """
        today = timezone.now().date()

        expired_qs = (
            self._expired_products_queryset()
            .select_related(
                "scanning_session__hospital",
                "scanning_session__batch",
                "scanning_session__scanned_by",
                "expired_report",
            )
            .order_by("expiry_date")
        )

        # Build a GTIN → name lookup for products whose stored product_name is empty,
        # so names added to the GTIN master after the scan are still shown.
        gtins_needing_lookup = {
            p.gtin_code
            for p in expired_qs
            if not p.product_name and p.gtin_code
        }
        gtin_to_name = {}
        if gtins_needing_lookup:
            for m in GTINMaterialMapping.objects.filter(gtin_code__in=gtins_needing_lookup):
                gtin_to_name[m.gtin_code] = (
                    m.product_name
                    or m.material_description
                    or m.product_desc
                    or m.brand
                    or ""
                )

        results = []
        for product in expired_qs:
            session = product.scanning_session
            days = (product.expiry_date - today).days

            if days < 0:
                level = "expired"
            elif days <= 7:
                level = "1w"
            elif days <= 30:
                level = "1m"
            elif days <= 90:
                level = "3m"
            else:
                level = "6m"

            user = session.scanned_by
            scanned_by_name = (f"{user.first_name} {user.last_name}".strip() or user.username) if user else "Unknown"

            results.append(
                {
                    "id": product.id,
                    "serial_number": product.serial_number,
                    "gtin_code": product.gtin_code,
                    "product_name": product.product_name or gtin_to_name.get(product.gtin_code or "", None),
                    "batch_number": product.batch_number,
                    "material_code": product.material_code,
                    "expiry_date": str(product.expiry_date),
                    "days_to_expiry": days,
                    "expiry_level": level,
                    "hospital": session.hospital_id,
                    "hospital_name": session.hospital.name if session.hospital else None,
                    "hospital_sap_code": session.hospital.sap_code if session.hospital else None,
                    "session_batch_id": session.batch.batch_id if session.batch else None,
                    "session_key": session.session_key,
                    "scanned_by": session.scanned_by_id,
                    "scanned_by_name": scanned_by_name,
                    "scanned_at": product.scanned_at.isoformat() if product.scanned_at else None,
                    "acknowledged": getattr(getattr(product, "expired_report", None), "acknowledged", False),
                }
            )

        return Response({"results": results, "count": len(results)})

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Return summary stats for expired products."""

        expired_qs = self._expired_products_queryset().select_related("scanning_session__hospital")

        total = expired_qs.count()

        # Group by hospital
        from django.db.models import Count

        by_hospital = list(
            expired_qs.values("scanning_session__hospital__name", "scanning_session__hospital__sap_code")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Rename keys for cleaner response
        by_hospital_clean = [
            {
                "hospital_name": item["scanning_session__hospital__name"],
                "hospital_sap_code": item["scanning_session__hospital__sap_code"],
                "count": item["count"],
            }
            for item in by_hospital
        ]

        return Response(
            {
                "total_expired": total,
                "unacknowledged": total,  # All are "unacknowledged" since we're not tracking
                "by_hospital": by_hospital_clean,
            }
        )

    @action(detail=False, methods=["post"])
    def acknowledge(self, request):
        """
        Acknowledge expired product(s).
        Creates or updates ExpiredProductReport records and sends email to warehouse managers.
        Accepts { id: <ScannedProduct PK> } or { ids: [<pk>, ...] }.
        """
        if is_viewer_only(request.user):
            return Response({"error": "You have view-only access."}, status=status.HTTP_403_FORBIDDEN)
        ids = request.data.get("ids", [])
        single_id = request.data.get("id")
        if single_id:
            ids = [single_id]

        if not ids:
            return Response({"error": "No product IDs provided"}, status=400)

        today = timezone.now().date()
        acknowledged_count = 0

        for pid in ids:
            try:
                product = ScannedProduct.objects.select_related(
                    "scanning_session__hospital",
                    "scanning_session__batch",
                    "scanning_session__scanned_by",
                ).get(pk=pid)
            except ScannedProduct.DoesNotExist:
                continue

            days_expired = (today - product.expiry_date).days if product.expiry_date else 0

            report, created = ExpiredProductReport.objects.update_or_create(
                scanned_product=product,
                defaults={
                    "serial_number": product.serial_number,
                    "gtin_code": product.gtin_code,
                    "product_name": product.product_name,
                    "batch_number": product.batch_number,
                    "material_code": product.material_code,
                    "expiry_date": product.expiry_date or today,
                    "days_expired": days_expired,
                    "hospital": product.scanning_session.hospital,
                    "session": product.scanning_session,
                    "session_batch_id": (
                        product.scanning_session.batch.batch_id if product.scanning_session.batch else "N/A"
                    ),
                    "scanned_by": product.scanning_session.scanned_by,
                    "scanned_at": product.scanned_at,
                    "acknowledged": True,
                    "acknowledged_by": request.user,
                    "acknowledged_at": timezone.now(),
                },
            )
            acknowledged_count += 1

        # Send email notification to warehouse managers
        if acknowledged_count > 0:
            from .notification_service import notify_expired_acknowledged

            try:
                notify_expired_acknowledged(
                    product_ids=ids,
                    acknowledged_by=request.user,
                    count=acknowledged_count,
                )
            except Exception as exc:
                logger.warning(f"Ack email failed: {exc}")

        return Response({"status": "ok", "acknowledged_count": acknowledged_count})


# ─────────────────────────────────────────────────────────────────────────────
# H2H STOCK TRANSFER VIEWSET
# ─────────────────────────────────────────────────────────────────────────────
class TransferSessionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Hospital-to-Hospital Stock Transfer sessions.

    GET  /api/transfers/                   → list all
    POST /api/transfers/start_transfer/    → start new transfer
    POST /api/transfers/scan_product/      → scan product to transfer
    POST /api/transfers/complete_transfer/ → mark as completed
    POST /api/transfers/upload_attachment/ → upload confirmation doc
    GET  /api/transfers/download_attachment/ → download attachment
    """

    queryset = (
        TransferSession.objects.select_related("from_hospital", "to_hospital", "initiated_by")
        .prefetch_related("transferred_products")
        .all()
    )
    serializer_class = TransferSessionSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"])
    def start_transfer(self, request):
        """
        Start a new H2H stock transfer session.
        """
        if not can_use_scanner(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        from_hospital_id = request.data.get("from_hospital_id")
        to_hospital_id = request.data.get("to_hospital_id")
        transfer_id = request.data.get("transfer_id")  # e.g. "010326-JD-12345-TO-67890"

        if not from_hospital_id or not to_hospital_id:
            return Response(
                {"error": "Both from_hospital_id and to_hospital_id are required"}, status=status.HTTP_400_BAD_REQUEST
            )

        if from_hospital_id == to_hospital_id:
            return Response(
                {"error": "Source and destination hospitals must be different"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from_hospital = Hospital.objects.get(pk=from_hospital_id)
            to_hospital = Hospital.objects.get(pk=to_hospital_id)
        except Hospital.DoesNotExist:
            return Response({"error": "Hospital not found"}, status=status.HTTP_404_NOT_FOUND)

        # Auto-cancel any previous empty transfer sessions for this user
        old_empty = TransferSession.objects.annotate(prod_count=models.Count("transferred_products")).filter(
            initiated_by=request.user, status="active", prod_count=0
        )
        old_empty.update(status="cancelled")

        # Generate session key if not provided
        if not transfer_id:
            now = timezone.now()
            initials = "".join([n[0] for n in request.user.get_full_name().split()][:2]).upper() or "XX"
            transfer_id = f"{now.strftime('%d%m%y')}-{initials}-{from_hospital.sap_code or 'FROM'}-TO-{to_hospital.sap_code or 'TO'}"

        session_key = f"xfer-{uuid.uuid4().hex[:12]}"

        transfer = TransferSession.objects.create(
            session_key=session_key,
            transfer_id=transfer_id,
            from_hospital=from_hospital,
            to_hospital=to_hospital,
            initiated_by=request.user,
            status="active",
        )

        serializer = TransferSessionSerializer(transfer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def scan_product(self, request):
        """
        Scan a product into an active transfer session.
        """
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key, status="active")
        except TransferSession.DoesNotExist:
            return Response({"error": "Active transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        serial_number = request.data.get("serial_number")
        if not serial_number:
            return Response({"error": "serial_number is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Auto-fill material_code from GTIN mapping if not provided by client — done
        # BEFORE the duplicate check so material/GTIN can participate in the composite key.
        transfer_gtin = request.data.get("gtin_code")
        transfer_matcode = request.data.get("material_code")
        transfer_name = request.data.get("product_name")
        if not transfer_matcode and transfer_gtin:
            try:
                gtin_map = GTINMaterialMapping.objects.get(gtin_code=transfer_gtin)
                transfer_matcode = gtin_map.material_code
                if not transfer_name:
                    transfer_name = gtin_map.product_name
                logger.info(f"Transfer GTIN mapping applied: {transfer_gtin} → {transfer_matcode}")
            except GTINMaterialMapping.DoesNotExist:
                pass

        # Composite duplicate check: same serial under a different material/GTIN is allowed.
        dup_qs = TransferredProduct.objects.filter(transfer_session=transfer, serial_number=serial_number)
        if (transfer_matcode or "").strip():
            dup_qs = dup_qs.filter(material_code=transfer_matcode.strip())
        if (transfer_gtin or "").strip():
            dup_qs = dup_qs.filter(gtin_code=transfer_gtin.strip())
        if dup_qs.exists():
            return Response(
                {"error": f"Product {serial_number} already scanned in this transfer"}, status=status.HTTP_409_CONFLICT
            )

        resolved_transfer_batch = ensure_batch_number(request.data.get("batch_number"), serial_number)

        try:
            product = TransferredProduct.objects.create(
                transfer_session=transfer,
                serial_number=serial_number,
                gtin_code=transfer_gtin,
                material_code=transfer_matcode,
                product_name=transfer_name,
                batch_number=resolved_transfer_batch,
                expiry_date=request.data.get("expiry_date"),
                production_date=request.data.get("production_date"),
                best_before_date=request.data.get("best_before_date"),
                packaging_date=request.data.get("packaging_date"),
                reference_number=request.data.get("reference_number", ""),
                quantity=request.data.get("quantity", 1),
                qr_data=request.data.get("qr_data"),
            )
        except IntegrityError:
            # Composite unique_together violated by a concurrent scan.
            return Response(
                {"error": f"Product {serial_number} already scanned in this transfer"}, status=status.HTTP_409_CONFLICT
            )

        transfer.last_scan_time = timezone.now()
        transfer.save(update_fields=["last_scan_time"])

        serializer = TransferredProductSerializer(product)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def complete_transfer(self, request):
        """
        Mark a transfer session as completed (submitted).
        """
        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if transfer.status != "active":
            return Response(
                {"error": f"Cannot complete transfer in {transfer.status} status"}, status=status.HTTP_400_BAD_REQUEST
            )

        transfer.mark_completed()
        serializer = TransferSessionSerializer(transfer)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request):
        """
        Upload a confirmation document for a completed transfer.
        """
        session_key = request.data.get("session_key") or request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if transfer.status not in ("completed", "active"):
            return Response(
                {"error": "Transfer must be active or completed to upload attachment"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        # Read file content into memory
        file_data = uploaded_file.read()

        # Create or update attachment
        attachment, created = TransferAttachment.objects.update_or_create(
            transfer_session=transfer,
            defaults={
                "file_data": file_data,
                "content_type": uploaded_file.content_type,
                "file_size": len(file_data),
                "original_filename": uploaded_file.name,
                "uploaded_by": request.user,
                "notes": request.data.get("notes", ""),
            },
        )

        # Mark transfer as closed
        transfer.mark_closed()

        serializer = TransferAttachmentSerializer(attachment)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def download_attachment(self, request):
        """
        Download the attachment for a transfer session.
        """
        session_key = request.query_params.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
            attachment = transfer.attachment
        except (TransferSession.DoesNotExist, TransferAttachment.DoesNotExist):
            return Response({"error": "Transfer or attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        response = HttpResponse(attachment.file_data, content_type=attachment.content_type)
        response["Content-Disposition"] = f'attachment; filename="{attachment.original_filename}"'
        return response

    @action(detail=False, methods=["post"])
    def terminate_transfer(self, request):
        """
        Terminate/cancel a transfer session.
        """
        if not can_use_dashboard(request.user):
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        session_key = request.data.get("session_key")
        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = TransferSession.objects.get(session_key=session_key)
        except TransferSession.DoesNotExist:
            return Response({"error": "Transfer session not found"}, status=status.HTTP_404_NOT_FOUND)

        if transfer.status == "closed":
            return Response({"error": "Cannot terminate a closed transfer"}, status=status.HTTP_400_BAD_REQUEST)

        transfer.status = "cancelled"
        transfer.save(update_fields=["status"])

        return Response({"status": "ok", "message": "Transfer terminated"})


# Helper: Add count annotation for transfer sessions
from django.db import models

# ─────────────────────────────────────────────────────────────────────────────
# SALESPERSON PERFORMANCE DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────


class SalespersonPerformanceViewSet(viewsets.ViewSet):
    """
    API endpoints for salesperson performance dashboard.

    Salespersons are discovered automatically from Hospital.salesperson_email.
    A matching Django User account is NOT required — the email itself is the
    primary identifier.

    Access rules:
      • PS Salesperson → sees only their own data (matched by request.user.email)
      • PS Warehouse / Product Scanner / Sales / Superuser → sees all / filtered
    """

    permission_classes = [IsAuthenticated]

    # ── helpers ──────────────────────────────────────────────────────────────

    def _is_warehouse(self, user):
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales", "PS Viewer"]).exists()

    def _is_salesperson_only(self, user):
        if user.is_superuser:
            return False
        in_sp = user.groups.filter(name="PS Salesperson").exists()
        in_wh = user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists()
        return in_sp and not in_wh

    @staticmethod
    def _discover_salesperson_emails():
        """
        Returns a list of dicts for every distinct salesperson_email in the
        Hospital master:
          [{'email': 'x@y.com', 'name': 'X Y', 'user_id': 5, 'hospital_count': 3}, ...]
        name / user_id come from a matching Django User when one exists.
        """
        from django.db.models import Count

        # Distinct emails with hospital counts
        rows = (
            Hospital.objects.exclude(salesperson_email__isnull=True)
            .exclude(salesperson_email="")
            .values("salesperson_email")
            .annotate(hospital_count=Count("id"))
            .order_by("salesperson_email")
        )

        # Pre-fetch any matching user accounts (case-insensitive)
        all_emails = [r["salesperson_email"].lower() for r in rows]
        from django.db.models import Q

        q = Q()
        for em in set(all_emails):
            q |= Q(email__iexact=em)
        user_map = {}  # lower-email → User
        if q:
            for u in User.objects.filter(q, is_active=True):
                user_map[u.email.lower()] = u

        result = []
        for r in rows:
            email = r["salesperson_email"]
            user = user_map.get(email.lower())
            # Derive display name: from User if available, else from email prefix
            if user:
                name = user.get_full_name() or user.username
                user_id = user.id
            else:
                # Turn 'debbie@merillife.com' → 'Debbie'
                # Turn 'sakshi.chopra@merillife.com' → 'Sakshi Chopra'
                local = email.split("@")[0]
                name = " ".join(part.capitalize() for part in local.replace(".", " ").replace("_", " ").split())
                user_id = None
            result.append(
                {
                    "email": email,
                    "name": name,
                    "user_id": user_id,
                    "hospital_count": r["hospital_count"],
                }
            )
        return result

    @staticmethod
    def _get_hospitals_for_email(sp_email):
        """Hospitals assigned to a salesperson email."""
        return Hospital.objects.filter(salesperson_email__iexact=sp_email)

    @staticmethod
    def _sessions_for_email_month(sp_email, year, month):
        """
        All scanning sessions at hospitals assigned to this salesperson in a given
        month, regardless of who did the scanning.  This ensures that if another
        user scans one of the salesperson's hospitals it still counts as 'visited'
        in their monthly overview.  Sessions with zero scanned products are excluded.
        """
        from django.db.models import Count as _Count

        return (
            ScanningSession.objects.filter(
                hospital__salesperson_email__iexact=sp_email,
                started_at__year=year,
                started_at__month=month,
            )
            .exclude(status="cancelled")
            .annotate(_sc=_Count("scanned_products"))
            .exclude(_sc=0)
            .select_related("hospital", "batch", "scanned_by")
        )

    @classmethod
    def _build_month_summary_email(cls, sp_email, sp_name, year, month):
        """
        Build a complete month summary dict for one salesperson email.
        """
        assigned_hospitals = list(cls._get_hospitals_for_email(sp_email))
        sessions = cls._sessions_for_email_month(sp_email, year, month)

        visited_hospital_ids = set(sessions.values_list("hospital_id", flat=True))
        pending_hospitals = [h for h in assigned_hospitals if h.id not in visited_hospital_ids]
        completed_sessions = sessions.filter(status__in=["completed", "closed"])
        total_scans = ScannedProduct.objects.filter(scanning_session__in=sessions).count()

        hospital_details = []
        for h in assigned_hospitals:
            h_sessions = [s for s in sessions if s.hospital_id == h.id]
            h_completed = [s for s in h_sessions if s.status in ("completed", "closed")]
            h_scans = ScannedProduct.objects.filter(scanning_session__in=h_sessions).count() if h_sessions else 0
            hospital_details.append(
                {
                    "id": h.id,
                    "name": h.name,
                    "sap_code": h.sap_code or "",
                    "province": h.province or "",
                    "visited": len(h_sessions) > 0,
                    "sessions_count": len(h_sessions),
                    "completed_sessions": len(h_completed),
                    "scans_count": h_scans,
                    "status": (
                        "completed" if len(h_completed) > 0 else "in_progress" if len(h_sessions) > 0 else "pending"
                    ),
                    "last_visit": (
                        max((s.started_at for s in h_sessions), default=None).strftime("%d %b %Y")
                        if h_sessions
                        else None
                    ),
                }
            )

        return {
            "year": year,
            "month": month,
            "month_label": datetime(year, month, 1).strftime("%B %Y"),
            "assigned_hospitals": len(assigned_hospitals),
            "visited_hospitals": len(visited_hospital_ids),
            "pending_hospitals": len(pending_hospitals),
            "total_sessions": sessions.count(),
            "completed_sessions": completed_sessions.count(),
            "total_scans": total_scans,
            "visit_rate": (
                round(len(visited_hospital_ids) / len(assigned_hospitals) * 100, 1) if assigned_hospitals else 0
            ),
            "hospitals": hospital_details,
            "pending_hospital_names": [h.name for h in pending_hospitals],
        }

    # ── overview: all salespersons summary (warehouse view) ──────────────────

    @action(detail=False, methods=["get"], url_path="overview")
    def overview(self, request):
        """
        GET /api/sp-performance/overview/?year=2026&month=4
        GET /api/sp-performance/overview/?year=2026&month=4&sp_email=x@y.com
        """
        year = int(request.query_params.get("year", timezone.now().year))
        month = int(request.query_params.get("month", timezone.now().month))

        if self._is_warehouse(request.user):
            all_sps = self._discover_salesperson_emails()
            target_email = request.query_params.get("sp_email", "").strip()
            if target_email:
                all_sps = [s for s in all_sps if s["email"].lower() == target_email.lower()]
        else:
            # Non-warehouse user: only their own
            email = request.user.email
            all_sps = [
                {
                    "email": email,
                    "name": request.user.get_full_name() or request.user.username,
                    "user_id": request.user.id,
                    "hospital_count": Hospital.objects.filter(salesperson_email__iexact=email).count(),
                }
            ]

        result = []
        for sp in all_sps:
            summary = self._build_month_summary_email(sp["email"], sp["name"], year, month)
            result.append(
                {
                    "salesperson_email": sp["email"],
                    "salesperson_name": sp["name"],
                    "salesperson_user_id": sp.get("user_id"),
                    **summary,
                }
            )

        return Response(
            {
                "year": year,
                "month": month,
                "salespersons": result,
            }
        )

    # ── salesperson detail with full history ─────────────────────────────────

    @action(detail=False, methods=["get"], url_path="detail")
    def performance_detail(self, request):
        """
        GET /api/sp-performance/detail/?sp_email=x@y.com&months=6
        GET /api/sp-performance/detail/?months=6  (warehouse: aggregated all)
        """
        user = request.user
        sp_email = request.query_params.get("sp_email", "").strip()
        months_back = min(int(request.query_params.get("months", 6)), 24)

        if sp_email and not self._is_warehouse(user):
            if sp_email.lower() != user.email.lower():
                return Response({"error": "Permission denied"}, status=403)

        # Build month list
        now = timezone.now()
        month_keys = []
        seen = set()
        for i in range(months_back - 1, -1, -1):
            target = now - timedelta(days=i * 30)
            key = (target.year, target.month)
            if key not in seen:
                seen.add(key)
                month_keys.append(key)

        # Warehouse with no email → aggregate all salespersons
        if not sp_email and self._is_warehouse(user):
            all_sps = self._discover_salesperson_emails()
            history = []
            for yr, mo in month_keys:
                totals = {
                    "year": yr,
                    "month": mo,
                    "month_label": datetime(yr, mo, 1).strftime("%B %Y"),
                    "assigned_hospitals": 0,
                    "visited_hospitals": 0,
                    "pending_hospitals": 0,
                    "total_sessions": 0,
                    "completed_sessions": 0,
                    "total_scans": 0,
                    "visit_rate": 0,
                    "hospitals": [],
                    "pending_hospital_names": [],
                }
                for sp in all_sps:
                    summary = self._build_month_summary_email(sp["email"], sp["name"], yr, mo)
                    totals["assigned_hospitals"] += summary["assigned_hospitals"]
                    totals["visited_hospitals"] += summary["visited_hospitals"]
                    totals["pending_hospitals"] += summary["pending_hospitals"]
                    totals["total_sessions"] += summary["total_sessions"]
                    totals["completed_sessions"] += summary["completed_sessions"]
                    totals["total_scans"] += summary["total_scans"]
                if totals["assigned_hospitals"]:
                    totals["visit_rate"] = round(totals["visited_hospitals"] / totals["assigned_hospitals"] * 100, 1)
                history.append(totals)
            return Response(
                {
                    "salesperson_email": "",
                    "salesperson_name": "All Salespersons",
                    "history": history,
                }
            )

        if not sp_email:
            sp_email = user.email

        # Resolve display name
        all_sps = self._discover_salesperson_emails()
        sp_info = next((s for s in all_sps if s["email"].lower() == sp_email.lower()), None)
        sp_name = sp_info["name"] if sp_info else sp_email.split("@")[0].replace(".", " ").title()

        # Build history for N months back from current month
        now = timezone.now()
        history = []
        for i in range(months_back - 1, -1, -1):
            target = now - timedelta(days=i * 30)
            history.append(self._build_month_summary_email(sp_email, sp_name, target.year, target.month))

        # Remove duplicates
        seen = set()
        unique_history = []
        for m in history:
            key = (m["year"], m["month"])
            if key not in seen:
                seen.add(key)
                unique_history.append(m)

        return Response(
            {
                "salesperson_email": sp_email,
                "salesperson_name": sp_name,
                "history": unique_history,
            }
        )

    # ── current month detail for a salesperson ───────────────────────────────

    @action(detail=False, methods=["get"], url_path="current-month")
    def current_month(self, request):
        """
        GET /api/sp-performance/current-month/?sp_email=x@y.com
        GET /api/sp-performance/current-month/   (warehouse: aggregated all)
        """
        user = request.user
        sp_email = request.query_params.get("sp_email", "").strip()
        now = timezone.now()
        year = int(request.query_params.get("year", now.year))
        month = int(request.query_params.get("month", now.month))

        if sp_email and not self._is_warehouse(user):
            if sp_email.lower() != user.email.lower():
                return Response({"error": "Permission denied"}, status=403)

        # Warehouse with no email → aggregate across all salespersons
        if not sp_email and self._is_warehouse(user):
            all_sps = self._discover_salesperson_emails()
            totals = {
                "year": year,
                "month": month,
                "month_label": datetime(year, month, 1).strftime("%B %Y"),
                "assigned_hospitals": 0,
                "visited_hospitals": 0,
                "pending_hospitals": 0,
                "total_sessions": 0,
                "completed_sessions": 0,
                "total_scans": 0,
                "visit_rate": 0,
                "hospitals": [],
                "pending_hospital_names": [],
            }
            seen_hospital_ids = set()  # avoid double-counting shared hospitals
            for sp in all_sps:
                summary = self._build_month_summary_email(sp["email"], sp["name"], year, month)
                for h in summary.get("hospitals", []):
                    if h["id"] not in seen_hospital_ids:
                        seen_hospital_ids.add(h["id"])
                        totals["hospitals"].append(h)
                totals["assigned_hospitals"] += summary["assigned_hospitals"]
                totals["visited_hospitals"] += summary["visited_hospitals"]
                totals["pending_hospitals"] += summary["pending_hospitals"]
                totals["total_sessions"] += summary["total_sessions"]
                totals["completed_sessions"] += summary["completed_sessions"]
                totals["total_scans"] += summary["total_scans"]
                totals["pending_hospital_names"].extend(summary.get("pending_hospital_names", []))
            if totals["assigned_hospitals"]:
                totals["visit_rate"] = round(totals["visited_hospitals"] / totals["assigned_hospitals"] * 100, 1)
            return Response(
                {
                    "salesperson_email": "",
                    "salesperson_name": "All Salespersons",
                    **totals,
                }
            )

        if not sp_email:
            sp_email = user.email

        # Resolve display name
        all_sps = self._discover_salesperson_emails()
        sp_info = next((s for s in all_sps if s["email"].lower() == sp_email.lower()), None)
        sp_name = sp_info["name"] if sp_info else sp_email.split("@")[0].replace(".", " ").title()

        summary = self._build_month_summary_email(sp_email, sp_name, year, month)
        return Response(
            {
                "salesperson_email": sp_email,
                "salesperson_name": sp_name,
                **summary,
            }
        )

    # ── list all salespersons (for warehouse dropdown) ────────────────────────

    @action(detail=False, methods=["get"], url_path="salesperson-list")
    def salesperson_list(self, request):
        """
        GET /api/sp-performance/salesperson-list/
        Returns all distinct salesperson emails from Hospital master.
        """
        if not self._is_warehouse(request.user):
            return Response({"error": "Permission denied"}, status=403)

        data = self._discover_salesperson_emails()
        return Response({"salespersons": data})

    # ── hospital session details ──────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="hospital-sessions")
    def hospital_sessions(self, request):
        """
        GET /api/sp-performance/hospital-sessions/?hospital_id=X&year=2026&month=4
        GET /api/sp-performance/hospital-sessions/?hospital_id=X&year=2026&month=4&sp_email=x@y.com
        Returns scanning sessions for a hospital in a given month.
        When sp_email is provided, only sessions submitted by that salesperson are returned.
        Sessions with zero scanned products are always excluded.
        """
        hospital_id = request.query_params.get("hospital_id")
        if not hospital_id:
            return Response({"error": "hospital_id required"}, status=400)

        now = timezone.now()
        year = int(request.query_params.get("year", now.year))
        month = int(request.query_params.get("month", now.month))
        sp_email = request.query_params.get("sp_email", "").strip()

        try:
            hospital = Hospital.objects.get(pk=int(hospital_id))
        except (Hospital.DoesNotExist, ValueError):
            return Response({"error": "Hospital not found"}, status=404)

        # Mapping-based permission removed: any authenticated user with access
        # may view sessions for any hospital. 'scanned_by' captures the actual scanner.

        from django.db.models import Count as _Count

        qs = (
            ScanningSession.objects.filter(hospital=hospital, started_at__year=year, started_at__month=month)
            .exclude(status="cancelled")
            .annotate(_sc=_Count("scanned_products"))
            .exclude(_sc=0)
            .select_related("scanned_by", "batch")
            .order_by("-started_at")
        )

        # Filter by salesperson assignment (same logic as _sessions_for_email_month)
        # so the modal shows the same sessions that are counted in the dashboard card.
        # Filtering by scanned_by would miss sessions scanned by a colleague at
        # an SP-assigned hospital, which _sessions_for_email_month correctly counts.
        if sp_email:
            qs = qs.filter(hospital__salesperson_email__iexact=sp_email)

        session_data = []
        for s in qs:
            scans_count = s._sc
            scanned_by_name = ""
            if s.scanned_by:
                scanned_by_name = s.scanned_by.get_full_name() or s.scanned_by.username
            session_data.append(
                {
                    "session_key": s.session_key,
                    "batch_id": s.batch.batch_id if s.batch else "",
                    "status": s.status,
                    "session_type": s.session_type,
                    "scanned_by": scanned_by_name,
                    "started_at": s.started_at.strftime("%d %b %Y, %I:%M %p"),
                    "completed_at": s.completed_at.strftime("%d %b %Y, %I:%M %p") if s.completed_at else None,
                    "scans_count": scans_count,
                    "detail_url": f"/product_scanning/session/{s.session_key}/",
                }
            )

        return Response(
            {
                "hospital_id": hospital.id,
                "hospital_name": hospital.name,
                "year": year,
                "month": month,
                "sessions": session_data,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# REMARKS — Product-wise & Batch-level
# ─────────────────────────────────────────────────────────────────────────────


class ScannedProductRemarkViewSet(viewsets.GenericViewSet):
    """
    ViewSet for per-product remarks.

    POST /api/product-remarks/         — add a remark (salesperson or warehouse)
    GET  /api/product-remarks/?product=<id>  — list remarks for a product

    Role is inferred from the requesting user's groups:
      PS Warehouse / Product Scanner / superuser → 'warehouse'
      Everyone else (PS Salesperson, etc.)       → 'salesperson'

    When a warehouse user adds a remark, an email notification is sent to
    the session's salesperson.
    """

    serializer_class = ScannedProductRemarkSerializer
    permission_classes = [IsAuthenticated]

    def _infer_role(self, user):
        if user.is_superuser:
            return "warehouse"
        if user.groups.filter(name__in={"PS Warehouse", "Product Scanner"}).exists():
            return "warehouse"
        return "salesperson"

    def list(self, request):
        product_id = request.query_params.get("product")
        session_key = request.query_params.get("session")
        if product_id:
            try:
                product = ScannedProduct.objects.get(pk=product_id)
            except ScannedProduct.DoesNotExist:
                return Response({"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND)
            remarks = ScannedProductRemark.objects.filter(scanned_product=product).select_related("created_by")
        elif session_key:
            remarks = ScannedProductRemark.objects.filter(
                scanned_product__scanning_session__session_key=session_key
            ).select_related("created_by", "scanned_product")
        else:
            return Response({"error": "product or session query param is required"}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ScannedProductRemarkSerializer(remarks, many=True)
        return Response(serializer.data)

    def create(self, request):
        if is_viewer_only(request.user):
            return Response({"error": "You have view-only access."}, status=status.HTTP_403_FORBIDDEN)
        product_id = request.data.get("product")
        remark_text = (request.data.get("remark") or "").strip()
        if not product_id:
            return Response({"error": "product is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not remark_text:
            return Response({"error": "remark cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            product = ScannedProduct.objects.select_related(
                "scanning_session__scanned_by",
                "scanning_session__batch",
                "scanning_session__hospital",
            ).get(pk=product_id)
        except ScannedProduct.DoesNotExist:
            return Response({"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND)

        role = self._infer_role(request.user)
        remark = ScannedProductRemark.objects.create(
            scanned_product=product,
            remark=remark_text,
            role=role,
            created_by=request.user,
        )

        # Email salesperson when warehouse adds a product remark
        if role == "warehouse":
            try:
                notify_warehouse_remark(
                    session=product.scanning_session,
                    remark_text=remark_text,
                    added_by=request.user,
                    remark_type="product",
                    product=product,
                )
            except Exception:
                logger.exception("Failed to send warehouse product remark email")

        serializer = ScannedProductRemarkSerializer(remark)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ScanSessionRemarkViewSet(viewsets.GenericViewSet):
    """
    ViewSet for batch-level session remarks.

    POST /api/session-remarks/               — add a batch remark
    GET  /api/session-remarks/?session=<key> — list remarks for a session

    Role is inferred the same way as ScannedProductRemarkViewSet.
    Warehouse remarks trigger an email to the salesperson.
    """

    serializer_class = ScanSessionRemarkSerializer
    permission_classes = [IsAuthenticated]

    def _infer_role(self, user):
        if user.is_superuser:
            return "warehouse"
        if user.groups.filter(name__in={"PS Warehouse", "Product Scanner"}).exists():
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
        serializer = ScanSessionRemarkSerializer(remarks, many=True)
        return Response(serializer.data)

    def create(self, request):
        if is_viewer_only(request.user):
            return Response({"error": "You have view-only access."}, status=status.HTTP_403_FORBIDDEN)
        session_key = request.data.get("session")
        remark_text = (request.data.get("remark") or "").strip()
        if not session_key:
            return Response({"error": "session is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not remark_text:
            return Response({"error": "remark cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            session = ScanningSession.objects.select_related(
                "scanned_by",
                "batch",
                "hospital",
            ).get(session_key=session_key)
        except ScanningSession.DoesNotExist:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

        role = self._infer_role(request.user)
        remark = ScanSessionRemark.objects.create(
            session=session,
            remark=remark_text,
            role=role,
            created_by=request.user,
        )

        # Email salesperson when warehouse adds a batch-level remark
        if role == "warehouse":
            try:
                notify_warehouse_remark(
                    session=session,
                    remark_text=remark_text,
                    added_by=request.user,
                    remark_type="session",
                )
            except Exception:
                logger.exception("Failed to send warehouse session remark email")

        serializer = ScanSessionRemarkSerializer(remark)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
