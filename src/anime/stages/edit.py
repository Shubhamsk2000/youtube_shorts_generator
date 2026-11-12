"""Stage 5: turn picks into a clean edit decision list (selection.json).

  - cut points move onto nearby shot changes, so no stray frame of the previous shot flashes by
  - a clip never starts or ends in the middle of a subtitle line
  - montage beats get a zoom punch on their loudest hit (edit.auto_punch)
Pure functions on top of the earlier stages' JSON; rendering reads only selection.json.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path

import numpy as np

from common.progress import Reporter

from ..config import Config, EditCfg
from ..models import Cue, EpisodeMeta, Pick, Segment, Selection, Signals, Subs, load_json, save_json
from .ingest import SUBS
from .signals import OUT as SIGNALS
from .signals import intensity
from .watch import OUT as WATCHED

OUT = "selection.json"
OVERRIDES = "overrides.json"  # {pick id: {"tweet": ..., "title": ...}} typed in the web UI
EPS = 0.03  # stay this far inside a shot so the neighbouring shot never leaks in
SLOWMO_MAX_S = 3.0
MIN_BEAT_S = 2.5


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, report: Reporter | None = None) -> tuple[Selection, str]:
    sel = load_json(Selection, job_dir / WATCHED)
    sig = load_json(Signals, job_dir / SIGNALS)
    subs = load_json(Subs, job_dir / SUBS)
    over = load_overrides(job_dir)
    if sel.style == "song":
        picks = song_edits(sel.picks, sig, meta.duration, cfg, over, report or (lambda fraction=None, message=None: None),
                           chosen=song_overrides(job_dir))
        out = sel.model_copy(update={"picks": picks})
        save_json(out, job_dir / OUT)
        return out, (f"{len(picks)} song edits · " + ", ".join(f"{p.song.title} ({p.song.bpm:.0f} BPM)" for p in picks
                                                               if p.song))
    taken: list[tuple[float, float]] = []  # beats already used by earlier montages: keeps edits different
    picks = [finalize(p, sig, subs.cues, meta.duration, cfg, taken).model_copy(update=over.get(p.id, {}))
             for p in sel.picks]
    picks = [p for p in picks if p.segments]  # nothing left outside the OP/ED
    out = sel.model_copy(update={"picks": picks})
    save_json(out, job_dir / OUT)
    fx = sum(len(s.effects) for p in picks for s in p.segments)
    return out, f"{len(picks)} edits · {sum(p.duration for p in picks):.0f} s of footage · {fx} effects"


def load_overrides(job_dir: Path) -> dict[str, dict]:
    f = job_dir / OVERRIDES
    if not f.exists():
        return {}
    data = json.loads(f.read_text())
    return {pid: {k: v for k, v in o.items() if k in ("tweet", "title")} for pid, o in data.items()}


def song_overrides(job_dir: Path) -> dict[str, str]:
    """{pick id: song id} chosen in the UI."""
    f = job_dir / OVERRIDES
    data = json.loads(f.read_text()) if f.exists() else {}
    return {pid: o["song"] for pid, o in data.items() if o.get("song")}


def save_override(job_dir: Path, pick_id: str, **fields) -> None:
    """tweet / title / song (a music library id; "" = back to automatic)."""
    f = job_dir / OVERRIDES
    data = json.loads(f.read_text()) if f.exists() else {}
    data[pick_id] = {**data.get(pick_id, {}), **{k: v for k, v in fields.items() if v is not None}}
    if data[pick_id].get("song") == "":
        data[pick_id].pop("song")
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(f)


class NoSongsError(RuntimeError):
    pass


def song_edits(picks: list[Pick], sig: Signals, duration: float, cfg: Config, over: dict[str, dict], report,
               chosen: dict[str, str] | None = None) -> list[Pick]:
    """Song style: a song per edit (yours if you picked one, else the best fit not yet used in this episode),
    its best section, and the shots laid on its beats."""
    from .. import music
    from ..models import SongRef
    from . import amv

    tracks = music.library(tag_with_gemini=cfg.music.tag_with_gemini,
                           models=[cfg.select.model, *cfg.select.fallback_models], report=lambda m: report(None, m))
    if not tracks:
        raise NoSongsError("The music library is empty: add songs on the Music page (or to data/anime/music/)")
    by_id = {t.id: t for t in tracks}
    chosen = chosen or {}
    used: set[str] = set()
    out = []
    for p in picks:
        mine = chosen.get(p.id)
        if mine in by_id:
            t, auto = by_id[mine], False
        else:
            ranked = music.rank(tracks, p.kind or "hype", cfg.music.min_s, avoid=used)
            t = ranked[0][0] if ranked else max(tracks, key=lambda x: x.duration)  # all too short: the longest
            auto = True
        used.add(t.id)
        a, b = music.best_section(t, cfg.music.min_s, cfg.music.max_s)
        segs = amv.build(p, t, a, b, sig, duration, cfg)
        if not segs:
            continue
        song = SongRef(id=t.id, title=t.title, start=a, end=b, bpm=t.bpm, mood=t.mood, auto=auto)
        out.append(p.model_copy(update={"segments": segs, "song": song, **over.get(p.id, {})}))
    return out


def finalize(p: Pick, sig: Signals, cues: list[Cue], duration: float, cfg: Config,
             taken: list[tuple[float, float]] | None = None) -> Pick:
    ec = cfg.edit
    segs = []
    for i, s in enumerate(p.segments):
        s = s.model_copy()
        if p.style == "clip":
            s.start, s.end = snap_clip(s.start, s.end, sig.cuts, cues, ec, cfg.select.max_clip_s)
        else:
            s.start, s.end = snap_beat(s.start, s.end, sig.cuts, ec)
        s.start, s.end = max(0.0, s.start), min(duration, s.end)
        s.start, s.end = avoid_excluded(s.start, s.end, sig.excluded)
        if s.end - s.start < 1.0:  # the whole segment was inside the OP/ED
            continue
        if not segs:
            s.transition_in = "cut"
        if p.style == "montage":
            if "slowmo" in s.effects and s.duration > SLOWMO_MAX_S:  # slow-mo doubles the length: keep it short
                mid = s.punch_at if s.punch_at is not None else (s.start + s.end) / 2
                s.start = max(s.start, min(mid - SLOWMO_MAX_S / 2, s.end - SLOWMO_MAX_S))
                s.end = s.start + SLOWMO_MAX_S
            add_punch(s, sig, ec)
        else:
            s.effects = [e for e in s.effects if e == "freeze_end"]
        if s.punch_at is not None and not s.start <= s.punch_at <= s.end:
            s.punch_at = None
        segs.append(s)
    if p.style == "montage" and segs:
        segs = fill_montage(segs, sig, duration, cfg, taken if taken is not None else [])
    if ec.end_freeze_s > 0 and segs and "freeze_end" not in segs[-1].effects:
        segs[-1].effects.append("freeze_end")
    return p.model_copy(update={"segments": segs})


# how long a transition overlaps its neighbours, in units of edit.transition_s (render.py uses the same)
OVERLAP = {"flash": 1.0, "zoom": 1.2, "whip": 0.8, "fade": 1.6, "glitch": 1.0}


def out_length(segs: list[Segment], transition_s: float = 0.25) -> float:
    """Seconds on screen: slow-mo plays twice as long, a freeze adds its hold, transitions overlap."""
    total = 0.0
    prev = None
    for i, s in enumerate(segs):
        length = s.duration / (0.5 if "slowmo" in s.effects else 1.0) + (0.6 if "freeze_end" in s.effects else 0.0)
        if i and s.transition_in in OVERLAP:
            total -= min(transition_s * OVERLAP[s.transition_in], prev / 2, length / 2)
        total += length
        prev = length
    return total


def fill_montage(segs: list[Segment], sig: Signals, duration: float, cfg: Config,
                 taken: list[tuple[float, float]], beat_s: float = 3.5) -> list[Segment]:
    """Guarantee a montage reaches select.montage_min_s.

    1. beats shorter than MIN_BEAT_S grow around their middle (staying clear of the OP/ED)
    2. still short: the episode's most intense moments that no montage uses yet become extra beats,
       in time order, before the last beat (Gemini's strongest beat often closes the edit)
    """
    target = cfg.select.montage_min_s
    out = []
    for s in segs:
        s = s.model_copy()
        if s.duration < MIN_BEAT_S:
            mid = (s.start + s.end) / 2
            s.start, s.end = avoid_excluded(max(0.0, mid - MIN_BEAT_S / 2), min(duration, mid + MIN_BEAT_S / 2),
                                            sig.excluded)
        out.append(s)
    taken.extend((s.start, s.end) for s in out)
    ts = cfg.edit.transition_s
    if out_length(out, ts) >= target:
        return out

    action, loud = intensity(sig)
    score = 0.6 * action + 0.4 * loud
    n = max(1, int(beat_s / sig.bin_s))
    smooth = np.convolve(score, np.ones(n) / n, mode="valid")
    extra: list[Segment] = []
    for i in np.argsort(-smooth):
        if out_length([*out[:-1], *extra, out[-1]] if len(out) > 1 else [*out, *extra], ts) >= target:
            break
        a = float(i) * sig.bin_s
        b = min(duration, a + beat_s)
        if any(max(0.0, min(b, y) - max(a, x)) > 0 for x, y, _ in sig.excluded):
            continue
        if any(min(b, y) + 1.0 > max(a, x) for x, y in taken):  # overlaps (or touches) a used beat
            continue
        if (c := _nearest_cut(a, sig.cuts, cfg.edit.snap_s)) is not None and b - c > 2.0:
            a = c + EPS  # open on a shot change
        seg = Segment(start=round(a, 3), end=round(b, 3), transition_in="flash")
        add_punch(seg, sig, cfg.edit)
        extra.append(seg)
        taken.append((a, b))
    if not extra:
        return out
    extra.sort(key=lambda s: s.start)
    return [*out[:-1], *extra, out[-1]] if len(out) > 1 else [*out, *extra]


def avoid_excluded(a: float, b: float, excluded: list[tuple[float, float, str]]) -> tuple[float, float]:
    """Earlier steps may stretch a scene (Gemini extending into padding, snapping): never into the OP/ED."""
    for x, y, _ in excluded:
        if a < x < b:      # runs into an excluded range: stop just before it
            b = x - EPS
        elif x <= a < y:   # starts inside one: begin right after it
            a = y + EPS
    return round(a, 3), round(b, 3)


def _nearest_cut(t: float, cuts: list[float], within: float, ok=lambda c: True) -> float | None:
    i = bisect.bisect_left(cuts, t)
    best = None
    for c in cuts[max(0, i - 3): i + 3]:
        if abs(c - t) <= within and ok(c) and (best is None or abs(c - t) < abs(best - t)):
            best = c
    return best


def _inside_line(t: float, cues: list[Cue], margin: float = 0.25) -> Cue | None:
    for c in cues:
        if c.start + margin < t < c.end - margin:
            return c
    return None


def snap_clip(a: float, b: float, cuts: list[float], cues: list[Cue], ec: EditCfg, max_s: float) -> tuple[float, float]:
    # never open or close mid-sentence
    if (c := _inside_line(a, cues)) and a - c.start <= 2.5:
        a = c.start
    if (c := _inside_line(b, cues)) and c.end - b <= 2.5 and c.end - a <= max_s:
        b = c.end
    # then land on a shot change, unless that would cut into a line
    if (c := _nearest_cut(a, cuts, ec.snap_s, lambda x: _inside_line(x, cues) is None)) is not None:
        a = c + EPS
    if (c := _nearest_cut(b, cuts, ec.snap_s, lambda x: _inside_line(x, cues) is None)) is not None:
        b = c - EPS
    return round(a, 3), round(b, 3)


def snap_beat(a: float, b: float, cuts: list[float], ec: EditCfg) -> tuple[float, float]:
    if (c := _nearest_cut(a, cuts, ec.snap_s)) is not None and b - c > 1.2:
        a = c + EPS
    if (c := _nearest_cut(b, cuts, ec.snap_s)) is not None and c - a > 1.2:
        b = c - EPS
    return round(a, 3), round(b, 3)


def add_punch(s: Segment, sig: Signals, ec: EditCfg) -> None:
    """Zoom punch on the loudest hit of a beat (if it really is a hit for this episode)."""
    loud = np.array(sig.loudness)
    i0, i1 = int(s.start / sig.bin_s), int(s.end / sig.bin_s)
    if i1 <= i0 or i0 >= len(loud):
        return
    w = loud[i0: min(i1, len(loud))]
    k = int(np.argmax(w))
    peak_t = (i0 + k + 0.5) * sig.bin_s
    if "zoom_punch" in s.effects and s.punch_at is None:
        s.punch_at = peak_t
    elif ec.auto_punch and "zoom_punch" not in s.effects and s.punch_at is None:
        if w[k] >= np.percentile(loud, 85) and s.start + 0.2 < peak_t < s.end - 0.2:
            s.punch_at = peak_t
            s.effects.append("zoom_punch")
    if s.punch_at is not None:
        s.punch_at = min(max(s.punch_at, s.start), s.end)
