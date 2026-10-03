from django.contrib import admin

from .models import VisaApplication, VisaApplicationLog, VisaCompany, VisaCountry, VisaLetterTemplate


@admin.register(VisaCompany)
class VisaCompanyAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'cin', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'name']


@admin.register(VisaCountry)
class VisaCountryAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'embassy_city', 'default_entry_type', 'is_active']
    list_filter = ['is_active', 'default_entry_type']


@admin.register(VisaLetterTemplate)
class VisaLetterTemplateAdmin(admin.ModelAdmin):
    list_display = ['company', 'country', 'entry_type', 'subject_line', 'is_active', 'updated_at']
    list_filter = ['is_active', 'company', 'country', 'entry_type']
    search_fields = ['subject_line']


class VisaApplicationLogInline(admin.TabularInline):
    model = VisaApplicationLog
    extra = 0
    readonly_fields = ['action', 'performed_by', 'notes', 'timestamp']
    can_delete = False


@admin.register(VisaApplication)
class VisaApplicationAdmin(admin.ModelAdmin):
    list_display = ['ref_no', 'applicant_name', 'company', 'country', 'status', 'created_at']
    list_filter = ['status', 'company', 'country']
    search_fields = ['ref_no', 'applicant_name', 'passport_number']
    readonly_fields = ['ref_no', 'created_at', 'updated_at']
    inlines = [VisaApplicationLogInline]
