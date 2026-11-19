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
from ..models import (AudioSignals, Clip, ClipScores, Focus, Selection, Sentence, Transcript, VideoInfo, VideoMeta,
                      load_json, save_json)
from common.progress import Reporter
from . import signals
from .signals import Mark

OUT = "selection.json"
FOCUS = "focus.json"            # the creator's note: what to look for (optional)
FOCUS_MAX = 500
PAD_LEAD, PAD_TAIL = 0.15, 0.4  # seconds of air before/after the speech
PAD_TAIL_PAYOFF = 0.8           # more air when the payoff is the last line: let the moment land
CACHE_DIR = "llm_cache"  # keyed by model + full request, so prompt edits invalidate it
CTX_BEFORE, CTX_AFTER = 6, 4  # sentences of context the editor pass sees (and may extend the clip into)

# spoken lead-ins that only delay the hook: cut from the clip's first sentence ("And now we're back...")
LEAD_FILLERS = {"and", "so", "but", "okay", "ok", "alright", "um", "uh", "umm", "uhm", "hmm", "like", "now", "well",
                "anyway", "anyways", "yeah", "yep", "then", "also", "basically", "actually", "right"}
LEAD_PHRASES = [("all", "right"), ("you", "know"), ("i", "mean")]
# a clip that opens on one of these probably points at something the viewer hasn't seen
DANGLING = {"he", "she", "they", "him", "her", "them", "his", "their", "it", "it's", "this", "that", "that's",
            "these", "those", "there"}
# endings that kill a Short: plugs, sponsors, "go watch the full video"
PROMO = re.compile(
    r"\b(check (it|them|this) out|link (is )?(in|below)|in the description|go watch|watch (our|my|the) (new|full)|"
    r"(new|full) video|available (at|in|on|now)|sponsor(ed)?|use (my |the )?code|promo code|download (it|the app)|"
    r"prime video|wherever you buy|retailers|shop now|merch|go to \w+\.(com|org))\b|\w\.(com|org)\b", re.I)
TRANSITIONS = ("next", "now let's", "let's move", "moving on", "okay so", "alright so", "so now", "up next", "after that")

SYSTEM = """You are the lead short-form editor for a top YouTube channel. You have cut thousands of \
Shorts/Reels/TikToks and you know exactly what makes a viewer stop scrolling, watch to the end, \
replay and share. You are ruthless about weak openings and clips that need context."""

