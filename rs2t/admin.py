from django.contrib import admin

from .models import TranslationSession


@admin.register(TranslationSession)
class TranslationSessionAdmin(admin.ModelAdmin):
    list_display = ["user", "title", "ended_at", "duration_seconds", "word_count", "speaker_count"]
    list_filter = ["user", "target_language"]
    search_fields = ["user__username", "title", "summary"]
    readonly_fields = ["ended_at", "transcript", "summary"]
    ordering = ["-ended_at"]


from .models import GlossaryTerm  # noqa: E402


@admin.register(GlossaryTerm)
class GlossaryTermAdmin(admin.ModelAdmin):
    list_display = ("term", "hint", "user", "created_at")
    list_filter = ("user",)
    search_fields = ("term", "hint")
