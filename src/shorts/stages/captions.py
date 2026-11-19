"""Stage 5: pacing + word-by-word captions + hook text -> edits/<id>.json, captions/<id>.ass.

Pacing first (which pauses to cut, see pacing.py), then captions timed on the edited timeline.
One caption "line" holds up to max_words words; it is drawn once per word, with that word
highlighted, so the highlight follows the speech. In facecam "split" shots the line sits on the seam
between cam and screen. The hook text (Gemini's on-screen line) is shown at the top for the first
seconds (below the corner cam in "pip" shots). Everything is laid out on a 1080x1920 canvas and libass scales it to the output size.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import CaptionCfg, Config, HookCfg, with_job_layout
from ..text import merge_fragments
from ..models import Clip, Selection, Transcript, VideoMeta, load_json
from common.progress import Reporter
from . import pacing
from .canvas import canvas
from . import framing
from .reframe import pip_bottom, split_panel_frac, visible_text_boxes
from .select import OUT as SELECTION

CAPTIONS_DIR = "captions"
W, H = 1080, 1920


@dataclass
class CapWord:
    text: str          # as displayed
    start: float       # seconds from the clip start
    end: float
    sentence_start: bool = False
    emphasis: bool = False
    ends_clause: bool = False  # original text ended in . , ? ! ; :


@dataclass
class CapLine:
    words: list[CapWord]
    start: float
    end: float


def ass_path(job_dir: Path, clip: Clip) -> Path:
    return job_dir / CAPTIONS_DIR / f"{clip.id}.ass"


def run(job_dir: Path, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    out_dir = job_dir / CAPTIONS_DIR
    out_dir.mkdir(exist_ok=True)
    (job_dir / pacing.EDITS_DIR).mkdir(exist_ok=True)
    cfg = with_job_layout(cfg, job_dir)
    sel = load_json(Selection, job_dir / SELECTION)
    t = load_json(Transcript, job_dir / "transcript.json")
    meta = load_json(VideoMeta, job_dir / "meta.json")
    cc = cfg.captions
    wanted, wanted_edits = set(), set()
    n_lines, n_flash, cut_s = 0, 0, 0.0
    for i, c in enumerate(sel.clips):
        report(i / max(len(sel.clips), 1), f"Clip {i + 1}/{len(sel.clips)}")
        keep, flash, n = build_clip(job_dir, cfg, t, meta, c)
        if keep or flash:
            wanted_edits.add(pacing.edits_path(job_dir, c).name)
        if keep:
            cut_s += c.duration - pacing.kept_duration(keep, c.duration)
        n_flash += bool(flash)
        n_lines += n
        if ass_path(job_dir, c).exists():
            wanted.add(ass_path(job_dir, c).name)
    for f in out_dir.iterdir():
        if f.name not in wanted:
            f.unlink()
    for f in (job_dir / pacing.EDITS_DIR).iterdir():
        if f.name not in wanted_edits:
            f.unlink()
    parts = [f"{len(sel.clips)} clips"]
    if cc.enabled:
        parts.append(f"{n_lines} caption lines")
    if n_flash:
        parts.append(f"{n_flash} cold open{'s' * (n_flash != 1)}")
    if cfg.pacing.enabled:
        parts.append(f"{cut_s:.0f}s of pauses cut")
    return " · ".join(parts)


def build_clip(job_dir: Path, cfg: Config, t: Transcript, meta: VideoMeta, c: Clip
               ) -> tuple[list[pacing.Span] | None, pacing.Span | None, int]:
    """Pacing + cold open + captions for one clip -> edits/<id>.json and captions/<id>.ass.
    Returns (keep, flash, caption lines); no .ass is left when the clip has neither captions nor a hook."""
    cfg = with_job_layout(cfg, job_dir)
    cc = cfg.captions
    cv = canvas(cfg, W, H, c)  # tweet card: everything sits inside its video window
    keep = None
    audio = (pacing.ClipAudio(job_dir / "audio.wav", c.start, c.duration)
             if cfg.pacing.enabled or cfg.cold_open.enabled else None)
    if cfg.pacing.enabled:
        keep = pacing.keep_segments(spoken_spans(t, c), c.duration, cfg.pacing, audio)
    flash = pacing.flash_window(c, t, audio, cfg.cold_open)
    pacing.save_edits(job_dir, c, keep, flash)
    lead = flash[1] - flash[0] if flash else 0.0   # the cold open plays before the clip's own timeline

    lines = build_lines(edit_words(clip_words(t, c, cc), keep, c.duration, cc, lead), cc) if cc.enabled else []
    hook = hook_text(c) if cfg.hook.enabled and (c.show_hook or not cfg.hook.auto) else ""
    if cv.framed:
        hook = ""  # the tweet text is the hook
    p = ass_path(job_dir, c)
    if not lines and not hook:
        p.unlink(missing_ok=True)
        return keep, flash, 0
    plan = framing.load_plan(job_dir, c, cfg, meta)
    win_aspect = cv.win[2] / cv.win[3]

    def seam(shot) -> int | None:
        """Stacked facecam shot -> y (canvas px) of the line between the cam panel and the gameplay."""
        if shot.mode == "split" and shot.cams and cfg.reframe.screen_layout == "split":
            return cv.y(split_panel_frac(shot.cams, meta.width, meta.height, cfg.reframe, win_aspect))
        return None

    def line_y(start: float) -> int | None:
        """Facecam split shot -> caption centred on the seam between cam and screen; otherwise moved off
        any burned-in text it would cover. None = the default position."""
        default = cv.y(cc.position) - round(cc.size * 0.6) if cv.framed else None
        if plan is None:
            return default
        fi = int(pacing.unmap(max(0.0, start - lead), keep) * plan.fps)
        shot = plan.shot_at(fi)
        if (y := seam(shot)) is not None:
            return y
        if cv.framed:  # source text positions aren't mapped into the card window
            return default
        return avoid_y(visible_text_boxes(plan, fi, cfg.reframe), cc)

    hook_min_y = None
    if hook and plan is not None:  # keep the hook clear of the facecam (corner or top panel) while it shows
        spans = [(0, int(pacing.unmap(min(cfg.hook.duration_s, c.duration), keep) * plan.fps) + 1)]
        if flash:  # the cold open plays first, from the payoff's frames
            spans.append((int(flash[0] * plan.fps), int(flash[1] * plan.fps) + 1))
        bottoms = []
        for sh in plan.shots:
            if not any(sh.start < e and s < sh.end for s, e in spans):
                continue
            if (b := pip_bottom(sh, plan.src_w, plan.src_h, cfg.reframe, H)) is not None:
                bottoms.append(b)
            elif (y := seam(sh)) is not None:  # below the caption line that sits on the seam
                bottoms.append(y + round(cc.size * 0.6))
        hook_min_y = max(bottoms) if bottoms else None
    text = to_ass(lines, cc, hook, cfg.hook, line_y, lead=lead, hook_min_y=hook_min_y)
    if not p.exists() or p.read_text() != text:  # unchanged files keep their render cache valid
        p.write_text(text)
    return keep, flash, len(lines)


def refresh_clip(job_dir: Path, cfg: Config, c: Clip) -> None:
    """Rebuild one clip's edits + captions with the current settings (e.g. the cold open was switched off
    after the captions were made: the old .ass is shifted behind a flash that no longer plays)."""
    build_clip(job_dir, cfg, load_json(Transcript, job_dir / "transcript.json"),
               load_json(VideoMeta, job_dir / "meta.json"), c)

def spoken_spans(t: Transcript, clip: Clip) -> list[tuple[float, float]]:
    """(start, end) of every word in the clip, relative to the clip start (for pacing)."""
    return [(max(0.0, w.start - clip.start), w.end - clip.start) for s in t.sentences
            if clip.start_sentence <= s.id <= clip.end_sentence for w in s.words if w.end > clip.start]


def edit_words(words: list[CapWord], keep: list[tuple[float, float]] | None, dur: float,
               cc: CaptionCfg, lead: float = 0.0) -> list[CapWord]:
    """Move caption words onto the output timeline: pauses removed, and `lead` seconds of cold open in front."""
    if not keep and not lead:
        return words
    moved = [CapWord(w.text, pacing.remap(w.start, keep) + lead, pacing.remap(w.end, keep) + lead,
                     w.sentence_start, w.emphasis, w.ends_clause) for w in words]
    return fix_timing(moved, pacing.kept_duration(keep, dur) + lead, cc.min_word_s)


def avoid_y(boxes: list[tuple[float, float, float, float]], cc: CaptionCfg) -> int | None:
    """Centre y (px, 1080x1920 canvas) for a caption line that would cover burned-in text: the nearest free
    spot above or below it inside the safe zone. None = the default position is fine (or nothing fits)."""
    if not boxes or not cc.avoid_text:
        return None
    block = cc.size * 1.25
    bottom = cc.position * H
    top = bottom - block
    m = cc.text_margin

    def covers(t: float, b: float) -> bool:
        return any(y0 * H - m < b and y1 * H + m > t for _, y0, _, y1 in boxes)

    if not covers(top, bottom):
        return None
    lo, hi = cc.safe_top * H + block / 2, cc.safe_bottom * H - block / 2
    cands = [c for _, y0, _, y1 in boxes for c in (y0 * H - m - block / 2, y1 * H + m + block / 2)]
    free = [c for c in cands if lo <= c <= hi and not covers(c - block / 2, c + block / 2)]
    if not free:
        return None
    default = bottom - block / 2
    best = min(free, key=lambda c: abs(c - default))
    return math.ceil(best) if best > default else math.floor(best)   # round away from the text, never into it


def hook_text(c: Clip) -> str:
    return (c.hook_text or c.title or "").strip()


# ---------------------------------------------------------------- words (pure)

def clip_words(t: Transcript, clip: Clip, cc: CaptionCfg) -> list[CapWord]:
    """The clip's words, timed relative to the clip, cleaned up for display."""
    off = cc.offset_ms / 1000
    dur = clip.end - clip.start
    words: list[CapWord] = []
    for s in t.sentences:
        if not (clip.start_sentence <= s.id <= clip.end_sentence):
            continue
        for k, w in enumerate(merge_fragments(s.words)):   # also fixes transcripts made before a merge rule
            text = display_text(w.text, cc.uppercase)
            if not text:
                continue
            words.append(CapWord(text, w.start - clip.start + off, w.end - clip.start + off,
                                 sentence_start=k == 0, ends_clause=bool(re.search(r"[.,?!;:]$", w.text.strip()))))
    mark_emphasis(words, clip.emphasis_words)
    if cc.emphasize_numbers:
        for w in words:
            if re.search(r"[$€£₹]|\d", w.text):
                w.emphasis = True
    return fix_timing([w for w in words if w.end > 0 and w.start < dur], dur, cc.min_word_s)


