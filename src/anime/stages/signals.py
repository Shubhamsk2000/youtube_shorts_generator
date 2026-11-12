"""Stage 2: how intense is every moment of the episode?

One pass over the audio (loudness) and one over tiny grey frames (motion + shot changes):
  loudness  dB per bin: screams, impacts, music swells
  motion    average pixel change per bin, cuts excluded: animation intensity (sakuga, fights)
  cuts      shot changes, so edits land on clean boundaries
OP / ED / next-episode preview are excluded: from chapter names, from song-lyric subtitle styles,
or (no chapters, no lyrics) from a ~90 s stretch near the start/end with steady music and no dialogue.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from common import ffmpeg
from common.progress import Reporter

from ..config import Config, SignalsCfg
from ..models import EpisodeMeta, Signals, Subs, load_json, save_json
from .ingest import SUBS

OUT = "signals.json"
BIN_S = 0.5
AUDIO_SR = 8000
CHAPTER_RE = re.compile(r"\b(op|opening|intro|ed|ending|outro|credits?|preview|next|eyecatch)\b", re.I)


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, force: bool = False,
        report: Reporter | None = None) -> tuple[Signals, str]:
    report = report or (lambda fraction=None, message=None: None)
    out = job_dir / OUT
    if out.exists() and not force:
        s = load_json(Signals, out)
        if cfg.signals.skip_op_ed:  # cheap: re-check the OP/ED so detection fixes reach old episodes too
            new = exclusions(Path(meta.source), meta, s, load_json(Subs, job_dir / SUBS), cfg.signals)
            if new != s.excluded:
                s.excluded = new
                save_json(s, out)
        return s, f"{len(s.cuts)} shots · {_excl_text(s)} · cached"

    sc = cfg.signals
    t0 = time.monotonic()
    report(0.0, "Measuring loudness…")
    loud = loudness(Path(meta.source), meta, BIN_S)
    diffs, fps = frame_diffs(Path(meta.source), meta, sc, lambda f, m: report(0.05 + 0.95 * f, m))
    cuts = detect_cuts(diffs, fps, sc)
    motion = motion_bins(diffs, fps, cuts, BIN_S, len(loud))
    subs = load_json(Subs, job_dir / SUBS)
    sig = Signals(bin_s=BIN_S, loudness=[round(x, 1) for x in loud], motion=[round(x, 4) for x in motion],
                  cuts=[round(c, 3) for c in cuts])
    sig.excluded = exclusions(Path(meta.source), meta, sig, subs, sc) if sc.skip_op_ed else []
    save_json(sig, out)
    el = time.monotonic() - t0
    return sig, (f"{len(cuts)} shots · {_excl_text(sig)} · {el / 60:.1f} min "
                 f"({meta.duration / max(el, 1e-6):.1f}× realtime)")


def _excl_text(s: Signals) -> str:
    if not s.excluded:
        return "no OP/ED found"
    return "skipping " + ", ".join(f"{why} {_mmss(a)}–{_mmss(b)}" for a, b, why in s.excluded)


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ---------------------------------------------------------------- audio

def loudness(src: Path, meta: EpisodeMeta, bin_s: float) -> list[float]:
    proc = subprocess.run(
        [ffmpeg.require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-map", f"0:a:{meta.audio_index}", "-ac", "1", "-ar", str(AUDIO_SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ffmpeg.FFmpegError(proc.stderr.decode(errors="replace")[-1500:])
    x = np.frombuffer(proc.stdout, dtype=np.float32)
    n = int(bin_s * AUDIO_SR)
    bins = max(1, len(x) // n)
    rms = np.sqrt(np.mean(x[: bins * n].reshape(bins, n).astype(np.float64) ** 2, axis=1) + 1e-12)
    return (20 * np.log10(rms + 1e-9)).clip(-90, 0).tolist()


# ---------------------------------------------------------------- video

def frame_diffs(src: Path, meta: EpisodeMeta, sc: SignalsCfg, report) -> tuple[np.ndarray, float]:
    """Mean absolute change between consecutive frames (0-1), on tiny grey frames."""
    w = sc.analysis_width
    h = max(2, round(w * meta.height / meta.width / 2) * 2)
    num, _, den = meta.fps.partition("/")
    fps = float(num) / float(den or 1)
    total = max(1, int(meta.duration * fps))
    proc = subprocess.Popen(
        [ffmpeg.require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-threads", "0", "-i", str(src),
         "-map", "0:v:0", "-an", "-sn", "-vf", f"scale={w}:{h}:flags=area,format=gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    size = w * h
    chunk = 240
    out: list[np.ndarray] = []
    prev = None
    done = 0
    t0 = last = time.monotonic()
    try:
        while True:
            buf = proc.stdout.read(size * chunk)
            if not buf:
                break
            k = len(buf) // size
            frames = np.frombuffer(buf[: k * size], dtype=np.uint8).reshape(k, h, w).astype(np.int16)
            if prev is not None:
                frames = np.concatenate([prev[None], frames])
            d = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)) / 255.0
            out.append(d if prev is not None else np.concatenate([[0.0], d]))
            prev = frames[-1]
            done += k
            now = time.monotonic()
            if now - last > 1.0:
                last = now
                frac = min(done / total, 0.999)
                el = now - t0
                eta = f" · ~{(el / frac - el) / 60:.1f} min left" if frac > 0.03 else ""
                report(frac, f"Scanning shots and motion {_mmss(done / fps)} / {_mmss(meta.duration)}{eta}")
        err = proc.stderr.read().decode(errors="replace")
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise ffmpeg.FFmpegError(err[-1500:])
    return (np.concatenate(out) if out else np.zeros(1)), fps


def detect_cuts(d: np.ndarray, fps: float, sc: SignalsCfg) -> list[float]:
    """A cut = a frame change that is big in absolute terms AND a spike against its neighbours."""
    if len(d) < 3:
        return []
    r = 6
    pad = np.pad(d, r, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * r + 1)
    neigh = (win.sum(axis=1) - d) / (2 * r)  # local mean without the frame itself
    cand = np.where((d > sc.cut_threshold) & (d > sc.cut_ratio * (neigh + 0.004)))[0]
    cuts: list[float] = []
    min_gap = sc.min_shot_s * fps
    for i in cand:
        if cuts and i - cuts[-1] < min_gap:
            if d[i] > d[cuts[-1]]:
                cuts[-1] = i
            continue
        cuts.append(int(i))
    return [i / fps for i in cuts]


def motion_bins(d: np.ndarray, fps: float, cuts: list[float], bin_s: float, n_bins: int) -> list[float]:
    m = d.copy()
    for c in cuts:  # a cut is not motion
        i = int(round(c * fps))
        m[max(0, i - 1): i + 2] = 0
    per = bin_s * fps
    idx = (np.arange(len(m)) / per).astype(int)
    n = max(n_bins, idx.max() + 1 if len(idx) else 1)
    sums = np.bincount(idx, weights=m, minlength=n)
    cnt = np.bincount(idx, minlength=n).clip(min=1)
    return (sums / cnt)[:n_bins].tolist()


# ---------------------------------------------------------------- OP / ED / preview

def exclusions(src: Path, meta: EpisodeMeta, sig: Signals, subs: Subs, sc: SignalsCfg) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for ch in chapters(src):
        title = ch["title"]
        if CHAPTER_RE.search(title) and ch["end"] - ch["start"] < 200:
            out.append((ch["start"], ch["end"], title.strip().lower()))
    for a, b in subs.song_ranges:
        if not any(_overlap((a, b), (x, y)) > 0 for x, y, _ in out):
            out.append((a, b, "opening" if a < meta.duration / 2 else "ending"))
    out += chapter_layout(chapters(src), meta.duration, out)
    have_op = any(a < meta.duration * 0.4 for a, _, _ in out)
    have_ed = any(a >= meta.duration * 0.6 for a, _, _ in out)
    if not have_op:
        g = _guess_song(sig, subs, 0, min(meta.duration * 0.4, 480), sc.op_ed_len_s)
        if g:
            out.append((*g, "opening (guess)"))
    if not have_ed:
        g = _guess_song(sig, subs, max(meta.duration * 0.6, meta.duration - 420), meta.duration, sc.op_ed_len_s)
        if g:
            out.append((*g, "ending (guess)"))
    return sorted((round(a, 2), round(b, 2), why) for a, b, why in out)


def chapter_layout(chs: list[dict], duration: float, found: list) -> list[tuple[float, float, str]]:
    """Generic chapter names ("Chapter 02"): an anime episode's layout still shows in the lengths.
    An 75-100 s chapter early = the opening, one late = the ending, a short last chapter after it = the preview."""
    out: list[tuple[float, float, str]] = []
    have_op = any(a < duration * 0.4 for a, _, _ in found)
    have_ed = any(a >= duration * 0.6 for a, _, _ in found)
    ed_end = None
    songlike = [c for c in chs if not CHAPTER_RE.search(c["title"]) and 75 <= c["end"] - c["start"] <= 100]
    closest = lambda cs: min(cs, key=lambda c: abs(c["end"] - c["start"] - 90), default=None)  # OP/ED run ~90 s
    op = closest([c for c in songlike if c["start"] < duration * 0.4])
    ed = closest([c for c in songlike if c["start"] > duration * 0.6])
    if op and not have_op:
        out.append((op["start"], op["end"], "opening"))
    if ed and not have_ed:
        out.append((ed["start"], ed["end"], "ending"))
        ed_end = ed["end"]
    if ed_end is not None and chs:
        last = chs[-1]
        if last["start"] >= ed_end - 1 and last["end"] - last["start"] < 75:
            out.append((last["start"], last["end"], "next-episode preview"))
    return out


def chapters(src: Path) -> list[dict]:
    proc = subprocess.run([ffmpeg.require("ffprobe"), "-v", "error", "-print_format", "json", "-show_chapters",
                           str(src)], capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    out = []
    for c in json.loads(proc.stdout or "{}").get("chapters", []):
        out.append({"start": float(c["start_time"]), "end": float(c["end_time"]),
                    "title": (c.get("tags") or {}).get("title", "")})
    return out


def _guess_song(sig: Signals, subs: Subs, lo: float, hi: float, length: float) -> tuple[float, float] | None:
    """The ~90 s window in [lo, hi] with steady loud music, lots of cuts and (almost) no dialogue."""
    loud = np.array(sig.loudness)
    b = sig.bin_s
    n = int(length / b)
    a0, a1 = int(lo / b), int(hi / b) - n
    if a1 <= a0 or len(loud) < n:
        return None
    talk = np.zeros(len(loud))
    for c in subs.cues:
        talk[int(c.start / b): int(c.end / b) + 1] = 1
    cut_bins = np.zeros(len(loud))
    for c in sig.cuts:
        if int(c / b) < len(cut_bins):
            cut_bins[int(c / b)] += 1
    med_loud = float(np.median(loud))
    best, best_i = 0.0, None
    for i in range(a0, a1 + 1, 2):
        w = slice(i, i + n)
        lw = loud[w]
        talk_cov = talk[w].mean()
        steady = lw.std() < 6 and lw.mean() > med_loud and (lw < -45).mean() < 0.05
        cut_rate = cut_bins[w].sum() / length
        if not steady or talk_cov > 0.1:
            continue
        score = cut_rate * (1 - talk_cov)
        if score > best:
            best, best_i = score, i
    episode_rate = len(sig.cuts) / max(len(loud) * b, 1)
    if best_i is None or best < episode_rate:
        return None
    return best_i * b, (best_i + n) * b


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


# ---------------------------------------------------------------- helpers for later stages

def intensity(sig: Signals) -> tuple[np.ndarray, np.ndarray]:
    """(action, loud) per bin, each 0-1 by rank within the episode (robust to mastering levels)."""
    def rank01(x: np.ndarray) -> np.ndarray:
        if len(x) < 2:
            return np.zeros(len(x))
        return np.argsort(np.argsort(x)) / (len(x) - 1)

    loud = np.array(sig.loudness)
    motion = np.array(sig.motion)
    cut_bins = np.zeros(len(loud))
    for c in sig.cuts:
        i = int(c / sig.bin_s)
        if i < len(cut_bins):
            cut_bins[i] += 1
    k = max(1, int(2 / sig.bin_s))  # cut rate over ~2 s
    cut_rate = np.convolve(cut_bins, np.ones(k) / k, mode="same")
    action = 0.6 * rank01(motion) + 0.4 * rank01(cut_rate)
    return action, rank01(loud)


def is_excluded(sig: Signals, a: float, b: float) -> bool:
    return any(_overlap((a, b), (x, y)) > 1.0 for x, y, _ in sig.excluded)
