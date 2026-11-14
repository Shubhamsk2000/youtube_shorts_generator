"""Music library for song edits (AMV style): analysis, mood tags, and picking a song for each edit.

Songs live in data/anime/music/ (a subfolder name such as hype/ or sad/ is used as the mood tag).
Each song is analysed once and cached in data/anime/music/.index.json:
  tempo + beat grid      spectral-flux onsets -> tempo by autocorrelation -> beats by dynamic programming
  downbeats              the beat phase (every 4th beat) with the most low-end attack (kick drums)
  energy per beat        loudness between beats, 0-1 within the song: where the build and the drop are
  hits                   the strongest onsets: where zoom punches, flashes and shakes land
  mood                   folder name > Gemini listening to 30 s of it > a tempo/energy guess
Plain numpy, no audio library: the server stays light.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel

from common import ffmpeg
from common.paths import DATA_DIR

MUSIC_DIR = DATA_DIR / "anime" / "music"
INDEX = MUSIC_DIR / ".index.json"
USAGE = MUSIC_DIR / ".usage.json"
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"}
MOODS = ("hype", "epic", "sad", "chill", "funny")
Mood = Literal["hype", "epic", "sad", "chill", "funny"]
SR = 22050
HOP = 512
FPS = SR / HOP  # onset-envelope frames per second (~43)

# which moods suit which kind of edit (first = best)
KIND_MOODS: dict[str, tuple[str, ...]] = {
    "fight": ("hype", "epic"), "hype": ("hype", "epic"), "reveal": ("epic", "hype"),
    "emotional": ("sad", "chill", "epic"), "comedy": ("funny", "chill", "hype"), "dialogue": ("chill", "epic"),
}


class Track(BaseModel):
    id: str                       # file name relative to MUSIC_DIR (stable, readable)
    title: str
    duration: float
    bpm: float
    beats: list[float]            # seconds
    downbeats: list[float]
    energy: list[float]           # per beat interval, 0-1
    hits: list[tuple[float, float]]  # (time, strength 0-1), strongest onsets
    mood: Mood
    mood_source: Literal["folder", "gemini", "guess"]
    genre: str = ""
    size: int = 0
    mtime: float = 0.0


# ---------------------------------------------------------------- analysis

def decode(path: Path) -> np.ndarray:
    proc = subprocess.run([ffmpeg.require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path),
                           "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise ffmpeg.FFmpegError(f"Can't read {path.name}: {proc.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def onset_envelopes(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(full-band spectral flux, low-band flux (kicks), rms) per hop."""
    n_fft = 2048
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::HOP]
    win = np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames * win, axis=1))
    logm = np.log1p(100 * mag)
    flux = np.maximum(0, np.diff(logm, axis=0, prepend=logm[:1]))
    low_bins = int(150 / (SR / n_fft))
    full = flux.sum(axis=1)
    low = flux[:, 1:low_bins + 1].sum(axis=1)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return _norm(full), _norm(low), rms


def _norm(v: np.ndarray) -> np.ndarray:
    v = v - np.median(v)
    v = np.maximum(v, 0)
    p = np.percentile(v, 99) if len(v) else 0
    return v / p if p > 0 else v


def tempo(onset: np.ndarray, lo_bpm: float = 70, hi_bpm: float = 190) -> float:
    o = onset - onset.mean()
    ac = np.correlate(o, o, mode="full")[len(o) - 1:]
    lags = np.arange(len(ac))
    with np.errstate(divide="ignore"):
        bpm = 60 * FPS / np.where(lags > 0, lags, np.inf)
    ok = (bpm >= lo_bpm) & (bpm <= hi_bpm)
    prior = np.exp(-0.5 * (np.log2(np.where(ok, bpm, 1) / 120) / 0.9) ** 2)  # mild preference for ~120
    score = np.where(ok, ac * prior, -np.inf)
    lag = int(np.argmax(score))
    # parabolic refinement of the peak
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0
        lag_f = lag + float(np.clip(d, -0.5, 0.5))
    else:
        lag_f = float(lag)
    return 60 * FPS / lag_f


