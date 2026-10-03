from django.contrib import admin

from .models import PRAssignment, PRLog, TrackingMaster


@admin.register(TrackingMaster)
class TrackingMasterAdmin(admin.ModelAdmin):
    list_display  = ('tracking_number', 'description', 'is_active', 'created_at')
    search_fields = ('tracking_number', 'description')
    list_filter   = ('is_active',)


@admin.register(PRAssignment)
class PRAssignmentAdmin(admin.ModelAdmin):
    list_display  = ('pr_number', 'responsible_person', 'assigned_person', 'status', 'assigned_at')
    search_fields = ('pr_number', 'po_number')
    list_filter   = ('status',)
    raw_id_fields = ('responsible_person', 'assigned_person', 'assigned_by', 'last_filled_by')


@admin.register(PRLog)
class PRLogAdmin(admin.ModelAdmin):
    list_display  = ('pr', 'action', 'performed_by', 'timestamp')
    search_fields = ('pr__pr_number', 'action')