PROMPT = """Video: "{title}"{channel}
{about}This is part {part} of {parts} of the transcript ({span}). Each line is: [sentence id] (mm:ss) text{heat_note}

{lines}
{focus_note}
Find EVERY moment in this part that could work as a standalone short ({min_s:.0f}-{max_s:.0f} seconds; 25-45 s is
the sweet spot: the most-viewed Shorts on YouTube are almost all under 47 s). Be generous: a human reviews your picks, so include borderline ones
too and let the scores say how good they are. This part is {minutes:.0f} minutes long: expect around {target}
candidates (about one per 1-2 minutes of content); return fewer only if the content truly has nothing.

Build every clip as HOOK -> TENSION -> PAYOFF:
1. Find the PAYOFF first: the result, reveal, twist, punchline or peak reaction. Its sentence id is payoff_id.
2. Walk back to the best HOOK: the latest line that still makes a cold viewer understand what is at stake and
   want to see the payoff. That is start_id. The shorter the road from hook to payoff, the better.
3. end_id = the payoff line, or one or two lines after it if they carry the reaction. Never beyond that.

What goes viral:
- THE FIRST SENTENCE STATES THE PREMISE: what is happening and what is at stake, the way a title would, in under
  3 seconds. The biggest Shorts on YouTube (billions of views) open like this: "Can you slice a bullet with a
  katana?", "Guess the gift and you win it", "I'll give you $10,000 if you fill this with water in 2 minutes",
  "This is how you open a $100,000 car door". The weakest ones open with lines that need context: "Have a good
  day, Panther", "What happened?", "Jimmy, this place is legendary". Look for the sentence where someone SAYS the
  premise or the challenge out loud, and start there.
- THE FIRST LINE IS EVERYTHING. The viewer decides in 1-2 seconds. Start ON the hook: a bold claim, money or
  stakes, a question, conflict, a shocking statement, or the instant before something big happens.
  Never start on filler ("so", "okay", "alright", "um", "anyway", greetings, recaps, rules being explained).
  If the strong line comes a sentence or two in, START THERE and drop the setup.
- It makes sense to someone who has NOT seen the video: no unexplained "he/that/this one" in the opening, no
  "next" / "now we're back" / "for this challenge" openers that point at the rest of the video.
- It has a payoff inside the clip: a result, a twist, a punchline, a big reaction, a reveal. End right after it
  (a reaction to the payoff is a great last beat). Never end mid-explanation, and never on a plug ("go check it
  out", "link in the description", sponsors, "go watch the full video"): those endings kill a Short.
- Tension and stakes: competition, money on the line, someone about to win or lose, a risky decision, a fail.
- Strong emotion is a signal: screaming, laughing, disbelief ("no way", "oh my god"), trash talk, celebration.
- For gameplay / challenge videos the best shorts are clutch moments, near-misses, fails, eliminations,
  big rewards and the funniest reactions, not rules or logistics. For talking videos: hot takes, surprising
  facts, stories with a twist, one sharp piece of advice.

Rules:
- start_id / end_id are sentence ids from the lines above (inclusive). Use the (mm:ss) times to keep
  the clip between {min_s:.0f} and {max_s:.0f} seconds long.
- payoff_id: the sentence id (inside the clip) where the payoff lands.
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
- context: what a viewer who never saw the video would be missing at the start of the clip ("" if nothing).
  If it is more than a few words, move start_id or lower standalone_score.
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
                    "payoff_id": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "hook": {"type": "STRING"},
                    "context": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                    "hook_score": {"type": "INTEGER"},
                    "standalone_score": {"type": "INTEGER"},
                    "payoff_score": {"type": "INTEGER"},
                    "energy_score": {"type": "INTEGER"},
                    "emphasis_words": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["start_id", "end_id", "payoff_id", "title", "hook", "reason", "hook_score",
                             "standalone_score", "payoff_score", "energy_score"],
            },
        }
    },
    "required": ["clips"],
}

REFINE_PROMPT = """Video: "{title}"{channel}
{about}A first pass proposed the candidate shorts below. Each shows the transcript around the clip as
[sentence id] (mm:ss) text; lines marked ">" are currently in the clip, "*" marks the payoff line, the others are
context you may use. "Opens with" is exactly what the viewer hears first (filler words are already cut).
"Missing for a cold viewer" is what the first pass thought the opening does not explain. "Check:" lines are
automatic warnings: confirm or fix each one.{heat_note}

{candidates}
{focus_note}
You are now the final editor. Compare ALL candidates against each other (they come from the same video) and run
each one through these tests, as a viewer scrolling Shorts who has NEVER seen this video:
- 2-second test: would the opening words alone stop the scroll? A question, a number, stakes, conflict or a bold
  claim beats any setup.
