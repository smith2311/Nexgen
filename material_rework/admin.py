from django.contrib import admin
from django.utils.html import format_html
from .models import Vertical, Department, DispositionBatch, DispositionItem


@admin.register(Vertical)
class VerticalAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'vertical', 'hod_email', 'warehouse_email', 'is_active')
    list_filter = ('vertical', 'is_active')
    search_fields = ('name', 'hod_email', 'vertical__name')
    autocomplete_fields = ('vertical',)


class DispositionItemInline(admin.TabularInline):
    model = DispositionItem
    extra = 0
    fields = ('material', 'batch_no', 'qty', 'box_no', 'remark', 'accepted_qty', 'pending_qty', 'status')
    readonly_fields = ('production_reviewed_at', 'warehouse_received_at')


STATUS_COLORS = {
    'pending_hod':        '#f59e0b',
    'hod_approved':       '#3b82f6',
    'hod_rejected':       '#ef4444',
    'production_done':    '#8b5cf6',
    'completed':          '#10b981',
}

ITEM_STATUS_COLORS = {
    'pending_hod':        '#f59e0b',
    'hod_rejected':       '#ef4444',
    'pending_production': '#3b82f6',
    'accepted':           '#10b981',
    'rejected':           '#ef4444',
    'partially_accepted': '#f97316',
    'pending_warehouse':  '#8b5cf6',
    'received':           '#059669',
}


@admin.register(DispositionBatch)
class DispositionBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'vertical', 'department', 'uploaded_by_email', 'uploaded_at', 'batch_status', 'hod_reviewed_at')
    list_filter = ('status', 'vertical', 'department')
    search_fields = ('uploaded_by_email', 'uploaded_by_name', 'department__name')
    readonly_fields = ('approval_token', 'approval_token_claimed_at', 'approval_token_session_key', 'uploaded_at', 'hod_reviewed_at')
    inlines = (DispositionItemInline,)

    def batch_status(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:.8rem;">{}</span>',
            color, obj.get_status_display(),
        )
    batch_status.short_description = 'Status'


@admin.register(DispositionItem)
class DispositionItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch', 'material', 'batch_no', 'qty', 'accepted_qty', 'pending_qty', 'item_status')
    list_filter = ('status', 'batch__vertical', 'batch__department')
    search_fields = ('material', 'batch_no', 'box_no')
    readonly_fields = ('production_reviewed_at', 'warehouse_received_at')

    def item_status(self, obj):
        color = ITEM_STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:.8rem;">{}</span>',
            color, obj.get_status_display(),
        )
    item_status.short_description = 'Status'
