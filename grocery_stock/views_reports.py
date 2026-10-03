"""
Grocery Stock — dashboard and reports API (Phase 3)
─────────────────────────────────────────────────────────────────────────────
Specification sections 1 (Dashboard) and 15 (Reports).

Everything here is read-only and derived from the Phase 2 ledger. Two rules:

  * Aggregate in the database, never in Python. A stock ledger only grows, so
    pulling rows back to sum them gets slower every month; every figure below
    is one GROUP BY.
  * Ledger values are signed — receipts positive, issues negative. Reports
    negate the issue side so a consumption figure reads as a positive cost.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    DocumentStatus,
    GroceryGoodsReceipt,
    GroceryItem,
    GroceryItemValuation,
    GroceryStock,
    GroceryStockBatch,
    GroceryStockTransaction,
    StockMovementType,
)
from .roles import GroceryRolePermission
from .serializers_txn import AUDIT_FIELDS  # noqa: F401  (keeps import surface obvious)

MONTHS_OF_HISTORY = 12
TOP_N = 8

_DEC = DecimalField(max_digits=18, decimal_places=2)


def _zero(expression):
    """Coalesce an aggregate to 0 so the client never has to null-check."""
    return Coalesce(expression, Value(Decimal('0')), output_field=_DEC)


def _month_start(reference, months_back):
    """First of the month, ``months_back`` months before ``reference``."""
    year = reference.year
    month = reference.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


class GroceryDashboardView(APIView):
    """
    GET /grocery/api/v1/dashboard/

    Every KPI and chart series from specification section 1, in one request.
    The dashboard is the first screen a user opens, so it is deliberately one
    round trip rather than ten.
    """

    permission_classes = [GroceryRolePermission]

    def get(self, request):
        today = date.today()
        month_start = today.replace(day=1)
        window_start = _month_start(today, MONTHS_OF_HISTORY - 1)

        return Response({
            'as_of': today,
            'kpis': self._kpis(today, month_start),
            'monthly': self._monthly(window_start),
            'category_value': self._category_value(),
            'supplier_purchase': self._supplier_purchase(window_start),
            'top_items': self._top_items(window_start),
            'expiry_buckets': self._expiry_buckets(today),
        })

    # ── KPIs ────────────────────────────────────────────────────────────────

    def _kpis(self, today, month_start):
        valuation = GroceryItemValuation.objects.aggregate(
            inventory_value=_zero(Sum('total_value')),
            stock_quantity=_zero(Sum('quantity_on_hand')),
            # Section 15 "Cost Variance Report", valued across stock on hand:
            # (actual average − benchmark) × quantity.
            cost_variance=_zero(Sum(
                (F('average_cost') - F('item__standard_cost')) * F('quantity_on_hand'),
                output_field=_DEC,
            )),
        )

        # One pass over stock, bucketed by status, rather than three counts.
        stock_states = GroceryStock.objects.aggregate(
            low=Count('pk', filter=Q(item__reorder_level__gt=0,
                                     quantity_available__gt=0,
                                     quantity_available__lte=F('item__reorder_level'))),
            out=Count('pk', filter=Q(quantity_available__lte=0)),
            lines=Count('pk'),
        )

        month_moves = (GroceryStockTransaction.objects
                       .filter(moved_on__gte=month_start)
                       .aggregate(
                           purchases=_zero(Sum('value', filter=Q(
                               movement_type=StockMovementType.RECEIPT))),
                           consumption=_zero(Sum('value', filter=Q(
                               movement_type=StockMovementType.ISSUE))),
                       ))

        return {
            'inventory_value': valuation['inventory_value'],
            'stock_quantity': valuation['stock_quantity'],
            'cost_variance': valuation['cost_variance'],
            'stock_lines': stock_states['lines'],
            'low_stock_items': stock_states['low'],
            'out_of_stock_items': stock_states['out'],
            'month_purchase_cost': month_moves['purchases'],
            # Issues are stored negative; a cost reads better positive.
            'month_consumption_cost': -month_moves['consumption'],
            'active_items': GroceryItem.objects.filter(is_active=True).count(),
            'expiring_30_days': GroceryStockBatch.objects.filter(
                remaining_quantity__gt=0, expiry_date__isnull=False,
                expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30),
            ).count(),
            'expired_batches': GroceryStockBatch.objects.filter(
                remaining_quantity__gt=0, expiry_date__isnull=False,
                expiry_date__lt=today,
            ).count(),
            'draft_receipts': GroceryGoodsReceipt.objects.filter(
                status=DocumentStatus.DRAFT).count(),
        }

    # ── Charts ──────────────────────────────────────────────────────────────

    def _monthly(self, window_start):
        """Purchase and consumption cost by month — sections 1 and 15."""
        rows = (GroceryStockTransaction.objects
                .filter(moved_on__gte=window_start)
                .annotate(month=TruncMonth('moved_on'))
                .values('month')
                .annotate(
                    purchases=_zero(Sum('value', filter=Q(
                        movement_type=StockMovementType.RECEIPT))),
                    consumption=_zero(Sum('value', filter=Q(
                        movement_type=StockMovementType.ISSUE))),
                )
                .order_by('month'))

        by_month = {r['month']: r for r in rows}

        # Emit a continuous series so a quiet month is a gap in the line, not a
        # missing point that the chart would silently close over.
        series = []
        cursor = window_start
        today = date.today()
        while cursor <= today.replace(day=1):
            row = by_month.get(cursor)
            series.append({
                'month': cursor.strftime('%Y-%m'),
                'label': cursor.strftime('%b %Y'),
                'purchases': row['purchases'] if row else Decimal('0'),
                'consumption': -row['consumption'] if row else Decimal('0'),
            })
            cursor = _month_start(cursor, -1) if cursor.month < 12 \
                else date(cursor.year + 1, 1, 1)
        return series

    def _category_value(self):
        """Inventory value by category (section 1 'Category-wise Cost')."""
        rows = (GroceryItemValuation.objects
                .filter(quantity_on_hand__gt=0)
                .values('item__category__name')
                .annotate(value=_zero(Sum('total_value')))
                .order_by('-value')[:TOP_N])
        return [{'name': r['item__category__name'] or 'Uncategorised',
                 'value': r['value']} for r in rows]

    def _supplier_purchase(self, window_start):
        rows = (GroceryGoodsReceipt.objects
                .filter(status=DocumentStatus.POSTED, receipt_date__gte=window_start)
                .values('supplier__name')
                .annotate(value=_zero(Sum('inventory_value')), receipts=Count('pk'))
                .order_by('-value')[:TOP_N])
        return [{'name': r['supplier__name'], 'value': r['value'],
                 'receipts': r['receipts']} for r in rows]

    def _top_items(self, window_start):
        rows = (GroceryStockTransaction.objects
                .filter(movement_type=StockMovementType.RECEIPT,
                        moved_on__gte=window_start)
                .values('item__name')
                .annotate(quantity=_zero(Sum('quantity')), value=_zero(Sum('value')))
                .order_by('-value')[:TOP_N])
        return [{'name': r['item__name'],
                 'quantity': r['quantity'], 'value': r['value']} for r in rows]

    def _expiry_buckets(self, today):
        """Section 13 alert bands, as a chart series."""
        batches = GroceryStockBatch.objects.filter(
            remaining_quantity__gt=0, expiry_date__isnull=False)

        def band(lo, hi):
            return batches.filter(
                expiry_date__gte=today + timedelta(days=lo),
                expiry_date__lte=today + timedelta(days=hi)).count()
        return [
            {'label': 'Expired', 'count': batches.filter(expiry_date__lt=today).count()},
            {'label': '≤ 30 days', 'count': band(0, 30)},
            {'label': '31–60 days', 'count': band(31, 60)},
            {'label': '61–90 days', 'count': band(61, 90)},
        ]


class ConsumptionReportViewSet(viewsets.ViewSet):
    """
    GET /grocery/api/v1/consumption/

    Department consumption by item (specification section 9 reports and
    section 15 'Department Consumption Cost').

    A ViewSet rather than a ModelViewSet: the rows are aggregates, not model
    instances, so there is no queryset to serialise.
    """

    permission_classes = [GroceryRolePermission]

    def list(self, request):
        queryset = GroceryStockTransaction.objects.filter(
            movement_type=StockMovementType.ISSUE)

        params = request.query_params
        for param, field in (('department', 'department_id'),
                             ('warehouse', 'warehouse_id'),
                             ('item', 'item_id'),
                             ('category', 'item__category_id')):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        if params.get('from'):
            queryset = queryset.filter(moved_on__gte=params['from'])
        if params.get('to'):
            queryset = queryset.filter(moved_on__lte=params['to'])

        search = (params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(item__name__icontains=search)
                | Q(department__name__icontains=search))

        rows = (queryset
                .values('department__name', 'item__name', 'item__unit__code')
                .annotate(
                    quantity=_zero(Sum('quantity')),
                    value=_zero(Sum('value')),
                    movements=Count('pk'),
                )
                .order_by('department__name', 'item__name'))

        results = [{
            'id': f"{r['department__name']}|{r['item__name']}",
            'department_name': r['department__name'],
            'item_name': r['item__name'],
            'unit_code': r['item__unit__code'],
            # Negate: issues are stored signed-negative.
            'quantity_consumed': -r['quantity'],
            'cost_consumed': -r['value'],
            'movements': r['movements'],
        } for r in rows]

        # Hand-rolled paging to match the DRF envelope the report script reads.
        page_size = 50
        try:
            page = max(1, int(params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        start = (page - 1) * page_size

        return Response({
            'count': len(results),
            'next': None if start + page_size >= len(results) else page + 1,
            'previous': None if page == 1 else page - 1,
            'results': results[start:start + page_size],
        })
