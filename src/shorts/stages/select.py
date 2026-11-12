"""Stage 3: find the most postable moments -> selection.json.

Gemini reads the transcript (in ~20 min overlapping windows) and answers with *sentence ids*,
never timestamps: times come from Whisper, and clip edges always land on sentence boundaries.
Every pick is then validated locally (length, score, duplicates). If Gemini is unreachable the
stage falls back to an offline heuristic so the pipeline still finishes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..config import Config, SelectCfg
from ..models import Clip, ClipScores, Selection, Sentence, Transcript, VideoInfo, VideoMeta, load_json, save_json
from common.progress import Reporter

OUT = "selection.json"
PAD_LEAD, PAD_TAIL = 0.15, 0.4  # seconds of air before/after the speech
CACHE_DIR = "llm_cache"  # keyed by model + full request, so prompt edits invalidate it

SYSTEM = """You are the lead short-form editor for a top YouTube channel. You have cut thousands of \
Shorts/Reels/TikToks and you know exactly what makes a viewer stop scrolling, watch to the end, \
replay and share. You are ruthless about weak openings and clips that need context."""

PROMPT = """Video: "{title}"{channel}
{about}This is part {part} of {parts} of the transcript ({span}). Each line is: [sentence id] (mm:ss) text{heat_note}

{lines}

Find EVERY moment in this part that could work as a standalone short ({min_s:.0f}-{max_s:.0f} seconds; 20-45 s is
the sweet spot unless a story needs longer). Be generous: a human reviews your picks, so include borderline ones
too and let the scores say how good they are.

What goes viral:
- THE FIRST LINE IS EVERYTHING. The viewer decides in 1-2 seconds. Start ON the hook: a bold claim, money or
  stakes, a question, conflict, a shocking statement, or the instant before something big happens.
  Never start on filler ("so", "okay", "alright", "um", "anyway", greetings, recaps, rules being explained).
  If the strong line comes a sentence or two in, START THERE and drop the setup.
- It makes sense to someone who has NOT seen the video: no unexplained "he/that/this one" in the opening.
- It has a payoff inside the clip: a result, a twist, a punchline, a big reaction, a reveal. End right after it
  (a reaction to the payoff is a great last beat). Never end mid-explanation.
- Tension and stakes: competition, money on the line, someone about to win or lose, a risky decision, a fail.
- Strong emotion is a signal: screaming, laughing, disbelief ("no way", "oh my god"), trash talk, celebration.
- For gameplay / challenge videos the best shorts are clutch moments, near-misses, fails, eliminations,
  big rewards and the funniest reactions, not rules or logistics. For talking videos: hot takes, surprising
  facts, stories with a twist, one sharp piece of advice.

Rules:
- start_id / end_id are sentence ids from the lines above (inclusive). Use the (mm:ss) times to keep
  the clip between {min_s:.0f} and {max_s:.0f} seconds long.
- clips must not overlap each other.
- scores are 0-10 integers. Calibrate against this scale and USE THE WHOLE RANGE:
    2-3 = filler, needs context, or no payoff; nobody would finish it
    5   = fine: understandable, mild hook or payoff; an average short
    7   = strong: clear hook in the first 2 s AND a real payoff
    9-10 = exceptional: would be the best short of the whole video (at most one or two per video)
  Score each dimension independently; most clips should land between 4 and 7.
  hook_score = how strongly the FIRST sentence of your clip grabs attention (not the clip's best line).
- title: a short catchy title for the short (max 60 chars), no hashtags.
- hook: the opening line of the clip, as spoken.
- reason: one sentence on why this works as a short.
- emphasis_words: 0-4 words spoken in the clip that deserve visual emphasis (exact words from the text).
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_id": {"type": "INTEGER"},
                    "end_id": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "hook": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                    "hook_score": {"type": "INTEGER"},
                    "standalone_score": {"type": "INTEGER"},
                    "payoff_score": {"type": "INTEGER"},
                    "energy_score": {"type": "INTEGER"},
                    "emphasis_words": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["start_id", "end_id", "title", "hook", "reason", "hook_score",
                             "standalone_score", "payoff_score", "energy_score"],
            },
        }
    },
    "required": ["clips"],
}