def track_beats(onset: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Dynamic-programming beat tracker (Ellis 2007): beats on strong onsets, spaced ~one period apart."""
    period = 60 * FPS / bpm
    n = len(onset)
    score = onset.astype(np.float64).copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    for t in range(lo, n):
        a, b = max(0, t - hi), t - lo
        if b <= a:
            continue
        prev = np.arange(a, b)
        trans = -tightness * np.log((t - prev) / period) ** 2
        cand = score[a:b] + trans
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = onset[t] + cand[k]
            back[t] = prev[k]
    # end on the best-scoring frame in the last period
    t = int(np.argmax(score[max(0, n - hi):])) + max(0, n - hi)
    beats = [t]
    while back[t] >= 0:
        t = int(back[t])
        beats.append(t)
    return np.array(beats[::-1]) / FPS


def analyse(path: Path, rel: str) -> Track:
    x = decode(path)
    full, low, rms = onset_envelopes(x)
    bpm = tempo(full)
    beats = track_beats(full, bpm)
    if len(beats) < 8:
        raise ValueError(f"{path.name}: no steady beat found")
    # downbeat phase: every 4th beat, the phase with the most kick-drum attack
    idx = np.clip((beats * FPS).astype(int), 0, len(low) - 1)
    phase = int(np.argmax([low[idx[k::4]].mean() for k in range(4)]))
    downbeats = beats[phase::4]
    # energy between consecutive beats
    e = []
    for a, b in zip(beats, beats[1:]):
        seg = rms[int(a * FPS): max(int(a * FPS) + 1, int(b * FPS))]
        e.append(float(seg.mean()) if len(seg) else 0.0)
    e = np.array(e)
    lo_e, hi_e = np.percentile(e, 5), np.percentile(e, 95)
    energy = np.clip((e - lo_e) / max(hi_e - lo_e, 1e-9), 0, 1)
    energy = np.convolve(energy, np.ones(4) / 4, mode="same")  # one-bar smoothing
    hits = strong_hits(full, low)
    mood, genre, src = guess_mood(bpm, energy), "", "guess"
    folder = Path(rel).parent.name.lower()
    if folder in MOODS:
        mood, src = folder, "folder"
    return Track(id=rel, title=re.sub(r"[_]+", " ", Path(rel).stem).strip(), duration=round(len(x) / SR, 2),
                 bpm=round(bpm, 1), beats=[round(float(b), 3) for b in beats],
                 downbeats=[round(float(b), 3) for b in downbeats], energy=[round(float(v), 3) for v in energy],
                 hits=hits, mood=mood, mood_source=src, genre=genre)


def strong_hits(full: np.ndarray, low: np.ndarray, top: float = 92) -> list[tuple[float, float]]:
    env = 0.5 * full + 0.5 * low
    thr = np.percentile(env, top)
    peaks = np.where((env > thr) & (env >= np.roll(env, 1)) & (env >= np.roll(env, -1)))[0]
    out: list[tuple[float, float]] = []
    mx = env.max() or 1.0
    for p in peaks:
        t = p / FPS
        if out and t - out[-1][0] < 0.15:
            continue
        out.append((round(float(t), 3), round(float(env[p] / mx), 3)))
    return out


def guess_mood(bpm: float, energy: np.ndarray) -> Mood:
    loud = float(np.mean(energy))
    if bpm >= 118 and loud >= 0.45:
        return "hype"
    if bpm >= 100 and loud >= 0.35:
        return "epic"
    if bpm < 90 and loud < 0.4:
        return "sad"
    return "chill"


def best_section(t: Track, min_s: float, max_s: float) -> tuple[float, float]:
    """The bar-aligned stretch of min_s-max_s seconds with the most energy and a clear rise (build -> drop)."""
    db = np.array(t.downbeats)
    beats = np.array(t.beats)
    energy = np.array(t.energy)
    if t.duration <= min_s or len(db) < 2:
        return 0.0, min(t.duration, max_s)

    def e_at(a: float, b: float) -> np.ndarray:
        i, j = np.searchsorted(beats, a), np.searchsorted(beats, b)
        return energy[i:max(i + 1, j)]

    best, best_ab = -1e9, (float(db[0]), float(min(db[0] + max_s, t.duration)))
    for a in db:
        if a > t.duration - min_s:
            break
        ends = db[(db >= a + min_s) & (db <= a + max_s)]
        if not len(ends):
            continue
        b = float(ends[-1])
        ev = e_at(a, b)
        if len(ev) < 8:
            continue
        third = max(1, len(ev) // 3)
        rise = float(ev[third:].max() - ev[:third].mean())          # something builds and lands
        hook = float(e_at(a, a + 4).mean())                          # a short needs energy in its first seconds
        score = float(ev.mean()) + 0.5 * max(rise, 0) + 0.5 * hook + 0.002 * (b - a)
        if score > best:
            best, best_ab = score, (float(a), b)
    return round(best_ab[0], 3), round(best_ab[1], 3)


# ---------------------------------------------------------------- library

def files() -> list[Path]:
    if not MUSIC_DIR.exists():
        return []
    return sorted(p for p in MUSIC_DIR.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."))


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def library(*, tag_with_gemini: bool = False, models: list[str] | None = None, report=None) -> list[Track]:
    """Every song in the folder, analysed (cached by size + mtime). New songs are analysed now."""
    report = report or (lambda m: None)
    idx = _load(INDEX)
    out: list[Track] = []
    changed = False
    for f in files():
        rel = f.relative_to(MUSIC_DIR).as_posix()
        st = f.stat()
        cached = idx.get(rel)
        if cached and cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime:
            out.append(Track.model_validate(cached))
            continue
        report(f"Analysing {f.name}…")
        try:
            t = analyse(f, rel)
        except Exception as e:  # noqa: BLE001  a broken file must not stop the library
            report(f"Skipping {f.name}: {e}")
            continue
        t.size, t.mtime = st.st_size, st.st_mtime
        if tag_with_gemini and t.mood_source == "guess":
            tag(t, f, models or [], report)
        idx[rel] = t.model_dump()
        out.append(t)
        changed = True
    for rel in list(idx):
        if not (MUSIC_DIR / rel).exists():
            idx.pop(rel)
            changed = True
    if changed:
        _save(INDEX, idx)
    return out


TAG_SCHEMA = {"type": "OBJECT", "properties": {
    "mood": {"type": "STRING", "enum": list(MOODS)}, "genre": {"type": "STRING"},
}, "required": ["mood"]}


def tag(t: Track, f: Path, models: list[str], report) -> None:
    """Gemini listens to 30 s from the song's best part and names its mood (once per song, cached)."""
    from common.llm import LLMError, generate_json

    a, _ = best_section(t, min(30, t.duration), min(30, t.duration))
    try:
        clip = subprocess.run([ffmpeg.require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{a:.2f}",
                               "-t", "30", "-i", str(f), "-vn", "-ac", "1", "-b:a", "48k", "-f", "mp3", "-"],
                              capture_output=True, check=True).stdout
        data, _ = generate_json(
            ["Listen to this music for an anime edit channel. Which ONE mood fits it best: "
             "hype (aggressive, fight scenes, phonk/trap/rock), epic (big, cinematic, power-ups), "
             "sad (emotional, piano, slow), chill (calm, lo-fi, everyday scenes), funny (bouncy, playful)? "
             "Also name its genre in 1-3 words.", ("audio/mp3", clip)],
            TAG_SCHEMA, models=models, cache_dir=MUSIC_DIR / ".llm_cache", temperature=0.2, max_retries=2,
            task="anime.music_mood",
            on_status=report)
        if data.get("mood") in MOODS:
            t.mood, t.mood_source = data["mood"], "gemini"
            t.genre = str(data.get("genre") or "")[:40]
    except (LLMError, subprocess.CalledProcessError, ffmpeg.FFmpegError) as e:
        report(f"Mood tagging skipped for {t.title} ({e}); using a guess")


def set_mood(track_id: str, mood: Mood) -> None:
    idx = _load(INDEX)
    if track_id in idx:
        idx[track_id].update(mood=mood, mood_source="folder")
        _save(INDEX, idx)


def usage() -> dict[str, int]:
    return {k: int(v) for k, v in _load(USAGE).items()}


def mark_used(track_id: str) -> None:
    u = _load(USAGE)
    u[track_id] = int(u.get(track_id, 0)) + 1
    u[f"_last:{track_id}"] = time.time()
    _save(USAGE, u)


def rank(tracks: list[Track], kind: str, min_s: float, avoid: set[str] | None = None) -> list[tuple[Track, float, str]]:
    """Songs ranked for one edit: mood fit, long enough, high energy, not overused, not already in this episode."""
    avoid = avoid or set()
    uses = usage()
    wanted = KIND_MOODS.get(kind, ("hype", "epic"))
    out = []
    for t in tracks:
        if t.duration < min_s * 0.9:
            continue
        mood_fit = 1.0 if t.mood == wanted[0] else 0.6 if t.mood in wanted else 0.15
        a, b = best_section(t, min_s, min_s + 20)
        i, j = np.searchsorted(t.beats, a), np.searchsorted(t.beats, b)
        e = float(np.mean(t.energy[i:j])) if j > i else 0.0
        fresh = 1.0 / (1 + uses.get(t.id, 0) * 0.5)
        score = 2.0 * mood_fit + 0.8 * e + 0.6 * fresh - (1.5 if t.id in avoid else 0.0)
        why = f"{t.mood} · {t.bpm:.0f} BPM" + (f" · used {uses[t.id]}×" if uses.get(t.id) else "")
        out.append((t, round(score, 3), why))
    out.sort(key=lambda r: -r[1])
    return out
