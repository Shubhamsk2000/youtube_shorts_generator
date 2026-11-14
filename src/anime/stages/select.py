"""Stage 3: find the moments worth a short.

Gemini reads the whole episode at once: every subtitle line plus an intensity timeline (action and
loudness every 5 s, from the signals stage), so fights with little dialogue are visible too.
  clip style     one continuous scene per short (a line that hits, a reveal, a breakdown, a fight beat)
  montage style  4-8 short beats from across the episode cut together into one hype edit
Without Gemini (no key / offline) the most intense stretches are picked by the signals alone.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from common.progress import Reporter

from ..config import Config, Style
from ..models import EpisodeMeta, Pick, Segment, Selection, Signals, Subs, load_json, save_json
from .ingest import SUBS
from .signals import OUT as SIGNALS
from .signals import intensity, is_excluded

OUT = "candidates.json"
LLM_CACHE = "llm_cache"
TRANSITIONS = ["cut", "flash", "zoom", "whip", "fade", "glitch"]
EFFECTS = ["zoom_punch", "shake", "slowmo", "freeze_end"]

SYSTEM = """You are the editor of a fast-growing anime shorts channel (YouTube Shorts / TikTok / Reels).
You know what gets replayed and shared: hype fight beats and power-ups, iconic one-liners, brutal
comebacks, emotional breakdowns, plot twists and reveals, funny reactions, and scenes so well animated
people rewatch them. A short must work for a viewer who has NOT seen the episode: it needs an instant
hook in the first 2 seconds and a clear payoff. Never pick the opening/ending song or the next-episode preview."""

COMMON = """Episode file: "{title}" ({duration}). Work out the anime's name from the file name / dialogue if you can.