REFINE_PROMPT = """Video: "{title}"{channel}
{about}A first pass proposed the candidate shorts below. Each shows the transcript around the clip as
[sentence id] (mm:ss) text; lines marked ">" are currently in the clip, the others are context you may use.{heat_note}

{candidates}

You are now the final editor. Compare ALL candidates against each other (they come from the same video) and for each:
1. keep: false ONLY if it clearly fails (makes no sense without context, no payoff at all) or is a near-duplicate
   of a better candidate. When in doubt keep it and give it a low score: a human reviews every pick.
2. Tighten it: move start_id LATER if the first lines are setup/filler, so the clip opens on its strongest line;
   move start_id earlier only if a crucial line is missing. end_id: right after the payoff/reaction. Stay within
   the ids shown and {min_s:.0f}-{max_s:.0f} seconds.
3. Re-score 0-10 relative to the other candidates (the single best short of this video gets 9-10, a typical one 5-6;
   hook_score judges the NEW first line).
4. Write the packaging:
   - hook_text: on-screen text shown over the first 3 seconds. 3-7 words, creates a curiosity gap or states the
     stakes, makes people stay. Not a spoiler of the payoff, no emojis, no hashtags.
     Good: "He bet $10,000 on one throw", "Nobody expected this ending", "I can't believe he did this".
   - title: the Shorts title, max 60 chars, curiosity + stakes, no hashtags.
   - description: 1-2 sentences for the upload description.
   - hashtags: 3-5 relevant hashtags (with #, include #shorts).
   - emphasis_words: 0-4 spoken words in the final clip worth highlighting.
Return one entry per candidate id (cid)."""

REFINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "cid": {"type": "STRING"},
                    "keep": {"type": "BOOLEAN"},
                    "start_id": {"type": "INTEGER"},
                    "end_id": {"type": "INTEGER"},
                    "hook_score": {"type": "INTEGER"},
                    "standalone_score": {"type": "INTEGER"},
                    "payoff_score": {"type": "INTEGER"},
                    "energy_score": {"type": "INTEGER"},
                    "hook_text": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "emphasis_words": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "reason": {"type": "STRING"},
                },
                "required": ["cid", "keep", "start_id", "end_id", "hook_score", "standalone_score",
                             "payoff_score", "energy_score", "hook_text", "title"],
            },
        }
    },
    "required": ["clips"],
}

HEAT_NOTE = ("\n🔥 = viewers rewatched this part a lot (YouTube \"Most replayed\"), 🔥🔥 = the most. "
             "That is real audience data: weigh it heavily.")


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, force: bool = False,
        report: Reporter | None = None) -> tuple[Selection, str]:
    report = report or (lambda fraction=None, message=None: None)
    out = job_dir / OUT
    if out.exists() and not force:
        sel = load_json(Selection, out)
        # an offline pick is a stopgap: try Gemini again on the next run
        if sel.source == "gemini" or not _has_key():
            return sel, describe(sel, cached=True)

    t = load_json(Transcript, job_dir / "transcript.json")
    if not t.sentences:
        sel = Selection(source="gemini", note="Transcript is empty", clips=[])
    else:
        from .download import load_info

        info = load_info(job_dir, meta, cfg) if cfg.select.use_heatmap or cfg.select.refine else None
        try:
            sel = _select_llm(t, meta, cfg.select, job_dir / CACHE_DIR, report, refresh=force, info=info)
        except Exception as e:  # noqa: BLE001  (any LLM failure -> offline fallback)
            from common.llm import LLMError
            if not cfg.select.offline_fallback:
                raise
            reason = str(e) if isinstance(e, LLMError) else f"{type(e).__name__}: {e}"
            report(None, f"Gemini failed ({reason}); using offline picks")
            sel = select_heuristic(t, cfg.select, heat_fn=heat_fn(info, cfg.select))
            sel.note = f"Gemini unavailable: {reason}. These are rough offline picks; Retry later for AI picks."
    save_json(sel, out)
    return sel, describe(sel)


