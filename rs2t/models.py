from django.contrib.auth.models import User
from django.db import models


class TranslationSession(models.Model):
    """
    Persists one real-time translation session per authenticated user.
    At most MAX_HISTORY_PER_USER sessions are kept per user (oldest pruned on save).
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="translation_sessions")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(auto_now_add=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    target_language = models.CharField(max_length=10, default="en")
    word_count = models.PositiveIntegerField(default=0)
    speaker_count = models.PositiveIntegerField(default=0)
    detected_languages = models.JSONField(default=list)  # ["English", "Hindi"]
    # Each entry: {speaker_id, speaker_name, original, translation, language, timestamp}
    transcript = models.JSONField(default=list)
    summary = models.TextField(blank=True)
    # Short bullet list produced with the summary (decisions, numbers, actions)
    key_points = models.JSONField(default=list, blank=True)
    title = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-ended_at"]
        verbose_name = "Translation Session"
        verbose_name_plural = "Translation Sessions"

    def __str__(self):
        ts = self.ended_at.strftime("%Y-%m-%d %H:%M") if self.ended_at else "?"
        return f"{self.user.username} — {ts} ({self.word_count}w)"


class GlossaryTerm(models.Model):
    """A term the translator must get right: product names, jargon, acronyms.

    Used in two places, which is what makes it worth storing:
      * Whisper's initial_prompt, biasing speech recognition toward the term —
        this is what stops "packaging vendor" being heard as "Caging Wender".
      * The translation prompt, so the term is rendered the agreed way instead
        of translated literally ("cardiovascular", not "audio vascular").
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rs2t_glossary")
    term = models.CharField(max_length=120)
    # Optional guidance: a preferred rendering, or "keep in English".
    hint = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["term"]
        unique_together = ("user", "term")
        verbose_name = "Glossary Term"
        verbose_name_plural = "Glossary Terms"

    def __str__(self):
        return f"{self.term} -> {self.hint}" if self.hint else self.term
