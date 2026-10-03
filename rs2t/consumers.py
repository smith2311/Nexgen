import asyncio
import json
import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .services.audio_processor import AudioProcessor
from .services.session_manager import SessionManager

logger = logging.getLogger(__name__)


class TranslatorConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        """Handle WebSocket connection"""
        # Identify the user first: the auth gate needs it, and AudioProcessor
        # reads it to stamp an owner on every live transcript line.
        self.user = self.scope.get("user")

        # The page is @login_required but this socket was not, so anyone who
        # could reach the host could transcribe and translate anonymously —
        # burning GPU and landing a live transcript with no owner. Sessions are
        # private per user, so the socket has to know whose it is.
        if settings.RS2T_REQUIRE_AUTH and not (self.user and getattr(self.user, "is_authenticated", False)):
            logger.warning("🚫 Rejecting unauthenticated WebSocket connection")
            await self.close(code=4401)  # 4401: application-level "unauthorized"
            return

        self.audio_processor = AudioProcessor(self)
        self.session_manager = SessionManager()
        self.session_id = self.channel_name
        self.target_language = "en"
        self.session_start = datetime.now()

        await self.accept()

        logger.info(f"✅ Client connected: {self.session_id[:8]}")

        # Warm the translation model now, while the user is still picking an
        # audio source. A cold qwen3:8b load takes ~70s, which would otherwise
        # time out the first few units and pass them through untranslated.
        asyncio.create_task(self._warm_translator())

        # Load this user's glossary once; it steers every window from here on.
        await self._load_glossary()

        # Send connection status
        await self.send(
            text_data=json.dumps(
                {
                    "type": "connection_status",
                    "status": "connected",
                }
            )
        )

    async def _load_glossary(self):
        """Fetch the user's glossary terms into the audio processor."""
        if not (self.user and getattr(self.user, "is_authenticated", False)):
            return
        try:
            from rs2t.models import GlossaryTerm

            self.audio_processor.glossary = await sync_to_async(list)(
                GlossaryTerm.objects.filter(user=self.user).values("term", "hint")
            )
            if self.audio_processor.glossary:
                logger.info(f"📖 Glossary loaded: {len(self.audio_processor.glossary)} terms")
        except Exception as e:
            logger.warning(f"⚠️ Could not load glossary: {e}")

    async def _warm_translator(self):
        """Preload the Ollama model off the critical path."""
        try:
            await sync_to_async(self.audio_processor.translation_service.warmup, thread_sensitive=False)()
        except Exception as e:
            logger.warning(f"⚠️ Translator warmup skipped: {e}")

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection — auto-save session if authenticated."""
        # A connection rejected in connect() never got these attributes.
        if not hasattr(self, "session_id"):
            return
        logger.info(f"✂️ Client disconnected: {self.session_id[:8]}")
        if hasattr(self, "audio_processor"):
            await self.audio_processor.cleanup()
        # Persist session for authenticated users
        if hasattr(self, "user") and self.user and getattr(self.user, "is_authenticated", False):
            try:
                duration = int((datetime.now() - self.session_start).total_seconds())
                await sync_to_async(self.session_manager.save_user_session)(
                    self.session_id, self.user, self.target_language, duration
                )
            except Exception as e:
                logger.error(f"❌ Error auto-saving session on disconnect: {e}")

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages"""
        try:
            if text_data:
                data = json.loads(text_data)
                message_type = data.get("type")

                if message_type == "clear_session":
                    await self.handle_clear_session(data)
                elif message_type == "get_stats":
                    await self.handle_get_stats()
                elif message_type == "set_target_language":
                    await self.handle_set_target_language(data)
                elif message_type == "get_history":
                    await self.handle_get_history()
                elif message_type == "reload_glossary":
                    await self._load_glossary()

            elif bytes_data:
                await self.handle_audio_chunk(bytes_data)

        except Exception as e:
            logger.error(f"❌ Error in receive: {e}")
            await self.send_error(str(e))

    async def handle_audio_chunk(self, audio_data):
        """Process incoming audio chunk"""
        try:
            if len(audio_data) < 1000:
                logger.warning(f"⚠️ Chunk too small: {len(audio_data)} bytes")
                return

            logger.info(f"📥 Received: {len(audio_data):,} bytes")

            await self.audio_processor.process_audio(audio_data, self.session_id, target_lang=self.target_language)

        except Exception as e:
            logger.error(f"❌ Error processing audio: {e}")
            await self.send_error(f"Audio processing failed: {str(e)}")

    async def handle_set_target_language(self, data):
        """Handle target language change"""
        try:
            target_lang = data.get("target_language", "en")
            self.target_language = target_lang
            logger.info(f"🌍 Target language set to: {target_lang}")

            await self.send(text_data=json.dumps({"type": "language_updated", "target_language": target_lang}))
        except Exception as e:
            logger.error(f"❌ Error setting target language: {e}")

    async def handle_get_history(self):
        """Return the last 10 saved sessions for this user."""
        try:
            if not (self.user and getattr(self.user, "is_authenticated", False)):
                await self.send(text_data=json.dumps({"type": "history_data", "sessions": []}))
                return

            from rs2t.models import TranslationSession
            from rs2t.services.session_manager import MAX_HISTORY_PER_USER

            sessions_qs = await sync_to_async(list)(
                TranslationSession.objects.filter(user=self.user).order_by("-ended_at")[:MAX_HISTORY_PER_USER]
            )
            data = [
                {
                    "id": s.pk,
                    "title": s.title,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat(),
                    "duration_seconds": s.duration_seconds,
                    "target_language": s.target_language,
                    "word_count": s.word_count,
                    "speaker_count": s.speaker_count,
                    "detected_languages": s.detected_languages,
                    "summary": s.summary,
                }
                for s in sessions_qs
            ]
            await self.send(text_data=json.dumps({"type": "history_data", "sessions": data}))
        except Exception as e:
            logger.error(f"❌ Error fetching history: {e}")
            await self.send(text_data=json.dumps({"type": "history_data", "sessions": []}))

    async def handle_clear_session(self, data=None):
        """Start a fresh session, keeping or discarding the current transcript.

        `save` defaults to True so an older client that sends a bare
        clear_session still gets the save-then-clear behaviour. With
        `save: false` the transcript is dropped and never reaches the database.
        """
        try:
            save = True if data is None else bool(data.get("save", True))
            saved = False

            if save and self.user and getattr(self.user, "is_authenticated", False):
                duration = int((datetime.now() - self.session_start).total_seconds())
                sess = await sync_to_async(self.session_manager.save_user_session)(
                    self.session_id, self.user, self.target_language, duration
                )
                saved = sess is not None
            else:
                # Drops the live transcript without writing a history row.
                await sync_to_async(self.session_manager.clear_session)(self.session_id)
                if not save:
                    logger.info(f"🗑️ Session discarded unsaved: {self.session_id[:8]}")

            # A new session must not inherit the old one's translation context
            # or a half-filled sentence buffer.
            if hasattr(self, "audio_processor"):
                await self.audio_processor.reset_session_state()

            # Reset session timer for the new fresh session
            self.session_start = datetime.now()

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "session_cleared",
                        "status": "success",
                        "saved": saved,
                        "discarded": not save,
                    }
                )
            )
        except Exception as e:
            logger.error(f"❌ Error clearing session: {e}")

    async def handle_get_stats(self):
        """Get session statistics"""
        try:
            stats = await sync_to_async(self.session_manager.get_stats)(self.session_id)
            await self.send(text_data=json.dumps({"type": "stats_update", **stats}))
        except Exception as e:
            logger.error(f"❌ Error getting stats: {e}")

    async def send_original_text(self, data):
        """Send original transcribed text to client"""
        await self.send(text_data=json.dumps({"type": "original_text", **data}))

    async def send_translation_stream(self, data):
        """Send translation stream chunk to client"""
        await self.send(text_data=json.dumps({"type": "translation_stream", **data}))

    async def send_error(self, message):
        """Send error message to client"""
        await self.send(text_data=json.dumps({"type": "audio_error", "message": message}))
