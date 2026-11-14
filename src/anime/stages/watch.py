"""Stage 4: Gemini watches every pick before it is edited.

Subtitles can't show animation quality, a facial expression or how hard a hit lands. Each pick
becomes a tiny proxy video (270p, with sound, a few seconds of padding around every segment) and
Gemini rescores it, drops the duds, tightens the in/out points, places zoom punches and rewrites the
hook to match what is on screen. Proxies and answers are cached, so reruns are free.
"""
from __future__ import annotations

from pathlib import Path

from common import ffmpeg
from common.progress import Reporter

from ..config import Config
from ..models import EpisodeMeta, Pick, Segment, Selection, load_json, save_json
from .select import EFFECTS, LLM_CACHE, SYSTEM, TRANSITIONS, clean_tweet
from .select import OUT as CANDIDATES

OUT = "watched.json"
PROXY_DIR = "proxies"
CLIP_PAD = 8.0  # room to extend a scene that continues past the proposed edges
BEAT_PAD = 1.5

PROMPT = """Anime episode file: "{file}".
Candidate short #{rank} ({style}): "{title}"
Planned hook (shown as a tweet above the video): "{tweet}"
Why it was picked: {why}

{videos}

Watch every video carefully, picture AND sound, and judge it as the channel's editor.
- keep: false if it is dull, confusing without context, mostly static talking with nothing happening on
  screen, badly cut mid-sentence beyond repair, or part of the opening/ending song.
- score: 0-10 viral potential from what you actually see and hear (animation, impact, emotion, clarity).
- segments: one entry per video, in the same order. start/end are seconds IN THAT VIDEO.
  {segment_rule}
  punch_at: seconds in that video of the single biggest visual impact, if there is one (else omit).
  transition_in / effects: {fx_rule}
- tweet: the hook text, rewritten to match what is on screen: max 90 characters, no emojis, no hashtags,
  casual internet voice, makes people want to watch without spoiling the payoff.
- title: upload title, max 70 characters."""

CLIP_RULE = ("Start right before the hook (first line / first movement that matters), end right after the payoff "
             "and reaction; keep the whole exchange so it makes sense on its own. Aim for about {target_s:.0f} s "
             "if the scene supports it, {min_s:.0f}-{max_s:.0f} s allowed, never padded. You may extend into "
             "the padding if the scene continues there.")
BEAT_RULE = ("Keep the most striking 2.5-6 s of each beat; together the beats must still add up to at least "
             "{min_s:.0f} s, so trim only what is dull.")
CLIP_FX = "use cut and no effects (single scene)."
MONTAGE_FX = ("transition_in = cut | flash | zoom | whip | fade | glitch; effects from zoom_punch, shake, slowmo, "
              "freeze_end (freeze_end only on the last).")

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "keep": {"type": "BOOLEAN"},
        "score": {"type": "NUMBER"},
        "tweet": {"type": "STRING"},
        "title": {"type": "STRING"},
        "segments": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "punch_at": {"type": "NUMBER"},
                "transition_in": {"type": "STRING", "enum": TRANSITIONS},
                "effects": {"type": "ARRAY", "items": {"type": "STRING", "enum": EFFECTS}},
            },
            "required": ["start", "end"],
        }},
    },
    "required": ["keep", "score", "segments"],
}


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, force: bool = False,
        report: Reporter | None = None) -> tuple[Selection, str]:
    report = report or (lambda fraction=None, message=None: None)
    cand = load_json(Selection, job_dir / CANDIDATES)
    out = job_dir / OUT
    if out.exists() and not force:
        w = load_json(Selection, out)
        if w.style == cand.style and {p.id for p in w.picks} <= {p.id for p in cand.picks}:
            return w, f"{len(w.picks)} picks · cached"

    sc = cfg.select
    if not sc.watch or cand.source != "gemini" or cand.style == "song":
        sel = cand.model_copy(update={"picks": cand.picks[: sc.count]})
        save_json(sel, out)
        why = ("select.watch is off" if not sc.watch else "song edits are cut to the music" if cand.style == "song"
               else "no Gemini picks")
        return sel, f"skipped ({why})"

    from common.llm import LLMError, generate_json

    kept: list[Pick] = []
    rejected: list[Pick] = []
    dropped, failed = 0, 0
    for i, p in enumerate(cand.picks):
        if p.style == "song":  # a pool of moments, cut to the song later: nothing to judge as a finished short
            kept.append(p)
            continue
        report(i / len(cand.picks), f"Gemini is watching pick {i + 1}/{len(cand.picks)}: {p.title[:50]}")
        try:
            parts, starts = _parts(job_dir, meta, cfg, p)
            data, _ = generate_json(
                parts, SCHEMA, models=[sc.model, *sc.fallback_models], cache_dir=job_dir / LLM_CACHE,
                system=SYSTEM, refresh=force, temperature=0.3, timeout_s=sc.timeout_s,
                max_retries=sc.max_retries, min_interval_s=sc.min_interval_s, task="anime.watch",
                on_status=lambda m: report(None, m),
            )
        except (LLMError, ffmpeg.FFmpegError) as e:
            failed += 1
            report(None, f"Could not watch pick {i + 1} ({e}); keeping it as is")
            kept.append(p)
            continue
        q = apply(p, data, starts, cfg)
        if q is None:
            dropped += 1
            rejected.append(p.model_copy(update={"score": min(p.score, _num(data.get("score")) or 0), "watched": True}))
        else:
            kept.append(q)
    note = None
    if not kept and rejected:  # Gemini liked nothing: still give the best tries to review, clearly labelled
        kept = sorted(rejected, key=lambda p: -p.score)[: min(2, sc.count)]
        note = "Gemini found no strong moment in this episode: these are its least-bad picks, review before posting"
    kept.sort(key=lambda p: (not p.watched, -p.score))
    kept = kept[: sc.count]
    for i, p in enumerate(kept, 1):
        p.rank = i
    sel = cand.model_copy(update={"picks": kept, "note": note})
    save_json(sel, out)
    msg = f"watched {len(cand.picks) - failed} picks · dropped {dropped} · kept {len(kept)}"
    return sel, msg + (f" · {failed} could not be watched" if failed else "")


