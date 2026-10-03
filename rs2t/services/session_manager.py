import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_HISTORY_PER_USER = 20  # oldest sessions pruned beyond this
MAX_TRANSCRIPT_ENTRIES = 300  # cap stored transcript to avoid huge JSON blobs

# ---------------------------------------------------------------------------
# Live (in-flight) transcripts.
#
# This used to be a single JSON file shared by every user, rewritten in full on
# every translated line. Three problems with that: unrelated users' live
# meeting transcripts sat commingled in one plaintext file on disk with no
# owner recorded; abandoned sessions accumulated forever (15 sessions and 126
# transcript lines were still there); and load-mutate-save was not atomic, so
# two users speaking at once could drop each other's lines.
#
# It bought nothing in return. A session is keyed by its channel name, which is
# meaningless after a restart, so nothing was ever recovered from the file.
# In-process memory is the correct home: one owner per session, no disk I/O per
# line, and it disappears with the process.
# ---------------------------------------------------------------------------
_live: dict = {}
# Re-entrant so a method holding the lock can call another that also takes it.
_live_lock = threading.RLock()


class SessionManager:
    """Holds in-flight translation sessions and persists finished ones to the
    TranslationSession model.

    Every live session records the user it belongs to, and finalising one
    verifies that owner — a session can only ever be saved to its own user.
    """

    def _get(self, session_id):
        return _live.get(session_id)

    def add_translation(
        self,
        session_id,
        original,
        translation,
        language,
        confidence,
        source_type,
        target_language,
        speaker_id="A",
        speaker_name="Speaker A",
        user_id=None,
    ):
        """Append one line to a live session, creating it on first use.

        The whole read-modify-write runs under the lock. Guarding only the write
        (as the file version did) let two concurrent speakers read the same
        snapshot and clobber each other's line.
        """
        try:
            with _live_lock:
                entry = _live.get(session_id)
                if entry is None:
                    entry = {
                        "translations": [],
                        "created_at": datetime.now().isoformat(),
                        "user_id": user_id,
                    }
                    _live[session_id] = entry
                elif user_id is not None and entry.get("user_id") is None:
                    entry["user_id"] = user_id

                entry["translations"].append(
                    {
                        "original": original,
                        "translation": translation,
                        "language": language,
                        "target_language": target_language,
                        "confidence": confidence,
                        "source_type": source_type,
                        "speaker_id": speaker_id,
                        "speaker_name": speaker_name,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                # Bound the live transcript too, not just the saved copy.
                overflow = len(entry["translations"]) - MAX_TRANSCRIPT_ENTRIES
                if overflow > 0:
                    del entry["translations"][:overflow]

        except Exception as e:
            logger.error(f"⚠️ Error adding translation: {e}")

    def clear_session(self, session_id):
        """Drop a live session and everything it holds."""
        with _live_lock:
            return _live.pop(session_id, None) is not None

    def get_stats(self, session_id):
        with _live_lock:
            entry = _live.get(session_id)
            if entry:
                return {
                    "total": len(entry.get("translations", [])),
                    "created_at": entry.get("created_at", "unknown"),
                }
        return {"total": 0}

    def live_session_count(self):
        """How many sessions are in flight right now (for diagnostics)."""
        with _live_lock:
            return len(_live)

    # ── persistent user history ───────────────────────────────────────────────

    def save_user_session(self, session_id, user, target_language, duration_seconds=0):
        """
        Finalise a session: read the live transcript → summarise → write a
        TranslationSession row → enforce MAX_HISTORY_PER_USER → drop the live
        copy.

        Returns the TranslationSession instance, or None if nothing to save.
        """
        try:
            with _live_lock:
                session_data = _live.get(session_id)
                translations = list((session_data or {}).get("translations", []))
                owner_id = (session_data or {}).get("user_id")

            if not translations:
                return None  # nothing to persist

            # A session belongs to exactly one user. If the live transcript was
            # opened by someone else, refuse rather than file another user's
            # meeting under this account.
            if owner_id is not None and user is not None and owner_id != user.id:
                logger.error(
                    f"❌ Refusing to save session {session_id[:8]}: owned by user "
                    f"{owner_id}, requested by {user.id}"
                )
                return None

            # Truncate to avoid huge blobs
            translations = translations[-MAX_TRANSCRIPT_ENTRIES:]

            # Build clean transcript
            transcript = [
                {
                    "speaker_id": t.get("speaker_id", "A"),
                    "speaker_name": t.get("speaker_name", "Speaker A"),
                    "original": t.get("original", ""),
                    "translation": t.get("translation", ""),
                    "language": t.get("language", ""),
                    "timestamp": t.get("timestamp", ""),
                }
                for t in translations
            ]

            # Stats
            word_count = sum(len(t["original"].split()) for t in translations)
            speaker_ids = {t.get("speaker_id", "A") for t in translations}
            detected_langs = list({t.get("language", "") for t in translations if t.get("language")})

            # Session title — first 80 chars of first spoken sentence
            first_text = translations[0].get("original", "")
            title = (first_text[:80] + "…" if len(first_text) > 80 else first_text) or datetime.now().strftime(
                "Session %d %b %Y %H:%M"
            )

            # started_at from metadata
            created_str = session_data.get("created_at")
            started_at = datetime.fromisoformat(created_str) if created_str else datetime.now()

            # Make timezone-aware if Django requires it
            try:
                from django.utils.timezone import is_naive, make_aware

                if is_naive(started_at):
                    started_at = make_aware(started_at)
            except Exception:
                pass

            # Summarise on the local Ollama server. This used to be a paid GPT
            # call and was removed for cost; the local model makes it free again.
            summary, key_points = self._generate_summary(translations, target_language)

            # Persist to DB
            from rs2t.models import TranslationSession

            sess = TranslationSession.objects.create(
                user=user,
                started_at=started_at,
                duration_seconds=duration_seconds,
                target_language=target_language,
                word_count=word_count,
                speaker_count=len(speaker_ids),
                detected_languages=detected_langs,
                transcript=transcript,
                summary=summary,
                key_points=key_points,
                title=title,
            )

            # Prune oldest beyond quota
            old_pks = list(
                TranslationSession.objects.filter(user=user)
                .order_by("-ended_at")
                .values_list("pk", flat=True)[MAX_HISTORY_PER_USER:]
            )
            if old_pks:
                TranslationSession.objects.filter(pk__in=old_pks).delete()

            # The live copy has been persisted; drop it.
            self.clear_session(session_id)

            logger.info(f"✅ Session saved to DB for {user.username}: {sess.pk} ({word_count}w)")
            return sess

        except Exception as e:
            logger.error(f"❌ Error saving user session: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _generate_summary(self, translations, target_language):
        """Summarise a finished session. Returns (summary, key_points).

        Asks the local Ollama model for a real summary; if it is unreachable,
        times out or returns nothing usable, falls back to stitching the first
        few translated sentences together so a session is never saved blank.
        """
        lines = [
            f'{t.get("speaker_name", "Speaker")}: {t.get("translation") or t.get("original", "")}'
            for t in translations
            if t.get("translation") or t.get("original")
        ]

        try:
            from .translation_service import TranslationService

            summary, key_points = TranslationService().summarize_session(lines, target_language)
            if summary:
                return summary, key_points
        except Exception as e:
            logger.warning(f"⚠️ Ollama summary unavailable, using fallback: {e}")

        parts = [
            t.get("translation", "") or t.get("original", "")
            for t in translations[:5]
            if t.get("translation") or t.get("original")
        ]
        return (" ".join(parts) if parts else ""), []
