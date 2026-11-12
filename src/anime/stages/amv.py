"""Song edits (AMV style): lay the episode's moments onto a song's beat grid.

  cut rate      follows the song: every beat on the drop, every 2 beats in the middle, a bar when calm
                (calm cuts wait for the bar line); see music.*_cut_beats
  what goes     moment intensity (Gemini, 1-5) matches song energy: 5s land on the drop, 1-2s on the intro
  inside a      moments are used shot by shot, moving forward (a cursor per moment): nothing repeats
  moment
  effects       zoom punch on the song's strongest hits, shake on the very hardest, a white flash where
                the drop lands and every 2 bars after it, slow-mo for dramatic shots in calm parts
Every shot starts exactly on a beat (out_start); render.py turns those into frame-exact cuts.
"""
from __future__ import annotations

import bisect

import numpy as np

from ..config import Config
from ..models import Pick, Segment, Signals
from ..music import Track
from .edit import EPS, avoid_excluded

DROP_E, MID_E = 0.7, 0.4    # energy thresholds (0-1 within the song)
SLOWMO = 0.5


def slots(t: Track, a: float, b: float, cfg: Config) -> list[tuple[float, float, float]]:
    """(start, end, energy) per shot, in seconds from the section start, on beats."""
    mc = cfg.music
    i0 = bisect.bisect_left(t.beats, a - 1e-3)
    i1 = bisect.bisect_right(t.beats, b + 1e-3) - 1
    down = set(round(x, 3) for x in t.downbeats)
    out = []
    i = i0
    while i < i1:
        e = t.energy[i] if i < len(t.energy) else 0.0
        step = mc.drop_cut_beats if e >= DROP_E else mc.mid_cut_beats if e >= MID_E else mc.calm_cut_beats
        step = max(step, int(np.ceil(mc.min_shot_s * t.bpm / 60 - 1e-6)))  # fast songs: never a blur of cuts
        j = min(i + step, i1)
        if step >= 4:  # calm: run to the next bar line, so long shots change with the music
            k = i + 1
            while k < i1 and round(t.beats[k], 3) not in down:
                k += 1
            j = min(k, i1) if k - i <= step else j
        out.append((round(t.beats[i] - a, 3), round(t.beats[j] - a, 3), float(e)))
        i = j
    return out


def top_up_pool(pool: list[Segment], sig: Signals, duration: float, need_s: float) -> list[Segment]:
    """Gemini's moments too few for the song: add the episode's most intense unused windows."""
    have = sum(s.duration for s in pool)
    if have >= need_s:
        return pool
    from .signals import intensity

    action, loud = intensity(sig)
    score = 0.6 * action + 0.4 * loud
    n = max(1, int(4.0 / sig.bin_s))
    smooth = np.convolve(score, np.ones(n) / n, mode="valid")
    extra = []
    for i in np.argsort(-smooth):
        if have >= need_s:
            break
        x, y = float(i) * sig.bin_s, min(duration, float(i) * sig.bin_s + 4.0)
        if any(max(0.0, min(y, q) - max(x, p)) > 0 for p, q, _ in sig.excluded):
            continue
        if any(min(y, s.end) + 0.5 > max(x, s.start) for s in [*pool, *extra]):
            continue
        extra.append(Segment(start=round(x, 3), end=round(y, 3), intensity=4 if smooth[i] > 0.7 else 3))
        have += y - x
    return [*pool, *extra]


