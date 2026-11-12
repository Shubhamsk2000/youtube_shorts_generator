"""Stage 6: render each edit as a 9:16 short, in ONE ffmpeg run per short.

  segments   one input per segment (accurate seek) -> speed (slowmo) -> scale to the card window
             -> zoom punch / shake (zoompan) -> freeze frame
  joins      hard cuts (concat) or transitions (xfade: flash, zoom, whip, fade, glitch) + audio crossfades
  layout     blurred, dimmed copy of the edit fills 9:16 -> edit in the card window -> tweet card on top
             -> dialogue captions under the card (ASS) -> loudness to -14 LUFS
Outputs are cached by everything that affects them (segments, card, captions, settings).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from common import ffmpeg
from common.paths import resolve
from common.progress import Reporter

from ..card import Layout, layout, render_card
from ..config import Config, RenderCfg
from ..models import Cue, EpisodeMeta, Pick, Segment, Selection, Subs, Word, load_json
from .edit import OUT as SELECTION
from .edit import OVERLAP
from .ingest import SUBS

PREVIEW_DIR = "previews"
WORK_DIR = "render"
XFADE = {k: (name, OVERLAP[k]) for k, name in  # ffmpeg xfade name, length x edit.transition_s
         {"flash": "fadewhite", "zoom": "zoomin", "whip": "smoothleft", "fade": "fade", "glitch": "pixelize"}.items()}
SLOWMO = 0.5
FREEZE_S = 0.6


@dataclass
class Placed:
    seg: Segment
    speed: float
    length: float      # output seconds, after speed + freeze
    out_start: float   # where it starts in the finished short
    overlap: float     # transition overlap with the previous segment


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    sel = load_json(Selection, job_dir / SELECTION)
    out_dir = job_dir / PREVIEW_DIR
    out_dir.mkdir(exist_ok=True)
    names = {p.id: output_name(p, cfg, cfg.preview) for p in sel.picks}
    for f in out_dir.glob("*.mp4"):  # stale previews from an older selection / settings
        if f.name not in names.values():
            f.unlink()
    todo = [p for p in sel.picks if not (out_dir / names[p.id]).exists()]
    if not sel.picks:
        return "Nothing to render"
    if not todo:
        return f"{len(sel.picks)} previews · cached"
    subs = load_json(Subs, job_dir / SUBS)
    t0 = time.monotonic()
    for i, p in enumerate(todo):
        report(i / len(todo), f"Rendering {i + 1}/{len(todo)}: {p.title[:50]}")
        render_pick(job_dir, meta, p, subs.cues, cfg, cfg.preview, out_dir / names[p.id],
                    lambda f, i=i: report((i + f) / len(todo), None))
    return f"{len(sel.picks)} previews · {cfg.preview.width}×{cfg.preview.height} · {(time.monotonic() - t0) / 60:.1f} min"


def output_name(p: Pick, cfg: Config, rs: RenderCfg) -> str:
    av = resolve(cfg.card.avatar) if cfg.card.avatar else None
    avatar = f"{av.stat().st_mtime_ns}-{av.stat().st_size}" if av and av.exists() else None
    key = json.dumps([p.model_dump(mode="json", include={"segments", "tweet", "style", "song"}), avatar,
                      rs.model_dump(mode="json"), cfg.card.model_dump(mode="json"), cfg.captions.model_dump(),
                      cfg.edit.model_dump(), cfg.audio.model_dump(), "v3"], sort_keys=True)
    return f"{p.rank:02d}-{p.id}-{hashlib.sha1(key.encode()).hexdigest()[:6]}.mp4"


# ---------------------------------------------------------------- timeline

def place(p: Pick, cfg: Config) -> list[Placed]:
    out: list[Placed] = []
    t = 0.0
    for i, s in enumerate(p.segments):
        speed = SLOWMO if "slowmo" in s.effects else 1.0
        length = s.duration / speed + (FREEZE_S if "freeze_end" in s.effects else 0.0)
        ov = 0.0
        if i and s.transition_in in XFADE:
            ov = min(cfg.edit.transition_s * XFADE[s.transition_in][1], out[-1].length / 2, length / 2)
        start = t - ov
        out.append(Placed(seg=s, speed=speed, length=length, out_start=start, overlap=ov))
        t = start + length
    return out


def total_length(placed: list[Placed]) -> float:
    return placed[-1].out_start + placed[-1].length if placed else 0.0


def map_cues(placed: list[Placed], cues: list[Cue], min_s: float = 0.35) -> list[Cue]:
    """Subtitle lines on the finished short's timeline (only the parts of lines that are shown)."""
    out: list[Cue] = []
    for i, pl in enumerate(placed):
        s = pl.seg
        vis_end = pl.out_start + pl.length - (placed[i + 1].overlap if i + 1 < len(placed) else 0)
        for c in cues:
            a, b = max(c.start, s.start), min(c.end, s.end)
            if b - a <= 0:
                continue
            lo = pl.out_start + pl.overlap
            oa = max(lo, pl.out_start + (a - s.start) / pl.speed)
            ob = min(vis_end, pl.out_start + (b - s.start) / pl.speed)
            if ob - oa < min_s:
                continue
            words = []
            for w in c.words:  # words fully inside the shown part, on the short's timeline
                if s.start <= w.start and w.end <= s.end:
                    wa = pl.out_start + (w.start - s.start) / pl.speed
                    wb = pl.out_start + (w.end - s.start) / pl.speed
                    if wa >= lo - 0.05 and wb <= vis_end + 0.05:
                        words.append(Word(text=w.text, start=round(wa, 3), end=round(wb, 3)))
            text = " ".join(w.text for w in words) if c.words else c.text
            if text:
                out.append(Cue(start=round(oa, 3), end=round(ob, 3), text=text, words=words))
    return out


