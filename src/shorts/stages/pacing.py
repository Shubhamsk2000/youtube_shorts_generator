"""Tighter pacing: which parts of a clip to keep (jump cuts over dead air) -> edits/<clip_id>.json.

A pause between two words is shortened when it is longer than `min_gap_s`, shorter than `max_gap_s`
(long pauses usually mean something is happening on screen) and quiet: its loudness is well under
the speech around it, so reactions, explosions and laughs are never cut. Written by the captions
stage (captions are timed on the edited timeline) and read by the renderer.
"""
from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np

from ..config import ColdOpenCfg, PacingCfg
from ..models import Clip, Transcript

EDITS_DIR = "edits"
MIN_CUT_S = 0.12  # cuts shorter than this aren't worth a jump
FLASH_STEP_S = 0.1
FLASH_MARGIN_S = 0.15  # a wordless reaction window keeps this far from any word
FLASH_PAUSE_S = 0.2    # a phrase flash needs at least this much silence on both sides
FLASH_LEAD_S, FLASH_TAIL_S = 0.05, 0.1

Span = tuple[float, float]


def edits_path(job_dir: Path, clip: Clip) -> Path:
    return job_dir / EDITS_DIR / f"{clip.id}.json"


def load_edits(job_dir: Path, clip: Clip) -> tuple[list[Span] | None, Span | None]:
    """(kept segments, cold-open flash), both clip-relative seconds. None = keep everything / no flash."""
    p = edits_path(job_dir, clip)
    if not p.exists():
        return None, None
    d = json.loads(p.read_text())
    keep = [(float(a), float(b)) for a, b in d.get("keep") or []] or None
    fl = d.get("flash")
    return keep, ((float(fl[0]), float(fl[1])) if fl else None)


def load_keep(job_dir: Path, clip: Clip) -> list[Span] | None:
    return load_edits(job_dir, clip)[0]


def save_edits(job_dir: Path, clip: Clip, keep: list[Span] | None, flash: Span | None = None) -> None:
    p = edits_path(job_dir, clip)
    if keep is None and flash is None:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(exist_ok=True)
    d: dict = {}
    if keep:
        d["keep"] = [[round(a, 3), round(b, 3)] for a, b in keep]
    if flash:
        d["flash"] = [round(flash[0], 3), round(flash[1], 3)]
    text = json.dumps(d)
    if not p.exists() or p.read_text() != text:  # unchanged file keeps the render cache valid
        p.write_text(text)


def save_keep(job_dir: Path, clip: Clip, keep: list[Span] | None) -> None:
    save_edits(job_dir, clip, keep, None)


def flash_window(clip: Clip, t: Transcript, audio: "ClipAudio | None", co: ColdOpenCfg) -> Span | None:
    """Clip-relative (a, b) of the cold open: a tease from the payoff, played before the clip starts.

    Never a slice through speech (that sounds like a botched cut): either a reaction with no words in it
    (crash, scream, laugh) or a short phrase of whole words with a pause on both sides. None = no clean
    moment, so no flash rather than a bad one."""
    if not co.enabled or clip.payoff_sentence is None:
        return None
    if co.when == "no_premise" and not clip.show_hook:
        return None
    s = next((x for x in t.sentences if x.id == clip.payoff_sentence), None)
    if s is None:
        return None
    a0 = s.start - clip.start
    if a0 < co.min_lead_s:
        return None
    lo, hi = max(0.0, a0), min(clip.duration, s.end - clip.start + 3.0)  # the line and the reaction after it
    near = [w for x in t.sentences for w in x.words if w.end - clip.start > lo - 2.0 and w.start - clip.start < hi + 2.0]
    words = [(w.start - clip.start, w.end - clip.start) for w in near]
    ends = [w.text.rstrip("\"')]").endswith((".", "!", "?")) for w in near]
    cands = _phrase_windows(words, ends, lo, hi, co)
    speech = _speech_level(words, audio)
    if speech is not None:  # wordless windows only count when they're loud (a crash, a scream), not silence
        cands += [ab for ab in _reaction_windows(words, lo, hi, co) if (audio.rms(*ab) or 0.0) >= speech]
    if not cands:
        return None
    if speech is None:  # nothing to judge loudness by: the earliest clean phrase
        a, b = min(cands)
    else:
        a, b = max(cands, key=lambda ab: (audio.rms(*ab) or 0.0))
    return round(a, 3), round(b, 3)


def _speech_level(words: list[Span], audio: "ClipAudio | None") -> float | None:
    """Median loudness of the words: the bar a wordless reaction has to clear."""
    if audio is None:
        return None
    levels = [r for a, b in words if b - a > 0.05 and (r := audio.rms(max(0.0, a), b)) is not None]
    return float(np.median(levels)) if levels else None


