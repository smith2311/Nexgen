import asyncio
import logging
import math
import os
import struct
import subprocess
import tempfile
import threading
import time
import wave
from queue import Empty, Queue

from django.conf import settings

from asgiref.sync import sync_to_async

from .session_manager import SessionManager
from .translation_service import TranslationService, _clean_translation

# Minimum RMS energy (out of 32768) to consider audio as speech, not silence.
# Lowered to 80 — short 1-second chunks have lower average energy than longer ones.
MIN_SPEECH_RMS = 80

# Minimum WebM blob size (bytes) before attempting FFmpeg conversion.
# At 32kbps, 1s of real speech ≈ 4 KB. Silent/near-empty WebM files are <1 KB.
# Skipping FFmpeg+Whisper for tiny blobs saves significant CPU and API cost.
MIN_WEBM_BYTES = 1500

logger = logging.getLogger(__name__)

# Capitalised openers that still continue the previous sentence. Whisper
# capitalises the first word of every window, so capitalisation alone cannot
# distinguish "Through more than 150 nations" (a continuation) from a real
# new sentence.
_CONTINUATION_WORDS = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "which",
    "that",
    "who",
    "whom",
    "whose",
    "where",
    "when",
    "while",
    "with",
    "without",
    "through",
    "throughout",
    "into",
    "onto",
    "of",
    "for",
    "from",
    "to",
    "in",
    "on",
    "at",
    "by",
    "as",
    "than",
    "then",
    "plus",
    "also",
    "including",
    "such",
    "like",
    "across",
    "over",
    "under",
    "after",
    "before",
    "during",
    "against",
    "about",
    "along",
    "among",
    "between",
    "beyond",
    "toward",
    "towards",
    "upon",
    "within",
}


