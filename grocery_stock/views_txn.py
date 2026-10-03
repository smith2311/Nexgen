"""
Grocery Stock — transaction API (Phase 2)
─────────────────────────────────────────────────────────────────────────────
Documents (GRN, Issue) are editable while DRAFT and frozen once posted.
Stock, ledger, batches and valuation are read-only endpoints: they are
*derived* from posting, and the only supported way to change them is to post a
document. Exposing them as writable would let a client edit inventory without
leaving an audit trail.

Roles: every document here — GRN, issue, adjustment — is Grocery-Manager's to
write, and Grocery-Admin passes every write as well. The read-only derived
endpoints are open to either role. Specification section 18 split this across
six specialised roles; migration 0007 dropped them.
"""
from datetime import date, timedelta

from django.db.models import (
    Case, CharField, Count, DecimalField, F, Q, Sum, Value, When,
)
from django.db.models.functions import Coalesce
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from logistics_performance.middleware import set_current_username

from . import roles
from .models import (
    DocumentStatus,
    GroceryGoodsReceipt,
    GroceryItemValuation,
    GroceryStock,
    GroceryStockAdjustment,
    GroceryStockBatch,
    GroceryStockIssue,
    GroceryStockTransaction,
)
from .roles import GroceryRolePermission
from .serializers_txn import (
    GoodsReceiptListSerializer,
    GoodsReceiptSerializer,
    ItemValuationSerializer,
    StockAdjustmentListSerializer,
    StockAdjustmentSerializer,
    StockBatchSerializer,
    StockIssueListSerializer,
    StockIssueSerializer,
    StockSerializer,
    StockTransactionSerializer,
)
from .services.posting import (
    PostingError,
    cancel_document,
    post_goods_receipt,
    post_stock_adjustment,
    post_stock_issue,
)

TRUE_VALUES = {'1', 'true', 'yes', 'y'}

# Section 13 alert thresholds.
EXPIRY_BANDS = (30, 60, 90)


def _error(code, message, http_status=status.HTTP_400_BAD_REQUEST):
    """The envelope used by every action on this module's documents."""
    return Response(
        {'success': False, 'error': {'code': code, 'message': message}},
        status=http_status,
    )