def describe(sel: Selection, cached: bool = False) -> str:
    n = len(sel.clips)
    if sel.source == "gemini":
        msg = f"{n} moment{'s' * (n != 1)} · {sel.model}"
        if sel.candidates:
            msg += f" · {sel.candidates} proposed"
    else:
        msg = f"{n} offline pick{'s' * (n != 1)} (Gemini unavailable)"
    return msg + (" · cached" if cached else "")


def _has_key() -> bool:
    import os
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


# ---------------------------------------------------------------- Gemini

def _context(meta: VideoMeta, info: VideoInfo | None) -> tuple[str, str]:
    channel = f" by {info.channel}" if info and info.channel else ""
    about = ""
    if info and info.description.strip():
        desc = re.sub(r"https?://\S+", "", info.description).strip()
        desc = re.sub(r"\n{2,}", "\n", desc)[:500]
        about = f"Video description (for context): {desc}\n"
    return channel, about


def heat_fn(info: VideoInfo | None, sc: SelectCfg):
    """t0, t1 -> mean "Most replayed" level (0-1) over that span, or None without data."""
    if not (sc.use_heatmap and info and info.heatmap):
        return None
    pts = [(float(h["start_time"]), float(h["end_time"]), float(h["value"])) for h in info.heatmap
           if h.get("start_time") is not None and h.get("value") is not None]
    if not pts:
        return None
    top = max(v for *_, v in pts) or 1.0

    def heat(t0: float, t1: float) -> float:
        acc = w = 0.0
        for a, b, v in pts:
            o = min(b, t1) - max(a, t0)
            if o > 0:
                acc += o * v / top
                w += o
        return round(acc / w, 3) if w else 0.0
    return heat


def _line(s: Sentence, heat) -> str:
    mark = ""
    if heat:
        h = heat(s.start, s.end)
        mark = " 🔥🔥" if h >= 0.7 else " 🔥" if h >= 0.45 else ""
    return f"[{s.id}] ({_mmss(s.start)}) {s.text}{mark}"


def _select_llm(t: Transcript, meta: VideoMeta, sc: SelectCfg, cache_dir: Path, report: Reporter,
                refresh: bool = False, info: VideoInfo | None = None) -> Selection:
    from common.llm import generate_json

    heat = heat_fn(info, sc)
    channel, about = _context(meta, info)
    windows = make_windows(t.sentences, sc.window_min * 60, sc.overlap_min * 60)
    raw: list[dict] = []
    used_model = None
    calls = 0
    for i, win in enumerate(windows):
        span = f"{_mmss(win[0].start)}–{_mmss(win[-1].end)}"
        report(i / (len(windows) + sc.refine), f"Asking Gemini about {span} ({i + 1}/{len(windows)})…")
        prompt = PROMPT.format(
            title=meta.title, channel=channel, about=about, part=i + 1, parts=len(windows), span=span,
            heat_note=HEAT_NOTE if heat else "", lines="\n".join(_line(s, heat) for s in win),
            min_s=sc.min_clip_s, max_s=sc.max_clip_s,
        )
        data, used_model = generate_json(
            prompt, SCHEMA,
            models=[sc.model, *sc.fallback_models], cache_dir=cache_dir, system=SYSTEM, refresh=refresh,
            temperature=sc.temperature, timeout_s=sc.timeout_s, max_retries=sc.max_retries,
            min_interval_s=sc.min_interval_s, on_status=lambda m: report(i / len(windows), m),
        )
        calls += 1
        raw.extend(data.get("clips") or [])
    report(len(windows) / (len(windows) + sc.refine), f"Checking {len(raw)} proposed moments…")
    # keep some spare candidates for the editor pass to choose from
    wide = sc.model_copy(update={"max_clips": sc.max_clips + 10}) if sc.refine else sc
    clips, rejected = validate(raw, t.sentences, meta.duration, wide, heat)
    refined = False
    if sc.refine and clips:
        report(len(windows) / (len(windows) + 1), f"Editor pass: comparing {len(clips)} candidates…")
        try:
            clips, rej2 = _refine(clips, t, meta, info, sc, cache_dir, report, refresh, heat)
            calls += 1
            refined = True
            for k, v in rej2.items():
                rejected[k] = rejected.get(k, 0) + v
        except Exception as e:  # noqa: BLE001  (first-pass picks are still good)
            report(None, f"Editor pass failed ({e}); keeping first-pass picks")
            clips = clips[:sc.max_clips]
    return Selection(source="gemini", model=used_model, calls=calls, candidates=len(raw), rejected=rejected,
                     clips=clips, note=None if refined or not sc.refine else "Editor pass unavailable: first-pass picks")


