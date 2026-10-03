from django.contrib import admin

from .meril_models import ProductCatalog
from .models import AgentConversation, ConversationMessage, SalesAgent


class ConversationMessageInline(admin.TabularInline):
    model = ConversationMessage
    extra = 0
    readonly_fields = ("role", "content", "timestamp")


@admin.register(SalesAgent)
class SalesAgentAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(AgentConversation)
class AgentConversationAdmin(admin.ModelAdmin):
    list_display = ("agent", "user", "session_id", "started_at", "ended_at")
    list_filter = ("agent",)
    search_fields = ("session_id",)
    inlines = [ConversationMessageInline]


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "timestamp")
    list_filter = ("role",)


@admin.register(ProductCatalog)
class ProductCatalogAdmin(admin.ModelAdmin):
    list_display = ("productname", "category", "subcategory", "availabilitystatus", "manufacturer")
    list_filter = ("availabilitystatus", "category", "subcategory")
    search_fields = ("productname", "description", "manufacturer")
    readonly_fields = ("id",)