def display_text(text: str, uppercase: bool) -> str:
    """Caption-style text: no trailing , . ; : (keeps ? ! and numbers), safe for ASS."""
    t = text.strip().replace("{", "(").replace("}", ")").replace("\\", "").rstrip(",.;:")
    return t.upper() if uppercase else t


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$%']", "", s.lower())


def mark_emphasis(words: list[CapWord], phrases: list[str]) -> None:
    """Mark words that form one of Gemini's emphasis phrases (matched as a whole sequence)."""
    norm = [_norm(w.text) for w in words]
    for ph in phrases:
        toks = [x for x in (_norm(p) for p in ph.split()) if x]
        if not toks:
            continue
        for i in range(len(words) - len(toks) + 1):
            if norm[i:i + len(toks)] == toks:
                for w in words[i:i + len(toks)]:
                    w.emphasis = True


def fix_timing(words: list[CapWord], dur: float, min_s: float) -> list[CapWord]:
    """Monotonic starts, inside the clip, every word visible for at least min_s."""
    out: list[CapWord] = []
    for i, w in enumerate(words):
        start = max(0.0, w.start, out[-1].start + 0.01 if out else 0.0)
        nxt = words[i + 1].start if i + 1 < len(words) else dur
        end = min(max(w.end, start + min_s), max(nxt, start + min_s), dur)
        if end <= start:
            continue
        out.append(CapWord(w.text, round(start, 3), round(end, 3), w.sentence_start, w.emphasis, w.ends_clause))
    return out