def _reaction_windows(words: list[Span], lo: float, hi: float, co: ColdOpenCfg) -> list[Span]:
    """Fixed-length windows in the payoff region that no word touches (with a margin)."""
    out, a, length = [], lo, co.duration_s
    while a + length <= hi + 1e-9:
        b = a + length
        if not any(ws < b + FLASH_MARGIN_S and we > a - FLASH_MARGIN_S for ws, we in words):
            out.append((a, b))
        a += FLASH_STEP_S
    return out


def _phrase_windows(words: list[Span], ends: list[bool], lo: float, hi: float, co: ColdOpenCfg) -> list[Span]:
    """Whole short sentences inside [lo, hi] with a pause before and after: a complete exclamation
    ("Drop him!", "Keep calm, okay?"), never half a thought ("So if I were you,")."""
    out = []
    for i in range(len(words)):
        if words[i][0] < lo or (i > 0 and (words[i][0] - words[i - 1][1] < FLASH_PAUSE_S or not ends[i - 1])):
            continue
        for j in range(i, len(words)):
            a, b = words[i][0] - FLASH_LEAD_S, words[j][1] + FLASH_TAIL_S
            if b - a > co.max_phrase_s or words[j][1] > hi:
                break
            nxt = words[j + 1][0] if j + 1 < len(words) else math.inf
            if ends[j] and nxt - words[j][1] >= FLASH_PAUSE_S and b - a >= co.min_phrase_s:
                out.append((max(0.0, a), b))
    return out


# ---------------------------------------------------------------- audio

class ClipAudio:
    """RMS loudness of the job's 16 kHz mono audio.wav over a clip."""

    def __init__(self, wav: Path, start: float, dur: float):
        self.sr = 16000
        self.x = np.zeros(0, np.float32)
        if not wav.exists():
            return
        with wave.open(str(wav)) as w:
            self.sr = w.getframerate()
            w.setpos(min(w.getnframes(), max(0, int(start * self.sr))))
            raw = w.readframes(int(dur * self.sr))
            if w.getsampwidth() == 2:
                self.x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
                if w.getnchannels() > 1:
                    self.x = self.x.reshape(-1, w.getnchannels()).mean(axis=1)

    def rms(self, a: float, b: float) -> float | None:
        if not len(self.x):
            return None
        seg = self.x[max(0, int(a * self.sr)):max(0, int(b * self.sr))]
        return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else None


# ---------------------------------------------------------------- decisions (pure)

def keep_segments(words: list[tuple[float, float]], dur: float, pc: PacingCfg,
                  audio: ClipAudio | None = None) -> list[tuple[float, float]] | None:
    """words = (start, end) clip-relative, in order. Returns kept segments, or None if nothing is cut."""
    if not pc.enabled or len(words) < 2:
        return None
    speech = None
    if audio is not None:
        levels = [r for a, b in words if b - a > 0.05 and (r := audio.rms(a, b)) is not None]
        speech = float(np.median(levels)) if levels else None

    cuts: list[tuple[float, float]] = []
    for (_, a_end), (b_start, _) in zip(words, words[1:]):
        gap = b_start - a_end
        if not (pc.min_gap_s < gap <= pc.max_gap_s):
            continue
        c0, c1 = a_end + pc.keep_gap_s / 2, b_start - pc.keep_gap_s / 2
        if c1 - c0 < MIN_CUT_S:
            continue
        if speech and audio is not None:
            lvl = audio.rms(c0, c1)
            if lvl is not None and lvl > pc.quiet_ratio * speech:
                continue  # something loud happens in the pause: keep it
        cuts.append((max(0.0, c0), min(dur, c1)))
    if not cuts:
        return None
    keep, t = [], 0.0
    for c0, c1 in cuts:
        if c0 > t:
            keep.append((t, c0))
        t = c1
    if t < dur:
        keep.append((t, dur))
    return [(round(a, 3), round(b, 3)) for a, b in keep if b - a > 0.01]


def remap(t: float, keep: list[tuple[float, float]] | None) -> float:
    """Original clip time -> edited time (a time inside a cut lands on the cut point)."""
    if not keep:
        return t
    out = 0.0
    for a, b in keep:
        if t <= a:
            return out
        if t <= b:
            return out + t - a
        out += b - a
    return out


def unmap(t: float, keep: list[tuple[float, float]] | None) -> float:
    """Edited time -> original clip time."""
    if not keep:
        return t
    acc = 0.0
    for a, b in keep:
        if t <= acc + (b - a):
            return a + (t - acc)
        acc += b - a
    return keep[-1][1]


def kept_duration(keep: list[tuple[float, float]] | None, dur: float) -> float:
    return sum(b - a for a, b in keep) if keep else dur