def _refine(clips: list[Clip], t: Transcript, meta: VideoMeta, info: VideoInfo | None, sc: SelectCfg,
            cache_dir: Path, report: Reporter, refresh: bool, heat) -> tuple[list[Clip], dict[str, int]]:
    from common.llm import generate_json

    ss = t.sentences
    by_id = {s.id: i for i, s in enumerate(ss)}
    blocks, cids = [], {}
    for n, c in enumerate(sorted(clips, key=lambda c: c.start), 1):
        cid = f"C{n}"
        cids[cid] = c
        ia, ib = by_id[c.start_sentence], by_id[c.end_sentence]
        lo, hi = max(0, ia - 3), min(len(ss) - 1, ib + 2)
        lines = [("> " if ia <= j <= ib else "  ") + _line(ss[j], heat) for j in range(lo, hi + 1)]
        blocks.append(f"### {cid} · {c.duration:.0f}s · first-pass score {c.score:g} · \"{c.title}\"\n"
                      + "\n".join(lines))
    channel, about = _context(meta, info)
    prompt = REFINE_PROMPT.format(title=meta.title, channel=channel, about=about,
                                  heat_note=HEAT_NOTE if heat else "", candidates="\n\n".join(blocks),
                                  min_s=sc.min_clip_s, max_s=sc.max_clip_s)
    data, _ = generate_json(
        prompt, REFINE_SCHEMA, models=[sc.model, *sc.fallback_models], cache_dir=cache_dir, system=SYSTEM,
        refresh=refresh, temperature=sc.temperature, timeout_s=sc.timeout_s, max_retries=sc.max_retries,
        min_interval_s=sc.min_interval_s, on_status=lambda m: report(None, m),
    )
    answers = {str(r.get("cid", "")).strip(): r for r in data.get("clips") or []}
    raw: list[dict] = []
    dropped = 0
    for cid, c in cids.items():
        r = answers.get(cid)
        orig = {"start_id": c.start_sentence, "end_id": c.end_sentence, "title": c.title, "hook": c.hook,
                "reason": c.reason, "hook_score": c.scores.hook, "standalone_score": c.scores.standalone,
                "payoff_score": c.scores.payoff, "energy_score": c.scores.energy,
                "emphasis_words": c.emphasis_words}
        if r is None:
            raw.append(orig)  # the editor skipped it: keep the first-pass version
            continue
        if r.get("keep") is False:
            dropped += 1
            continue
        # the editor may only move the edges within the context it was shown
        ia, ib = by_id[c.start_sentence], by_id[c.end_sentence]
        lo, hi = ss[max(0, ia - 3)].id, ss[min(len(ss) - 1, ib + 2)].id
        try:
            a, b = int(r["start_id"]), int(r["end_id"])
        except (KeyError, TypeError, ValueError):
            a, b = c.start_sentence, c.end_sentence
        if not (lo <= a <= b <= hi):
            a, b = c.start_sentence, c.end_sentence
        hook = next((s.text for s in ss if s.id == a), c.hook)
        raw.append({**orig, **{k: v for k, v in r.items() if v not in (None, "", [])},
                    "start_id": a, "end_id": b, "hook": hook,
                    "reason": r.get("reason") or c.reason})
    clips2, rejected = validate(raw, ss, meta.duration, sc, heat)
    if dropped:
        rejected["dropped by editor pass"] = dropped
    return clips2, rejected