INTENSITY TIMELINE (every 5 s: a = animation/action intensity 0-9, l = loudness 0-9; 9 = the episode's peak).
High a+l with little dialogue usually means a fight or a big animated moment.
{excluded}
{timeline}

SUBTITLES ([mm:ss] line):
{subs}
"""

CLIP_TASK = """TASK: pick the {n} best moments for a single-scene short each.
- One continuous stretch that tells a COMPLETE mini-story: setup, build-up, payoff. Follow the script: include the
  whole exchange or the whole fight beat, don't stop mid-conversation.
- Length: aim for about {target_s:.0f} s when the scene supports it (most good scenes do); a moment that is
  naturally short may be {min_s:.0f}-40 s; never longer than {max_s:.0f} s; never padded with filler.
- Start right before the setup line/action (no dead air), end right after the payoff/reaction.
- Ranked by viral potential; no two picks may overlap.
- start/end as "mm:ss" taken from the subtitle/timeline times.
- "tweet": the hook shown above the video as a tweet: max 90 characters, no emojis, no hashtags, casual
  internet voice (e.g. "nobody was ready for what he said next", "this is why he's the strongest").
  It must make people want to watch without spoiling the payoff.
- "title": an upload title (max 70 chars, may include the anime name); "description": 1-2 sentences;
  "hashtags": 5 tags without '#', include the anime name; "kind": fight|emotional|comedy|reveal|hype|dialogue;
  "score": 0-10 viral potential; "why": one sentence."""

MONTAGE_TASK = """TASK: design {n} hype edits. Each edit cuts together 8-14 beats from anywhere in this episode
into one short of {min_s:.0f}-{max_s:.0f} s IN TOTAL (add up your beats: under {min_s:.0f} s is rejected) with one theme
(a character's best moments, the fight's biggest hits, the emotional arc...).
- Each beat 2.5-6 s, "start"/"end" as "mm:ss"; beats are played in the order you list them; no two beats overlap.
- Use the intensity timeline to find visual peaks; the strongest beat goes first or last.
- "transition_in" per beat (how it starts): cut | flash | zoom | whip | fade | glitch. Mostly cuts and flashes on
  hits; fade only for emotional beats.
- "effects" per beat (optional): zoom_punch (on an impact), shake (explosions/hits), slowmo (a key frame of animation),
  freeze_end (only on the last beat).
- "tweet": the hook shown above the video as a tweet: max 90 characters, no emojis, no hashtags, casual internet voice.
- "title" (max 70 chars), "description", "hashtags" (5, without '#', include the anime name),
  "kind": fight|emotional|comedy|hype, "score": 0-10 viral potential, "why"."""

SONG_TASK = """TASK: plan {n} AMV-style edits that will be cut to the beat of a song (the song is chosen later; the
episode's own audio is replaced by it). Each edit has one theme (a character's best moments, the fight, the
emotional arc, the hype of one scene...) and a POOL of 12-20 moments from anywhere in this episode to cut from.
- Each moment 2-10 s, "start"/"end" as "mm:ss", no two overlapping; visually strong moments win (action, a
  transformation, a stare-down, a big reaction, a striking shot); dialogue does not matter, it will not be heard.
- "intensity" 1-5 per moment: 5 = the biggest hits and most explosive shots (they land on the song's drop),
  1 = calm, atmospheric shots (they fit the intro and quiet parts). Use the whole range.
- "kind" per edit: fight|emotional|comedy|hype|reveal (it picks the song's mood).
- "tweet": the hook shown above the video as a tweet: max 90 characters, no emojis, no hashtags, casual internet voice.
- "title" (max 70 chars), "description", "hashtags" (5, without '#', include the anime name), "score": 0-10, "why"."""

_META_PROPS = {
    "title": {"type": "STRING"}, "tweet": {"type": "STRING"}, "description": {"type": "STRING"},
    "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}, "kind": {"type": "STRING"},
    "score": {"type": "NUMBER"}, "why": {"type": "STRING"},
}
CLIP_SCHEMA = {
    "type": "OBJECT",
    "properties": {"picks": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"start": {"type": "STRING"}, "end": {"type": "STRING"}, **_META_PROPS},
        "required": ["start", "end", "title", "tweet", "score"],
    }}},
    "required": ["picks"],
}
MONTAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"picks": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "beats": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "STRING"}, "end": {"type": "STRING"},
                    "transition_in": {"type": "STRING", "enum": TRANSITIONS},
                    "effects": {"type": "ARRAY", "items": {"type": "STRING", "enum": EFFECTS}},
                },
                "required": ["start", "end"],
            }},
            **_META_PROPS,
        },
        "required": ["beats", "title", "tweet", "score"],
    }}},
    "required": ["picks"],
}
SONG_SCHEMA = {
    "type": "OBJECT",
    "properties": {"picks": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "beats": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"start": {"type": "STRING"}, "end": {"type": "STRING"},
                               "intensity": {"type": "INTEGER"}},
                "required": ["start", "end", "intensity"],
            }},
            **_META_PROPS,
        },
        "required": ["beats", "title", "tweet", "score", "kind"],
    }}},
    "required": ["picks"],
}
TASKS = {"clip": (CLIP_TASK, CLIP_SCHEMA), "montage": (MONTAGE_TASK, MONTAGE_SCHEMA), "song": (SONG_TASK, SONG_SCHEMA)}


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, style: Style | None = None, force: bool = False,
        report: Reporter | None = None) -> tuple[Selection, str]:
    report = report or (lambda fraction=None, message=None: None)
    style = style or cfg.style
    out = job_dir / OUT
    if out.exists() and not force:
        sel = load_json(Selection, out)
        if sel.style == style:
            return sel, f"{len(sel.picks)} {style} picks ({sel.source}) · cached"

    subs = load_json(Subs, job_dir / SUBS)
    sig = load_json(Signals, job_dir / SIGNALS)
    try:
        sel = _gemini(job_dir, meta, subs, sig, cfg, style, report, refresh=force)
    except Exception as e:  # noqa: BLE001
        from common.llm import LLMError

        if not (cfg.select.offline_fallback and isinstance(e, LLMError)):
            raise
        report(None, f"Gemini unavailable ({e}); picking by action and loudness only")
        sel = heuristic(meta, sig, cfg, style)
        sel.note = f"Gemini unavailable ({e}): picked by intensity only, titles are placeholders"
    save_json(sel, out)
    return sel, f"{len(sel.picks)} {style} picks from {sel.source}" + (f" ({sel.model})" if sel.model else "")


# ---------------------------------------------------------------- Gemini

def _gemini(job_dir: Path, meta: EpisodeMeta, subs: Subs, sig: Signals, cfg: Config, style: Style,
            report: Reporter, refresh: bool) -> Selection:
    from common.llm import generate_json

    sc = cfg.select
    n = sc.count + 3  # spares: the watch pass may drop some
    prompt = COMMON.format(
        title=Path(meta.source).name, duration=_mmss(meta.duration),
        excluded=("EXCLUDED (opening/ending/preview, never pick from here): "
                  + ", ".join(f"{_mmss(a)}-{_mmss(b)}" for a, b, _ in sig.excluded)) if sig.excluded else "",
        timeline=timeline_text(sig), subs=subs_text(subs),
    ) + "\n" + TASKS[style][0].format(
        n=n,
        target_s=sc.target_clip_s,
        min_s=sc.min_clip_s if style == "clip" else sc.montage_min_s,
        max_s=sc.max_clip_s if style == "clip" else sc.montage_max_s,
    )
    if not subs.cues:
        prompt += "\n(No subtitles were available: judge by the intensity timeline only.)"
    report(None, f"Asking Gemini for {style} moments…")
    data, model = generate_json(
        prompt, TASKS[style][1],
        models=[sc.model, *sc.fallback_models], cache_dir=job_dir / LLM_CACHE, system=SYSTEM, refresh=refresh,
        temperature=sc.temperature, timeout_s=sc.timeout_s, max_retries=sc.max_retries,
        min_interval_s=sc.min_interval_s, task="anime.select", on_status=lambda m: report(None, m),
    )
    picks = validate(data.get("picks") or [], style, meta, sig, cfg)
    return Selection(source="gemini", model=model, style=style, picks=picks[: n])


def timeline_text(sig: Signals, step_s: float = 5.0) -> str:
    action, loud = intensity(sig)
    k = max(1, int(step_s / sig.bin_s))
    rows = []
    for i in range(0, len(action), k):
        a = int(min(9, action[i: i + k].max() * 10))
        lv = int(min(9, loud[i: i + k].max() * 10))
        rows.append(f"{_mmss(i * sig.bin_s)} a{a} l{lv}")
    return " | ".join(rows)


def subs_text(subs: Subs) -> str:
    return "\n".join(f"[{_mmss(c.start)}] {c.text.replace(chr(10), ' / ')}" for c in subs.cues)


def validate(raw: list[dict], style: Style, meta: EpisodeMeta, sig: Signals, cfg: Config) -> list[Pick]:
    sc = cfg.select
    picks: list[Pick] = []
    for r in raw:
        if style == "clip":
            beats = [{"start": r.get("start"), "end": r.get("end")}]
        else:
            beats = r.get("beats") or []
        segs: list[Segment] = []
        for i, b in enumerate(beats):
            a, e = parse_time(b.get("start")), parse_time(b.get("end"))
            if a is None or e is None:
                continue
            a, e = max(0.0, a), min(meta.duration, e)
            if e - a < 1.0 or is_excluded(sig, a, e):
                continue
            segs.append(Segment(
                start=a, end=e,
                transition_in=b.get("transition_in") if b.get("transition_in") in TRANSITIONS else "cut",
                effects=[x for x in dict.fromkeys(b.get("effects") or []) if x in EFFECTS],
                intensity=int(min(5, max(1, b["intensity"]))) if isinstance(b.get("intensity"), (int, float)) else None,
            ))
        if not segs:
            continue
        if style == "clip":
            s = segs[0]
            if s.duration < sc.min_clip_s * 0.6:
                continue
            s.end = min(s.end, s.start + sc.max_clip_s)
        elif len(segs) < (4 if style == "song" else 2):
            continue
        picks.append(_pick(style, r, segs))
    picks.sort(key=lambda p: -p.score)
    kept: list[Pick] = []
    for p in picks:  # no two shorts showing the same footage
        if all(_shared(p, q) < 2.0 for q in kept):
            kept.append(p)
    for i, p in enumerate(kept, 1):
        p.rank = i
    return kept


def _pick(style: Style, r: dict, segs: list[Segment]) -> Pick:
    key = "|".join(f"{s.start:.1f}-{s.end:.1f}" for s in segs)
    return Pick(
        id=f"{style[0]}{hashlib.sha1(key.encode()).hexdigest()[:6]}", rank=0, style=style,
        score=float(r.get("score") or 0), kind=str(r.get("kind") or ""),
        title=str(r.get("title") or "").strip()[:100], tweet=clean_tweet(str(r.get("tweet") or "")),
        description=str(r.get("description") or "").strip(),
        hashtags=[h.strip().lstrip("#").replace(" ", "") for h in r.get("hashtags") or [] if h.strip()][:8],
        why=str(r.get("why") or "").strip(), segments=segs,
    )


def clean_tweet(t: str) -> str:
    """No emoji or CJK (the card font has neither) and no hashtags on the card."""
    t = "".join(ch for ch in t if ord(ch) < 0x2190)
    t = re.sub(r"(^|\s)#\w+", "", t)
    return re.sub(r"\s+", " ", t).strip()[:140]


def _shared(p: Pick, q: Pick) -> float:
    return sum(max(0.0, min(a.end, b.end) - max(a.start, b.start)) for a in p.segments for b in q.segments)


def parse_time(v) -> float | None:
    """'mm:ss', 'mm:ss.s', 'h:mm:ss' or seconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        parts = [float(x) for x in s.split(":")]
    except ValueError:
        return None
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def _mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


# ---------------------------------------------------------------- no-LLM fallback

def heuristic(meta: EpisodeMeta, sig: Signals, cfg: Config, style: Style) -> Selection:
    sc = cfg.select
    action, loud = intensity(sig)
    score = 0.6 * action + 0.4 * loud
    for a, b, _ in sig.excluded:
        score[int(a / sig.bin_s): int(b / sig.bin_s) + 1] = -1
    b = sig.bin_s
    picks: list[Pick] = []
    if style == "clip":
        n = max(1, int(min(sc.target_clip_s, sc.max_clip_s) / b))
        smooth = np.convolve(score, np.ones(n) / n, mode="valid")
        taken = np.zeros(len(score), bool)
        for i in np.argsort(-smooth):
            if len(picks) >= sc.count or smooth[i] < 0:
                break
            if taken[i: i + n].any():
                continue
            taken[i: i + n] = True
            seg = Segment(start=i * b, end=min(meta.duration, (i + n) * b))
            picks.append(_pick(style, {"title": f"Moment at {_mmss(seg.start)}", "tweet": "wait for it",
                                       "score": round(float(smooth[i]) * 10, 1), "kind": "hype"}, [seg]))
    elif style == "song":
        win = max(1, int(4.0 / b))
        smooth = np.convolve(score, np.ones(win) / win, mode="valid")
        order = [int(i) for i in np.argsort(-smooth) if smooth[i] > 0]
        used: list[int] = []
        for i in order:
            if all(abs(i - j) > win for j in used):
                used.append(i)
            if len(used) >= 16 * sc.count:
                break
        ranks = {i: r for r, i in enumerate(sorted(used, key=lambda i: -smooth[i]))}
        for k in range(sc.count):
            group = sorted(used[k::sc.count])[:16]
            if len(group) < 6:
                break
            segs = [Segment(start=i * b, end=(i + win) * b, intensity=5 - min(4, ranks[i] * 5 // max(1, len(used))))
                    for i in group]
            picks.append(_pick(style, {"title": f"Episode edit {k + 1}", "tweet": "this episode went crazy",
                                       "score": 5, "kind": "hype"}, segs))
    else:
        beat = max(1, int(3.5 / b))
        per_edit = max(6, int(np.ceil(sc.montage_min_s / 3.5)))
        smooth = np.convolve(score, np.ones(beat) / beat, mode="valid")
        order = [i for i in np.argsort(-smooth) if smooth[i] > 0]
        used: list[int] = []
        for i in order:
            if all(abs(i - j) > beat * 2 for j in used):
                used.append(int(i))
            if len(used) >= per_edit * sc.count:
                break
        for k in range(sc.count):
            group = sorted(used[k::sc.count])[:per_edit]
            if len(group) < 3:
                break
            segs = [Segment(start=i * b, end=(i + beat) * b, transition_in="flash" if j else "cut")
                    for j, i in enumerate(group)]
            picks.append(_pick(style, {"title": f"Hype edit {k + 1}", "tweet": "this episode went crazy",
                                       "score": 5, "kind": "hype"}, segs))
    for i, p in enumerate(picks, 1):
        p.rank = i
    return Selection(source="heuristic", style=style, picks=picks)