def build_lines(words: list[CapWord], cc: CaptionCfg, bridge_s: float = 0.5, tail_s: float = 0.2) -> list[CapLine]:
    lines: list[list[CapWord]] = []
    cur: list[CapWord] = []
    for w in words:
        if cur:
            chars = sum(len(x.text) for x in cur) + len(cur) + len(w.text)
            if (len(cur) >= cc.max_words or chars > cc.max_chars or w.sentence_start
                    or w.start - cur[-1].end > cc.pause_s):
                lines.append(cur)
                cur = []
        cur.append(w)
        if w.ends_clause:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)

    out: list[CapLine] = []
    for i, ws in enumerate(lines):
        nxt = lines[i + 1][0].start if i + 1 < len(lines) else None
        end = ws[-1].end
        # hold the line through short gaps so it doesn't blink off between lines
        end = nxt if nxt is not None and nxt - end <= bridge_s else end + tail_s
        if nxt is not None:
            end = min(end, nxt)
        out.append(CapLine(ws, ws[0].start, round(end, 3)))
    return out


# ---------------------------------------------------------------- ASS (pure)

def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(s: float) -> str:
    cs = max(0, round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def wrap(text: str, max_chars: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def to_ass(lines: list[CapLine], cc: CaptionCfg, hook: str = "", hc: HookCfg | None = None,
           line_y=None, lead: float = 0.0, hook_min_y: int | None = None) -> str:
    hc = hc or HookCfg()
    margin_v = round(H * (1 - cc.position))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cc.font},{cc.size},{_ass_color(cc.text_color)},{_ass_color(cc.text_color)},&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,{cc.outline:g},{cc.shadow:g},2,70,70,{margin_v},1
Style: Hook,{hc.font},{hc.size},{_ass_color(hc.text_color)},{_ass_color(hc.text_color)},{_ass_color(hc.box_color)},&H80000000,0,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hi, em, base = _ass_color(cc.highlight_color), _ass_color(cc.emphasis_color), _ass_color(cc.text_color)
    pop = round(cc.pop_scale * 100)
    events = []
    if hook:
        text = "\\N".join(display_text(x, False) for x in wrap(hook, hc.max_chars_per_line))
        y = round(hc.position * H)
        if hook_min_y is not None:  # top of the hook box goes below the corner cam
            n = text.count("\\N") + 1
            y = max(y, hook_min_y + 28 + round(n * hc.size * 1.25 / 2) + 16)
        events.append(f"Dialogue: 1,{_ts(0)},{_ts(lead + hc.duration_s)},Hook,,0,0,0,,"
                      f"{{\\an5\\pos({W // 2},{y})\\fad(80,220)\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)}}{text}")
    for line in lines:
        y = line_y(line.start) if line_y else None
        pos = f"{{\\an5\\pos({W // 2},{y})}}" if y is not None else ""
        for i, active in enumerate(line.words):
            a = line.start if i == 0 else active.start
            b = line.words[i + 1].start if i + 1 < len(line.words) else line.end
            if b - a < 0.01:
                continue
            parts = []
            for w in line.words:
                if w is active:  # the spoken word is always the highlight colour; emphasis pops bigger
                    s = round(pop * 1.06) if w.emphasis else pop
                    parts.append(f"{{\\c{hi}\\fscx{s}\\fscy{s}}}{w.text}{{\\r}}")
                elif w.emphasis:
                    parts.append(f"{{\\c{em}}}{w.text}{{\\c{base}}}")
                else:
                    parts.append(w.text)
            events.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{pos}{' '.join(parts)}")
    return head + "\n".join(events) + "\n"