def make_windows(sentences: list[Sentence], window_s: float, overlap_s: float) -> list[list[Sentence]]:
    """Split into n roughly equal windows (n = round(duration / window)), each widened by the overlap."""
    if not sentences:
        return []
    t0, t1 = sentences[0].start, sentences[-1].end
    n = max(1, round((t1 - t0) / window_s))
    step = (t1 - t0) / n
    out = []
    for i in range(n):
        a = t0 + i * step - (overlap_s / 2 if i else 0)
        b = t0 + (i + 1) * step + overlap_s / 2 if i < n - 1 else float("inf")
        win = [s for s in sentences if a <= s.start < b]
        if win:
            out.append(win)
    return out


# ---------------------------------------------------------------- validation (pure)

def validate(raw: list[dict], sentences: list[Sentence], duration: float,
             sc: SelectCfg, heat=None) -> tuple[list[Clip], dict[str, int]]:
    """Turn LLM picks into clean clips: snap/fit length, drop low scores and duplicates, rank."""
    rejected: dict[str, int] = {}
    by_id = {s.id: i for i, s in enumerate(sentences)}

    def reject(why: str) -> None:
        rejected[why] = rejected.get(why, 0) + 1

    cands: list[Clip] = []
    for r in raw:
        try:
            a, b = int(r["start_id"]), int(r["end_id"])
        except (KeyError, TypeError, ValueError):
            reject("malformed"); continue
        if a > b:
            a, b = b, a
        if a not in by_id or b not in by_id:
            reject("unknown sentence id"); continue
        # leave room for the padding added below, so the final clip still respects max_clip_s
        ia, ib = fit_span(sentences, by_id[a], by_id[b], sc.min_clip_s, sc.max_clip_s - PAD_LEAD - PAD_TAIL)
        if ia is None:
            reject(f"can't fit {sc.min_clip_s:g}-{sc.max_clip_s:g}s"); continue
        scores = ClipScores(
            hook=_clamp(r.get("hook_score")), standalone=_clamp(r.get("standalone_score")),
            payoff=_clamp(r.get("payoff_score")), energy=_clamp(r.get("energy_score")),
        )
        start, end = pad(sentences, ia, ib, duration)
        span = sentences[ia:ib + 1]
        h = heat(start, end) if heat else None
        # viewers' replays are the strongest signal there is: lift the clips they rewatched
        bonus = sc.heat_bonus * max(0.0, (h - 0.4) / 0.6) if h is not None else 0.0
        score = round(min(10.0, scores.overall() + bonus), 1)
        if score < sc.min_score:
            reject("score below threshold"); continue
        cands.append(Clip(
            id=clip_id(start, end), rank=0, start=start, end=end,
            start_sentence=span[0].id, end_sentence=span[-1].id,
            title=str(r.get("title") or "Untitled").strip()[:80],
            hook=str(r.get("hook") or span[0].text).strip(),
            reason=str(r.get("reason") or "").strip(),
            scores=scores, score=score,
            emphasis_words=[str(w) for w in (r.get("emphasis_words") or [])][:4],
            text=" ".join(s.text for s in span),
            hook_text=str(r.get("hook_text") or "").strip()[:80],
            description=str(r.get("description") or "").strip()[:500],
            hashtags=[_tag(x) for x in (r.get("hashtags") or []) if _tag(x)][:6],
            heat=h,
        ))

    kept: list[Clip] = []
    for c in sorted(cands, key=lambda c: (-c.score, -c.scores.hook, c.start)):
        if any(_overlap(c, k) > sc.max_overlap_s for k in kept):
            reject("duplicate / overlapping"); continue
        if len(kept) >= sc.max_clips:
            reject("over max_clips"); continue
        kept.append(c)
    for i, c in enumerate(kept, 1):
        c.rank = i
    return kept, rejected


