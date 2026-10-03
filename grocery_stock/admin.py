"""
Grocery Stock & Cost Management — Django admin
─────────────────────────────────────────────────────────────────────────────
The admin is a back door for support work, not the operational UI (that is the
Flutter client). It is registered so a superuser can correct master data
without a database console.

``get_queryset`` uses ``all_objects`` so soft-deleted rows remain visible and
recoverable here — the API hides them, the admin does not.
"""
from django.contrib import admin

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

AUDIT_READONLY = ('created_at', 'updated_at', 'created_by', 'updated_by',
                  'is_deleted', 'deleted_by', 'deleted_at')


class GroceryAdmin(admin.ModelAdmin):
    """Shared admin behaviour — audit columns visible, soft-deleted rows shown."""
    readonly_fields = AUDIT_READONLY
    list_per_page = 50

    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(GroceryCategory)
class GroceryCategoryAdmin(GroceryAdmin):
    list_display = ('code', 'name', 'is_active', 'is_deleted', 'updated_at')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('code', 'name')


@admin.register(GroceryBrand)
class GroceryBrandAdmin(GroceryAdmin):
    list_display = ('code', 'name', 'is_active', 'is_deleted', 'updated_at')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('code', 'name')


@admin.register(GroceryUnit)
class GroceryUnitAdmin(GroceryAdmin):
    list_display = ('code', 'name', 'base_unit', 'conversion_factor',
                    'is_active', 'is_deleted')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('code', 'name')


@admin.register(GrocerySupplier)
class GrocerySupplierAdmin(GroceryAdmin):
    list_display = ('name', 'phone', 'credit_days', 'is_active', 'is_deleted')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('name', 'phone', 'email')


@admin.register(GroceryWarehouse)
class GroceryWarehouseAdmin(GroceryAdmin):
    list_display = ('code', 'name', 'is_active', 'is_deleted', 'updated_at')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('code', 'name')


@admin.register(GroceryLocation)
class GroceryLocationAdmin(GroceryAdmin):
    list_display = ('warehouse', 'code', 'name', 'is_active', 'is_deleted')
    list_filter = ('warehouse', 'is_active', 'is_deleted')
    search_fields = ('code', 'name', 'warehouse__code', 'warehouse__name')
    autocomplete_fields = ('warehouse',)


@admin.register(GroceryDepartment)
class GroceryDepartmentAdmin(GroceryAdmin):
    list_display = ('code', 'name', 'is_active', 'is_deleted', 'updated_at')
    list_filter = ('is_active', 'is_deleted')
    search_fields = ('code', 'name')


@admin.register(GroceryItem)
class GroceryItemAdmin(GroceryAdmin):
    list_display = ('name', 'category', 'brand', 'unit',
                    'gst_percent', 'reorder_level', 'expiry_required',
                    'is_active', 'is_deleted')
    list_filter = ('category', 'brand', 'unit', 'expiry_required',
                   'valuation_method', 'is_active', 'is_deleted')
    search_fields = ('name', 'local_name')
    autocomplete_fields = ('category', 'brand', 'unit', 'storage_location')
    fieldsets = (
        ('Identity', {
            'fields': ('name', 'local_name'),
        }),
        ('Classification', {
            'fields': ('category', 'brand', 'unit'),
        }),
        ('Tax', {
            'fields': ('gst_percent', 'is_gst_creditable'),
        }),
        ('Stock levels', {
            'fields': ('reorder_level', 'minimum_stock', 'maximum_stock'),
        }),
        ('Shelf life', {
            'fields': ('shelf_life_days', 'expiry_required'),
        }),
        ('Storage & costing', {
            'fields': ('storage_location', 'standard_cost', 'valuation_method'),
        }),
        ('Status', {
            'fields': ('is_active',),
        }),
        ('Audit', {
            'classes': ('collapse',),
            'fields': AUDIT_READONLY,
        }),
    )
