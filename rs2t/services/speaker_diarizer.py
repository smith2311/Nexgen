"""
rs2t/services/speaker_diarizer.py

Lightweight CPU-only speaker diarizer.
Uses MFCC features (computed with pure numpy) + cosine similarity against a
per-session bank of speaker voice-print profiles.

No heavy ML libraries required — numpy is already a transitive dependency of
soundfile which is in requirements.txt.
"""

import logging
import math
import threading
import wave

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:  # pragma: no cover
    _NUMPY_OK = False
    logger.warning("numpy not available — speaker diarization disabled (all audio → Speaker A)")

# ── DSP constants (must match ffmpeg's -ar 16000 -ac 1 -acodec pcm_s16le) ──
_SR = 16_000  # sample rate
_N_FFT = 512  # FFT window size
_N_MELS = 40  # mel filter banks
_N_MFCC = 20  # MFCCs to keep (coefficients 0..19)
_HOP = 160  # 10 ms hop at 16 kHz
_MIN_FR = 10  # minimum frames needed for a reliable embedding

# ── Speaker assignment parameters ──
SIMILARITY_THRESHOLD = 0.80  # cosine sim ≥ this  →  same speaker
MAX_SPEAKERS = 8  # cap to prevent runaway splits
EMA_ALPHA = 0.10  # voice-print EMA adaptation rate (lower = more stable)

SPEAKER_NAMES = [
    "Speaker A",
    "Speaker B",
    "Speaker C",
    "Speaker D",
    "Speaker E",
    "Speaker F",
    "Speaker G",
    "Speaker H",
]


# ── One-time precomputation at module load ────────────────────────────────────


def _build_mel_fb() -> "np.ndarray":
    """Mel filterbank matrix of shape (_N_MELS, _N_FFT//2 + 1)."""

    def hz2mel(f):
        return 2595.0 * math.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    low_hz, high_hz = 80.0, _SR / 2.0
    mel_pts = np.linspace(hz2mel(low_hz), hz2mel(high_hz), _N_MELS + 2)
    hz_pts = np.array([mel2hz(m) for m in mel_pts])
    bins = np.floor((_N_FFT + 1) * hz_pts / _SR).astype(int)

    fb = np.zeros((_N_MELS, _N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, _N_MELS + 1):
        lo, mid, hi = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        if mid > lo:
            fb[m - 1, lo:mid] = (np.arange(lo, mid) - lo) / (mid - lo)
        if hi > mid:
            fb[m - 1, mid:hi] = (hi - np.arange(mid, hi)) / (hi - mid)
    return fb


def _build_dct() -> "np.ndarray":
    """DCT-II matrix of shape (_N_MFCC, _N_MELS)."""
    m = np.arange(_N_MELS, dtype=np.float32)
    k = np.arange(_N_MFCC, dtype=np.float32).reshape(-1, 1)
    return np.cos(np.pi * k * (2 * m + 1) / (2 * _N_MELS)).astype(np.float32)


if _NUMPY_OK:
    _MEL_FB = _build_mel_fb()  # (40, 257)
    _DCT_MAT = _build_dct()  # (20, 40)
    _HAMMING = np.hamming(_N_FFT).astype(np.float32)  # (512,)


# ── Feature extraction ────────────────────────────────────────────────────────


def _extract_embedding(wav_path: str):
    """
    Read a 16-bit PCM mono WAV file and return an L2-normalised
    MFCC-based speaker embedding (1-D float32 array of length 2*_N_MFCC),
    or None if the clip is too short or extraction fails.
    """
    if not _NUMPY_OK:
        return None

    try:
        with wave.open(wav_path, "rb") as wf:
            if wf.getsampwidth() != 2:
                return None  # need 16-bit PCM
            n_ch = wf.getnchannels()
            n_samps = wf.getnframes()
            raw = wf.readframes(n_samps)

        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        if n_ch > 1:
            samples = samples[::n_ch]  # keep left channel only

        if len(samples) < _N_FFT * _MIN_FR:
            return None  # clip too short

        # Amplitude normalise
        peak = np.abs(samples).max()
        if peak > 0:
            samples /= peak

        # Build all frames at once: shape (T, N_FFT)
        starts = np.arange(0, len(samples) - _N_FFT, _HOP)
        if len(starts) < _MIN_FR:
            return None
        frames = np.stack([samples[i : i + _N_FFT] * _HAMMING for i in starts])

        # Power spectrum: (T, N_FFT//2 + 1)
        power = np.abs(np.fft.rfft(frames, n=_N_FFT, axis=1)) ** 2

        # Mel filterbank: (T, N_MELS)
        mel = power @ _MEL_FB.T

        # Log-mel + DCT → MFCC: (T, N_MFCC)
        log_mel = np.log(mel + 1e-10)
        mfcc = log_mel @ _DCT_MAT.T

        # Global statistics: mean + std per coefficient → (2*N_MFCC,)
        embedding = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)])
        norm = np.linalg.norm(embedding)
        if norm == 0:
            return None
        return (embedding / norm).astype(np.float32)

    except Exception as exc:
        logger.debug(f"Speaker embedding extraction failed: {exc}")
        return None