- Premise test: does the first sentence say what is happening and what is at stake, like a title would?
- Cold-viewer test: is every person, object and rule in the first 5 seconds understandable without the video?
- Payoff test: does the clip deliver what its hook promises, and end within a beat of it?
- Share test: would someone send it to a friend or replay it (surprise, laugh, outrage, "no way")?
Then for each candidate:
1. keep: false ONLY if it has no payoff at all or is a near-duplicate of a better candidate. Missing context is
   NOT a reason to drop: if 3-7 on-screen words can supply what a cold viewer is missing ("His car, dropped from
   a helicopter"), keep the clip and write that setup as hook_text. Let the scores show its weaknesses: a human
   reviews every pick.
2. Tighten it: move start_id LATER if the first lines are setup/filler, so the clip opens on its strongest line;
   move start_id EARLIER only if a crucial line for understanding is missing. end_id: the payoff, or right after
   its reaction; cut trailing transitions to the next segment. payoff_id: the payoff line of the final clip. Stay
   within the ids shown and {min_s:.0f}-{max_s:.0f} seconds. Shorter is better when nothing is lost.
3. Re-score 0-10 relative to the other candidates. Be strict: the single best short of this video gets 9-10, a
   typical one 5-6, and at most two candidates may get 9+ on any dimension. hook_score judges the NEW first line,
   standalone_score the cold-viewer test.
4. Write the packaging:
   - opening_states_premise: true if the first spoken sentence of the FINAL clip already states the premise and
     the stakes on its own (then no text banner is needed, the captions carry it).
   - hook_text: on-screen text shown over the first 3 seconds when the spoken opening does NOT state the premise:
     3-7 words that do it instead. If something is missing for a cold viewer, the text SUPPLIES it: name the
     setup and the stakes plainly ("His $40K car, dropped from a helicopter", "$100K if he beats a pro").
     Only when nothing is missing may it tease instead ("Nobody expected this ending"). Not a spoiler of the
     payoff, no emojis, no hashtags, and never a copy of the title: the title is read before the clip, the text is
     read over its first seconds, so they do different jobs.
   - loops: true if the last line of the final clip sets up or echoes its first line (a question the opening
     answers, the same object or stakes), so the Short replays without a seam. When nothing is lost, prefer the
     end_id that makes this true.
   - title: the Shorts title, max 60 chars, no hashtags: the premise in as few words as possible, with the
     concrete number or object. Shapes that win billions of views: "Flip a Coin, Win $30,000",
     "Katana Vs Bullet", "Would You Fly To Paris For A Baguette?", "How Much Tape To Stop A Lamborghini?",
     "$100,000,000 Car Doors".
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
                    "payoff_id": {"type": "INTEGER"},
                    "hook_score": {"type": "INTEGER"},
                    "standalone_score": {"type": "INTEGER"},
                    "payoff_score": {"type": "INTEGER"},
                    "energy_score": {"type": "INTEGER"},
                    "hook_text": {"type": "STRING"},
                    "opening_states_premise": {"type": "BOOLEAN"},
                    "loops": {"type": "BOOLEAN"},
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

FOCUS_NOTE = """
THE CREATOR'S REQUEST: they want shorts about this, in their own words: "{text}"
{rule}
Set matches_focus = true on every clip that is about the request, false on the others.
"""
FOCUS_RULES = {
    "prefer": ("Find EVERY moment in this part that matches the request first, including weaker ones (a human reviews "
               "them), then the other strong moments as usual."),
    "only": "Return ONLY moments that match the request. If nothing in this part matches, return an empty list.",
}
REFINE_FOCUS_NOTE = """
THE CREATOR'S REQUEST: they want shorts about this, in their own words: "{text}"
{rule}
matches_focus: true if the final clip is about the request.
"""
REFINE_FOCUS_RULES = {
    "prefer": "Keep every candidate that matches the request unless it has no payoff at all; judge the others as usual.",
    "only": "Drop (keep: false) every candidate that does not match the request.",
}

HEAT_NOTE = ("\n🔥 = viewers rewatched this part a lot (YouTube \"Most replayed\"), 🔥🔥 = the most. "
             "That is real audience data: weigh it heavily.")
SIGNAL_NOTE = ("\n🔊 = one of the loudest lines of the video (shouting, a big reaction). "
               "💥 = a loud non-speech moment right after the line (crash, explosion, screaming, laughter), "
               "💥💥 = a long one, measured from the audio. A 💥💥 is very often the payoff: prefer clips that contain "
               "one and end the clip ON that line, so it ends on the sound. But a 💥 is only a hint: never end a clip "
               "before it has delivered what its first line promised just to land on a sound.")


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, force: bool = False,
        report: Reporter | None = None) -> tuple[Selection, str]:
    report = report or (lambda fraction=None, message=None: None)
    out = job_dir / OUT
    focus = load_focus(job_dir)
    if out.exists() and not force:
        sel = load_json(Selection, out)
        # an offline pick is a stopgap: try Gemini again on the next run; a new focus note picks again
        if (sel.source == "gemini" or not _has_key()) and _same_focus(sel.focus, focus):
            return sel, describe(sel, cached=True)

    t = load_json(Transcript, job_dir / "transcript.json")
    if not t.sentences:
        sel = Selection(source="gemini", note="Transcript is empty", clips=[])
    else:
        from .download import load_info

        info = load_info(job_dir, meta, cfg) if cfg.select.use_heatmap or cfg.select.refine else None
        sig, marks = _audio(job_dir, t, cfg.select, force, report)
        try:
            sel = _select_llm(t, meta, cfg.select, job_dir / CACHE_DIR, report, refresh=force, info=info, marks=marks,
                              focus=focus)
        except Exception as e:  # noqa: BLE001  (any LLM failure -> offline fallback)
            from common.llm import LLMError
            if not cfg.select.offline_fallback:
                raise
            reason = str(e) if isinstance(e, LLMError) else f"{type(e).__name__}: {e}"
            report(None, f"Gemini failed ({reason}); using offline picks")
            sel = select_heuristic(t, cfg.select, heat_fn=heat_fn(info, cfg.select), sig=sig, marks=marks)
            sel.note = f"Gemini unavailable: {reason}. These are rough offline picks; Retry later for AI picks."
            if focus:
                sel.note += " Your focus note needs the AI: it applies on the retry."
    sel.focus = focus
    save_json(sel, out)
    return sel, describe(sel)


