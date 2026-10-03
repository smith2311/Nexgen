import concurrent.futures
import json
import logging
import re
import threading

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Thread pool for Google Translate calls (avoids blocking the event loop)
_google_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="google-tx")

# ---------------------------------------------------------------------------
# Local Whisper (faster-whisper / CTranslate2) — loaded once, shared by all
# worker threads. WhisperModel.transcribe() is thread-safe for concurrent
# calls, so a single instance serves every AudioProcessor worker.
# ---------------------------------------------------------------------------
_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    """Lazily load the local Whisper model (first call downloads it)."""
    global _whisper_model
    if _whisper_model is None:
        with _whisper_lock:
            if _whisper_model is None:
                from faster_whisper import WhisperModel

                logger.info(
                    f"⏳ Loading local Whisper '{settings.WHISPER_MODEL_SIZE}' "
                    f"({settings.WHISPER_DEVICE}/{settings.WHISPER_COMPUTE_TYPE})..."
                )
                _whisper_model = WhisperModel(
                    settings.WHISPER_MODEL_SIZE,
                    device=settings.WHISPER_DEVICE,
                    compute_type=settings.WHISPER_COMPUTE_TYPE,
                    download_root=settings.WHISPER_MODEL_DIR,
                    cpu_threads=settings.WHISPER_CPU_THREADS,
                )
                logger.info("✅ Local Whisper model ready")
    return _whisper_model


# Strips the <think>...</think> block reasoning models (qwen3) may emit even
# when thinking is disabled.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# qwen3 sometimes emits its own control tokens as literal text in the reply.
_CONTROL_TOKEN_RE = re.compile(r"/(?:no_)?think\b", re.IGNORECASE)

# Small models sometimes wrap the answer or prefix it despite being told not to.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:translation|translated text|here(?:\s+is|\'s)\s+the\s+translation)\s*[:\-]\s*",
    re.IGNORECASE,
)


def _clean_translation(content):
    """Strip thinking blocks, preambles and wrapping quotes from model output."""
    out = _THINK_RE.sub("", content or "").strip()
    out = _CONTROL_TOKEN_RE.sub("", out).strip()
    out = _PREAMBLE_RE.sub("", out).strip()
    # Only unwrap when the WHOLE string is quoted, so quoted speech survives.
    if len(out) > 1 and out[0] in "\"'\u201c\u2018" and out[-1] in "\"'\u201d\u2019":
        out = out[1:-1].strip()
    return out


# Simple dict-based LRU cache for translation results (avoids duplicate API calls)
_translation_cache: dict = {}

# Phrases Whisper commonly hallucinates on silence / low-signal audio
WHISPER_HALLUCINATIONS = {
    re.compile(r, re.IGNORECASE)
    for r in [
        r"^thank you[.!,]*$",
        r"^thanks?[.!,]*$",
        r"^thanks? for watching[.!,]*$",
        r"^please subscribe[.!,]*$",
        r"^like and subscribe[.!,]*$",
        r"^subscribe[.!,]*$",
        r"^(you[.,!]?\s*){1,5}$",
        r"^bye[- ]?bye[.!,]*$",
        r"^goodbye[.!,]*$",
        r"^\.\.\.+$",
        r"^[\s\.,!?]+$",
        r"^subtitles by",
        r"^\[.*\]$",  # [Music], [Applause] etc.
        r"^\(.*\)$",  # (silence), (music) etc.
        r"^[\u266a\u266b\u2669♩♪♫♬\s]+$",  # musical notes only
        r"^see you[.!,]*$",
        r"^see you next time[.!,]*$",
        r"^(ha){2,}[.!,]*$",  # hahaha
        r"^(um|uh|hmm|ah)[.!,]*$",
        r"^okay[.!,]*$",
        r"^alright[.!,]*$",
        r"^so[.!,]*$",
        r"^welcome[.!,]*$",
        r"^hello[.!,]*$",
        r"^hi[.!,]*$",
        r"^yes[.!,]*$",
        r"^no[.!,]*$",
        # Our own Whisper prompt echoed back verbatim
        r"^real spoken conversation audio[.!,\s]*$",
        r"^spoken conversation audio[.!,\s]*$",
        # Music / background noise hallucinations
        r"^(music[\s,!.]*)+$",
        r"^(\[?music\]?[\s,!.]*)+$",
        r"^(موسیقی[\s,!.]*)+$",  # Persian/Urdu for music
        r"^(muziek[\s,!.]*)+$",  # Dutch for music
        r"^audio[.!,\s]*$",
        r"^background music[.!,\s]*$",
        r"^(applause[\s,!.]*)+$",
        r"^(laughter[\s,!.]*)+$",
        # Speaker labels injected by Whisper
        r"^speaker \d+[.!,\s]*$",
        r"^speaker \d+ on audio[.!,\s]*$",
        # Social media / YouTube hallucinations
        r"^like,?\s*comment,?\s*share\s*(and\s*subscribe)?[.!,\s]*$",
        r"^like and subscribe[.!,\s]*$",
        r"^comment,?\s*share[.!,\s]*$",
    ]
}