class AudioProcessor:
    """Handles audio processing, transcription, and translation"""

    _audio_queue = None
    _workers_started = False
    _workers = []
    _lock = threading.Lock()

    def __init__(self, consumer):
        self.consumer = consumer
        self.translation_service = TranslationService()
        self.session_manager = SessionManager()

        # ── Sentence buffer (per connection) ──
        # Transcribed windows collect here until they form a translatable unit.
        self._buffer = []
        self._flush_task = None
        # Last unit that went out, fed to the translator as context so the next
        # one continues the thought instead of restarting it.
        self._prev_source = ""
        self._prev_translation = ""
        # Emission ordering: windows are numbered on arrival and released in
        # that order, since several worker threads finish out of sequence.
        self._seq_in = 0
        self._seq_out = 0
        # Per connection, not class-wide — see _emit_window.
        self._last_transcript = ""
        # [{term, hint}] loaded once per connection; steers both Whisper and
        # the translator. Empty until the consumer supplies it.
        self.glossary = []
        # Owner of every live line this connection produces.
        self.user_id = getattr(getattr(consumer, "user", None), "id", None)

        # Start workers if not already started
        AudioProcessor._ensure_workers_started()

    # ── buffering ────────────────────────────────────────────────────────────

    def _join_buffer(self):
        """Join buffered windows, dropping the full stops Whisper invents.

        Whisper terminates every window it transcribes, so a sentence split
        across two windows arrives as "…now operates." + "Through more than 150
        nations." Joining verbatim keeps a full stop the speaker never said. A
        boundary period is only trusted when the next window opens like a new
        sentence.
        """
        parts = [p.strip() for p in self._buffer if p and p.strip()]
        if not parts:
            return ""

        out = parts[0]
        for nxt in parts[1:]:
            # Does the next window look like a genuinely new sentence? Only
            # then is the previous window's terminator believable.
            first = nxt.split()[0] if nxt.split() else ""
            starts_new = first[:1].isupper() and first.lower() not in _CONTINUATION_WORDS
            if out.endswith((".", "!", "?")) and not starts_new:
                out = out[:-1].rstrip()
            out = f"{out} {nxt}"
        return out.strip()

    def _should_flush(self, buffered):
        """True when the buffer is worth translating now.

        Latency-first. Holding a window back to wait for the sentence to finish
        means waiting a whole chunk interval (~3s) for the next window to be
        recorded and transcribed — that single decision was most of a 4.7s lag.

        Waiting is not what makes the output coherent anyway: the previous unit
        is handed to the translator as context, so a window that continues the
        last sentence is translated as a continuation, and a word split across
        the boundary is still repaired from context. Only genuinely tiny
        fragments wait, and only until the idle timer fires.
        """
        if not buffered:
            return False
        return len(buffered.split()) >= settings.RS2T_MIN_UNIT_WORDS

    def _arm_flush_timer(self, session_id, detected_lang, lang_name, target_lang, confidence):
        """Flush anyway once the speaker stops — a trailing fragment must not be lost."""
        self._cancel_flush_timer()

        async def _later():
            try:
                await asyncio.sleep(settings.RS2T_UNIT_IDLE_FLUSH_SECONDS)
                buffered = self._join_buffer()
                if buffered:
                    logger.info(f"⏱️ Idle flush ({len(buffered.split())} words)")
                    await self._flush_unit(session_id, buffered, detected_lang, lang_name, target_lang, confidence)
            except asyncio.CancelledError:
                raise  # a newer window superseded this timer
            except Exception as e:
                logger.error(f"❌ Idle flush error: {type(e).__name__}: {e}")

        self._flush_task = asyncio.ensure_future(_later())

    def _cancel_flush_timer(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    async def _flush_unit(self, session_id, unit, detected_lang, lang_name, target_lang, confidence):
        """Translate one buffered unit and send it as a completed segment."""
        self._buffer = []

        same_lang = (detected_lang or "").lower().strip() == (target_lang or "").lower().strip()
        if same_lang:
            translation = unit
            await self.consumer.send_translation_stream(
                {"chunk": translation, "is_final": False, "word_count": 0, "total_words": -1}
            )
        else:
            # Ollama first: it is the only path that can take the previous unit
            # as context, keep the register conversational, and repair words
            # broken across window boundaries. Streamed, because the first token
            # lands in ~0.25s while the finished sentence takes ~0.95s — the
            # reader starts reading a full step earlier.
            translation = await self._stream_unit(unit, target_lang)

            if not translation:
                # Google is a keyless safety net for when Ollama is down:
                # literal and context-free, but present.
                logger.info("📡 Ollama unavailable — falling back to Google Translate")
                translation = await sync_to_async(self.translation_service.translate_fast, thread_sensitive=False)(
                    unit, detected_lang, target_lang
                )
                if translation:
                    await self.consumer.send_translation_stream(
                        {"chunk": translation, "is_final": False, "word_count": 0, "total_words": -1}
                    )

            if not translation:
                logger.error("❌ Both translators failed — passing the source through")
                translation = unit
                await self.consumer.send_translation_stream(
                    {"chunk": translation, "is_final": False, "word_count": 0, "total_words": -1}
                )

        words = len(translation.split())
        await self.consumer.send_translation_stream(
            {
                # Empty: the text already went out as stream pieces above.
                "chunk": "",
                # Authoritative cleaned text — the client replaces whatever the
                # stream painted, so a leaked control token never survives.
                "text": translation,
                "is_final": True,
                "word_count": words,
                "total_words": words,
                # Tells the client this thought is finished, so the next window
                # opens a new segment instead of extending this one forever.
                "end_of_unit": True,
            }
        )

        self._prev_source = unit
        self._prev_translation = translation
        logger.info(f"✅ Unit translated: {words} words")

        await sync_to_async(self.session_manager.add_translation)(
            session_id,
            unit,
            translation,
            lang_name,
            confidence,
            "system",
            target_lang,
            user_id=self.user_id,
        )

    async def _stream_unit(self, unit, target_lang):
        """Push Ollama's tokens to the client as they arrive.

        The generator is blocking (requests with stream=True), so it is drained
        on a worker thread and the pieces are handed back to this event loop.
        Returns the assembled translation, or None if the stream produced
        nothing so the caller can fall back.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        prev_source, prev_translation = self._prev_source, self._prev_translation
        glossary = self.glossary

        def producer():
            try:
                for piece in self.translation_service.translate_contextual_stream(
                    unit, target_lang, prev_source, prev_translation, glossary
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, piece)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=producer, name="ollama-stream", daemon=True).start()

        raw = []
        while True:
            piece = await queue.get()
            if piece is None:
                break
            raw.append(piece)
            # Stream the piece as-is. Cleaning incrementally would mean diffing
            # a rewritten string on every token, which misfires when the model
            # opens with a stray quote. The final message carries the cleaned
            # text and the client replaces what it showed, so any "/no_think"
            # that slipped through is corrected on the last frame.
            await self.consumer.send_translation_stream(
                {"chunk": piece, "is_final": False, "word_count": 0, "total_words": -1}
            )

        return _clean_translation("".join(raw)) or None

    @classmethod
    def _ensure_workers_started(cls):
        """Ensure worker threads are started (singleton pattern)"""
        with cls._lock:
            if not cls._workers_started:
                # Create queue AFTER settings are ready
                if cls._audio_queue is None:
                    cls._audio_queue = Queue(maxsize=settings.AUDIO_QUEUE_SIZE)

                logger.info(f"🔧 Starting {settings.MAX_WORKERS} audio workers...")
                for i in range(settings.MAX_WORKERS):

                    worker = threading.Thread(target=cls._audio_worker, name=f"AudioWorker-{i+1}", daemon=True)
                    worker.start()
                    cls._workers.append(worker)
                cls._workers_started = True
                logger.info(f"✅ Started {settings.MAX_WORKERS} workers")

    @classmethod
    def _audio_worker(cls):
        """Worker thread that processes audio from queue"""
        thread_name = threading.current_thread().name
        logger.info(f"🔧 Worker {thread_name} started")

        while True:
            try:
                task = cls._audio_queue.get(timeout=0.1)

                if task is None:  # Shutdown signal
                    logger.info(f"🛑 Worker {thread_name} stopping")
                    break

                processor, audio_data, session_id, target_lang, consumer_loop, seq = task

                # Run on the consumer's ASGI event loop so consumer.send() works.
                # asyncio.run() would create a different loop and silently break sends.
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        processor._process_audio_sync(audio_data, session_id, target_lang, seq), consumer_loop
                    )
                    fut.result(timeout=30)  # wait up to 30s per chunk
                except Exception as e:
                    logger.error(f"❌ Worker {thread_name} error: {type(e).__name__}: {e}")

                cls._audio_queue.task_done()

            except Empty:
                continue  # tight poll keeps task pickup latency low
            except Exception as e:
                logger.error(f"❌ Worker {thread_name} fatal error: {e}")

    async def process_audio(self, audio_data, session_id, target_lang="en"):
        """Queue audio for processing"""
        try:
            # Capture the consumer's ASGI event loop so the worker can
            # schedule coroutines back onto it (consumer.send() requires it).
            consumer_loop = asyncio.get_running_loop()
            seq = self._seq_in
            self._seq_in += 1
            AudioProcessor._audio_queue.put(
                (self, audio_data, session_id, target_lang, consumer_loop, seq), timeout=0.5
            )
            queue_size = AudioProcessor._audio_queue.qsize()
            logger.info(f"📥 Queued audio (queue: {queue_size}/{self._audio_queue.maxsize})")
        except Exception:
            logger.warning("⚠️ Queue full, dropping chunk")
            await self.consumer.send_error("Processing queue full, please wait...")

    async def _process_audio_sync(self, audio_data, session_id, target_lang, seq=0):
        """Transcribe one window, then emit it in arrival order.

        Transcription runs wherever a worker thread picks it up, so windows can
        finish out of order — several workers share one queue. That was
        tolerable when each window was translated on its own, but the sentence
        buffer needs the text in the order it was spoken, otherwise units come
        out scrambled. So the CPU work stays parallel and only the emit step is
        sequenced, behind `seq`.
        """
        heard = await self._transcribe_window(audio_data, target_lang)

        # ── ordered section ──
        await self._await_turn(seq)
        try:
            if heard is None:
                return
            await self._emit_window(session_id, target_lang, *heard)
        finally:
            self._advance_turn(seq)

    async def _await_turn(self, seq):
        """Block until it is this window's turn to be emitted."""
        deadline = time.monotonic() + settings.RS2T_ORDER_WAIT_SECONDS
        while self._seq_out != seq:
            if time.monotonic() > deadline:
                # A window died without releasing its turn. Skip ahead rather
                # than stalling every window behind it for the whole session.
                logger.warning(f"⚠️ Ordering wait timed out at window {seq}; releasing")
                self._seq_out = seq
                return
            await asyncio.sleep(0.02)

    def _advance_turn(self, seq):
        if self._seq_out == seq:
            self._seq_out = seq + 1

    async def _emit_window(self, session_id, target_lang, text, detected_lang, confidence, transcribe_time):
        """Send one transcribed window and buffer it toward a translatable unit."""
        # ── Deduplication: skip a window identical to the previous one.
        # Per connection, not global: two people saying the same sentence on
        # separate connections must not silence each other.
        normalized = " ".join(text.lower().split())
        if normalized == self._last_transcript:
            logger.warning(f"⚠️ Duplicate window, skipping: '{text[:60]}'")
            return
        self._last_transcript = normalized

        is_valid, reason = self._is_valid_transcription(text)
        if not is_valid:
            logger.warning(f"⚠️ Invalid: {reason}")
            await self.consumer.send_error(f"Audio quality issue: {reason}")
            return

        lang_name = self.translation_service.detect_language_name(detected_lang)

        # Show the raw transcript straight away — the reader sees speech land
        # live even though translation waits for a complete thought.
        logger.info(f"📤 Heard: '{text}'  ({lang_name})")
        await self.consumer.send_original_text(
            {
                "text": text,
                "language": lang_name,
                "confidence": confidence,
                "word_count": len(text.split()),
                "transcribe_time": round(transcribe_time, 1),
            }
        )

        # ── Buffer until the unit is worth translating ──
        # Translating each window on its own is what produced disconnected,
        # over-literal output: the translator never saw enough of the sentence
        # to know how it ends.
        self._buffer.append(text)
        buffered = self._join_buffer()
        if not self._should_flush(buffered):
            logger.info(f"⏳ Buffering ({len(buffered.split())} words) — waiting for a full thought")
            self._arm_flush_timer(session_id, detected_lang, lang_name, target_lang, confidence)
            return

        self._cancel_flush_timer()
        await self._flush_unit(session_id, buffered, detected_lang, lang_name, target_lang, confidence)

    async def _transcribe_window(self, audio_data, target_lang):
        """Convert and transcribe one window.

        Returns (text, detected_lang, confidence, transcribe_time), or None if
        the window held no usable speech. Safe to run concurrently.
        """
        webm_path = None
        wav_path = None

        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🎵 Processing {len(audio_data):,} bytes")
            logger.info(f"   Target: {self.translation_service.get_language_name(target_lang)}")
            logger.info(f"{'='*60}")
            # ── Blob size pre-filter: skip FFmpeg + Whisper for tiny silent blobs
            if len(audio_data) < MIN_WEBM_BYTES:
                logger.warning(f"⚠️ WebM too small ({len(audio_data)} bytes) — likely silence, skipping")
                return None
            # Save WebM
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as webm_tmp:
                webm_tmp.write(audio_data)
                webm_path = webm_tmp.name

            # Convert to WAV
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_tmp:
                wav_path = wav_tmp.name

            logger.info("📻 Converting audio...")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        webm_path,
                        "-vn",
                        "-acodec",
                        "pcm_s16le",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-loglevel",
                        "error",
                        wav_path,
                    ],
                    capture_output=True,
                    timeout=8,
                ),
            )

            if result.returncode != 0:
                logger.error("❌ FFmpeg conversion failed")
                await self.consumer.send_error("Audio conversion failed")
                return None

            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
                logger.warning("⚠️ WAV file too small, skipping")
                return None

            logger.info(f"✅ WAV ready: {os.path.getsize(wav_path):,} bytes")

            # --- Silence detection ---
            rms = self._wav_rms(wav_path)
            logger.info(f"   RMS energy: {rms:.1f}")
            if rms < MIN_SPEECH_RMS:
                logger.warning(f"⚠️ Audio too quiet (RMS={rms:.1f}), skipping — likely silence")
                return None

            # Transcribe
            start_time = time.time()
            logger.info("\n🎤 Starting transcription...")

            text, detected_lang, confidence = await sync_to_async(
                self.translation_service.transcribe_audio, thread_sensitive=False
            )(wav_path, [t["term"] for t in self.glossary])

            transcribe_time = time.time() - start_time
            logger.info(f"⏱️ Transcription took {transcribe_time:.1f}s")

            if not text or not detected_lang:
                logger.error("❌ Transcription failed")
                return None

            if len(text) < 2:
                logger.warning("⚠️ Text too short")
                return None

            return text, detected_lang, confidence, transcribe_time

        except subprocess.TimeoutExpired:
            logger.error("❌ FFmpeg timeout")
            await self.consumer.send_error("Audio conversion timeout")
            return None
        except Exception as e:
            logger.error(f"❌ Processing error: {type(e).__name__}: {e}")
            await self.consumer.send_error(f"Error: {str(e)}")
            return None
        finally:
            # Cleanup temp files
            for path in (webm_path, wav_path):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception as e:
                        logger.warning(f"⚠️ Cleanup error: {e}")

    async def _stream_translation(self, text, lang_name, target_lang):
        """Emit the Ollama translation to the client through the streaming protocol."""
        try:
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()

            def producer():
                try:
                    for token in self.translation_service.translate_text_stream(text, lang_name, target_lang):
                        asyncio.run_coroutine_threadsafe(queue.put(token), loop).result()
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

            thread = threading.Thread(target=producer, daemon=True)
            thread.start()

            full_translation = ""
            word_count = 0

            while True:
                token = await queue.get()
                if token is None:
                    break

                full_translation += token
                # Count words only on space boundaries (approx)
                word_count += token.count(" ")

                # Send each token to the UI immediately — no buffering
                await self.consumer.send_translation_stream(
                    {"chunk": token, "is_final": False, "word_count": word_count, "total_words": -1}
                )

            # Final signal — no extra chunk needed, full_translation is already displayed
            if full_translation:
                word_count = len(full_translation.split())
            await self.consumer.send_translation_stream(
                {"chunk": "", "is_final": True, "word_count": word_count, "total_words": word_count}
            )

            thread.join(timeout=2)
            return full_translation, word_count

        except Exception as e:
            logger.error(f"⚠️ Streaming error: {e}")
            return text, len(text.split())

    @staticmethod
    def _wav_rms(wav_path):
        """Return the RMS amplitude of a mono 16-bit PCM WAV file."""
        try:
            with wave.open(wav_path, "rb") as wf:
                n_frames = wf.getnframes()
                if n_frames == 0:
                    return 0.0
                raw = wf.readframes(n_frames)
                count = len(raw) // 2
                if count == 0:
                    return 0.0
                samples = struct.unpack(f"<{count}h", raw)
                rms = math.sqrt(sum(s * s for s in samples) / count)
                return rms
        except Exception as e:
            logger.warning(f"⚠️ RMS calculation failed: {e}")
            return MIN_SPEECH_RMS + 1  # fail open — let Whisper decide

    def _is_valid_transcription(self, text):
        """Validate transcription quality"""
        if not text or len(text.strip()) < 2:
            return False, "Text too short"

        words = text.split()

        if len(words) <= 3:
            if len(set(text.replace(" ", "").lower())) <= 2:
                return False, "Single character repetition"
            return True, "Valid (short text)"

        if len(words) >= 5:
            word_counts = {}
            for word in words:
                word_lower = word.lower().strip(".,!?;:")
                word_counts[word_lower] = word_counts.get(word_lower, 0) + 1

            if word_counts:
                max_count = max(word_counts.values())
                repetition_ratio = max_count / len(words)

                if repetition_ratio > 0.5:
                    return False, "Excessive repetition"

        return True, "Valid"

    async def reset_session_state(self):
        """Forget the current session so a new one starts clean.

        Without this, the first unit of the next session is handed the previous
        session's last exchange as context, and any half-filled window buffer
        would be prepended to it.
        """
        self._cancel_flush_timer()
        self._buffer = []
        self._prev_source = ""
        self._prev_translation = ""
        self._last_transcript = ""

    async def cleanup(self):
        """Flush anything still buffered, then drop the pending timer."""
        self._cancel_flush_timer()
        self._buffer = []
