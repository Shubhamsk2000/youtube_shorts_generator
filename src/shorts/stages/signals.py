"""Audio signals for the moment picker -> signals.json.

The transcript says what was said; this says how loud it was and what happened between the words.
From the job's 16 kHz audio.wav: loudness per 0.25 s bin, and from that, per sentence:
  - loud: one of the loudest lines of the video (shouting, a big reaction)
  - burst_after: seconds of loud non-speech right after the line (crash, explosion, screams, laughter),
    which is usually the payoff a clip should end on
Everything is relative to the video's own levels, so a quiet podcast and a loud stunt video both work.
Reading a one-hour wav takes a few seconds; nothing here decodes video.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..models import AudioSignals, Sentence, Transcript, load_json, save_json

OUT = "signals.json"
AUDIO = "audio.wav"
BIN_S = 0.25
BURST_WINDOW_S = 3.0   # how far after a line a reaction is still "right after"
BURST_ONSET_S = 1.0    # ...and how soon it has to start


def run(job_dir: Path, t: Transcript, *, force: bool = False) -> AudioSignals | None:
    """Cached per job; None when there is no audio to read."""
    out = job_dir / OUT
    if out.exists() and not force:
        return load_json(AudioSignals, out)
    wav = job_dir / AUDIO
    if not wav.exists():
        return None
    sig = analyze(wav, t)
    save_json(sig, out)
    return sig


def analyze(wav: Path, t: Transcript, bin_s: float = BIN_S) -> AudioSignals:
    db = loudness(wav, bin_s)
    speech = speech_mask(t.sentences, bin_s, len(db))
    spoken = db[speech]
    ref = spoken if len(spoken) else db
    speech_db = float(np.median(ref)) if len(ref) else -60.0
    return AudioSignals(bin_s=bin_s, loudness=[round(float(x), 1) for x in db], speech_db=round(speech_db, 1))


def loudness(wav: Path, bin_s: float) -> np.ndarray:
    """RMS level in dBFS per bin, streamed so a long file never sits in memory twice."""
    out: list[np.ndarray] = []
    with wave.open(str(wav)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        per = max(1, int(bin_s * sr))
        while True:
            raw = w.readframes(per * 400)
            if not raw:
                break
            if sw == 2:
                x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
            elif sw == 4:
                x = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648
            else:
                x = np.frombuffer(raw, np.uint8).astype(np.float32) / 128 - 1
            if ch > 1:
                x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
            k = len(x) // per
            if k == 0:
                break
            rms = np.sqrt(np.mean(x[: k * per].reshape(k, per) ** 2, axis=1) + 1e-12)
            out.append(20 * np.log10(rms + 1e-9))
    return np.concatenate(out).clip(-90, 0) if out else np.zeros(0)


def speech_mask(sentences: list[Sentence], bin_s: float, n: int) -> np.ndarray:
    """True for bins that overlap a transcribed word."""
    m = np.zeros(n, dtype=bool)
    for s in sentences:
        for w in s.words:
            a = int(w.start / bin_s)
            b = int(np.ceil(w.end / bin_s))
            m[max(0, a): min(n, max(b, a + 1))] = True
    return m


# ---------------------------------------------------------------- per-sentence marks (pure)

@dataclass
class Mark:
    peak_db: float
    loud: bool             # among the loudest lines of the video
    burst_after_s: float   # loud non-speech right after the line (0 = none)


def sentence_marks(sig: AudioSignals, sentences: list[Sentence], *, loud_top: float = 0.12,
                   burst_min_s: float = 0.4, burst_margin_db: float = 3.0) -> dict[int, Mark]:
    db = np.asarray(sig.loudness, dtype=float)
    n, bs = len(db), sig.bin_s
    if n == 0 or not sentences:
        return {}
    speech = speech_mask(sentences, bs, n)

    def span(t0: float, t1: float) -> tuple[int, int]:
        a = min(n, max(0, int(t0 / bs)))
        return a, min(n, max(a, int(np.ceil(t1 / bs))))

    peaks = {}
    for s in sentences:
        a, b = span(s.start, s.end)
        peaks[s.id] = float(db[a:b].max()) if b > a else -90.0
    ordered = sorted(peaks.values())
    thr = ordered[int(round((1 - loud_top) * (len(ordered) - 1)))]
    loud_level = sig.speech_db + burst_margin_db   # a reaction has to beat typical speech clearly
    onset = max(1, int(BURST_ONSET_S / bs))

    marks: dict[int, Mark] = {}
    for i, s in enumerate(sentences):
        nxt = sentences[i + 1].start if i + 1 < len(sentences) else n * bs
        a, b = span(s.end, min(nxt, s.end + BURST_WINDOW_S))
        gap = (db[a:b] >= loud_level) & ~speech[a:b]
        burst = float(gap.sum() * bs) if gap[:onset].any() else 0.0
        marks[s.id] = Mark(peak_db=round(peaks[s.id], 1),
                           loud=peaks[s.id] >= thr and peaks[s.id] >= loud_level,
                           burst_after_s=round(burst, 2) if burst >= burst_min_s else 0.0)
    return marks


def energy01(sig: AudioSignals, t0: float, t1: float) -> float:
    """Peak loudness of a span on the video's own scale: 0 = quiet floor, 1 = the loudest it gets."""
    db = np.asarray(sig.loudness, dtype=float)
    if len(db) == 0:
        return 0.0
    a = min(len(db), max(0, int(t0 / sig.bin_s)))
    b = min(len(db), max(a + 1, int(np.ceil(t1 / sig.bin_s))))
    lo, hi = np.percentile(db, 10), np.percentile(db, 99)
    if hi - lo < 1e-6:
        return 0.0
    return float(np.clip((db[a:b].max() - lo) / (hi - lo), 0.0, 1.0))


def describe(marks: dict[int, Mark]) -> str:
    loud = sum(m.loud for m in marks.values())
    bursts = sum(1 for m in marks.values() if m.burst_after_s)
    return f"{loud} loud lines · {bursts} reactions"