# ---------------------------------------------------------------- render

def render_pick(job_dir: Path, meta: EpisodeMeta, p: Pick, cues: list[Cue], cfg: Config, rs: RenderCfg,
                dst: Path, on_progress=lambda f: None) -> None:
    work = job_dir / WORK_DIR
    work.mkdir(exist_ok=True)
    W, H = rs.width, rs.height
    lay = layout(cfg.card, W, H, meta.width, meta.height, p.tweet)
    card_png = render_card(cfg.card, lay, work / f"{p.id}-card-{W}.png")
    if p.style == "song" and p.song:
        render_song(meta, p, lay, cfg, rs, card_png, work, dst, on_progress)
        return
    placed = place(p, cfg)
    total = total_length(placed)
    ass = work / f"{p.id}-captions-{W}.ass"
    ass.write_text(captions_ass(map_cues(placed, cues) if cfg.captions.enabled else [], cfg, lay))
    fps = _fps(meta.fps, rs.max_fps)
    args, graph = build_graph(meta, placed, lay, cfg, rs, fps, card_png.name, ass.name, total)
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    ffmpeg.run_with_progress(
        [*args, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", rs.preset, "-crf", str(rs.crf), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", rs.audio_bitrate, "-ar", "48000", "-movflags", "+faststart",
         "-map_chapters", "-1", "-map_metadata", "-1",
         "-t", f"{total:.3f}", str(tmp.resolve())],
        total, on_progress, cwd=work,
    )
    tmp.replace(dst)


def _fps(src: str, max_fps: float) -> str:
    f = Fraction(src).limit_denominator(1001)
    if max_fps and float(f) > max_fps + 0.01:
        f = Fraction(max_fps).limit_denominator(1001)
    return f"{f.numerator}/{f.denominator}"