class GroceryBaseViewSet(viewsets.ModelViewSet):
    """Shared plumbing — same audit re-stamp and role check as the masters."""

    permission_classes = [GroceryRolePermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    write_roles = ()

    def initial(self, request, *args, **kwargs):
        # CurrentUserMiddleware reads request.user before DRF authenticates, so
        # a token request is still anonymous at middleware time. Re-stamp here
        # or created_by/updated_by land blank. Same fix as MasterViewSet.
        super().initial(request, *args, **kwargs)
        if request.user and request.user.is_authenticated:
            set_current_username(request.user.get_username())

    def perform_destroy(self, instance):
        instance.soft_delete(self.request.user.get_username())


class DocumentViewSet(GroceryBaseViewSet):
    """
    A DRAFT-then-POST document.

    Subclasses set ``post_service`` and ``document_noun``.
    """

    post_service = None
    document_noun = 'document'

    def _status_filtered(self, queryset):
        wanted = self.request.query_params.get('status')
        if wanted:
            queryset = queryset.filter(status=wanted.upper())

        date_from = self.request.query_params.get('from')
        date_to = self.request.query_params.get('to')
        field = self.date_field
        if date_from:
            queryset = queryset.filter(**{f'{field}__gte': date_from})
        if date_to:
            queryset = queryset.filter(**{f'{field}__lte': date_to})
        return queryset

    def destroy(self, request, *args, **kwargs):
        document = self.get_object()
        if document.status == DocumentStatus.POSTED:
            return _error(
                'POSTED_IMMUTABLE',
                f'A posted {self.document_noun} cannot be deleted. Its stock '
                f'movements are part of the audit trail.',
                status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def post_document(self, request, pk=None):
        """Move the stock. This is the only write path into the ledger."""
        document = self.get_object()
        try:
            posted = self.post_service(document, request.user.get_username())
        except PostingError as exc:
            return _error('CANNOT_POST', str(exc), status.HTTP_409_CONFLICT)

        serializer = self.get_serializer(posted)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        document = self.get_object()
        try:
            cancelled = cancel_document(document, request.user.get_username())
        except PostingError as exc:
            return _error('CANNOT_CANCEL', str(exc), status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(cancelled).data)


# ═══ GOODS RECEIPT ══════════════════════════════════════════════════════════

class GoodsReceiptViewSet(DocumentViewSet):
    queryset = GroceryGoodsReceipt.objects.select_related('supplier', 'warehouse')
    write_roles = (roles.MANAGER,)
    search_fields = ['grn_number', 'invoice_number', 'supplier__name', 'supplier__code']
    ordering_fields = ['receipt_date', 'grn_number', 'total_amount', 'created_at']
    post_service = staticmethod(post_goods_receipt)
    document_noun = 'goods receipt'
    date_field = 'receipt_date'

    def get_serializer_class(self):
        if self.action == 'list':
            return GoodsReceiptListSerializer
        return GoodsReceiptSerializer

    def get_queryset(self):
        queryset = self._status_filtered(super().get_queryset())

        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        warehouse = self.request.query_params.get('warehouse')
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)

        if self.action == 'list':
            return queryset.annotate(line_count=Count('lines')).order_by(
                '-receipt_date', '-id')
        return queryset.prefetch_related('lines__item__unit', 'lines__location')


# ═══ STOCK ISSUE ════════════════════════════════════════════════════════════

class StockIssueViewSet(DocumentViewSet):
    queryset = GroceryStockIssue.objects.select_related('department', 'warehouse')
    write_roles = (roles.MANAGER,)
    search_fields = ['issue_number', 'issued_to', 'purpose', 'department__name']
    ordering_fields = ['issue_date', 'issue_number', 'total_value', 'created_at']
    post_service = staticmethod(post_stock_issue)
    document_noun = 'stock issue'
    date_field = 'issue_date'

    def get_serializer_class(self):
        if self.action == 'list':
            return StockIssueListSerializer
        return StockIssueSerializer

    def get_queryset(self):
        queryset = self._status_filtered(super().get_queryset())

        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department_id=department)
        warehouse = self.request.query_params.get('warehouse')
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)

        if self.action == 'list':
            return queryset.annotate(line_count=Count('lines')).order_by(
                '-issue_date', '-id')
        return queryset.prefetch_related('lines__item__unit', 'lines__location')


# ═══ STOCK ADJUSTMENT ═══════════════════════════════════════════════════════

class StockAdjustmentViewSet(DocumentViewSet):
    """
    Section 11. Write access is the Grocery-Manager who runs the floor, same as
    a GRN or an issue — an adjustment can create stock out of nothing, but with
    the six specialised roles gone there is no narrower role left to hand it
    to, and Grocery-Admin passes every write anyway.
    """

    queryset = GroceryStockAdjustment.objects.select_related('warehouse')
    write_roles = (roles.MANAGER,)
    search_fields = ['adjustment_number', 'reference', 'remarks', 'warehouse__name']
    ordering_fields = ['adjustment_date', 'adjustment_number', 'total_value']
    post_service = staticmethod(post_stock_adjustment)
    document_noun = 'stock adjustment'
    date_field = 'adjustment_date'

    def get_serializer_class(self):
        if self.action == 'list':
            return StockAdjustmentListSerializer
        return StockAdjustmentSerializer

    def get_queryset(self):
        queryset = self._status_filtered(super().get_queryset())

        reason = self.request.query_params.get('reason')
        if reason:
            queryset = queryset.filter(reason=reason.upper())
        warehouse = self.request.query_params.get('warehouse')
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)

        if self.action == 'list':
            return queryset.annotate(line_count=Count('lines')).order_by(
                '-adjustment_date', '-id')
        return queryset.prefetch_related('lines__item__unit', 'lines__location',
                                         'lines__batch')


# ═══ CURRENT STOCK (read-only) ══════════════════════════════════════════════

