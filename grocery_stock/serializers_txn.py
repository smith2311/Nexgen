"""
Grocery Stock — transaction serializers (Phase 2)
─────────────────────────────────────────────────────────────────────────────
Kept separate from serializers.py so master data and documents stay readable
apart; both are imported by views.py.

Two rules run through everything here:

  * A posted document is immutable. Nothing but ``status`` changes after
    posting, and only the posting service changes that.
  * Computed money — landed cost, apportioned charges, line and issue values —
    is read-only. Those figures come from services/costing.py; accepting them
    from a client would let a caller write their own inventory valuation.
"""
from datetime import date

from rest_framework import serializers

from .models import (
    DocumentStatus,
    GroceryGoodsReceipt,
    GroceryGoodsReceiptLine,
    GroceryItemValuation,
    GroceryStock,
    GroceryStockBatch,
    GroceryStockAdjustment,
    GroceryStockAdjustmentLine,
    GroceryStockIssue,
    GroceryStockIssueLine,
    GroceryStockTransaction,
)

AUDIT_FIELDS = ('created_at', 'updated_at', 'created_by', 'updated_by')

# Everything the costing engine owns. Listed once, applied to both line
# serializers, so a new computed column cannot be left client-writable.
COMPUTED_LINE_FIELDS = (
    'taxable_amount', 'tax_amount', 'line_total',
    'apportioned_charges', 'landed_unit_cost', 'batch',
)


# ═══ GOODS RECEIPT ══════════════════════════════════════════════════════════

class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)

    class Meta:
        model = GroceryGoodsReceiptLine
        fields = (
            'id', 'item', 'item_name', 'unit_code',
            'location', 'location_label',
            'batch_number', 'manufacturing_date', 'expiry_date',
            'received_quantity', 'accepted_quantity', 'rejected_quantity',
            'unit_price', 'discount_amount', 'tax_percent',
        ) + COMPUTED_LINE_FIELDS
        read_only_fields = COMPUTED_LINE_FIELDS

    def validate(self, attrs):
        received = attrs.get('received_quantity', 0) or 0
        accepted = attrs.get('accepted_quantity', 0) or 0
        rejected = attrs.get('rejected_quantity', 0) or 0

        if accepted > received:
            raise serializers.ValidationError({
                'accepted_quantity': 'Cannot accept more than was received.',
            })
        # Not forced to be exact: goods can still be under inspection, so
        # accepted + rejected may legitimately be less than received.
        if accepted + rejected > received:
            raise serializers.ValidationError({
                'rejected_quantity':
                    'Accepted plus rejected cannot exceed the received quantity.',
            })
        return attrs


class GoodsReceiptSerializer(serializers.ModelSerializer):
    """Full GRN with writable nested lines."""

    lines = GoodsReceiptLineSerializer(many=True)

    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = GroceryGoodsReceipt
        fields = (
            'id', 'grn_number', 'status', 'status_display', 'is_editable',
            'supplier', 'supplier_name', 'warehouse', 'warehouse_name',
            'receipt_date', 'invoice_number', 'invoice_date',
            'freight_amount', 'loading_amount', 'insurance_amount', 'other_charges',
            'subtotal_amount', 'tax_amount', 'total_amount', 'inventory_value',
            'remarks', 'posted_at', 'posted_by', 'lines',
        ) + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + (
            'grn_number', 'status', 'posted_at', 'posted_by',
            'subtotal_amount', 'tax_amount', 'total_amount', 'inventory_value',
        )

    def validate(self, attrs):
        # A posted GRN has already moved stock and changed the weighted
        # average; editing it would silently invalidate both.
        if self.instance and self.instance.status != DocumentStatus.DRAFT:
            raise serializers.ValidationError(
                f'This receipt is {self.instance.get_status_display().lower()} '
                f'and can no longer be edited.'
            )
        return attrs

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError('Add at least one line.')
        return lines

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        receipt = GroceryGoodsReceipt.objects.create(**validated_data)
        for line in lines:
            GroceryGoodsReceiptLine.objects.create(receipt=receipt, **line)
        return receipt

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines', None)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if lines is not None:
            # Replace wholesale rather than diff by id. A draft's lines have no
            # downstream references yet, so rebuilding them is simpler and
            # cannot leave a half-updated set behind.
            instance.lines.all().delete()
            for line in lines:
                GroceryGoodsReceiptLine.objects.create(receipt=instance, **line)

        return instance


