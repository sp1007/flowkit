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
_SR = 16000                # whisperx.load_audio always resamples to 16 kHz
# whisperx.align refines each segment INSIDE its seeded [start,end] window, so the seed must be
# roomy enough to contain the real speech or its backtracking fails and it returns the seed
# untouched. Pad each window by this fraction of its own length (and at least _PAD_MIN seconds).
_PAD_FRAC = 0.35
_PAD_MIN = 0.6

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


def _plausible(spans: list[tuple[float, float]], sentences: list[str]) -> bool:
    """Sanity-check an alignment: every segment should take roughly the same seconds-per-word.
    A collapsed alignment squeezes some segments to ~0 and stretches others (we have seen a
    20-word shot land in 0.23s next to an 11-word one holding 34s). Reject if too many segments
    are wildly off the median rate — the caller then falls back to proportional timing."""
    rates = [(e - s) / len(t.split())
             for (s, e), t in zip(spans, sentences)
             if len(t.split()) >= 5 and e > s]
    if len(rates) < 5:
        return True
    rates_sorted = sorted(rates)
    med = rates_sorted[len(rates_sorted) // 2]
    if med <= 0:
        return False
    bad = sum(1 for r in rates if r < med / 2.5 or r > med * 2.5)
    return bad <= 0.15 * len(rates)


def _align_units(whisperx, audio, dur: float, units: list[str], model, metadata) -> list[float]:
    """Force-align whole SENTENCES and return one start time each (monotonic, in [0, dur]).

    Sentences — not shot fragments — are the unit that whisperx aligns reliably: it refines each
    segment inside its seeded window, and a 2–20 word fragment's word-count seed is too crude, so
    its backtracking fails ("resorting to original") and the segment keeps its seed time. Windows
    are padded so the real speech always fits inside."""
    wc = [max(1, len(u.split())) for u in units]
    tot = sum(wc)
    segs, t = [], 0.0
    for u, w in zip(units, wc):
        d = dur * w / tot
        pad = max(_PAD_MIN, d * _PAD_FRAC)
        segs.append({"start": round(max(0.0, t - pad), 2),
                     "end": round(min(dur, t + d + pad), 2),
                     "text": u})
        t += d
    res = whisperx.align(segs, model, metadata, audio, _DEVICE, return_char_alignments=False)
    got = res.get("segments") or []
    starts: list[float] = []
    prev = 0.0
    for i in range(len(units)):
        st = got[i].get("start") if i < len(got) else None
        v = float(st) if st is not None else prev
        v = min(max(v, prev), dur)          # monotonic + clamped
        starts.append(v)
        prev = v
    return starts


def _spread(starts: list[float], wc: list[int], dur: float) -> list[float]:
    """Pull apart runs of identical starts (a segment whisperx couldn't place gets clamped onto
    its predecessor) by redistributing the run across the gap to the next real anchor, in
    proportion to word counts. Without this, collapsed sentences yield zero-length shots."""
    n = len(starts)
    out = list(starts)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and out[j + 1] <= out[i] + 1e-6:
            j += 1
        if j > i:
            a = out[i]
            b = out[j + 1] if j + 1 < n else dur
            span = max(0.0, b - a)
            tot = sum(wc[i:j + 1]) or 1
            t = a
            for k in range(i, j + 1):
                out[k] = t
                t += span * wc[k] / tot
        i = j + 1
    return out


def _word_time(sent_starts: list[float], sent_wc: list[int], dur: float, k: int) -> float:
    """Time of global word index `k`, interpolating linearly inside the sentence that holds it."""
    base = 0
    for i, w in enumerate(sent_wc):
        if k < base + w:
            a = sent_starts[i]
            b = sent_starts[i + 1] if i + 1 < len(sent_starts) else dur
            return a + (b - a) * ((k - base) / w)
        base += w
    return dur


def align_sentences(wav_path: str, texts: list[str], *,
                    language: str = "vi") -> list[tuple[float, float]]:
    """Return one (start, end) per input text, monotonic and tiling [0, audio_dur].

    `texts` are the shots' verbatim spoken slices (often mid-sentence fragments). We do NOT align
    them directly — whisperx mis-places short fragments. Instead we align the SENTENCES of the
    joined narration, then interpolate each shot's boundary by its word position inside the
    sentence that contains it. Falls back to proportional (word-count) timing on any error, when
    the text can't be re-derived from sentences, or when the result fails `_plausible`.
    """
    texts = [t for t in (texts or []) if (t or "").strip()]
    if not texts:
        return []
    dur = _wav_duration(wav_path)
    seed = _proportional(texts, dur)
    if dur <= 0 or not available():
        return seed
    try:
        from agent.studio import vntext
        joined = " ".join(texts)
        sents = [s for s in vntext.sentences(joined) if s.split()] or [joined]
        text_wc = [len(t.split()) for t in texts]
        sent_wc = [len(s.split()) for s in sents]
        if sum(sent_wc) != sum(text_wc):        # sentence split lost/added words → can't map
            logger.warning("align: câu tách ra không khớp số từ — dùng canh giờ theo số từ")
            return seed
        with _lock:
            import whisperx
            audio = whisperx.load_audio(wav_path)
            dur = len(audio) / float(_SR)
            model, metadata = _load(language)
            sent_starts = _align_units(whisperx, audio, dur, sents, model, metadata)
        sent_starts = _spread(sent_starts, sent_wc, dur)
        # shot i spans global word indices [cum_i, cum_{i+1}) → interpolate its start
        out: list[tuple[float, float]] = []
        cum, prev = 0, 0.0
        starts: list[float] = []
        for w in text_wc:
            v = max(prev, _word_time(sent_starts, sent_wc, dur, cum))
            starts.append(v)
            prev, cum = v, cum + w
        starts = _spread(starts, text_wc, dur)      # no zero-length shots
        for i in range(len(texts)):
            end = starts[i + 1] if i + 1 < len(starts) else dur
            out.append((round(starts[i], 3), round(max(starts[i], end), 3)))
        if not _plausible(out, texts):
            logger.warning("WhisperX align không hợp lệ (nhiều đoạn lệch nhịp) — "
                           "dùng canh giờ theo số từ")
            return seed
        return out
    except Exception as e:  # noqa: BLE001 — never let alignment sink a build
        logger.warning("WhisperX align failed (%s) — dùng canh giờ theo số từ", e)
        return seed