def _windows(p: Pick, meta: EpisodeMeta) -> list[tuple[float, float]]:
    pad = CLIP_PAD if p.style == "clip" else BEAT_PAD
    return [(max(0.0, s.start - pad), min(meta.duration, s.end + pad)) for s in p.segments]


def _parts(job_dir: Path, meta: EpisodeMeta, cfg: Config, p: Pick) -> tuple[list, list[float]]:
    sc = cfg.select
    d = job_dir / PROXY_DIR
    d.mkdir(exist_ok=True)
    wins = _windows(p, meta)
    labels, parts = [], []
    for i, ((a, b), s) in enumerate(zip(wins, p.segments), 1):
        f = d / f"{p.id}-{i}-{sc.watch_height}.mp4"
        if not f.exists():
            make_proxy(Path(meta.source), meta, a, b, f, sc.watch_height)
        what = "the proposed clip" if p.style == "clip" else f"proposed beat {i}"
        labels.append(f"Video {i}: {b - a:.1f} s long; {what} is {s.start - a:.1f}-{s.end - a:.1f} s in this video.")
        parts += [f"Video {i}:", ("video/mp4", f.read_bytes())]
    lo, hi = (cfg.select.min_clip_s, cfg.select.max_clip_s)
    prompt = PROMPT.format(
        file=Path(meta.source).name, rank=p.rank, style=p.style, title=p.title, tweet=p.tweet,
        why=p.why or "-", videos="\n".join(labels),
        segment_rule=(CLIP_RULE.format(min_s=lo, max_s=hi, target_s=sc.target_clip_s) if p.style == "clip"
                      else BEAT_RULE.format(min_s=sc.montage_min_s)),
        fx_rule=CLIP_FX if p.style == "clip" else MONTAGE_FX,
    )
    return [prompt, *parts], [a for a, _ in wins]


def make_proxy(src: Path, meta: EpisodeMeta, a: float, b: float, dst: Path, height: int) -> None:
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    ffmpeg.run(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src),
                "-map", "0:v:0", "-map", f"0:a:{meta.audio_index}",
                "-vf", f"scale=-2:{height},fps=8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-c:a", "aac", "-ac", "1", "-b:a", "48k", "-movflags", "+faststart", str(tmp)])
    tmp.replace(dst)


def apply(p: Pick, data: dict, starts: list[float], cfg: Config) -> Pick | None:
    """Gemini's verdict -> updated pick (None = dropped). Bad numbers fall back to the original values."""
    if data.get("keep") is False:
        return None
    segs_in = data.get("segments") or []
    segs: list[Segment] = []
    for i, s in enumerate(p.segments):
        r = segs_in[i] if i < len(segs_in) else {}
        base = starts[i]
        a, b = _num(r.get("start")), _num(r.get("end"))
        new = s.model_copy()
        if a is not None and b is not None and b - a >= 1.0:
            new.start, new.end = base + max(0.0, a), base + b
        if (pa := _num(r.get("punch_at"))) is not None and new.start <= base + pa <= new.end:
            new.punch_at = base + pa
        if p.style == "montage":
            if r.get("transition_in") in TRANSITIONS:
                new.transition_in = r["transition_in"]
            if r.get("effects") is not None:
                new.effects = [x for x in dict.fromkeys(r["effects"]) if x in EFFECTS]
            if new.punch_at is not None and "zoom_punch" not in new.effects:
                new.effects.append("zoom_punch")
        segs.append(new)
    sc = cfg.select
    if p.style == "clip":
        s = segs[0]
        if s.duration < sc.min_clip_s * 0.6:  # Gemini cut too much: keep the original range
            segs = [p.segments[0]]
        segs[0].end = min(segs[0].end, segs[0].start + sc.max_clip_s)
    q = p.model_copy(update={"segments": segs, "watched": True})
    if (score := _num(data.get("score"))) is not None:
        q.score = score
    if t := clean_tweet(str(data.get("tweet") or "")):
        q.tweet = t
    if t := str(data.get("title") or "").strip():
        q.title = t[:100]
    return q


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