# ── SpeakerDiarizer ───────────────────────────────────────────────────────────


class SpeakerDiarizer:
    """
    Per-session speaker tracker.

    Maintains a small bank of L2-normalised MFCC voice-print profiles.
    For each new audio chunk it computes cosine similarity against all known
    profiles and either assigns the chunk to the best-matching speaker or
    creates a new one.

    Thread-safe: multiple worker threads may call assign_speaker() concurrently.
    """

    def __init__(self):
        self._lock: threading.Lock = threading.Lock()
        self._profiles: dict[str, dict] = {}  # sid → {'emb': ndarray, 'count': int}
        self._n: int = 0  # number of known speakers

    # ── public API ───────────────────────────────────────────────────────────

    def assign_speaker(self, wav_path: str) -> tuple[str, str, bool]:
        """
        Analyse the audio at *wav_path* and return
        ``(speaker_id, speaker_name, is_new_speaker)``.

        Falls back to the most-active speaker (or 'A') when extraction fails.
        """
        emb = _extract_embedding(wav_path)
        if emb is None:
            return self._fallback()

        with self._lock:
            if not self._profiles:
                return self._add(emb)

            # Vectorised cosine similarity against all stored profiles
            sids = list(self._profiles)
            embs = np.stack([self._profiles[s]["emb"] for s in sids])  # (K, D)
            sims = embs @ emb  # (K,)

            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])
            best_sid = sids[best_idx]

            if best_sim >= SIMILARITY_THRESHOLD:
                return self._update(best_sid, emb)
            elif self._n < MAX_SPEAKERS:
                return self._add(emb)
            else:
                # All slots full → assign to closest regardless of threshold
                return self._update(best_sid, emb)

    def reset(self) -> None:
        """Clear all voice-print profiles (e.g. when the user presses reset)."""
        with self._lock:
            self._profiles.clear()
            self._n = 0
        logger.info("🔄 Speaker profiles reset")

    # ── private helpers ──────────────────────────────────────────────────────

    def _name(self, idx: int) -> str:
        return SPEAKER_NAMES[idx] if idx < len(SPEAKER_NAMES) else f"Speaker {idx + 1}"

    def _add(self, emb: "np.ndarray") -> tuple[str, str, bool]:
        """Register a new speaker and return their (id, name, True)."""
        sid = chr(ord("A") + self._n)
        name = self._name(self._n)
        self._profiles[sid] = {"emb": emb.copy(), "count": 1}
        self._n += 1
        logger.info(f"👤 New speaker detected: {name} ({sid})")
        return sid, name, True

    def _update(self, sid: str, emb: "np.ndarray") -> tuple[str, str, bool]:
        """Update an existing speaker's voice-print EMA and return (id, name, False)."""
        p = self._profiles[sid]
        # Exponential moving average so the model gently adapts to voice drift
        updated = (1.0 - EMA_ALPHA) * p["emb"] + EMA_ALPHA * emb
        norm = np.linalg.norm(updated)
        p["emb"] = (updated / norm) if norm > 0 else updated
        p["count"] += 1
        idx = ord(sid) - ord("A")
        return sid, self._name(idx), False

    def _fallback(self) -> tuple[str, str, bool]:
        """Return the most-active known speaker, or 'A' if none yet."""
        with self._lock:
            if self._profiles:
                sid = max(self._profiles, key=lambda k: self._profiles[k]["count"])
                idx = ord(sid) - ord("A")
                return sid, self._name(idx), False
        return "A", SPEAKER_NAMES[0], False