def load_focus(job_dir: Path) -> Focus | None:
    """The project's focus note, or None (no note, or an empty one)."""
    p = job_dir / FOCUS
    if not p.exists():
        return None
    try:
        f = load_json(Focus, p)
    except Exception:  # noqa: BLE001
        return None
    return f if f.active else None


def save_focus(job_dir: Path, text: str, mode: str = "prefer") -> Focus | None:
    """Write (or with empty text, remove) the focus note. Returns what is now in effect."""
    text = re.sub(r"\s+", " ", text or "").strip()[:FOCUS_MAX]
    p = job_dir / FOCUS
    if not text:
        p.unlink(missing_ok=True)
        return None
    f = Focus(text=text, mode=mode if mode in FOCUS_RULES else "prefer")
    job_dir.mkdir(parents=True, exist_ok=True)
    save_json(f, p)
    return f


def _same_focus(a: Focus | None, b: Focus | None) -> bool:
    key = lambda f: (f.text.strip(), f.mode) if f and f.active else None  # noqa: E731
    return key(a) == key(b)


def _focus_note(focus: Focus | None, refine: bool = False) -> str:
    if not focus:
        return ""
    text = focus.text.replace('"', "'")
    if refine:
        return REFINE_FOCUS_NOTE.format(text=text, rule=REFINE_FOCUS_RULES[focus.mode])
    return FOCUS_NOTE.format(text=text, rule=FOCUS_RULES[focus.mode])


def _with_focus(schema: dict, focus: Focus | None) -> dict:
    """The answer schema, plus matches_focus when there is a note (without one the request stays byte-identical,
    so cached answers keep working)."""
    if not focus:
        return schema
    import copy
    s = copy.deepcopy(schema)
    s["properties"]["clips"]["items"]["properties"]["matches_focus"] = {"type": "BOOLEAN"}
    return s


def describe(sel: Selection, cached: bool = False) -> str:
    n = len(sel.clips)
    if sel.source == "gemini":
        msg = f"{n} moment{'s' * (n != 1)} · {sel.model}"
        if sel.candidates:
            msg += f" · {sel.candidates} proposed"
        if sel.focus:
            hits = sum(bool(c.focus_match) for c in sel.clips)
            msg += f" · {hits} match{'es' * (hits != 1)} your focus"
    else:
        msg = f"{n} offline pick{'s' * (n != 1)} (Gemini unavailable)"
    return msg + (" · cached" if cached else "")


def _audio(job_dir: Path, t: Transcript, sc: SelectCfg, force: bool,
           report: Reporter) -> tuple[AudioSignals | None, dict[int, Mark] | None]:
    """Loudness marks for the picker; never fails the stage."""
    if not sc.use_audio:
        return None, None
    try:
        report(None, "Reading audio levels…")
        sig = signals.run(job_dir, t, force=force)
        if sig is None:
            return None, None
        marks = signals.sentence_marks(sig, t.sentences, loud_top=sc.loud_top_fraction, burst_min_s=sc.burst_min_s)
        report(None, f"Audio: {signals.describe(marks)}")
        return sig, marks
    except Exception as e:  # noqa: BLE001
        report(None, f"Audio signals unavailable ({type(e).__name__}: {e}); picking from the transcript only")
        return None, None


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