class GoodsReceiptListSerializer(serializers.ModelSerializer):
    """Trimmed payload for the list screen — no lines."""

    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GroceryGoodsReceipt
        fields = (
            'id', 'grn_number', 'status', 'status_display',
            'supplier', 'supplier_name', 'warehouse', 'warehouse_name',
            'receipt_date', 'invoice_number',
            'total_amount', 'inventory_value', 'line_count', 'posted_at',
        )
        read_only_fields = fields


# ═══ STOCK ISSUE ════════════════════════════════════════════════════════════

class StockIssueLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)

    class Meta:
        model = GroceryStockIssueLine
        fields = (
            'id', 'item', 'item_name', 'unit_code',
            'location', 'location_label', 'quantity', 'remarks',
            'unit_cost', 'line_value',
        )
        # Cost is decided at posting by the item's valuation method.
        read_only_fields = ('unit_cost', 'line_value')

    def validate_quantity(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError('Quantity must be greater than zero.')
        return value


class StockIssueSerializer(serializers.ModelSerializer):
    lines = StockIssueLineSerializer(many=True)

    department_name = serializers.CharField(source='department.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = GroceryStockIssue
        fields = (
            'id', 'issue_number', 'status', 'status_display', 'is_editable',
            'department', 'department_name', 'warehouse', 'warehouse_name',
            'issue_date', 'issued_to', 'purpose', 'total_value',
            'remarks', 'posted_at', 'posted_by', 'lines',
        ) + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + (
            'issue_number', 'status', 'posted_at', 'posted_by', 'total_value',
        )

    def validate(self, attrs):
        if self.instance and self.instance.status != DocumentStatus.DRAFT:
            raise serializers.ValidationError(
                f'This issue is {self.instance.get_status_display().lower()} '
                f'and can no longer be edited.'
            )
        return attrs

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError('Add at least one line.')
        return lines

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        issue = GroceryStockIssue.objects.create(**validated_data)
        for line in lines:
            GroceryStockIssueLine.objects.create(issue=issue, **line)
        return issue

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines', None)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                GroceryStockIssueLine.objects.create(issue=instance, **line)

        return instance


class StockIssueListSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GroceryStockIssue
        fields = (
            'id', 'issue_number', 'status', 'status_display',
            'department', 'department_name', 'warehouse', 'warehouse_name',
            'issue_date', 'issued_to', 'total_value', 'line_count', 'posted_at',
        )
        read_only_fields = fields


# ═══ READ-ONLY VIEWS OF STOCK ═══════════════════════════════════════════════

class StockSerializer(serializers.ModelSerializer):
    """Current stock (specification section 7)."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    category_name = serializers.CharField(source='item.category.name', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)

    reorder_level = serializers.DecimalField(
        source='item.reorder_level', max_digits=14, decimal_places=3, read_only=True)
    average_cost = serializers.DecimalField(
        source='item.valuation.average_cost', max_digits=14, decimal_places=4,
        read_only=True)

    # Annotated in the viewset so the low-stock filter and this flag agree.
    stock_status = serializers.CharField(read_only=True)
    stock_value = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = GroceryStock
        fields = (
            'id', 'item', 'item_name', 'unit_code', 'category_name',
            'warehouse', 'warehouse_code', 'warehouse_name',
            'location', 'location_label',
            'quantity_available', 'quantity_reserved', 'quantity_blocked',
            'quantity_in_transit',
            'reorder_level', 'average_cost', 'stock_value', 'stock_status',
        )
        read_only_fields = fields


class StockTransactionSerializer(serializers.ModelSerializer):
    """One line of the stock ledger (specification section 15)."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    movement_display = serializers.CharField(
        source='get_movement_type_display', read_only=True)

    class Meta:
        model = GroceryStockTransaction
        fields = (
            'id', 'item', 'item_name', 'unit_code',
            'warehouse', 'warehouse_code', 'location', 'location_label',
            'batch', 'batch_number', 'department', 'department_name',
            'movement_type', 'movement_display', 'moved_on',
            'quantity', 'unit_cost', 'value', 'balance_quantity',
            'reference_type', 'reference_id', 'reference_number', 'remarks',
            'created_at', 'created_by',
        )
        read_only_fields = fields


class StockBatchSerializer(serializers.ModelSerializer):
    """Batch / cost layer, with the expiry countdown from section 13."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)

    # Computed here rather than annotated: a date difference is an interval in
    # Postgres, which will not cast to an integer column.
    days_to_expiry = serializers.SerializerMethodField()
    expiry_status = serializers.CharField(read_only=True)

    def get_days_to_expiry(self, batch):
        if not batch.expiry_date:
            return None
        return (batch.expiry_date - date.today()).days

    class Meta:
        model = GroceryStockBatch
        fields = (
            'id', 'item', 'item_name', 'unit_code',
            'warehouse', 'warehouse_code', 'location', 'location_label',
            'supplier', 'supplier_name',
            'batch_number', 'manufacturing_date', 'expiry_date',
            'received_quantity', 'remaining_quantity', 'unit_cost',
            'received_on', 'reference_number',
            'days_to_expiry', 'expiry_status',
        )
        read_only_fields = fields


class ItemValuationSerializer(serializers.ModelSerializer):
    """Per-item inventory position and cost (specification section 8)."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    category_name = serializers.CharField(source='item.category.name', read_only=True)
    standard_cost = serializers.DecimalField(
        source='item.standard_cost', max_digits=14, decimal_places=4, read_only=True)
    valuation_method = serializers.CharField(
        source='item.valuation_method', read_only=True)

    # Section 15 "Cost Variance Report": actual average against the benchmark.
    cost_variance = serializers.DecimalField(
        max_digits=14, decimal_places=4, read_only=True)

    class Meta:
        model = GroceryItemValuation
        fields = (
            'id', 'item', 'item_name', 'unit_code', 'category_name',
            'quantity_on_hand', 'total_value', 'average_cost',
            'standard_cost', 'cost_variance', 'valuation_method',
            'last_purchase_cost', 'last_purchase_date',
        )
        read_only_fields = fields


# ═══ STOCK ADJUSTMENT (specification section 11) ════════════════════════════

class StockAdjustmentLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    unit_code = serializers.CharField(source='item.unit.code', read_only=True)
    location_label = serializers.CharField(source='location.__str__', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)

    class Meta:
        model = GroceryStockAdjustmentLine
        fields = (
            'id', 'item', 'item_name', 'unit_code',
            'location', 'location_label', 'batch', 'batch_number',
            'counted_quantity', 'remarks',
            'system_quantity', 'difference', 'unit_cost', 'line_value',
        )
        # The before/after audit trail is established at posting from the
        # locked stock row, never taken from the client.
        read_only_fields = ('system_quantity', 'difference', 'unit_cost', 'line_value')

    def validate_counted_quantity(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError('A counted quantity cannot be negative.')
        return value


class StockAdjustmentSerializer(serializers.ModelSerializer):
    lines = StockAdjustmentLineSerializer(many=True)

    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    reason_display = serializers.CharField(source='get_reason_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = GroceryStockAdjustment
        fields = (
            'id', 'adjustment_number', 'status', 'status_display', 'is_editable',
            'warehouse', 'warehouse_name', 'reason', 'reason_display',
            'adjustment_date', 'reference', 'total_value',
            'remarks', 'posted_at', 'posted_by', 'lines',
        ) + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + (
            'adjustment_number', 'status', 'posted_at', 'posted_by', 'total_value',
        )

    def validate(self, attrs):
        if self.instance and self.instance.status != DocumentStatus.DRAFT:
            raise serializers.ValidationError(
                f'This adjustment is {self.instance.get_status_display().lower()} '
                f'and can no longer be edited.'
            )
        return attrs

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError('Add at least one line.')
        return lines

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        adjustment = GroceryStockAdjustment.objects.create(**validated_data)
        for line in lines:
            GroceryStockAdjustmentLine.objects.create(adjustment=adjustment, **line)
        return adjustment

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                GroceryStockAdjustmentLine.objects.create(adjustment=instance, **line)
        return instance


class StockAdjustmentListSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    reason_display = serializers.CharField(source='get_reason_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GroceryStockAdjustment
        fields = (
            'id', 'adjustment_number', 'status', 'status_display',
            'warehouse', 'warehouse_name', 'reason', 'reason_display',
            'adjustment_date', 'reference', 'total_value', 'line_count', 'posted_at',
        )
        read_only_fields = fields
