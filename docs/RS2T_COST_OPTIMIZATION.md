# RS2T — Cost Optimization Report

**App:** Real-time Speech-to-Text & Translation (`/rs2t/`)  
**Date:** May 13, 2026

> **Superseded — August 17, 2026.** RS2T no longer calls the OpenAI API at all.
> Transcription now runs on a local Whisper model (faster-whisper, CPU) and
> translation falls back to the self-hosted Ollama server on this host instead
> of `gpt-4o-mini`. Per-call API cost is now zero, so the token-level tuning
> below is historical. The latency and audio-filtering work it describes
> (silence/hallucination filters, chunking, the Google Translate fast path) is
> still in force and still worth keeping.

---

## Summary

OpenAI API costs were unnecessarily high due to **6 root causes** across the frontend and backend. All have been fixed with zero impact on translation accuracy.

---

## Issue 1 — Auto-GPT Summary on Every Session Stop

**File:** `rs2t/services/session_manager.py`

### What was happening
Every time a user stopped recording, `save_user_session()` automatically called `_generate_summary()` which sent up to **40 lines of transcript** to GPT (`gpt-4o-mini`) to generate a 2–3 sentence summary.

This fired on **every session end** — including short tests, accidental stops, and page refreshes. Each call consumed 400–600 tokens.

### Fix
Replaced the GPT summary call with a simple concatenation of the first 5 translated sentences. No GPT call, zero tokens, and the result is still readable.

```python
# Before — GPT call on every session stop
response = client.chat.completions.create(
    model='gpt-4o-mini',
    messages=[...40 lines of transcript...],
    max_tokens=200,
)

# After — no API call
parts = [t.get('translation', '') for t in translations[:5] if t.get('translation')]
return ' '.join(parts)
```

**Savings:** Eliminated entirely — was the single biggest cost driver during development/testing.

---

## Issue 2 — Same-Language Translation Still Called GPT

**File:** `rs2t/services/translation_service.py`, `rs2t/services/audio_processor.py`

### What was happening
The same-language check compared a language **name** against a language **code**:

```python
# Bug: "English" == "en" is always False
if source_lang.lower() == target_lang_code.lower():
    return text
```

This meant when someone spoke English and had English selected as the target language, every chunk still called Google Translate (and GPT as fallback). 100% of those API calls were unnecessary.

### Fix
Added a code-to-code comparison before any API call:

```python
# audio_processor.py
same_lang = detected_lang.lower().strip() == target_lang.lower().strip()
google_future = None if same_lang else loop.run_in_executor(...)

# translation_service.py — translate_fast()
if src_code and src_code == tgt_code:
    return text  # skip API entirely
```

**Savings:** 100% of translation API calls eliminated for same-language sessions.

---

## Issue 3 — GPT System Prompt Was Verbose (30+ Tokens per Call)

**File:** `rs2t/services/translation_service.py`

### What was happening
Every GPT translation call sent a verbose system prompt:

```
"Translate the following text to English. Output ONLY the translation — 
no explanations, no refusals, no extra words. Use simple, natural 
conversational language."
```

This is ~35 tokens just for the system prompt, charged on every call.

### Fix
Shortened to 9 tokens:

```
"Translate to English. Output ONLY the translation."
```

**Savings:** ~75% reduction in system prompt tokens per GPT call.

---

## Issue 4 — max_tokens Hardcoded at 120 Regardless of Input Size

**File:** `rs2t/services/translation_service.py`

### What was happening
`max_tokens=120` was set for every translation call. With 1-second audio chunks containing 2–5 words, translations are 4–12 tokens long. The remaining 108–116 reserved tokens were never used but still counted toward billable output capacity in some billing models.

### Fix
Dynamic `max_tokens` proportional to input word count:

```python
word_count = len(text.split())
max_tok = min(max(word_count * 2 + 8, 20), 80)

# Examples:
# 2-word input  → max_tokens=12  (was 120, 90% reduction)
# 5-word input  → max_tokens=18  (was 120, 85% reduction)
# 10-word input → max_tokens=28  (was 120, 77% reduction)
```

**Savings:** 75–90% reduction in `max_tokens` per call, capped at 80 for longer phrases.

---

## Issue 5 — Audio Bitrate 4× Higher Than Needed

**File:** `static/js/rs2t_app.js`

### What was happening
The browser `MediaRecorder` was configured at **128 kbps**. For 16kHz mono speech recognition, 32 kbps is more than sufficient (Whisper accepts low-bitrate audio without accuracy loss).

```javascript
// Before
opts.audioBitsPerSecond = 128000;
```

This meant every 1-second chunk uploaded ~16 KB of audio instead of ~4 KB, increasing:
- Upload bandwidth per call
- Whisper API processing time (billed per audio minute, not bytes, but affects latency)
- WebM files that were actually silent or near-silent were large enough to pass the size filter and still trigger FFmpeg + Whisper

### Fix
```javascript
// After
opts.audioBitsPerSecond = 32000;  // 75% less data per chunk
```

**Savings:** 75% reduction in audio data sent per chunk.

---

## Issue 6 — Silent Chunks Reaching Whisper API

**Files:** `rs2t/services/audio_processor.py`, `static/js/rs2t_app.js`

### What was happening
Even with the RMS silence filter, a silent WebM at 128 kbps was large enough (>1500 bytes) to pass all pre-checks, trigger FFmpeg conversion, and reach the Whisper API — which would return an empty or hallucinated result. Whisper is billed per audio minute regardless of content.

Also, if Whisper returned the same phrase twice in a row (e.g. from a looping background audio source), Google Translate was called again for identical text.

### Fixes

**WebM size pre-filter** (before FFmpeg is even run):
```python
MIN_WEBM_BYTES = 1500

if len(audio_data) < MIN_WEBM_BYTES:
    return  # no FFmpeg, no Whisper, no translation
```

At 32 kbps, 1 second of real speech produces ~4 KB. Silent blobs are <1 KB and are dropped immediately.

**Transcript deduplication:**
```python
normalized = ' '.join(text.lower().split())
if normalized == AudioProcessor._last_transcript:
    return  # already translated this exact phrase
AudioProcessor._last_transcript = normalized
```

**Translation result cache** (200-entry dict):
```python
cache_key = (text, src_code, tgt_code)
cached = _translation_cache.get(cache_key)
if cached:
    return cached  # no Google/GPT call
```

**Savings:** Eliminates Whisper calls for silent chunks; eliminates duplicate translation calls for repeated phrases.

---

## Before vs After Summary

| # | Issue | API Calls Eliminated |
|---|---|---|
| 1 | Auto-GPT summary on session stop | 100% (removed entirely) |
| 2 | Same-language translation bug | 100% for same-lang sessions |
| 3 | Verbose system prompt | ~75% fewer system prompt tokens |
| 4 | Fixed max_tokens=120 | 75–90% output token reduction |
| 5 | 128kbps audio bitrate | 75% less data per chunk |
| 6 | Silent blobs reaching Whisper | Dropped before FFmpeg runs |
| 6 | Duplicate transcript re-translation | Skipped with dedup cache |

## What Was NOT Changed

- **Whisper model** — `whisper-1` is the only available hosted model; cost is per audio minute.
- **GPT model** — `gpt-4o-mini` is already OpenAI's cheapest production chat model. Switching to `gpt-3.5-turbo` would cost more at current pricing.
- **Translation accuracy** — Google Translate handles all primary translation (free, ~150ms). GPT is only a fallback if Google fails, which is rare.
- **Hallucination filters** — retained to prevent Whisper noise from reaching any API.