def build(p: Pick, t: Track, a: float, b: float, sig: Signals, duration: float, cfg: Config) -> list[Segment]:
    sl = slots(t, a, b, cfg)
    if not sl:
        return []
    pool = []
    for s in p.segments:
        x, y = avoid_excluded(max(0.0, s.start), min(duration, s.end), sig.excluded)
        if y - x >= 0.8:
            pool.append(s.model_copy(update={"start": x, "end": y, "intensity": s.intensity or 3}))
    pool = top_up_pool(sorted(pool, key=lambda s: s.start), sig, duration, need_s=1.3 * (b - a))
    cursor = [s.start for s in pool]
    rr = {"high": 0, "mid": 0, "low": 0}  # round-robin position per tier: spread the shots across moments

    def tier(e: float) -> list[str]:
        return ["high", "mid", "low"] if e >= DROP_E else ["mid", "high", "low"] if e >= MID_E else ["low", "mid", "high"]

    def members(name: str) -> list[int]:
        return [k for k, s in enumerate(pool)
                if (name == "high" and s.intensity >= 4) or (name == "mid" and s.intensity == 3)
                or (name == "low" and s.intensity <= 2)]

    tiers = {name: members(name) for name in rr}
    # where the drop lands: the biggest energy jump between consecutive shots (or the start, if it opens loud)
    jumps = [sl[k][2] - sl[k - 1][2] for k in range(1, len(sl))]
    drop_k = (int(np.argmax(jumps)) + 1) if jumps and max(jumps) >= 0.3 else (0 if sl[0][2] >= DROP_E else None)
    last_punch = -9
    hits = t.hits
    hit_times = [h for h, _ in hits]
    strong = sorted(s for _, s in hits)
    punch_thr = strong[int(len(strong) * 0.8)] if strong else 1.0
    shake_thr = strong[int(len(strong) * 0.95)] if strong else 1.0
    bar = 4 * (60.0 / t.bpm)

    out: list[Segment] = []
    for k, (s0, s1, e) in enumerate(sl):
        d = s1 - s0
        chosen = None
        for name in tier(e):
            ids = tiers[name]
            for step in range(len(ids)):
                m = ids[(rr[name] + step) % len(ids)]
                speed = SLOWMO if (e < MID_E and d >= 1.6 and pool[m].intensity >= 4) else 1.0
                if pool[m].end - cursor[m] >= d * speed:
                    chosen, rr[name] = (m, speed), (rr[name] + step + 1) % len(ids)
                    break
            if chosen:
                break
        if not chosen:  # every moment used up: start the pool over half a shot in (rare: long song, few moments)
            cursor = [s.start + min(0.5, s.duration / 4) for s in pool]
            m = max(range(len(pool)), key=lambda q: pool[q].end - cursor[q])
            chosen = (m, 1.0)
        m, speed = chosen
        need = d * speed
        src_a = cursor[m]
        for _ in range(4):  # land inside ONE source shot, so the viewer only sees cuts on the beat
            c = bisect.bisect_right(sig.cuts, src_a + 0.12)
            if c < len(sig.cuts) and sig.cuts[c] < src_a + need - 0.12 and sig.cuts[c] + EPS + need <= pool[m].end:
                src_a = sig.cuts[c] + EPS
            else:
                break
        src_b = src_a + need
        nxt = bisect.bisect_left(sig.cuts, src_b)  # continue the moment from the next shot change
        cursor[m] = sig.cuts[nxt] + EPS if nxt < len(sig.cuts) and sig.cuts[nxt] < pool[m].end else src_b + 0.05
        effects: list[str] = []
        if speed != 1.0:
            effects.append("slowmo")
        punch_at = None
        lo, hi = bisect.bisect_left(hit_times, a + s0 + 0.05), bisect.bisect_right(hit_times, a + s1 - 0.08)
        if hi > lo and e >= 0.5 and k - last_punch >= 2:  # never two punched shots in a row
            h, strength = max(hits[lo:hi], key=lambda x: x[1])
            if strength >= punch_thr:
                last_punch = k
                effects.append("zoom_punch")
                punch_at = round(src_a + (h - a - s0) * speed, 3)
                if strength >= shake_thr and e >= DROP_E:
                    effects.append("shake")
        on_bar2 = drop_k is not None and k > drop_k and e >= DROP_E and min(
            ((s0 - sl[drop_k][0]) / (2 * bar)) % 1, 1 - ((s0 - sl[drop_k][0]) / (2 * bar)) % 1) < 0.03
        if (k == drop_k and k > 0) or on_bar2:
            effects.append("flash_in")
        out.append(Segment(start=round(src_a, 3), end=round(src_b, 3), out_start=s0, intensity=pool[m].intensity,
                           effects=effects, punch_at=punch_at))
    return out