class StockViewSet(GroceryBaseViewSet):
    """
    Live stock. Read-only by design: stock changes by posting a document, never
    by editing a balance, so there is always a ledger row explaining it.
    """

    http_method_names = ['get', 'head', 'options']
    serializer_class = StockSerializer
    queryset = GroceryStock.objects.select_related(
        'item', 'item__unit', 'item__category', 'item__valuation',
        'warehouse', 'location', 'location__warehouse',
    )
    search_fields = ['item__name',
                     'warehouse__code', 'warehouse__name']
    ordering_fields = ['item__name', 'quantity_available', 'warehouse__name']

    def get_queryset(self):
        queryset = super().get_queryset().annotate(
            stock_value=Coalesce(
                F('quantity_available') * F('item__valuation__average_cost'),
                Value(0), output_field=DecimalField(max_digits=16, decimal_places=2),
            ),
            stock_status=Case(
                When(quantity_available__lte=0, then=Value('OUT_OF_STOCK')),
                # Only flag low stock where a reorder level is actually set;
                # every item defaults to 0, and treating that as a threshold
                # would mark the whole catalogue low.
                # The two conditions are combined with & — When() takes a
                # single positional condition, so passing two Q objects makes
                # the second one bind to `then`.
                When(
                    Q(item__reorder_level__gt=0)
                    & Q(quantity_available__lte=F('item__reorder_level')),
                    then=Value('LOW'),
                ),
                default=Value('OK'),
                output_field=CharField(),
            ),
        )

        params = self.request.query_params
        for param, field in (('item', 'item_id'),
                             ('warehouse', 'warehouse_id'),
                             ('location', 'location_id'),
                             ('category', 'item__category_id')):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        status_filter = (params.get('stock_status') or '').upper()
        if status_filter in ('LOW', 'OUT_OF_STOCK', 'OK'):
            queryset = queryset.filter(stock_status=status_filter)

        if (params.get('nonzero') or '').lower() in TRUE_VALUES:
            queryset = queryset.filter(quantity_available__gt=0)

        return queryset.order_by('item__name', 'warehouse__name')

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Dashboard KPIs (specification section 1), computed in one query rather
        than pulling every row back to count it.
        """
        base = self.get_queryset()

        totals = base.aggregate(
            total_quantity=Coalesce(Sum('quantity_available'), Value(0),
                                    output_field=DecimalField()),
            total_value=Coalesce(Sum('stock_value'), Value(0),
                                 output_field=DecimalField()),
        )

        rows = list(base.values_list('stock_status', flat=True))
        today = date.today()

        expiring = {}
        for days in EXPIRY_BANDS:
            expiring[f'within_{days}_days'] = GroceryStockBatch.objects.filter(
                remaining_quantity__gt=0,
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=today + timedelta(days=days),
            ).count()

        return Response({
            'total_quantity': totals['total_quantity'],
            'total_value': totals['total_value'],
            'stock_lines': len(rows),
            'low_stock_items': rows.count('LOW'),
            'out_of_stock_items': rows.count('OUT_OF_STOCK'),
            'expired_batches': GroceryStockBatch.objects.filter(
                remaining_quantity__gt=0,
                expiry_date__isnull=False,
                expiry_date__lt=today,
            ).count(),
            'expiring': expiring,
        })


# ═══ STOCK LEDGER (read-only) ═══════════════════════════════════════════════

class StockLedgerViewSet(GroceryBaseViewSet):
    """Append-only ledger. No write verbs are routed at all."""

    http_method_names = ['get', 'head', 'options']
    serializer_class = StockTransactionSerializer
    queryset = GroceryStockTransaction.objects.select_related(
        'item', 'item__unit', 'warehouse', 'location', 'location__warehouse',
        'batch', 'department',
    )
    search_fields = ['item__name', 'reference_number',
                     'batch__batch_number']
    ordering_fields = ['moved_on', 'created_at', 'quantity', 'value']

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for param, field in (('item', 'item_id'),
                             ('warehouse', 'warehouse_id'),
                             ('department', 'department_id'),
                             ('batch', 'batch_id')):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        movement = params.get('movement_type')
        if movement:
            queryset = queryset.filter(movement_type=movement.upper())

        date_from = params.get('from')
        date_to = params.get('to')
        if date_from:
            queryset = queryset.filter(moved_on__gte=date_from)
        if date_to:
            queryset = queryset.filter(moved_on__lte=date_to)

        return queryset.order_by('-moved_on', '-id')

    @action(detail=False, methods=['get'])
    def by_department(self, request):
        """
        Consumption cost per department (specification section 9).

        Values are negated because issue rows are stored signed-negative; a
        report reads better as a positive cost of consumption.
        """
        queryset = self.get_queryset().filter(department__isnull=False)

        rows = (queryset
                .values('department', 'department__code', 'department__name')
                .annotate(
                    quantity=Coalesce(Sum('quantity'), Value(0),
                                      output_field=DecimalField()),
                    value=Coalesce(Sum('value'), Value(0),
                                   output_field=DecimalField()),
                    movements=Count('id'),
                )
                .order_by('department__name'))

        return Response([
            {
                'department': row['department'],
                'department_code': row['department__code'],
                'department_name': row['department__name'],
                'quantity_consumed': -row['quantity'],
                'cost_consumed': -row['value'],
                'movements': row['movements'],
            }
            for row in rows
        ])


# ═══ BATCHES / EXPIRY (read-only) ═══════════════════════════════════════════

class StockBatchViewSet(GroceryBaseViewSet):
    """Cost layers and expiry tracking (specification section 13)."""

    http_method_names = ['get', 'head', 'options']
    serializer_class = StockBatchSerializer
    queryset = GroceryStockBatch.objects.select_related(
        'item', 'item__unit', 'warehouse', 'location', 'location__warehouse', 'supplier',
    )
    search_fields = ['batch_number', 'item__name', 'reference_number']
    ordering_fields = ['expiry_date', 'received_on', 'remaining_quantity']

    def get_queryset(self):
        today = date.today()
        # days_to_expiry is NOT annotated: subtracting two dates in Postgres
        # yields an interval, not a number, so it cannot be typed as a numeric
        # output field. The serializer computes it in Python instead.
        queryset = super().get_queryset().annotate(
            expiry_status=Case(
                When(expiry_date__isnull=True, then=Value('NONE')),
                When(expiry_date__lt=today, then=Value('EXPIRED')),
                When(expiry_date__lte=today + timedelta(days=30), then=Value('CRITICAL')),
                When(expiry_date__lte=today + timedelta(days=60), then=Value('WARNING')),
                When(expiry_date__lte=today + timedelta(days=90), then=Value('NOTICE')),
                default=Value('OK'),
                output_field=CharField(),
            ),
        )

        params = self.request.query_params
        for param, field in (('item', 'item_id'), ('warehouse', 'warehouse_id')):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        # Default to layers that still hold stock — an exhausted batch is
        # history, and the expiry screens only care about what is on the shelf.
        if (params.get('include_exhausted') or '').lower() not in TRUE_VALUES:
            queryset = queryset.filter(remaining_quantity__gt=0)

        # The expiry report only concerns dated stock — an undated layer can
        # never trigger an alert, so listing it is noise.
        if (params.get('has_expiry') or '').lower() in TRUE_VALUES:
            queryset = queryset.filter(expiry_date__isnull=False)

        expiry_status = (params.get('expiry_status') or '').upper()
        if expiry_status:
            queryset = queryset.filter(expiry_status=expiry_status)

        within = params.get('expiring_within')
        if within and within.isdigit():
            queryset = queryset.filter(
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=today + timedelta(days=int(within)),
            )

        return queryset.order_by(F('expiry_date').asc(nulls_last=True), 'received_on')


# ═══ VALUATION (read-only) ══════════════════════════════════════════════════

class ItemValuationViewSet(GroceryBaseViewSet):
    """Inventory valuation and cost variance (specification sections 8 and 15)."""

    http_method_names = ['get', 'head', 'options']
    serializer_class = ItemValuationSerializer
    queryset = GroceryItemValuation.objects.select_related(
        'item', 'item__unit', 'item__category',
    )
    search_fields = ['item__name']
    ordering_fields = ['item__name', 'quantity_on_hand', 'total_value', 'average_cost']

    def get_queryset(self):
        queryset = super().get_queryset().annotate(
            cost_variance=Coalesce(
                F('average_cost') - F('item__standard_cost'),
                Value(0), output_field=DecimalField(max_digits=14, decimal_places=4),
            ),
        )

        params = self.request.query_params
        category = params.get('category')
        if category:
            queryset = queryset.filter(item__category_id=category)

        if (params.get('nonzero') or '').lower() in TRUE_VALUES:
            queryset = queryset.filter(quantity_on_hand__gt=0)

        return queryset.order_by('item__name')