def _line(s: Sentence, heat, marks: dict[int, Mark] | None = None) -> str:
    mark = ""
    if heat:
        h = heat(s.start, s.end)
        mark = " 🔥🔥" if h >= 0.7 else " 🔥" if h >= 0.45 else ""
    m = marks.get(s.id) if marks else None
    if m and m.loud:
        mark += " 🔊"
    if m and m.burst_after_s:
        mark += " 💥💥" if m.burst_after_s >= 2.0 else " 💥"
    return f"[{s.id}] ({_mmss(s.start)}) {s.text}{mark}"


def _notes(heat, marks) -> str:
    return (HEAT_NOTE if heat else "") + (SIGNAL_NOTE if marks else "")


def _select_llm(t: Transcript, meta: VideoMeta, sc: SelectCfg, cache_dir: Path, report: Reporter,
                refresh: bool = False, info: VideoInfo | None = None,
                marks: dict[int, Mark] | None = None, focus: Focus | None = None) -> Selection:
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
            heat_note=_notes(heat, marks), lines="\n".join(_line(s, heat, marks) for s in win),
            focus_note=_focus_note(focus),
            min_s=sc.min_clip_s, max_s=sc.max_clip_s, minutes=(win[-1].end - win[0].start) / 60,
            target=max(3, round((win[-1].end - win[0].start) / 90)),
        )
        data, used_model = generate_json(
            prompt, _with_focus(SCHEMA, focus),
            models=[sc.model, *sc.fallback_models], cache_dir=cache_dir, system=SYSTEM, refresh=refresh,
            temperature=sc.temperature, timeout_s=sc.timeout_s, max_retries=sc.max_retries,
            min_interval_s=sc.min_interval_s, task="shorts.select", on_status=lambda m: report(i / len(windows), m),
        )
        calls += 1
        raw.extend(data.get("clips") or [])
    report(len(windows) / (len(windows) + sc.refine), f"Checking {len(raw)} proposed moments…")
    # keep some spare candidates for the editor pass to choose from
    wide = sc.model_copy(update={"max_clips": sc.max_clips + 10}) if sc.refine else sc
    clips, rejected = validate(raw, t.sentences, meta.duration, wide, heat, marks=marks, focus=focus)
    note = None
    if focus and focus.mode == "only" and not clips and raw:
        # nothing matched: better the best moments than an empty project
        focus = focus.model_copy(update={"mode": "prefer"})
        clips, rejected = validate(raw, t.sentences, meta.duration, wide, heat, marks=marks, focus=focus)
        note = "Nothing matched your focus note, so these are the video's best moments instead."
    refined = False
    if sc.refine and clips:
        report(len(windows) / (len(windows) + 1), f"Editor pass: comparing {len(clips)} candidates…")
        try:
            clips, rej2 = _refine(clips, t, meta, info, sc, cache_dir, report, refresh, heat, marks, focus)
            calls += 1
            refined = True
            for k, v in rej2.items():
                rejected[k] = rejected.get(k, 0) + v
        except Exception as e:  # noqa: BLE001  (first-pass picks are still good)
            report(None, f"Editor pass failed ({e}); keeping first-pass picks")
            clips = clips[:sc.max_clips]
    if note is None and sc.refine and not refined:
        note = "Editor pass unavailable: first-pass picks"
    return Selection(source="gemini", model=used_model, calls=calls, candidates=len(raw), rejected=rejected,
                     clips=clips, note=note)