def build_graph(meta: EpisodeMeta, placed: list[Placed], lay: Layout, cfg: Config, rs: RenderCfg, fps: str,
                card_name: str, ass_name: str, total: float) -> tuple[list[str], str]:
    ec = cfg.edit
    vx, vy, VW, VH = lay.video
    args: list[str] = []
    chains: list[str] = []
    for i, pl in enumerate(placed):
        s = pl.seg
        args += ["-ss", f"{s.start:.3f}", "-t", f"{s.duration:.3f}", "-i", meta.source]
        v = [f"[{i}:v:0]setpts=PTS-STARTPTS"]
        if pl.speed != 1.0:
            v.append(f"setpts=PTS/{pl.speed}")
        v += [f"fps={fps}", f"scale={VW}:{VH}:flags=lanczos", "setsar=1"]
        zp = _zoompan(s, pl, VW, VH, fps, ec, lay.k)
        if zp:
            v.append(zp)
        if "flash_in" in s.effects:
            v.append("fade=t=in:st=0:d=0.15:color=white")
        if "freeze_end" in s.effects:
            v.append(f"tpad=stop_mode=clone:stop_duration={FREEZE_S}")
        v += ["format=yuv420p", "settb=AVTB"]
        chains.append(",".join(v) + f"[v{i}]")
        a = [f"[{i}:a:{meta.audio_index}]asetpts=PTS-STARTPTS", "aresample=48000",
             "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if pl.speed != 1.0:
            a.append(f"atempo={pl.speed}")
        if "freeze_end" in s.effects:
            a.append(f"apad=pad_dur={FREEZE_S}")
        a.append(f"atrim=0:{pl.length:.3f}")
        chains.append(",".join(a) + f"[a{i}]")

    # joins
    cur_v, cur_a, cur_len = "v0", "a0", placed[0].length
    for i in range(1, len(placed)):
        pl = placed[i]
        nv, na = f"vj{i}", f"aj{i}"
        if pl.overlap > 0:
            name = XFADE[pl.seg.transition_in][0]
            off = cur_len - pl.overlap
            chains.append(f"[{cur_v}][v{i}]xfade=transition={name}:duration={pl.overlap:.3f}:offset={off:.3f}[{nv}]")
            chains.append(f"[{cur_a}][a{i}]acrossfade=d={pl.overlap:.3f}:c1=tri:c2=tri[{na}]")
            cur_len += pl.length - pl.overlap
        else:
            chains.append(f"[{cur_v}][{cur_a}][v{i}][a{i}]concat=n=2:v=1:a=1[{nv}][{na}]")
            cur_len += pl.length
        cur_v, cur_a = nv, na

    # layout: blurred background, the edit in the card window, card on top, captions
    n = len(placed)
    args += ["-loop", "1", "-framerate", fps, "-t", f"{total:.3f}", "-i", card_name]
    bw, bh = _even_div(lay.W, 4), _even_div(lay.H, 4)
    blur = max(1, rs.bg_blur // 4)
    chains += [
        f"[{cur_v}]split=2[e1][e2]",
        f"[e1]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur={blur}:2,"
        f"scale={lay.W}:{lay.H},lutyuv=y=val*{rs.bg_dim},setsar=1[bg]",
        f"[bg][e2]overlay={vx}:{vy}[base]",
        f"[base][{n}:v]overlay=0:0:format=auto[carded]",
        f"[carded]subtitles={ass_name},format=yuv420p[vout]",
        f"[{cur_a}]loudnorm=I={cfg.audio.loudness_lufs}:TP={cfg.audio.true_peak}:LRA=11,aresample=48000[aout]",
    ]
    return args, ";\n".join(chains)


def render_song(meta: EpisodeMeta, p: Pick, lay: Layout, cfg: Config, rs: RenderCfg, card_png: Path, work: Path,
                dst: Path, on_progress) -> None:
    """Song edit, in two steps so memory stays flat however many shots there are:
      1. each shot becomes a small clip of EXACTLY its frame count (ends on its beat), one ffmpeg run each
      2. one pass joins them, adds the blurred background, the card and the song (the soundtrack)"""
    from ..music import MUSIC_DIR

    song = p.song
    total = song.duration
    fps = _fps(meta.fps, rs.max_fps)
    rate = Fraction(fps)
    vx, vy, VW, VH = lay.video
    bounds = [round(s.out_start * rate) for s in p.segments] + [round(total * rate)]
    shots_dir = work / f"{p.id}-shots-{lay.W}"
    shutil.rmtree(shots_dir, ignore_errors=True)
    shots_dir.mkdir(parents=True)
    listing = []
    try:
        for i, s in enumerate(p.segments):
            frames = bounds[i + 1] - bounds[i]
            if frames <= 0:
                continue
            speed = SLOWMO if "slowmo" in s.effects else 1.0
            v = []
            if speed != 1.0:
                v.append(f"setpts=PTS/{speed}")
            v += [f"fps={fps}", f"scale={VW}:{VH}:flags=lanczos", "setsar=1"]
            pl = Placed(seg=s, speed=speed, length=frames / float(rate), out_start=0.0, overlap=0.0)
            if zp := _zoompan(s, pl, VW, VH, fps, cfg.edit, lay.k):
                v.append(zp)
            if "flash_in" in s.effects:
                v.append("fade=t=in:st=0:d=0.12:color=white")
            v += ["tpad=stop_mode=clone:stop_duration=2", "format=yuv420p"]  # never short of frames
            shot = shots_dir / f"{i:04d}.mkv"
            ffmpeg.run(["-ss", f"{s.start:.3f}", "-t", f"{s.duration + 0.5:.3f}", "-i", meta.source,
                        "-map", "0:v:0", "-an", "-sn", "-vf", ",".join(v), "-frames:v", str(frames),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-r", fps, str(shot)])
            listing.append(f"file '{shot.name}'")
            on_progress(0.6 * (i + 1) / len(p.segments))
        (shots_dir / "shots.txt").write_text("\n".join(listing) + "\n")

        bw, bh = _even_div(lay.W, 4), _even_div(lay.H, 4)
        blur = max(1, rs.bg_blur // 4)
        fade_out = min(1.0, total / 6)
        graph = ";".join([
            f"[0:v]setpts=N/({fps})/TB,fps={fps},split=2[e1][e2]",
            f"[e1]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur={blur}:2,"
            f"scale={lay.W}:{lay.H},lutyuv=y=val*{rs.bg_dim},setsar=1[bg]",
            f"[bg][e2]overlay={vx}:{vy}[base]",
            "[base][1:v]overlay=0:0:format=auto,format=yuv420p[vout]",
            f"[2:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,afade=t=in:d=0.08,"
            f"afade=t=out:st={total - fade_out:.3f}:d={fade_out:.3f},"
            f"loudnorm=I={cfg.audio.loudness_lufs}:TP={cfg.audio.true_peak}:LRA=11,aresample=48000[aout]",
        ])
        tmp = dst.with_name(dst.stem + ".tmp.mp4")
        ffmpeg.run_with_progress(
            ["-f", "concat", "-safe", "0", "-i", str((shots_dir / "shots.txt").resolve()),
             "-loop", "1", "-framerate", fps, "-t", f"{total:.3f}", "-i", str(card_png.resolve()),
             "-ss", f"{song.start:.3f}", "-t", f"{total:.3f}", "-i", str((MUSIC_DIR / song.id).resolve()),
             "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", rs.preset, "-crf", str(rs.crf), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", rs.audio_bitrate, "-ar", "48000", "-movflags", "+faststart",
             "-map_chapters", "-1", "-map_metadata", "-1", "-t", f"{total:.3f}", str(tmp.resolve())],
            total, lambda f: on_progress(0.6 + 0.4 * f),
        )
        tmp.replace(dst)
    finally:
        shutil.rmtree(shots_dir, ignore_errors=True)


def _even_div(x: int, d: int) -> int:
    return max(2, (x // d) // 2 * 2)


def _zoompan(s: Segment, pl: Placed, VW: int, VH: int, fps: str, ec, k: float) -> str | None:
    """Zoom punch (fast in, eased out) and/or a short decaying shake, as one zoompan."""
    punch = "zoom_punch" in s.effects
    shake = "shake" in s.effects
    if not (punch or shake):
        return None
    p = ((s.punch_at if s.punch_at is not None else s.start + s.duration * 0.3) - s.start) / pl.speed
    z = "1"
    if punch:
        a, up, down = ec.punch_zoom, 0.06, max(0.1, ec.punch_s)
        z += (f"+{a}*if(lt(it,{p:.3f}),0,if(lt(it,{p + up:.3f}),(it-{p:.3f})/{up},"
              f"pow(max(0,1-(it-{p + up:.3f})/{down}),2)))")
    dx = dy = "0"
    if shake:
        amp = ec.shake_px * k * 2  # zoompan works on the 2x upscaled frame
        decay = f"max(0,1-(it-{p:.3f})/0.6)*gte(it,{p:.3f})"
        dx, dy = f"{amp:.1f}*sin(it*61)*{decay}", f"{amp:.1f}*cos(it*47)*{decay}"
        z += f"+{(4 * ec.shake_px * k) / VW:.4f}"  # a little headroom so shake never shows an edge
    return (f"scale={VW * 2}:{VH * 2}:flags=bicubic,"
            f"zoompan=z='{z}':x='iw/2-iw/zoom/2+{dx}':y='ih/2-ih/zoom/2+{dy}':d=1:s={VW}x{VH}:fps={fps}")


# ---------------------------------------------------------------- captions

def captions_ass(cues: list[Cue], cfg: Config, lay: Layout) -> str:
    cc = cfg.captions
    k = lay.k
    size = round(cc.size * k)
    margin_v = round(lay.caption_y + cc.gap * k)
    side = round(70 * k)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {lay.W}
PlayResY: {lay.H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cc.font},{size},{_ass_color(cc.color)},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{cc.outline * k:.1f},{2 * k:.1f},8,{side},{side},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    hi = _ass_color(cc.highlight_color)
    # every word of every cue in time order: a group's last word must vanish before the next word appears
    groups = [g for c in cues if c.words and cc.word_by_word for g in word_groups(c.words, cc.max_words, cc.max_chars)]
    starts = sorted(w.start for g in groups for w in g)
    for group in groups:
        for i, w in enumerate(group):  # one event per word: the group, current word highlighted
            if i + 1 < len(group):
                end = group[i + 1].start
            else:
                k = bisect.bisect_right(starts, w.start)
                end = min(w.end + 0.12, starts[k] if k < len(starts) else 1e9)
            parts = [(f"{{\\c{hi}}}{_esc(x.text)}{{\\c}}" if x is w else _esc(x.text)) for x in group]
            lines.append(f"Dialogue: 0,{_ts(w.start)},{_ts(max(end, w.start + 0.04))},Cap,,0,0,0,,"
                         + " ".join(parts))
    for c in cues:
        if not (c.words and cc.word_by_word):
            lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Cap,,0,0,0,,{_esc(c.text)}")
    return head + "\n".join(lines) + "\n"


def word_groups(words: list[Word], max_words: int, max_chars: int, pause_s: float = 0.35) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for w in words:
        g = groups[-1] if groups else None
        if (g and len(g) < max_words and len(" ".join(x.text for x in g) + " " + w.text) <= max_chars
                and w.start - g[-1].end <= pause_s and g[-1].text[-1:] not in ".?!"):
            g.append(w)
        else:
            groups.append([w])
    return groups


def _esc(t: str) -> str:
    return t.replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"
