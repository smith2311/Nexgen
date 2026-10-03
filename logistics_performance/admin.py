from django.contrib import admin
from .models import PincodeMaster, DomesticSalesCard, CourierMaster, ZoneMaster, ModeMaster, StateMaster, PlantMaster


@admin.register(PlantMaster)
class PlantMasterAdmin(admin.ModelAdmin):
    list_display = ['plant_name', 'plant_company', 'plant_email', 'updated_at']
    search_fields = ['plant_name', 'plant_company', 'plant_email']
    ordering = ['plant_name']


@admin.register(ZoneMaster)
class ZoneMasterAdmin(admin.ModelAdmin):
    list_display = ['zone', 'updated_at']
    search_fields = ['zone']
    ordering = ['zone']


@admin.register(ModeMaster)
class ModeMasterAdmin(admin.ModelAdmin):
    list_display = ['mode', 'updated_at']
    search_fields = ['mode']
    ordering = ['mode']


@admin.register(StateMaster)
class StateMasterAdmin(admin.ModelAdmin):
    list_display = ['state', 'updated_at']
    search_fields = ['state']
    ordering = ['state']


@admin.register(PincodeMaster)
class PincodeMasterAdmin(admin.ModelAdmin):
    list_display = ['pincode', 'location', 'state_name', 'courier_name', 'tat', 'rate', 'updated_at']
    list_filter = ['state_name', 'courier_name', 'tat']
    search_fields = ['pincode', 'location', 'state_name']
    ordering = ['-updated_at']


@admin.register(CourierMaster)
class CourierMasterAdmin(admin.ModelAdmin):
    list_display = ['courier_code', 'courier_name', 'updated_at']
    search_fields = ['courier_code', 'courier_name']
    ordering = ['-updated_at']


@admin.register(DomesticSalesCard)
class DomesticSalesCardAdmin(admin.ModelAdmin):
    list_display = ['division', 'sales_type', 'pending_show', 'created_at']
    list_filter = ['division', 'pending_show']
    search_fields = ['sales_type', 'sales_type_description']
    ordering = ['-created_at']


from .models import PincodeMaster, DomesticSalesCard, LogisticsPerformance, DivisionMaster


@admin.register(LogisticsPerformance)
class LogisticsPerformanceAdmin(admin.ModelAdmin):
    list_display = ['division', 'docket_no', 'courier_name', 'dispatch_date', 'delivery_date', 'tat_from_master']
    list_filter = ['division', 'courier_name', 'mode']
    search_fields = ['docket_no', 'courier_code', 'shipto_name', 'pincode']
    ordering = ['-created_at']


@admin.register(DivisionMaster)
class DivisionMasterAdmin(admin.ModelAdmin):
    list_display  = ['companyname', 'division', 'created_by', 'created_at', 'is_deleted']
    list_filter   = ['is_deleted', 'companyname']
    search_fields = ['companyname', 'division']
    ordering      = ['-updated_at']