def fit_span(sentences: list[Sentence], ia: int, ib: int, min_s: float, max_s: float) -> tuple[int | None, int | None]:
    """Shrink from the end if too long; grow forward (then backward) if too short."""
    dur = lambda a, b: sentences[b].end - sentences[a].start  # noqa: E731
    while dur(ia, ib) > max_s and ib > ia:
        ib -= 1
    if dur(ia, ib) > max_s:
        return None, None
    while dur(ia, ib) < min_s:
        if ib + 1 < len(sentences) and dur(ia, ib + 1) <= max_s:
            ib += 1
        elif ia > 0 and dur(ia - 1, ib) <= max_s:
            ia -= 1
        else:
            return None, None
    return ia, ib


def pad(sentences: list[Sentence], ia: int, ib: int, duration: float,
        lead: float | None = None, tail: float | None = None) -> tuple[float, float]:
    """A little air around the speech, without bleeding into the neighbouring sentence."""
    lead = PAD_LEAD if lead is None else lead
    tail = PAD_TAIL if tail is None else tail
    start = sentences[ia].start - lead
    if ia > 0:
        start = max(start, sentences[ia - 1].end)
    end = sentences[ib].end + tail
    if ib + 1 < len(sentences):
        end = min(end, sentences[ib + 1].start)
    return round(max(0.0, start), 3), round(min(end, duration or end), 3)


def _tag(x) -> str:
    t = re.sub(r"[^\w]", "", str(x))
    return f"#{t}" if t else ""


def clip_id(start: float, end: float) -> str:
    return hashlib.sha1(f"{start:.2f}-{end:.2f}".encode()).hexdigest()[:8]


def _overlap(a: Clip, b: Clip) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def _clamp(v) -> float:
    try:
        return float(min(10, max(0, float(v))))
    except (TypeError, ValueError):
        return 0.0


def _mmss(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- offline fallback

_HOOK = re.compile(
    r"\?|!|\$\s?\d|\b\d{2,}[\d,]*\b|\b(win|won|lose|lost|last|first|never|ever|biggest|insane|crazy|"
    r"million|thousand|challenge|bet|secret|impossible|surprise|beat|eliminated|whoever)\b",
    re.I,
)


def select_heuristic(t: Transcript, sc: SelectCfg, heat_fn=None) -> Selection:
    """Score windows that start at each sentence by hook words / punctuation density. Crude but offline."""
    ss = t.sentences
    raw = []
    target = min(max(sc.min_clip_s, 35.0), sc.max_clip_s)
    for i, s in enumerate(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1].end - s.start <= target:
            j += 1
        span_text = " ".join(x.text for x in ss[i:j + 1])
        hook_hits = len(_HOOK.findall(s.text)) + 0.5 * len(_HOOK.findall(ss[min(i + 1, j)].text))
        density = len(_HOOK.findall(span_text)) / max(1, len(span_text.split())) * 40
        h = min(10, 3 + 2.5 * hook_hits)
        e = min(10, 3 + density)
        raw.append({"start_id": s.id, "end_id": ss[j].id, "title": s.text[:60], "hook": s.text,
                    "reason": "Offline pick: hook words / punctuation in the opening line.",
                    "hook_score": h, "standalone_score": 5, "payoff_score": 5, "energy_score": e})
    offline = sc.model_copy(update={"max_clips": max(1, min(sc.max_clips, len(ss) // 40 + 3))})
    clips, rejected = validate(raw, ss, t.duration, offline, heat_fn)
    return Selection(source="heuristic", candidates=len(raw), rejected=rejected, clips=clips)