def _is_repetitive(text, threshold=0.55):
    """Return True if a single word dominates ≥threshold fraction of all tokens.

    Catches Whisper looping hallucinations like 'Music Music Music Music...'
    """
    from collections import Counter

    words = [w.lower().strip(".,!?()[]'\"") for w in text.split()]
    words = [w for w in words if len(w) > 1]  # skip stray punctuation tokens
    if len(words) < 5:
        return False
    top_word, top_count = Counter(words).most_common(1)[0]
    return top_count / len(words) >= threshold


# Regex to detect CJK (Chinese/Japanese/Korean) characters
_CJK_RE = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f]+", re.UNICODE)


class TranslationService:
    """Transcription via local Whisper, translation via the self-hosted Ollama server.

    No third-party AI API keys are involved: speech-to-text runs in-process
    through faster-whisper, and translation goes to Ollama on this host.
    """

    def __init__(self):
        self.ollama_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.ollama_model = settings.OLLAMA_MODEL
        logger.info(f"✅ Ollama translator: {self.ollama_model} @ {self.ollama_url}")

    @staticmethod
    def _is_hallucination(text):
        """Return True if text matches a known Whisper hallucination pattern."""
        t = text.strip()
        return any(p.match(t) for p in WHISPER_HALLUCINATIONS)

    def _ollama_translate(self, text, target_lang_code, timeout):
        """Single non-streaming Ollama chat call. Returns the translation or None."""
        # Prompt with the English language name — get_language_name() returns
        # native script ("हिंदी") for display, which prompts the model less reliably.
        target_lang_name = self.detect_language_name(target_lang_code)

        # Output ceiling proportional to input. Generous on purpose: generation
        # stops at EOS, so a high cap costs no latency, while a tight one
        # silently truncates. Non-Latin scripts (Devanagari, CJK, Arabic) cost
        # several tokens per word in qwen3's tokenizer — the old GPT-tuned
        # 2x cap cut Hindi output off mid-sentence.
        word_count = len(text.split())
        max_tok = min(max(word_count * 6 + 24, 48), 256)

        response = requests.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.ollama_model,
                "stream": False,
                # qwen3 and friends are reasoning models; thinking would blow the
                # latency budget and pollute the output for a translation task.
                "think": False,
                # Keep the model resident so a fallback isn't a cold-load stall.
                "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                "options": {"temperature": 0, "num_predict": max_tok},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are a translation engine. Translate the user's text into "
                            f"{target_lang_name}. Output ONLY the translation — no notes, "
                            f"no quotes, no explanation."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return _clean_translation(content) or None

    def warmup(self):
        """Load both engines before the first window arrives.

        Two cold starts otherwise land on the opening sentences: Whisper takes
        ~1-6s to load its weights, and a cold qwen3:8b load takes ~70s — far
        past OLLAMA_TIMEOUT, so those sentences used to pass through
        untranslated. Doing this at connect time, while the user is still
        choosing an audio source, keeps the first window as fast as the tenth.
        Fire-and-forget: failures are logged and ignored.
        """
        try:
            _get_whisper_model()
        except Exception as e:
            logger.warning(f"⚠️ Whisper preload failed: {type(e).__name__}: {e}")

        try:
            requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "options": {"num_predict": 1},
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=settings.OLLAMA_WARMUP_TIMEOUT,
            )
            logger.info(f"🔥 Ollama model '{self.ollama_model}' warm")
        except Exception as e:
            logger.warning(f"⚠️ Ollama warmup failed: {type(e).__name__}: {e}")

    def _contextual_prompt(self, text, target_lang_code, prev_source, prev_translation, glossary=None):
        """Build (system, user, max_tokens) for a contextual translation."""
        target_lang_name = self.detect_language_name(target_lang_code)

        glossary_block = ""
        if glossary:
            lines = []
            for entry in glossary[: settings.RS2T_GLOSSARY_PROMPT_TERMS]:
                term = entry.get("term") if isinstance(entry, dict) else str(entry)
                hint = entry.get("hint") if isinstance(entry, dict) else ""
                lines.append(f"  {term} — {hint}" if hint else f'  {term} — keep as "{term}"')
            glossary_block = (
                "\nGLOSSARY — these are fixed. Render each exactly as instructed, "
                "never translated literally:\n" + "\n".join(lines) + "\n"
            )

        system = (
            f"You are an interpreter speaking {target_lang_name} aloud for someone in a "
            f"meeting.\n"
            f"\n"
            f"REGISTER — say it the way a {target_lang_name} news anchor would say it out "
            f"loud, not the way an official document would write it. Use the common "
            f"everyday word whenever one exists, and use the borrowed English word when "
            f"that is what people actually say in speech (company, product, technology, "
            f"infrastructure, cardiovascular). Avoid heavy literary or bureaucratic "
            f"vocabulary.\n"
            f"\n"
            f"MEANING — the source is corporate marketing language. Say what it actually "
            f"means in ordinary words, and translate idioms by sense rather than word by "
            f"word.\n"
            f"\n"
            f"DAMAGE — the text comes from short audio windows, so expect words cut in "
            f"half, a word repeated where two windows overlap, and a full stop the speaker "
            f"never said. Work out what was meant and say that. A full stop at the end of "
            f"the input does NOT mean the sentence ended.\n"
            f"\n"
            f"CONTINUITY — if the new text continues the previous sentence, say it as a "
            f"continuation. Never restart the sentence or repeat the earlier part.\n"
            f"\n"
            f"{glossary_block}"
            f"\nOutput ONLY the spoken {target_lang_name}. No notes, no quotes, no "
            f"explanation, no romanisation."
        )

        if prev_source:
            user = (
                f"Already said (for context, do NOT translate again):\n"
                f"  original: {prev_source}\n"
                f"  your translation: {prev_translation}\n\n"
                f"New text to translate:\n{text}"
            )
        else:
            user = f"New text to translate:\n{text}"

        # Generous ceiling: generation stops at EOS, and Indic/CJK scripts cost
        # several tokens per word.
        max_tok = min(max(len(text.split()) * 8 + 32, 64), 512)
        return system, user, max_tok

    def translate_contextual(self, text, target_lang_code, prev_source="", prev_translation="", glossary=None):
        """Translate one transcript unit in the flow of what came before.

        Unlike translate_text() this is built for live ASR output rather than
        clean prose. Three things it does that a stateless sentence-by-sentence
        translator cannot:

        * Register — asks for plain spoken language. Literal engines render
          "comprehensive excellence" as "व्यापक उत्कृष्टता", which no one says
          out loud.
        * Continuity — the previous unit is supplied as context, so a fragment
          that continues the last sentence reads as a continuation instead of
          restarting.
        * Repair — audio is cut into fixed windows, so words split across a
          boundary ("card" + "iovascular"), duplicate at the join, and pick up
          a full stop that was never spoken. The model is told to expect that.

        Returns the translation, or None so the caller can fall back.
        """
        system, user, max_tok = self._contextual_prompt(text, target_lang_code, prev_source, prev_translation, glossary)

        try:
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "options": {"temperature": 0, "num_predict": max_tok},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=settings.OLLAMA_TIMEOUT,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            result = _clean_translation(content)
            if result:
                logger.info(f"🌐 Ollama ({self.detect_language_name(target_lang_code)}): '{result[:110]}'")
                return result
            logger.warning("⚠️ Ollama returned an empty translation")
        except Exception as e:
            logger.warning(f"⚠️ Ollama translation failed: {type(e).__name__}: {e}")
        return None

    def translate_contextual_stream(self, text, target_lang_code, prev_source="", prev_translation="", glossary=None):
        """Same as translate_contextual, but yields tokens as Ollama produces them.

        Waiting for the completed sentence costs ~0.95s; the first token lands
        in ~0.25s. For a live caption the reader only cares when text *starts*
        appearing, so streaming removes ~0.7s of felt lag for free.

        Yields text pieces. Yields nothing if the call fails, letting the caller
        fall back.
        """
        system, user, max_tok = self._contextual_prompt(text, target_lang_code, prev_source, prev_translation, glossary)
        try:
            with requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "stream": True,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "options": {"temperature": 0, "num_predict": max_tok},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=settings.OLLAMA_TIMEOUT,
                stream=True,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    piece = payload.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if payload.get("done"):
                        return
        except Exception as e:
            logger.warning(f"⚠️ Ollama stream failed: {type(e).__name__}: {e}")

    def summarize_session(self, lines, target_lang_code):
        """Summarise a finished session into a short paragraph + key points.

        Runs on the local Ollama server, so unlike the old GPT summary this
        costs nothing per session. Returns (summary, [key_point, ...]) and
        falls back to ("", []) on any failure — the caller keeps its own
        cheap fallback for that case.
        """
        if not lines:
            return "", []

        target_lang_name = self.detect_language_name(target_lang_code)
        # Cap the input: recent context matters most and a long meeting would
        # otherwise blow past the model's usable window.
        body = "\n".join(lines[-settings.OLLAMA_SUMMARY_MAX_LINES :])

        try:
            logger.info(f"📝 Summarising {len(lines)} transcript lines via Ollama...")
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "format": "json",
                    "options": {"temperature": 0.2, "num_predict": settings.OLLAMA_SUMMARY_MAX_TOKENS},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"You summarise meeting transcripts. Reply in {target_lang_name} "
                                f"as JSON with exactly two keys: "
                                f'"summary" (2-3 sentences on what the meeting covered) and '
                                f'"key_points" (an array of up to 5 short strings — decisions, '
                                f"numbers, dates and action items only). Use only what the "
                                f"transcript states; never invent details."
                            ),
                        },
                        {"role": "user", "content": body},
                    ],
                },
                timeout=settings.OLLAMA_SUMMARY_TIMEOUT,
            )
            response.raise_for_status()
            content = _THINK_RE.sub("", response.json().get("message", {}).get("content", "")).strip()
            data = json.loads(content)

            summary = str(data.get("summary") or "").strip()
            points = [str(p).strip() for p in (data.get("key_points") or []) if str(p).strip()][:5]
            logger.info(f"✅ Summary ready ({len(summary)} chars, {len(points)} points)")
            return summary, points

        except Exception as e:
            logger.warning(f"⚠️ Session summary failed: {type(e).__name__}: {e}")
            return "", []

    @staticmethod
    def build_whisper_prompt(glossary=None):
        """Whisper's initial_prompt: anti-hallucination text plus glossary terms.

        Naming the vocabulary up front is the single most effective way to stop
        Whisper guessing at domain words. Capped because the prompt shares the
        model's 224-token window.
        """
        prompt = "Real spoken conversation audio."
        terms = [t for t in (glossary or []) if t]
        if terms:
            prompt += " Terms used: " + ", ".join(terms[: settings.RS2T_GLOSSARY_PROMPT_TERMS]) + "."
        return prompt

    def transcribe_audio(self, audio_file_path, glossary=None):
        """Transcribe audio with the local Whisper model (no external API)."""
        try:
            logger.info("🎤 Running local Whisper...")

            model = _get_whisper_model()
            segment_iter, info = model.transcribe(
                audio_file_path,
                beam_size=settings.WHISPER_BEAM_SIZE,
                temperature=0,
                # Biases Whisper toward real speech (cutting filler-phrase and
                # CJK hallucinations) and toward the user's own vocabulary.
                initial_prompt=self.build_whisper_prompt(glossary),
                # Each chunk is independent — carrying context across chunks
                # is what makes Whisper loop on noise.
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )

            # transcribe() is lazy — decoding happens while draining this generator
            segments = list(segment_iter)
            text = " ".join(s.text for s in segments).strip()
            detected_lang = info.language or "unknown"

            if not text:
                logger.warning("⚠️ Empty transcription from local Whisper")
                return None, None, 0.0

            # --- Segment-level quality checks ---
            if segments:
                avg_no_speech = sum(getattr(s, "no_speech_prob", 0.0) for s in segments) / len(segments)
                avg_logprob = sum(getattr(s, "avg_logprob", 0.0) for s in segments) / len(segments)
                comp_ratio = sum(getattr(s, "compression_ratio", 1.0) for s in segments) / len(segments)
                # Worst-case single segment check — one bad segment taints the whole chunk
                max_no_speech = max(getattr(s, "no_speech_prob", 0.0) for s in segments)

                logger.info(
                    f"   Segments: no_speech={avg_no_speech:.2f} (max={max_no_speech:.2f})  "
                    f"logprob={avg_logprob:.2f}  compression={comp_ratio:.2f}"
                )

                if avg_no_speech > 0.85:
                    logger.warning("⚠️ High no-speech probability — likely silence/noise")
                    return None, None, 0.0

                if max_no_speech > 0.95:
                    logger.warning("⚠️ A segment has very high no-speech prob — discarding")
                    return None, None, 0.0

                if avg_logprob < -1.5:
                    logger.warning("⚠️ Very low log-probability — low confidence, discarding")
                    return None, None, 0.0

                if comp_ratio > 2.5:
                    logger.warning("⚠️ High compression ratio — likely Whisper looping/hallucinating")
                    return None, None, 0.0

                confidence = max(0.0, min(1.0, 1.0 + avg_logprob))  # logprob 0 → 1.0, -1 → 0.0
            else:
                confidence = 0.85

            # --- CJK false-language hallucination check ---
            # Whisper commonly hallucinates Chinese/Japanese characters on
            # near-silence or non-CJK audio. Reject short CJK-only strings.
            cjk_chars = _CJK_RE.findall(text)
            total_cjk = sum(len(c) for c in cjk_chars)
            if total_cjk > 0 and total_cjk >= len(text.replace(" ", "")) * 0.7:
                # Text is 70%+ CJK characters
                if confidence < 0.6 or len(text.replace(" ", "")) < 10:
                    logger.warning(f"⚠️ CJK hallucination suspected (conf={confidence:.2f}): '{text}'")
                    return None, None, 0.0

            # --- Phrase-level hallucination filter ---
            if self._is_hallucination(text):
                logger.warning(f"⚠️ Hallucination detected, discarding: '{text}'")
                return None, None, 0.0

            # --- Repetition filter (Whisper looping on music/noise) ---
            if _is_repetitive(text):
                logger.warning(f"⚠️ Repetitive hallucination detected, discarding: '{text[:80]}'")
                return None, None, 0.0

            logger.info(f"✅ Transcription: '{text[:500]}'")
            logger.info(f"   Language: {detected_lang}  confidence: {confidence:.2f}")

            return text, detected_lang, confidence

        except Exception as e:
            logger.error(f"❌ Local Whisper error: {type(e).__name__}: {str(e)}")
            import traceback

            traceback.print_exc()
            return None, None, 0.0

    def translate_text(self, text, source_lang, target_lang_code):
        """Translate text via the local Ollama server to the specified target language"""
        try:
            target_lang_name = self.get_language_name(target_lang_code)

            if source_lang.lower() == target_lang_code.lower():
                return text

            logger.info(f"🌐 Translating {source_lang} to {target_lang_name} via Ollama...")

            translation = self._ollama_translate(text, target_lang_code, settings.OLLAMA_TIMEOUT)
            if not translation:
                logger.warning("⚠️ Ollama returned an empty translation")
                return text

            logger.info(f"✅ Translation: '{translation[:100]}'")
            return translation

        except Exception as e:
            logger.error(f"❌ Translation error: {type(e).__name__}: {str(e)}")
            return text

    def translate_fast(self, text, source_lang_code, target_lang_code):
        """Fast translation via Google Translate (~150-300ms). Returns None on failure."""
        # Same-language: no API call needed
        src_code = (source_lang_code or "").lower().strip()
        tgt_code = (target_lang_code or "").lower().strip()
        if src_code and src_code == tgt_code:
            return text
        # Cache key: avoid duplicate calls for identical text+lang pairs
        cache_key = (text, src_code, tgt_code)
        cached = _translation_cache.get(cache_key)
        if cached:
            logger.info("⚡ Translation cache hit")
            return cached
        try:
            from deep_translator import GoogleTranslator

            result = GoogleTranslator(source=src_code or "auto", target=tgt_code).translate(text)
            if result and result.strip():
                r = result.strip()
                logger.info(f"⚡ Google Translate: '{r[:80]}'")
                _translation_cache[cache_key] = r
                # Keep cache bounded to 200 entries
                if len(_translation_cache) > 200:
                    _translation_cache.pop(next(iter(_translation_cache)))
                return r
        except Exception as e:
            logger.warning(f"⚠️ Google Translate failed: {e}")
        return None

    def translate_text_stream(self, text, source_lang, target_lang_code):
        """Translate via Ollama, yielding the full result as one token.
        Non-streaming: same token count, simpler, faster time-to-first-token."""
        try:
            target_lang_name = self.get_language_name(target_lang_code)

            if source_lang.lower() == target_lang_code.lower():
                yield text
                return

            logger.info(f"🌐 Translating (fallback) {source_lang} → {target_lang_name} via Ollama...")

            result = self._ollama_translate(text, target_lang_code, settings.OLLAMA_TIMEOUT)
            if result:
                yield result

        except Exception as e:
            logger.error(f"❌ Translation fallback error: {type(e).__name__}: {e}")
            yield text

    def detect_language_name(self, lang_code):
        """Convert language code to readable name"""
        lang_names = {
            "hi": "Hindi",
            "gu": "Gujarati",
            "zh": "Chinese",
            "en": "English",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "ja": "Japanese",
            "ko": "Korean",
            "ar": "Arabic",
            "ru": "Russian",
            "pt": "Portuguese",
            "it": "Italian",
            "nl": "Dutch",
            "pl": "Polish",
            "tr": "Turkish",
            "ta": "Tamil",
            "te": "Telugu",
            "bn": "Bengali",
            "mr": "Marathi",
            "pa": "Punjabi",
            "ur": "Urdu",
        }
        return lang_names.get(lang_code.lower(), lang_code.upper())

    def get_language_name(self, code):
        """Get language name by code for display"""
        lang_map = {
            "en": "English",
            "hi": "हिंदी",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "zh": "Chinese",
            "ja": "Japanese",
            "ar": "Arabic",
            "ru": "Russian",
            "pt": "Portuguese",
            "it": "Italian",
            "ko": "Korean",
        }
        return lang_map.get(code, "English")
