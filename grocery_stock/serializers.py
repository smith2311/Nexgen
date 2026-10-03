"""
Grocery Stock & Cost Management — master data serializers
─────────────────────────────────────────────────────────────────────────────
Every serializer exposes the audit columns read-only. created_by and
updated_by are stamped by CurrentUserMiddleware, so a client that tries to
send them is ignored rather than trusted.

Decimals are rendered as strings by DRF's default ``COERCE_DECIMAL_TO_STRING``,
which is what the Flutter client needs — a JSON float would lose precision on
costs and totals.
"""
from rest_framework import serializers

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

AUDIT_FIELDS = ('created_at', 'updated_at', 'created_by', 'updated_by')


class AuditedModelSerializer(serializers.ModelSerializer):
    """Base that makes the shared audit columns visible but never writable."""

    class Meta:
        abstract = True
        read_only_fields = AUDIT_FIELDS


# ── Category / Brand / Unit ─────────────────────────────────────────────────

class GroceryCategorySerializer(AuditedModelSerializer):
    item_count = serializers.IntegerField(read_only=True)

    class Meta(AuditedModelSerializer.Meta):
        model = GroceryCategory
        fields = ('id', 'code', 'name', 'description', 'is_active',
                  'item_count') + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + ('item_count',)


class GroceryBrandSerializer(AuditedModelSerializer):
    class Meta(AuditedModelSerializer.Meta):
        model = GroceryBrand
        fields = ('id', 'code', 'name', 'is_active') + AUDIT_FIELDS


class GroceryUnitSerializer(AuditedModelSerializer):
    base_unit_code = serializers.CharField(source='base_unit.code', read_only=True)

    class Meta(AuditedModelSerializer.Meta):
        model = GroceryUnit
        fields = ('id', 'code', 'name', 'base_unit', 'base_unit_code',
                  'conversion_factor', 'is_active') + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + ('base_unit_code',)

    def validate(self, attrs):
        """A unit either converts into another one or it doesn't — not half."""
        base_unit = attrs.get('base_unit', getattr(self.instance, 'base_unit', None))
        factor = attrs.get('conversion_factor',
                           getattr(self.instance, 'conversion_factor', None))

        if base_unit and not factor:
            raise serializers.ValidationError({
                'conversion_factor': 'Required when a base unit is set.',
            })
        if factor and not base_unit:
            raise serializers.ValidationError({
                'base_unit': 'Required when a conversion factor is set.',
            })
        if factor is not None and factor <= 0:
            raise serializers.ValidationError({
                'conversion_factor': 'Must be greater than zero.',
            })
        if base_unit and self.instance and base_unit.pk == self.instance.pk:
            raise serializers.ValidationError({
                'base_unit': 'A unit cannot convert into itself.',
            })
        return attrs


# ── Supplier ────────────────────────────────────────────────────────────────

class GrocerySupplierSerializer(AuditedModelSerializer):
    class Meta(AuditedModelSerializer.Meta):
        model = GrocerySupplier
        fields = ('id', 'name', 'phone', 'email', 'address', 'payment_terms',
                  'credit_days', 'is_active') + AUDIT_FIELDS


# ── Warehouse / bin location ────────────────────────────────────────────────

class GroceryWarehouseSerializer(AuditedModelSerializer):
    location_count = serializers.IntegerField(read_only=True)

    class Meta(AuditedModelSerializer.Meta):
        model = GroceryWarehouse
        fields = ('id', 'code', 'name', 'address', 'is_active',
                  'location_count') + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + ('location_count',)


class GroceryLocationSerializer(AuditedModelSerializer):
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta(AuditedModelSerializer.Meta):
        model = GroceryLocation
        fields = ('id', 'warehouse', 'warehouse_code', 'warehouse_name',
                  'code', 'name', 'is_active') + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + ('warehouse_code', 'warehouse_name')


# ── Department ──────────────────────────────────────────────────────────────

class GroceryDepartmentSerializer(AuditedModelSerializer):
    class Meta(AuditedModelSerializer.Meta):
        model = GroceryDepartment
        fields = ('id', 'code', 'name', 'is_active') + AUDIT_FIELDS


# ── Item ────────────────────────────────────────────────────────────────────

class GroceryItemListSerializer(AuditedModelSerializer):
    """
    Trimmed payload for list responses. The item list is the most-hit endpoint
    in the app, so it does not carry the costing or stock-level fields.
    """
    category_name = serializers.CharField(source='category.name', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)

    class Meta:
        model = GroceryItem
        fields = ('id', 'name', 'local_name',
                  'category', 'category_name', 'brand', 'brand_name',
                  'unit', 'unit_code', 'gst_percent', 'is_active')
        read_only_fields = fields


class GroceryItemSerializer(AuditedModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    storage_location_label = serializers.CharField(
        source='storage_location.__str__', read_only=True,
    )

    class Meta(AuditedModelSerializer.Meta):
        model = GroceryItem
        fields = (
            'id', 'name', 'local_name',
            'category', 'category_name', 'brand', 'brand_name',
            'unit', 'unit_code',
            'gst_percent',
            'reorder_level', 'minimum_stock', 'maximum_stock',
            'shelf_life_days', 'expiry_required',
            'storage_location', 'storage_location_label',
            'standard_cost', 'valuation_method', 'is_gst_creditable',
            'is_active',
        ) + AUDIT_FIELDS
        read_only_fields = AUDIT_FIELDS + (
            'category_name', 'brand_name', 'unit_code', 'storage_location_label',
        )

    def validate(self, attrs):
        def resolved(field):
            return attrs.get(field, getattr(self.instance, field, None))

        minimum = resolved('minimum_stock')
        maximum = resolved('maximum_stock')
        reorder = resolved('reorder_level')

        if minimum is not None and maximum is not None and maximum > 0 and minimum > maximum:
            raise serializers.ValidationError({
                'minimum_stock': 'Minimum stock cannot exceed maximum stock.',
            })
        if reorder is not None and maximum is not None and maximum > 0 and reorder > maximum:
            raise serializers.ValidationError({
                'reorder_level': 'Reorder level cannot exceed maximum stock.',
            })

        # An item that needs an expiry date needs a shelf life to derive it from,
        # otherwise the expiry alerts in section 13 have nothing to count down.
        if resolved('expiry_required') and not resolved('shelf_life_days'):
            raise serializers.ValidationError({
                'shelf_life_days': 'Required when expiry is tracked for this item.',
            })

        # A default bin must sit in a warehouse — enforced here rather than in
        # the model so the client gets a field-level error it can display.
        location = resolved('storage_location')
        if location is not None and not location.is_active:
            raise serializers.ValidationError({
                'storage_location': 'That bin location is inactive.',
            })
        return attrs