def _refine(clips: list[Clip], t: Transcript, meta: VideoMeta, info: VideoInfo | None, sc: SelectCfg,
            cache_dir: Path, report: Reporter, refresh: bool, heat,
            marks: dict[int, Mark] | None = None, focus: Focus | None = None) -> tuple[list[Clip], dict[str, int]]:
    from common.llm import generate_json

    ss = t.sentences
    by_id = {s.id: i for i, s in enumerate(ss)}
    blocks, cids = candidate_blocks(clips, ss, heat, marks)
    channel, about = _context(meta, info)
    prompt = REFINE_PROMPT.format(title=meta.title, channel=channel, about=about,
                                  heat_note=_notes(heat, marks), candidates="\n\n".join(blocks),
                                  min_s=sc.min_clip_s, max_s=sc.max_clip_s, focus_note=_focus_note(focus, refine=True))
    data, _ = generate_json(
        prompt, _with_focus(REFINE_SCHEMA, focus), models=[sc.model, *sc.fallback_models], cache_dir=cache_dir, system=SYSTEM,
        refresh=refresh, temperature=sc.temperature, timeout_s=sc.timeout_s, max_retries=sc.max_retries,
        min_interval_s=sc.min_interval_s, task="shorts.refine", on_status=lambda m: report(None, m),
    )
    answers = {str(r.get("cid", "")).strip(): r for r in data.get("clips") or []}
    raw: list[dict] = []
    dropped = 0
    for cid, c in cids.items():
        r = answers.get(cid)
        orig = {"start_id": c.start_sentence, "end_id": c.end_sentence, "title": c.title, "hook": c.hook,
                "reason": c.reason, "hook_score": c.scores.hook, "standalone_score": c.scores.standalone,
                "payoff_score": c.scores.payoff, "energy_score": c.scores.energy,
                "emphasis_words": c.emphasis_words, "payoff_id": c.payoff_sentence, "context": c.context,
                "matches_focus": c.focus_match}
        if r is None:
            raw.append(orig)  # the editor skipped it: keep the first-pass version
            continue
        if r.get("keep") is False:
            dropped += 1
            continue
        # the editor may only move the edges within the context it was shown
        ia, ib = by_id[c.start_sentence], by_id[c.end_sentence]
        lo, hi = ss[max(0, ia - CTX_BEFORE)].id, ss[min(len(ss) - 1, ib + CTX_AFTER)].id
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
    clips2, rejected = validate(raw, ss, meta.duration, sc, heat, final=True, marks=marks, focus=focus)
    if dropped:
        rejected["dropped by editor pass"] = dropped
    return clips2, rejected


def candidate_blocks(clips: list[Clip], ss: list[Sentence], heat=None,
                     marks: dict[int, Mark] | None = None) -> tuple[list[str], dict[str, Clip]]:
    """What the editor pass sees for each candidate: the clip in its context, what it opens with, what a
    cold viewer is missing and the automatic warnings."""
    by_id = {s.id: i for i, s in enumerate(ss)}
    blocks, cids = [], {}
    for n, c in enumerate(sorted(clips, key=lambda c: c.start), 1):
        cid = f"C{n}"
        cids[cid] = c
        ia, ib = by_id[c.start_sentence], by_id[c.end_sentence]
        lo, hi = max(0, ia - CTX_BEFORE), min(len(ss) - 1, ib + CTX_AFTER)
        lines = [("*" if ss[j].id == c.payoff_sentence else ">" if ia <= j <= ib else " ") + " " + _line(ss[j], heat, marks)
                 for j in range(lo, hi + 1)]
        missing = f"\nMissing for a cold viewer: {c.context}" if c.context else ""
        checks = "".join(f"\nCheck: {w}" for w in lint(c, ss))
        fm = "" if c.focus_match is None else " · matches the request" if c.focus_match else " · not about the request"
        blocks.append(f"### {cid} · {c.duration:.0f}s · first-pass score {c.score:g}{fm} · \"{c.title}\"\n"
                      f"Opens with: \"{_opening(c, ss)}\"{missing}{checks}\n" + "\n".join(lines))
    return blocks, cids


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

