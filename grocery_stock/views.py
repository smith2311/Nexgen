"""
Grocery Stock & Cost Management — master data API
─────────────────────────────────────────────────────────────────────────────
ViewSets follow the pattern in warehouse_inspection/views.py: DRF ViewSets
registered on a DefaultRouter, IsAuthenticated inherited from the project
REST_FRAMEWORK settings, plus a role check per view.

Two behaviours are shared by every master here:

  * ``destroy`` soft-deletes. The base model keeps the row and flags it, so a
    delete never orphans a historical document that points at the master.
  * ``?is_active=`` and ``?search=`` are accepted everywhere, because the
    Flutter client needs to offer an "active only" toggle on every picker.
"""
import csv
import io
import re
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from logistics_performance.middleware import set_current_username

from .models import (
    GroceryBrand,
    GroceryCategory,
    GroceryDepartment,
    GroceryItem,
    GroceryLocation,
    GrocerySupplier,
    GroceryUnit,
    GroceryWarehouse,
)
from .pages import MASTER_SPECS

from .roles import GroceryRolePermission
from .serializers import (
    GroceryBrandSerializer,
    GroceryCategorySerializer,
    GroceryDepartmentSerializer,
    GroceryItemListSerializer,
    GroceryItemSerializer,
    GroceryLocationSerializer,
    GrocerySupplierSerializer,
    GroceryUnitSerializer,
    GroceryWarehouseSerializer,
)

TRUE_VALUES = {'1', 'true', 'yes', 'y'}
FALSE_VALUES = {'0', 'false', 'no', 'n'}

# Bulk upload: which model an fk column resolves against, and which of its
# columns carries the identity in the uploaded file. Keys are the ``source``
# endpoints used by MASTER_SPECS fields; bin locations are handled separately
# in _resolve_fk because their code is only unique per warehouse.
UPLOAD_FK_SOURCES = {
    'categories': (GroceryCategory, 'code'),
    'brands': (GroceryBrand, 'code'),
    'units': (GroceryUnit, 'code'),
    'warehouses': (GroceryWarehouse, 'code'),
}

UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # matches the logistics masters' 10 MB cap


