"""Forced-alignment of KNOWN Vietnamese narration text to its TTS audio (WhisperX).

Storytelling now reads each scene paragraph as ONE continuous TTS take (natural prosody),
so we no longer get a per-beat duration for free. This module aligns the verbatim sentences
back to the waveform and recovers each sentence's REAL start/end time — so image changes and
subtitles land on the actual speech instead of a word-count estimate.

CPU-only; the wav2vec2 alignment model is loaded once per process and cached. EVERY failure
(whisperx missing, model download blocked, alignment error) degrades to proportional
(word-count) timing, so a scene still builds without WhisperX — just with rougher timing.

`align_sentences` is synchronous (torch); call it from async code via asyncio.to_thread.
"""
import logging
import os
import threading
import wave

logger = logging.getLogger(__name__)

# Set FLOWKIT_ALIGN=0 to force the proportional fallback (skip WhisperX entirely).
_ENABLED = os.environ.get("FLOWKIT_ALIGN", "1").strip().lower() not in ("0", "false", "no")
_DEVICE = os.environ.get("FLOWKIT_ALIGN_DEVICE", "cpu")

_cache: dict[str, tuple] = {}          # language -> (align_model, metadata)
_lock = threading.Lock()               # torch model load/run is not re-entrant


def available() -> bool:
    """True if WhisperX can be imported and alignment is enabled."""
    if not _ENABLED:
        return False
    try:
        import whisperx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _proportional(sentences: list[str], dur: float) -> list[tuple[float, float]]:
    """Word-count fallback: tile [0, dur] across sentences by their word share."""
    wc = [max(1, len((s or "").split())) for s in sentences]
    tot = sum(wc) or 1
    out, t = [], 0.0
    for w in wc:
        d = dur * w / tot
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    if out:                                     # pin the last end exactly to dur
        out[-1] = (out[-1][0], round(dur, 3))
    return out


def _load(language: str):
    """Load + cache the alignment model for `language`. Returns (model, metadata) or None."""
    if language in _cache:
        return _cache[language]
    import whisperx
    model, metadata = whisperx.load_align_model(language_code=language, device=_DEVICE)
    _cache[language] = (model, metadata)
    return _cache[language]


def align_sentences(wav_path: str, sentences: list[str], *,
                    language: str = "vi") -> list[tuple[float, float]]:
    """Return one (start, end) per sentence, monotonic and tiling [0, audio_dur].

    Uses WhisperX forced alignment seeded with proportional per-sentence windows (aligning a
    single long segment collapses, so we hand it sentence-sized segments). Boundaries come from
    the aligned per-sentence START times (the most reliable output); each sentence is tiled up
    to the next one's start. Falls back to proportional timing on any error.
    """
    sentences = [s for s in (sentences or []) if (s or "").strip()]
    if not sentences:
        return []
    dur = _wav_duration(wav_path)
    if dur <= 0 or not available():
        return _proportional(sentences, dur)
    try:
        with _lock:
            import whisperx
            audio = whisperx.load_audio(wav_path)
            dur = len(audio) / 16000.0
            seed = _proportional(sentences, dur)
            segs = [{"start": s, "end": e, "text": t}
                    for (s, e), t in zip(seed, sentences)]
            model, metadata = _load(language)
            res = whisperx.align(segs, model, metadata, audio, _DEVICE,
                                 return_char_alignments=False)
        aligned = res.get("segments") or []
        # collect a start per sentence; a segment WhisperX couldn't place has no/None start
        starts: list[float | None] = []
        for i in range(len(sentences)):
            st = aligned[i].get("start") if i < len(aligned) else None
            starts.append(float(st) if st is not None else None)
        starts = _repair_starts(starts, seed, dur)
        out = []
        for i in range(len(sentences)):
            end = starts[i + 1] if i + 1 < len(starts) else dur
            out.append((round(starts[i], 3), round(max(starts[i], end), 3)))
        return out
    except Exception as e:  # noqa: BLE001 — never let alignment sink a build
        logger.warning("WhisperX align failed (%s) — dùng canh giờ theo số từ", e)
        return _proportional(sentences, dur)


def _repair_starts(starts: list[float | None], seed: list[tuple[float, float]],
                   dur: float) -> list[float]:
    """Fill missing starts from the proportional seed and force a non-decreasing sequence in
    [0, dur], so consecutive-start tiling below is always valid."""
    fixed: list[float] = []
    prev = 0.0
    for i, st in enumerate(starts):
        v = st if st is not None else seed[i][0]
        if v is None or v < prev:
            v = prev
        v = min(max(v, 0.0), dur)
        fixed.append(v)
        prev = v
    if fixed:
        fixed[0] = min(fixed[0], max(0.0, seed[0][0]))   # don't push the first past its seed
    return fixed
