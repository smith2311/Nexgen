from django.contrib import admin
from django.utils.html import format_html
from .models import (
    WarehouseInspectionBuilding,
    ConditionCategory,
    WarehouseInspectionCheckpoint,
    WarehouseInspectionFloor,
    WarehouseInspectionFloorAssignment,
    WarehouseInspectionReportRecipient,
    WarehouseInspectionResponse,
    WarehouseInspectionSecondResponse,
    WarehouseInspectionSecondSubmission,
    WarehouseInspectionSubmission,
)
from .serializers import get_warehouse_user_identity


@admin.register(WarehouseInspectionBuilding)
class WarehouseInspectionBuildingAdmin(admin.ModelAdmin):
    list_display = ('building_id', 'building_name', 'building_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('building_name',)
    ordering = ('building_order', 'building_name')
    readonly_fields = ('building_id', 'created_at', 'updated_at')


@admin.register(ConditionCategory)
class ConditionCategoryAdmin(admin.ModelAdmin):
    list_display = (
        'condition_category_id', 'building', 'condition_category_name',
        'created_by', 'status_badge', 'created_at'
    )
    list_filter = ('building', 'is_deleted', 'created_at')
    search_fields = ('condition_category_name', 'building__building_name', 'created_by')
    readonly_fields = (
        'condition_category_id', 'created_by', 'created_at',
        'deleted_by', 'deleted_at',
    )

    fieldsets = (
        ('Category Information', {
            'fields': ('condition_category_id', 'building', 'condition_category_name', 'description')
        }),
        ('Settings', {
            'fields': ('is_deleted',)
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'deleted_by', 'deleted_at'),
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        obj.entity_type = 'admin'
        if not obj.created_by:
            obj.created_by, _ = get_warehouse_user_identity(request.user)
        super().save_model(request, obj, form, change)

    def status_badge(self, obj):
        if obj.is_deleted:
            color = '#ef4444'
            text = 'Deleted'
        else:
            color = '#10b981'
            text = 'Active'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px;">{}</span>',
            color, text
        )
    status_badge.short_description = 'Status'


class WarehouseInspectionFloorAssignmentInline(admin.TabularInline):
    model = WarehouseInspectionFloorAssignment
    extra = 0
    fields = ('floor', 'is_applicable', 'responsible_email')
    autocomplete_fields = ('floor',)


@admin.register(WarehouseInspectionCheckpoint)
class WarehouseInspectionCheckpointAdmin(admin.ModelAdmin):
    list_display = (
        'checkpoint_id', 'condition_category', 'checkpoint_name',
        'internal_department_email', 'checkpoint_order', 'is_active', 'created_at'
    )
    list_filter = ('is_active', 'condition_category__building', 'condition_category', 'created_at')
    search_fields = (
        'checkpoint_name',
        'condition_category__building__building_name',
        'condition_category__condition_category_name',
        'internal_department_email',
        'floor_assignments__responsible_email',
    )
    readonly_fields = ('checkpoint_id', 'created_at', 'updated_at')

    fieldsets = (
        ('Checkpoint Information', {
            'fields': (
                'checkpoint_id', 'condition_category', 'checkpoint_name',
                'checkpoint_description', 'internal_department_email'
            )
        }),
        ('Settings', {
            'fields': ('checkpoint_order', 'is_active', 'created_by')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ('condition_category', 'checkpoint_order')
    inlines = (WarehouseInspectionFloorAssignmentInline,)


@admin.register(WarehouseInspectionFloor)
class WarehouseInspectionFloorAdmin(admin.ModelAdmin):
    list_display = ('floor_id', 'building', 'floor_name', 'floor_order', 'is_active', 'updated_at')
    list_filter = ('building', 'is_active')
    search_fields = ('floor_name', 'building__building_name')
    ordering = ('building', 'floor_order', 'floor_name')
    readonly_fields = ('floor_id', 'created_at', 'updated_at')


@admin.register(WarehouseInspectionReportRecipient)
class WarehouseInspectionReportRecipientAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'is_active', 'created_by', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('email', 'name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(WarehouseInspectionFloorAssignment)
class WarehouseInspectionFloorAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'assignment_id', 'condition_name', 'checkpoint', 'floor',
        'is_applicable', 'responsible_email'
    )
    list_filter = ('floor__building', 'floor', 'is_applicable', 'checkpoint__condition_category')
    search_fields = (
        'checkpoint__checkpoint_name',
        'checkpoint__condition_category__building__building_name',
        'checkpoint__condition_category__condition_category_name',
        'floor__floor_name',
        'responsible_email',
    )
    autocomplete_fields = ('checkpoint', 'floor')
    readonly_fields = ('assignment_id', 'created_at', 'updated_at')

    def condition_name(self, obj):
        return obj.checkpoint.condition_category.condition_category_name
    condition_name.short_description = 'Condition'


def _photo_thumbnail(photo):
    if not photo:
        return '-'
    return format_html(
        '<a href="{0}" target="_blank" rel="noopener">'
        '<img src="{0}" style="max-height:70px; max-width:110px; border-radius:4px; border:1px solid #d1d5db;">'
        '</a>',
        photo.url,
    )


@admin.register(WarehouseInspectionResponse)
class WarehouseInspectionResponseAdmin(admin.ModelAdmin):
    list_display = (
        'response_id', 'submission', 'checkpoint', 'response_badge',
        'photo_thumbnail', 'responsible_email', 'internal_department_email',
        'responded_by_email', 'warehouse_floor', 'responded_at'
    )
    list_filter = ('response', 'floor', 'warehouse_floor', 'responded_at')
    search_fields = (
        'checkpoint__checkpoint_name',
        'responded_by_email',
        'warehouse_floor',
        'responsible_email',
        'internal_department_email',
    )
    readonly_fields = ('response_id', 'responded_at', 'photo_thumbnail')

    fieldsets = (
        ('Response Information', {
            'fields': (
                'response_id', 'submission', 'checkpoint', 'floor',
                'floor_assignment', 'response', 'comments', 'photo', 'photo_thumbnail'
            )
        }),
        ('Emails, Location & User', {
            'fields': (
                'warehouse_floor', 'responsible_email', 'internal_department_email',
                'responded_by_email'
            )
        }),
        ('Timestamps', {
            'fields': ('responded_at',),
            'classes': ('collapse',)
        }),
    )

    def response_badge(self, obj):
        colors = {
            'yes': '#10b981',
            'no': '#ef4444',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px;">{}</span>',
            colors.get(obj.response, '#6b7280'),
            obj.get_response_display()
        )
    response_badge.short_description = 'Response'

    def photo_thumbnail(self, obj):
        return _photo_thumbnail(obj.photo)
    photo_thumbnail.short_description = 'Photo'


@admin.register(WarehouseInspectionSecondResponse)
class WarehouseInspectionSecondResponseAdmin(admin.ModelAdmin):
    list_display = (
        'second_response_id', 'submission', 'checkpoint', 'response_badge',
        'photo_thumbnail', 'responsible_email', 'internal_department_email',
        'responded_by_email', 'warehouse_floor', 'responded_at'
    )
    list_filter = ('response', 'floor', 'warehouse_floor', 'responded_at')
    search_fields = (
        'checkpoint__checkpoint_name',
        'responded_by_email',
        'warehouse_floor',
        'responsible_email',
        'internal_department_email',
    )
    readonly_fields = ('second_response_id', 'responded_at', 'photo_thumbnail')

    def response_badge(self, obj):
        colors = {
            'yes': '#10b981',
            'no': '#ef4444',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px;">{}</span>',
            colors.get(obj.response, '#6b7280'),
            obj.get_response_display()
        )
    response_badge.short_description = 'Response'

    def photo_thumbnail(self, obj):
        return _photo_thumbnail(obj.photo)
    photo_thumbnail.short_description = 'Photo'


class WarehouseInspectionResponseInline(admin.TabularInline):
    model = WarehouseInspectionResponse
    fields = (
        'checkpoint', 'floor', 'response', 'comments',
        'responsible_email', 'internal_department_email', 'responded_at'
    )
    extra = 0
    readonly_fields = (
        'checkpoint', 'floor', 'response', 'comments',
        'responsible_email', 'internal_department_email', 'responded_at'
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class WarehouseInspectionSecondResponseInline(admin.TabularInline):
    model = WarehouseInspectionSecondResponse
    fields = (
        'checkpoint', 'floor', 'response', 'comments',
        'responsible_email', 'internal_department_email', 'responded_at'
    )
    extra = 0
    readonly_fields = (
        'checkpoint', 'floor', 'response', 'comments',
        'responsible_email', 'internal_department_email', 'responded_at'
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WarehouseInspectionSubmission)
class WarehouseInspectionSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        'submission_id', 'building', 'condition_category', 'floor', 'submitted_by_email',
        'submitted_at', 'email_sent', 'email_sent_at'
    )
    list_filter = ('building', 'condition_category', 'floor', 'email_sent', 'submitted_at')
    search_fields = (
        'submitted_by_email',
        'submitted_by_name',
        'building__building_name',
        'condition_category__condition_category_name',
        'floor__floor_name',
    )
    readonly_fields = (
        'submission_id', 'submitted_at', 'email_to', 'email_cc',
        'email_sent', 'email_sent_at', 'email_error'
    )
    inlines = (WarehouseInspectionResponseInline,)


@admin.register(WarehouseInspectionSecondSubmission)
class WarehouseInspectionSecondSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        'second_submission_id', 'first_submission', 'building', 'condition_category',
        'floor', 'submitted_by_email', 'inspection_date', 'submitted_at',
        'email_sent', 'email_sent_at'
    )
    list_filter = (
        'building', 'condition_category', 'floor',
        'inspection_date', 'email_sent', 'submitted_at'
    )
    search_fields = (
        'submitted_by_email',
        'submitted_by_name',
        'building__building_name',
        'condition_category__condition_category_name',
        'floor__floor_name',
    )
    readonly_fields = (
        'second_submission_id', 'first_submission', 'inspection_date',
        'submitted_at', 'email_to', 'email_cc',
        'email_sent', 'email_sent_at', 'email_error'
    )
    inlines = (WarehouseInspectionSecondResponseInline,)