class MasterViewSet(viewsets.ModelViewSet):
    """
    Shared behaviour for every master endpoint in this module.

    Subclasses set the usual queryset/serializer_class. None of them sets
    ``write_roles``: master data is Grocery-Admin's alone, and an empty
    ``write_roles`` is exactly what GroceryRolePermission reads as admin-only.
    Grocery-Manager can still read every master — it needs the item and
    supplier lists to record a receipt.
    """
    permission_classes = [GroceryRolePermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    write_roles = ()

    # Bulk upload (see the ``upload*`` actions below). ``spec_slug`` is the
    # master's key in MASTER_SPECS — the file's expected columns are derived
    # from that spec's form fields, so the file format always matches the add
    # form. ``upload_key_fields`` is the natural key used to spot duplicates.
    spec_slug = None
    upload_key_fields = ('code',)

    def initial(self, request, *args, **kwargs):
        """
        Re-stamp the audit thread-local once DRF has authenticated.

        CurrentUserMiddleware captures request.user before the view runs, which
        only resolves for session-authenticated requests. The Flutter client
        authenticates with a token, and at middleware time such a request is
        still anonymous — so created_by / updated_by would silently be left
        blank on every write from the app. DRF has resolved the real user by
        the time initial() runs, so re-stamping here lets the shared
        AuditSoftDeleteModel.save() work unchanged for both auth styles.
        """
        super().initial(request, *args, **kwargs)
        if request.user and request.user.is_authenticated:
            set_current_username(request.user.get_username())

    def get_queryset(self):
        """Apply the ``is_active`` filter every master shares."""
        queryset = super().get_queryset()
        raw = self.request.query_params.get('is_active')
        if raw is not None:
            value = raw.strip().lower()
            if value in TRUE_VALUES:
                queryset = queryset.filter(is_active=True)
            elif value in FALSE_VALUES:
                queryset = queryset.filter(is_active=False)
        return queryset

    def perform_destroy(self, instance):
        """
        Soft delete, never a row delete.

        The audit base keeps is_deleted/deleted_by/deleted_at and the default
        manager hides the row afterwards, so the record stays available to any
        historical document that references it.
        """
        instance.soft_delete(self.request.user.get_username())

    # ── Bulk upload (Excel/CSV) ─────────────────────────────────────────────
    # Same three-step flow as the logistics masters: POST <master>/upload/
    # parses the file into a session-held preview where every row gets a
    # status (ok / duplicate / error), the user reviews it, then
    # <master>/upload/confirm/ imports only the ok rows. Nothing touches the
    # database until confirm. Being POST actions, these inherit the same
    # write_roles gate as create.

    def _upload_session_key(self):
        return f'grocery_upload_{self.spec_slug}'

    def _upload_fields(self):
        return MASTER_SPECS[self.spec_slug]['fields']

    @staticmethod
    def _read_rows(file_obj, filename):
        """CSV or XLSX -> list of {header: str value} dicts, headers lowercased.
        The first row is the header row."""
        ext = (filename or '').rsplit('.', 1)[-1].lower()

        if ext == 'csv':
            text = file_obj.read().decode('utf-8-sig', errors='replace')
            return [
                {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
                for row in csv.DictReader(io.StringIO(text))
            ]

        if ext in ('xlsx', 'xls'):
            wb = load_workbook(io.BytesIO(file_obj.read()), read_only=True, data_only=True)
            ws = wb.active
            head_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if head_row is None:
                return []
            headers = [str(c).strip().lower() if c is not None else '' for c in head_row]
            return [
                {headers[i]: ('' if v is None else str(v).strip())
                 for i, v in enumerate(r) if i < len(headers) and headers[i]}
                for r in ws.iter_rows(min_row=2, values_only=True)
                if any(v is not None for v in r)
            ]

        raise ValueError(f'Unsupported file type ".{ext}" — use .csv, .xlsx or .xls.')

    @staticmethod
    def _resolve_fk(source, value):
        """Related-master code/name -> pk, active rows only. Returns None when
        there is no (or no unique) match. Bin locations accept "WAREHOUSE/BIN";
        a bare bin code works only while it is unique across warehouses."""
        if source == 'locations':
            if '/' in value:
                wh_code, bin_code = (part.strip() for part in value.split('/', 1))
                queryset = GroceryLocation.objects.filter(
                    warehouse__code__iexact=wh_code, code__iexact=bin_code)
            else:
                queryset = GroceryLocation.objects.filter(code__iexact=value)
            matches = list(queryset[:2])
            return matches[0].pk if len(matches) == 1 else None

        model, lookup = UPLOAD_FK_SOURCES.get(source, (None, None))
        if model is None:
            return None
        obj = model.objects.filter(**{f'{lookup}__iexact': value}).first()
        return obj.pk if obj else None

    def _row_to_payload(self, raw):
        """One file row -> serializer payload + fk-resolution errors.

        FK columns carry the related master's code (an Excel user cannot know
        database ids). Blank optional numbers/fks are omitted so the model
        default applies, mirroring what the add form sends."""
        payload, errors = {}, {}
        for field in self._upload_fields():
            name = field['name']
            value = (raw.get(name) or '').strip()
            ftype = field['type']

            if ftype == 'checkbox':
                payload[name] = (value.lower() in TRUE_VALUES) if value \
                    else bool(field.get('default'))
            elif ftype == 'fk':
                if not value:
                    continue
                pk = self._resolve_fk(field['source'], value)
                if pk is None:
                    errors[name] = (f'"{value}" does not match one active record '
                                    f'in {field["source"]}')
                else:
                    payload[name] = pk
            elif ftype in ('number', 'decimal'):
                if not value:
                    continue
                if ftype == 'number':
                    # Excel renders whole numbers as "30.0"; IntegerField
                    # rejects that spelling.
                    value = re.sub(r'\.0+$', '', value)
                payload[name] = value
            else:
                if value and field.get('uppercase'):
                    value = value.upper()
                payload[name] = value
        return payload, errors

    def _upload_key(self, payload):
        """Natural-key tuple for duplicate detection, or None when any part is
        missing (the serializer will report the missing field instead)."""
        parts = []
        for field in self.upload_key_fields:
            value = payload.get(field)
            if value in (None, ''):
                return None
            parts.append(str(value).upper())
        return tuple(parts)

    def _existing_match(self, payload):
        """'active' / 'deleted' / None for a stored row with this natural key.
        Checked explicitly because DRF's unique validators only see active
        rows (the default manager), while the DB unique index spans soft-
        deleted rows too."""
        filters_ = {}
        for field in self.upload_key_fields:
            value = payload.get(field)
            filters_[f'{field}__iexact' if isinstance(value, str) else field] = value
        row = self.queryset.model.all_objects.filter(**filters_).order_by('is_deleted').first()
        if row is None:
            return None
        return 'deleted' if row.is_deleted else 'active'

    @staticmethod
    def _flatten_errors(errors):
        parts = []
        for field, messages in errors.items():
            if isinstance(messages, (list, tuple)):
                text = ' '.join(str(m) for m in messages)
            else:
                text = str(messages)
            parts.append(text if field == 'non_field_errors' else f'{field}: {text}')
        return '; '.join(parts)

    @staticmethod
    def _only_unique_errors(errors):
        """True when every serializer error is a uniqueness clash — those rows
        are 'duplicate' (skipped quietly), not 'error'."""
        codes = []
        for messages in errors.values():
            for message in (messages if isinstance(messages, (list, tuple)) else [messages]):
                codes.append(getattr(message, 'code', None))
        return bool(codes) and all(code == 'unique' for code in codes)

    @action(detail=False, methods=['post'], url_path='upload')
    def upload(self, request):
        """Parse an uploaded CSV/XLSX into a previewed, session-held import."""
        if not self.spec_slug:
            return Response({'success': False, 'message': 'Bulk upload is not available here.'},
                            status=status.HTTP_404_NOT_FOUND)
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'success': False, 'message': 'No file uploaded.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if file_obj.size > UPLOAD_MAX_BYTES:
            return Response({'success': False, 'message': 'File is larger than 10 MB.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            rows = self._read_rows(file_obj, file_obj.name)
        except ValueError as exc:
            return Response({'success': False, 'message': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'success': False, 'message': 'Could not parse the uploaded file.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({'success': False,
                             'message': 'The file has no data rows below the header.'},
                            status=status.HTTP_400_BAD_REQUEST)

        field_names = [f['name'] for f in self._upload_fields()]
        seen, preview = set(), []
        for idx, raw in enumerate(rows, start=2):  # row 1 is the header
            payload, fk_errors = self._row_to_payload(raw)
            key = self._upload_key(payload)
            serializer = self.get_serializer(data=payload)
            valid = serializer.is_valid()

            if fk_errors:
                row_status, message = 'error', self._flatten_errors(fk_errors)
            elif key is not None and key in seen:
                row_status, message = 'duplicate', 'Duplicate row in file'
            elif not valid and not self._only_unique_errors(serializer.errors):
                row_status, message = 'error', self._flatten_errors(serializer.errors)
            elif not valid:
                row_status, message = 'duplicate', 'Already exists'
            else:
                existing = self._existing_match(payload) if key is not None else None
                if existing == 'active':
                    row_status, message = 'duplicate', 'Already exists'
                elif existing == 'deleted':
                    row_status, message = ('duplicate',
                                           'Exists as a removed (soft-deleted) record')
                else:
                    row_status, message = 'ok', ''

            if key is not None:
                seen.add(key)
            preview.append({
                'row': idx, 'status': row_status, 'message': message, 'data': payload,
                # Raw file values for display — payload holds resolved fk pks,
                # which mean nothing to the person reviewing the preview.
                'raw': {name: (raw.get(name) or '') for name in field_names},
            })

        token = secrets.token_urlsafe(16)
        request.session[self._upload_session_key()] = {
            'token': token, 'rows': preview, 'created_at': timezone.now().isoformat(),
        }
        request.session.modified = True

        return Response({
            'success': True, 'message': 'File parsed successfully', 'token': token,
            'rows': preview, 'count': len(preview),
            'ok': sum(1 for r in preview if r['status'] == 'ok'),
        })

    @action(detail=False, methods=['get'], url_path='upload/preview')
    def upload_preview(self, request):
        """Return the pending preview, mirroring the logistics preview API."""
        stored = request.session.get(self._upload_session_key())
        if not stored:
            return Response({'success': False, 'message': 'No upload preview available.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, 'token': stored['token'],
                         'rows': stored['rows'], 'count': len(stored['rows'])})

    @action(detail=False, methods=['post'], url_path='upload/confirm')
    def upload_confirm(self, request):
        """Import the previewed rows that passed validation."""
        stored = request.session.get(self._upload_session_key())
        token = request.data.get('token') if hasattr(request.data, 'get') else None
        if not stored or not token or stored.get('token') != token:
            return Response({'success': False,
                             'message': 'Preview expired — upload the file again.'},
                            status=status.HTTP_400_BAD_REQUEST)

        created = skipped = 0
        for row in stored['rows']:
            if row['status'] != 'ok':
                skipped += 1
                continue
            serializer = self.get_serializer(data=row['data'])
            if not serializer.is_valid():  # the DB may have changed since preview
                skipped += 1
                continue
            try:
                # Savepoint per row: one bad row (e.g. a soft-deleted record
                # holding the same unique name) must not poison the whole
                # request transaction.
                with transaction.atomic():
                    serializer.save()
                created += 1
            except IntegrityError:
                skipped += 1

        request.session.pop(self._upload_session_key(), None)
        request.session.modified = True
        return Response({'success': True, 'created': created, 'skipped': skipped,
                         'total': len(stored['rows'])})

    @action(detail=False, methods=['post'], url_path='upload/cancel')
    def upload_cancel(self, request):
        """Drop the pending preview without importing anything."""
        request.session.pop(self._upload_session_key(), None)
        request.session.modified = True
        return Response({'success': True})


# ── Category ────────────────────────────────────────────────────────────────

class GroceryCategoryViewSet(MasterViewSet):
    queryset = GroceryCategory.objects.all()
    serializer_class = GroceryCategorySerializer
    spec_slug = 'categories'
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['name', 'code', 'created_at']

    def get_queryset(self):
        # order_by is explicit because annotate() adds a GROUP BY, which makes
        # Django drop Meta.ordering — leaving pagination free to shuffle rows
        # between pages.
        return super().get_queryset().annotate(
            item_count=Count('items', filter=Q(items__is_deleted=False)),
        ).order_by('name')


# ── Brand ───────────────────────────────────────────────────────────────────

class GroceryBrandViewSet(MasterViewSet):
    queryset = GroceryBrand.objects.all()
    serializer_class = GroceryBrandSerializer
    spec_slug = 'brands'
    search_fields = ['code', 'name']
    ordering_fields = ['name', 'code', 'created_at']


# ── Unit ────────────────────────────────────────────────────────────────────

class GroceryUnitViewSet(MasterViewSet):
    queryset = GroceryUnit.objects.select_related('base_unit')
    serializer_class = GroceryUnitSerializer
    spec_slug = 'units'
    search_fields = ['code', 'name']
    ordering_fields = ['code', 'name', 'created_at']


# ── Supplier ────────────────────────────────────────────────────────────────

class GrocerySupplierViewSet(MasterViewSet):
    queryset = GrocerySupplier.objects.all()
    serializer_class = GrocerySupplierSerializer
    spec_slug = 'suppliers'
    upload_key_fields = ('name',)
    search_fields = ['name', 'phone', 'email']
    ordering_fields = ['name', 'credit_days', 'created_at']


# ── Warehouse ───────────────────────────────────────────────────────────────

class GroceryWarehouseViewSet(MasterViewSet):
    queryset = GroceryWarehouse.objects.all()
    serializer_class = GroceryWarehouseSerializer
    spec_slug = 'warehouses'
    search_fields = ['code', 'name', 'address']
    ordering_fields = ['name', 'code', 'created_at']

    def get_queryset(self):
        # See GroceryCategoryViewSet — annotate() drops Meta.ordering.
        return super().get_queryset().annotate(
            location_count=Count('locations', filter=Q(locations__is_deleted=False)),
        ).order_by('name')

    @action(detail=True, methods=['get'], url_path='locations')
    def locations(self, request, pk=None):
        """Bins in this warehouse — saves the client a second filtered call."""
        warehouse = self.get_object()
        queryset = GroceryLocation.objects.filter(warehouse=warehouse)
        if request.query_params.get('is_active', '').strip().lower() in TRUE_VALUES:
            queryset = queryset.filter(is_active=True)
        serializer = GroceryLocationSerializer(queryset, many=True)
        return Response(serializer.data)


# ── Bin location ────────────────────────────────────────────────────────────

class GroceryLocationViewSet(MasterViewSet):
    queryset = GroceryLocation.objects.select_related('warehouse')
    serializer_class = GroceryLocationSerializer
    spec_slug = 'locations'
    upload_key_fields = ('warehouse', 'code')
    search_fields = ['code', 'name', 'warehouse__code', 'warehouse__name']
    ordering_fields = ['code', 'created_at']

    def get_queryset(self):
        queryset = super().get_queryset()
        warehouse = self.request.query_params.get('warehouse')
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)
        return queryset


# ── Department ──────────────────────────────────────────────────────────────

class GroceryDepartmentViewSet(MasterViewSet):
    queryset = GroceryDepartment.objects.all()
    serializer_class = GroceryDepartmentSerializer
    spec_slug = 'departments'
    search_fields = ['code', 'name']
    ordering_fields = ['name', 'code', 'created_at']


# ── Item ────────────────────────────────────────────────────────────────────

class GroceryItemViewSet(MasterViewSet):
    queryset = GroceryItem.objects.select_related(
        'category', 'brand', 'unit', 'storage_location', 'storage_location__warehouse',
    )
    spec_slug = 'items'
    upload_key_fields = ('name',)
    search_fields = ['name', 'local_name']
    ordering_fields = ['name', 'created_at', 'standard_cost']

    def get_serializer_class(self):
        if self.action == 'list':
            return GroceryItemListSerializer
        return GroceryItemSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for param, field in (
            ('category', 'category_id'),
            ('brand', 'brand_id'),
            ('unit', 'unit_id'),
        ):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        expiry = params.get('expiry_required', '').strip().lower()
        if expiry in TRUE_VALUES:
            queryset = queryset.filter(expiry_required=True)
        elif expiry in FALSE_VALUES:
            queryset = queryset.filter(expiry_required=False)

        return queryset
