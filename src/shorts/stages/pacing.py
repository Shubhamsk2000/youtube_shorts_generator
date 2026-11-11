"""Tighter pacing: which parts of a clip to keep (jump cuts over dead air) -> edits/<clip_id>.json.

A pause between two words is shortened when it is longer than `min_gap_s`, shorter than `max_gap_s`
(long pauses usually mean something is happening on screen) and quiet: its loudness is well under
the speech around it, so reactions, explosions and laughs are never cut. Written by the captions
stage (captions are timed on the edited timeline) and read by the renderer.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from ..config import PacingCfg
from ..models import Clip

EDITS_DIR = "edits"
MIN_CUT_S = 0.12  # cuts shorter than this aren't worth a jump


def edits_path(job_dir: Path, clip: Clip) -> Path:
    return job_dir / EDITS_DIR / f"{clip.id}.json"


def load_keep(job_dir: Path, clip: Clip) -> list[tuple[float, float]] | None:
    """Kept segments (clip-relative seconds), or None = keep everything."""
    p = edits_path(job_dir, clip)
    if not p.exists():
        return None
    keep = json.loads(p.read_text()).get("keep")
    return [(float(a), float(b)) for a, b in keep] if keep else None


def save_keep(job_dir: Path, clip: Clip, keep: list[tuple[float, float]] | None) -> None:
    p = edits_path(job_dir, clip)
    if keep is None:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(exist_ok=True)
    text = json.dumps({"keep": [[round(a, 3), round(b, 3)] for a, b in keep]})
    if not p.exists() or p.read_text() != text:  # unchanged file keeps the render cache valid
        p.write_text(text)


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
