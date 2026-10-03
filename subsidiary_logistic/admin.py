from django.contrib import admin
from .models import SubLogisticEntry


@admin.register(SubLogisticEntry)
class SubLogisticEntryAdmin(admin.ModelAdmin):
    list_display  = ("country", "total_sales", "ob_logistics_cost", "operational_cost", "period_date", "is_deleted")
    list_filter   = ("period_date", "is_deleted")
    search_fields = ("country",)
    date_hierarchy = "period_date"