def validate(raw: list[dict], sentences: list[Sentence], duration: float, sc: SelectCfg, heat=None,
             final: bool = False, marks: dict[int, Mark] | None = None,
             focus: Focus | None = None) -> tuple[list[Clip], dict[str, int]]:
    """Turn LLM picks into clean clips: snap/fit length, keep the payoff in, cut lead-in filler, drop low
    scores and duplicates, rank. final=True (after the editor pass) also applies the hook/standalone minimums."""
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
        ia, ib = by_id[a], by_id[b]
        payoff = _int(r.get("payoff_id"))
        ip = by_id.get(payoff) if payoff is not None else None
        max_s = sc.max_clip_s - PAD_LEAD - PAD_TAIL_PAYOFF  # room for the padding, so the final clip respects max_clip_s
        if ip is not None and ip >= ia:
            if ip > ib:   # the clip stops before its own payoff: extend it
                ib = ip
            # too long: drop opening lines rather than the payoff
            while ia < ip and sentences[ib].end - sentences[ia].start > max_s:
                ia += 1
        else:
            ip = None
        ia, ib = fit_span(sentences, ia, ib, sc.min_clip_s, max_s)
        if ia is not None and sc.trim_promo:
            ib = _drop_promo_tail(sentences, ia, ib, ip, sc.min_clip_s)
        if ia is None:
            reject(f"can't fit {sc.min_clip_s:g}-{sc.max_clip_s:g}s"); continue
        scores = ClipScores(
            hook=_clamp(r.get("hook_score")), standalone=_clamp(r.get("standalone_score")),
            payoff=_clamp(r.get("payoff_score")), energy=_clamp(r.get("energy_score")),
        )
        start, end = pad(sentences, ia, ib, duration, tail=PAD_TAIL_PAYOFF if ip == ib else None)
        end = _keep_reaction(sentences, ib, start, end, duration, sc, marks)
        if sc.trim_lead_fillers:
            k = lead_trim(sentences[ia])
            if k:
                w0 = sentences[ia].words[k]
                t0 = max(w0.start - PAD_LEAD, sentences[ia].words[k - 1].end)
                if end - t0 >= sc.min_clip_s:
                    start = round(max(0.0, t0), 3)
        span = sentences[ia:ib + 1]
        opening = _words_from(span, start)
        h = heat(start, end) if heat else None
        # viewers' replays are the strongest signal there is: lift the clips they rewatched
        bonus = sc.heat_bonus * max(0.0, (h - 0.4) / 0.6) if h is not None else 0.0
        loop = bool(r.get("loops"))
        score = round(min(10.0, scores.overall() + bonus + (sc.loop_bonus if loop else 0.0)), 1)
        if score < sc.min_score:
            reject("score below threshold"); continue
        if final and scores.hook < sc.min_hook_score:
            reject("weak hook"); continue
        if final and scores.standalone < sc.min_standalone_score:
            reject("needs context from the video"); continue
        hit = bool(r.get("matches_focus")) if focus else None
        if focus and focus.mode == "only" and not hit:
            reject("outside your focus"); continue
        cands.append(Clip(
            id=clip_id(start, end), rank=0, start=start, end=end,
            start_sentence=span[0].id, end_sentence=span[-1].id,
            title=str(r.get("title") or "Untitled").strip()[:80],
            hook=" ".join(opening[:40]) if opening else str(r.get("hook") or span[0].text).strip(),
            reason=str(r.get("reason") or "").strip(),
            scores=scores, score=score,
            emphasis_words=[str(w) for w in (r.get("emphasis_words") or [])][:4],
            text=" ".join(opening),
            payoff_sentence=sentences[ip].id if ip is not None and ia <= ip <= ib else None,
            show_hook=not bool(r.get("opening_states_premise")),
            context=str(r.get("context") or "").strip()[:200],
            loop=loop,
            hook_text=str(r.get("hook_text") or "").strip()[:80],
            description=str(r.get("description") or "").strip()[:500],
            hashtags=[_tag(x) for x in (r.get("hashtags") or []) if _tag(x)][:6],
            heat=h,
            focus_match=hit,
        ))

    kept: list[Clip] = []
    # with a focus note, the moments asked for come first (and are the last to fall off at max_clips)
    for c in sorted(cands, key=lambda c: (not c.focus_match if focus else False, -c.score, -c.scores.hook, c.start)):
        if any(_overlap(c, k) > sc.max_overlap_s for k in kept):
            reject("duplicate / overlapping"); continue
        if len(kept) >= sc.max_clips:
            reject("over max_clips"); continue
        kept.append(c)
    for i, c in enumerate(kept, 1):
        c.rank = i
    return kept, rejected


def _keep_reaction(sentences: list[Sentence], ib: int, start: float, end: float, duration: float,
                   sc: SelectCfg, marks: dict[int, Mark] | None) -> float:
    """A crash / scream / laugh right after the last line is the ending: keep it in the clip."""
    m = marks.get(sentences[ib].id) if marks else None
    if not m or not m.burst_after_s:
        return end
    nxt = sentences[ib + 1].start if ib + 1 < len(sentences) else (duration or float("inf"))
    want = sentences[ib].end + m.burst_after_s + PAD_TAIL
    room = start + sc.max_clip_s
    return round(max(end, min(want, nxt, room, duration or want)), 3)


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def lead_trim(s: Sentence) -> int:
    """How many leading filler words to cut from a clip's first sentence (keeps at least 3 words)."""
    ws = [_norm(w.text) for w in s.words]
    k = 0
    while k < len(ws):
        if tuple(ws[k:k + 2]) in LEAD_PHRASES:
            k += 2
        elif ws[k] in LEAD_FILLERS or not ws[k]:
            k += 1
        else:
            break
    return k if 0 < k <= len(ws) - 3 else 0


def _words_from(span: list[Sentence], start: float) -> list[str]:
    """The clip's spoken words from `start` on (after the lead-in trim)."""
    return [w.text for s in span for w in s.words if w.end > start + 0.05] or [s.text for s in span]


def _opening(c: Clip, ss: list[Sentence], n: int = 14) -> str:
    return " ".join(c.text.split()[:n])


def lint(c: Clip, ss: list[Sentence]) -> list[str]:
    """Automatic warnings about a clip's edges, shown to the editor pass."""
    out = []
    words = c.text.split()
    first = _norm(words[0]) if words else ""
    if first in DANGLING:
        out.append(f'opens with "{words[0]}": will a new viewer know who/what that is?')
    last = next((s for s in ss if s.id == c.end_sentence), None)
    if last and last.text.rstrip().endswith("?"):
        out.append("ends on a question: is it answered inside the clip?")
    if last and last.text.lower().lstrip(" ,.").startswith(TRANSITIONS):
        out.append("last line sounds like a transition to the next segment: end earlier?")
    if last and PROMO.search(last.text):
        out.append("ends on a plug / promotion: end on the payoff instead")
    if c.payoff_sentence is None:
        out.append("no payoff line marked: where does it land?")
    elif c.end_sentence - c.payoff_sentence > 2:
        out.append("runs on after the payoff: end sooner?")
    if c.duration > 45:
        out.append(f"{c.duration:.0f}s is long for a short (top Shorts are 25-45 s): does every line earn its place?")
    return out


def _drop_promo_tail(ss: list[Sentence], ia: int, ib: int, ip: int | None, min_s: float) -> int:
    """Cut trailing plug / sponsor lines (never the payoff, never below the minimum length)."""
    while ib > ia and (ip is None or ib > ip) and PROMO.search(ss[ib].text) and ss[ib - 1].end - ss[ia].start >= min_s:
        ib -= 1
    return ib


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


def select_heuristic(t: Transcript, sc: SelectCfg, heat_fn=None, sig: AudioSignals | None = None,
                     marks: dict[int, Mark] | None = None) -> Selection:
    """Score windows that start at each sentence by hook words / punctuation density, plus how loud the
    audio gets in them and whether a reaction follows. Crude but offline."""
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
        p = 5
        if sig:
            e = max(e, round(10 * signals.energy01(sig, s.start, ss[j].end)))
        if marks:
            if marks.get(s.id) and marks[s.id].loud:
                h = min(10, h + 1.5)
            if any(marks.get(x.id) and marks[x.id].burst_after_s for x in ss[i:j + 1]):
                p = 7
        raw.append({"start_id": s.id, "end_id": ss[j].id, "title": s.text[:60], "hook": s.text,
                    "reason": "Offline pick: hook words / punctuation in the opening line"
                              + (", loud audio" if e >= 7 else "") + (", a reaction inside" if p > 5 else "") + ".",
                    "hook_score": h, "standalone_score": 5, "payoff_score": p, "energy_score": e})
    offline = sc.model_copy(update={"max_clips": max(1, min(sc.max_clips, len(ss) // 40 + 3))})
    clips, rejected = validate(raw, ss, t.duration, offline, heat_fn, marks=marks)
    return Selection(source="heuristic", candidates=len(raw), rejected=rejected, clips=clips)
