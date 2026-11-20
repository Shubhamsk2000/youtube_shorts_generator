"""Stage 6: 9:16 render of every selected clip (previews), also used for the final export.

Frames are decoded by ffmpeg at the source height (never above the output height), laid out per
frame in numpy and resized ONCE to the output size (Lanczos), then piped into a second ffmpeg that
sharpens lightly, burns in captions and muxes the audio:
  TRACK/CENTER  moving 9:16 crop window
  MANUAL        the window set in the clip editor: 9:16 fills the screen, a wider one (13:16, 1:1, 16:9 ...)
                is scaled to full width over a blurred copy of the frame
  FIT           whole frame over a blurred copy of itself
  SPLIT         facecam + gameplay/screen: "pip" = gameplay full frame, cam in a top corner;
                "split" = cam panel on top, the screen underneath (reframe.screen_layout)
Pauses removed by the pacing step are skipped frame by frame (video) and with aselect (audio).
Audio: high-pass + loudness normalisation to the Shorts level (two-pass for exports).

Each output is cached by (clip range + render settings + reframe plan + captions + edits).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import AudioCfg, Config, ExportCfg, PreviewCfg, ReframeCfg, with_job_layout
from ..models import Clip, ReframePlan, Selection, Shot, VideoMeta, load_json
from common.progress import Reporter
from common import ffmpeg
from .captions import ass_path, refresh_clip
from .pacing import edits_path, kept_duration, load_edits
from .canvas import Canvas, canvas, canvas_key
from . import framing
from .reframe import (crop_to_aspect, pip_band, pip_geometry, plan_path, screen_band, screen_crop,
                      screen_window, split_panel_frac)
from .select import OUT as SELECTION
from . import sources

CLIPS_DIR = "clips"
FLASH_FADE_S = 0.03
RenderCfg = PreviewCfg | ExportCfg


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "none"


def clip_file(job_dir: Path, clip: Clip, cfg: Config, rs: RenderCfg | None = None) -> str:
    """Cache name: changes whenever the range, settings, framing, captions or edits change."""
    cfg = with_job_layout(cfg, job_dir)
    rs = rs or cfg.preview
    parts = [round(clip.start, 3), round(clip.end, 3), rs.model_dump(mode="json"),
             cfg.audio.model_dump(), _digest(plan_path(job_dir, clip)), _digest(ass_path(job_dir, clip)),
             _digest(edits_path(job_dir, clip)), cfg.reframe.model_dump(mode="json"),
             canvas_key(cfg, clip), "v6"]
    fr = framing.framing_path(job_dir, clip)
    if fr.exists():  # only clips framed in the editor: every other preview and HD file keeps its name
        parts.append(_digest(fr))
    key = json.dumps(parts, sort_keys=True)
    return f"{clip.id}-{hashlib.sha1(key.encode()).hexdigest()[:6]}.mp4"


def refresh_if_stale(job_dir: Path, cfg: Config, c: Clip) -> bool:
    """Edits made with a cold open that has since been switched off: the captions are shifted behind a flash
    that will no longer play (they would run late), so rebuild that clip's edits + captions first."""
    if cfg.cold_open.enabled or not load_edits(job_dir, c)[1]:
        return False
    refresh_clip(job_dir, cfg, c)
    return True


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    cfg = with_job_layout(cfg, job_dir)
    sel = load_json(Selection, job_dir / SELECTION)
    pc = cfg.preview
    out_dir = job_dir / CLIPS_DIR
    out_dir.mkdir(exist_ok=True)

    for c in sel.clips:
        refresh_if_stale(job_dir, cfg, c)
    names = {c.id: clip_file(job_dir, c, cfg) for c in sel.clips}
    wanted = set(names.values())
    for f in out_dir.iterdir():  # stale previews from an earlier selection / settings
        if f.name not in wanted:
            f.unlink()

    todo = [c for c in sel.clips if not (out_dir / names[c.id]).exists()]
    if not sel.clips:
        return "No clips to render"
    if not todo:
        return f"{len(sel.clips)} previews · cached"

    total = sum(c.duration for c in todo)
    done_s = 0.0
    t0 = time.monotonic()
    for i, c in enumerate(todo):
        def prog(f: float, c=c, i=i) -> None:
            frac = (done_s + f * c.duration) / total
            el = time.monotonic() - t0
            eta = f" · ~{(el / frac - el) / 60:.0f} min left" if frac > 0.05 else ""
            report(frac, f"Clip {i + 1}/{len(todo)}: {c.title[:48]}{eta}")

        prog(0.0)
        render_clip(job_dir, meta, c, cfg, pc, out_dir / names[c.id], prog)
        done_s += c.duration

    el = time.monotonic() - t0
    framing = "face-tracked" if cfg.reframe.enabled else "centre crop"
    caps = " + captions" if cfg.captions.enabled else ""
    return (f"{len(sel.clips)} previews · {pc.width}×{pc.height} (export for HD) {framing}{caps} · "
            f"{el / 60:.1f} min ({total / max(el, 1e-6):.1f}× realtime)")


def render_clip(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config, rs: RenderCfg, dst: Path,
                on_progress, two_pass_loudness: bool = False) -> None:
    """Render one clip with everything the earlier stages decided (framing, captions, edits)."""
    cfg = with_job_layout(cfg, job_dir)
    refresh_if_stale(job_dir, cfg, c)
    ass = ass_path(job_dir, c)
    ass = ass if (cfg.captions.enabled or cfg.hook.enabled) and ass.exists() else None
    plan = framing.load_plan(job_dir, c, cfg, meta)
    cv = canvas(cfg, rs.width, rs.height, c, job_dir)
    keep, flash = load_edits(job_dir, c)
    keep = keep if cfg.pacing.enabled else None
    src, lc = sources.resolve(job_dir, meta, c, cfg)  # the clip's own video file, on that file's timeline
    measured = None
    if two_pass_loudness:
        on_progress(0.0)
        measured = measure_loudness(src, lc, keep, cfg.audio, flash)
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    _render(src, tmp, lc, meta, rs, plan, cfg.reframe, cfg.audio, keep, ass, measured, on_progress, flash, cv)
    tmp.replace(dst)


# ---------------------------------------------------------------- audio

def _select_expr(keep: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)


def _pre_audio_filters(keep: list[tuple[float, float]] | None, ac: AudioCfg) -> list[str]:
    f = []
    if keep:
        f += [f"aselect='{_select_expr(keep)}'", "asetpts=N/SR/TB"]
    if ac.highpass_hz > 0:
        f.append(f"highpass=f={ac.highpass_hz}")
    return f


def _audio_graph(src: Path, c: Clip, keep, flash, ac: AudioCfg, tail: list[str],
                 in0: int) -> tuple[list[str], list[str]]:
    """ffmpeg input args and audio filter args for a clip. in0 = index the first audio input will get.
    With a cold open, the payoff snippet is decoded as its own input and concatenated before the (edited) clip."""
    pre = _pre_audio_filters(keep, ac)
    inputs = ["-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", str(src)]
    if not flash:
        return inputs, ["-map", f"{in0}:a:0?", "-af", ",".join([*pre, *tail])]
    fa, fb = flash
    inputs = ["-ss", f"{c.start + fa:.3f}", "-t", f"{fb - fa:.3f}", "-i", str(src), *inputs]
    hp = [f"highpass=f={ac.highpass_hz}"] if ac.highpass_hz > 0 else []
    fd = min(FLASH_FADE_S, (fb - fa) / 4)  # no click where the flash starts and where it cuts to the clip
    fades = [f"afade=t=in:d={fd:.3f}", f"afade=t=out:st={fb - fa - fd:.3f}:d={fd:.3f}"]
    graph = (f"[{in0}:a:0]{','.join([*hp, *fades])}[fl];"
             f"[{in0 + 1}:a:0]{','.join(pre) or 'anull'}[mn];"
             f"[fl][mn]concat=n=2:v=0:a=1,{','.join(tail)}[aout]")
    return inputs, ["-filter_complex", graph, "-map", "[aout]"]


def measure_loudness(src: Path, c: Clip, keep, ac: AudioCfg, flash=None) -> dict | None:
    """First loudnorm pass: measure the clip so the second pass can normalise linearly (no pumping)."""
    tail = [f"loudnorm=I={ac.loudness_lufs}:TP={ac.true_peak}:LRA=11:print_format=json"]
    inputs, af = _audio_graph(src.resolve(), c, keep, flash, ac, tail, in0=0)
    proc = subprocess.run(
        [ffmpeg.require("ffmpeg"), "-hide_banner", "-nostdin", *inputs, "-vn", *af, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    if proc.returncode != 0 or not m:
        return None
    try:
        data = json.loads(m.group(0))
        float(data["input_i"])  # "-inf" for silence -> ValueError
        return data
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def _audio_tail(c: Clip, keep, flash, ac: AudioCfg, measured: dict | None) -> list[str]:
    """Filters after the edit: loudness, resampling, fades (on the output timeline)."""
    dur = kept_duration(keep, c.duration) + (flash[1] - flash[0] if flash else 0.0)
    fade = min(0.15, dur / 4)
    ln = f"loudnorm=I={ac.loudness_lufs}:TP={ac.true_peak}:LRA=11"
    if measured:
        ln += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
               f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
               f":offset={measured['target_offset']}:linear=true")
    return [ln, "aresample=48000", "afade=t=in:d=0.03", f"afade=t=out:st={dur - fade:.3f}:d={fade:.3f}"]


def _audio_codec(bitrate: str) -> list[str]:
    return ["-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-ac", "2"]


def _video_args(rs: RenderCfg) -> list[str]:
    return ["-c:v", "libx264", "-preset", rs.preset, "-crf", str(rs.crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart"]


# ---------------------------------------------------------------- video

def _kept_frames(n_hint: int, fps: float, keep) -> callable:
    if not keep:
        return lambda i: True
    return lambda i: any(a <= (i + 0.5) / fps < b for a, b in keep)


def _render(src: Path, tmp: Path, c: Clip, meta: VideoMeta, rs: RenderCfg, plan: ReframePlan | None,
            rc: ReframeCfg, ac: AudioCfg, keep, ass: Path | None, measured: dict | None, on_progress,
            flash=None, cv: Canvas | None = None) -> None:
    import cv2

    out_w, out_h = rs.width, rs.height
    # decode at the source height (capped at the output): every pixel is resampled once, at the end
    sh = min(meta.height, out_h)
    sh -= sh % 2
    sw = max(2, round(sh * meta.width / meta.height / 2) * 2)
    # previews of 60 fps sources: keep every 2nd frame (dropped in the decoder, before scaling)
    step = max(1, round(meta.fps / rs.max_fps)) if rs.max_fps and meta.fps > rs.max_fps * 1.2 else 1
    out_fps = meta.fps / step
    vf_dec = [f"select='not(mod(n,{step}))'"] if step > 1 else []
    if sh != meta.height:
        vf_dec.append(f"scale={sw}:{sh}:flags={'bilinear' if rs.fast else 'lanczos'}")
    ff = ffmpeg.require("ffmpeg")

    def decoder(start: float, dur: float) -> subprocess.Popen:
        return subprocess.Popen(
            [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src.resolve()),
             "-t", f"{dur:.3f}", "-map", "0:v:0", *(["-vf", ",".join(vf_dec)] if vf_dec else []),
             "-fps_mode", "passthrough", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    dec = decoder(c.start, c.duration)
    vf = ["setsar=1"]
    if rs.sharpen > 0:
        vf.insert(0, f"unsharp=5:5:{rs.sharpen:.2f}:5:5:0")
    if ass:
        vf.append(f"subtitles=filename={ass.name}")  # run from the captions dir: no path escaping
    err = tempfile.TemporaryFile()
    a_inputs, a_filters = _audio_graph(src.resolve(), c, keep, flash, ac, _audio_tail(c, keep, flash, ac, measured), in0=1)
    enc = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", f"{out_fps:.5f}", "-i", "pipe:0",
         *a_inputs, "-map", "0:v", *a_filters, "-vf", ",".join(vf),
         *_audio_codec(rs.audio_bitrate), *_video_args(rs), "-shortest", str(tmp.resolve())],
        stdin=subprocess.PIPE, stderr=err, cwd=ass.parent if ass else None,
    )
    size = sw * sh * 3
    n_est = max(1, round(c.duration * meta.fps))
    kept = _kept_frames(n_est, meta.fps, keep)
    interp = cv2.INTER_LINEAR if rs.fast else None
    draw = Framer(cv or Canvas(out_w, out_h, (0, 0, out_w, out_h)), meta, rc, cv2, interp)
    i = 0  # source frame index (the plan and the edits count source frames)
    try:
        if flash:  # the cold open: the payoff's frames first, then the clip from its start
            fa, fb = flash
            j = int(round(fa * meta.fps / step)) * step
            want = max(1, int(round((fb - fa) * out_fps)))
            dec_f = decoder(c.start + fa, fb - fa)
            try:
                for _ in range(want):
                    buf = dec_f.stdout.read(size)
                    if len(buf) < size:
                        break
                    frame = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
                    out = draw(frame, plan.shot_at(j) if plan else None, j)
                    enc.stdin.write(np.ascontiguousarray(out).tobytes())
                    j += step
            finally:
                dec_f.kill()
                dec_f.stdout.close()
                dec_f.wait()
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            if kept(i):
                frame = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
                out = draw(frame, plan.shot_at(i) if plan else None, i)
                enc.stdin.write(np.ascontiguousarray(out).tobytes())
            i += step
            if i % 30 < step:
                on_progress(min(i / n_est, 1.0))
        enc.stdin.close()
        enc.wait()
    except BaseException:
        enc.kill()
        dec.kill()
        enc.wait()
        raise
    finally:
        dec.stdout.close()
        dec.wait()
    if enc.returncode != 0:
        err.seek(0)
        raise ffmpeg.FFmpegError(err.read().decode(errors="replace").strip()[-2000:] or "encoder failed")
    on_progress(1.0)


def _resize(img: np.ndarray, w: int, h: int, cv2, interp=None) -> np.ndarray:
    if img.shape[1] == w and img.shape[0] == h:
        return img
    if interp is None:
        interp = cv2.INTER_CUBIC if w > img.shape[1] else cv2.INTER_AREA
    return cv2.resize(img, (w, h), interpolation=interp)


def compose(frame: np.ndarray, shot: Shot | None, i: int, out_w: int, out_h: int, meta: VideoMeta,
            rc: ReframeCfg, cv2, interp=None) -> np.ndarray:
    """One output frame (out_w x out_h) from one decoded source frame."""
    sh, sw = frame.shape[:2]
    if shot is not None and shot.mode == "fit":
        return compose_fit(frame, out_w, out_h, rc, cv2, interp)
    if shot is not None and shot.mode == "split" and shot.cams and rc.screen_layout != "track":
        if rc.screen_layout == "pip":
            return compose_pip(frame, shot, out_w, out_h, rc, cv2, interp, i)
        return compose_split(frame, shot, out_w, out_h, meta, rc, cv2, interp, i)
    x = 0.5
    if shot is not None and shot.x:
        x = shot.x[min(max(i - shot.start, 0), len(shot.x) - 1)]
    if shot is not None and shot.a:
        a = shot.a[min(max(i - shot.start, 0), len(shot.a) - 1)]
        if a > out_w / out_h * 1.01:
            return compose_wide(frame, x, a, out_w, out_h, rc, cv2, interp)
    cw = min(sw, round(sh * out_w / out_h))
    left = int(np.clip(round(x * sw - cw / 2), 0, sw - cw))
    ch = min(sh, round(cw * out_h / out_w))
    top = (sh - ch) // 2
    return _resize(frame[top:top + ch, left:left + cw], out_w, out_h, cv2, interp)


def wide_band(a: float, out_w: int, out_h: int, rc: ReframeCfg) -> tuple[int, int]:
    """(top, height) px of a window of shape `a` (wider than the output) scaled to the full output width."""
    h = min(out_h, max(2, round(out_w / a / 2) * 2))
    return int(np.clip(round(rc.fit_y * out_h - h / 2), 0, out_h - h)), h


def compose_wide(frame: np.ndarray, x: float, a: float, out_w: int, out_h: int, rc: ReframeCfg, cv2,
                 interp=None) -> np.ndarray:
    """A full-height window of shape `a` centred on x, at full output width over a blurred copy of the frame."""
    sh, sw = frame.shape[:2]
    cw = min(sw, max(2, round(sh * a)))
    left = int(np.clip(round(x * sw - cw / 2), 0, sw - cw))
    a = cw / sh  # the whole frame is the widest there is
    bg = blurred_bg(frame, out_w, out_h, rc, cv2)
    y, h = wide_band(a, out_w, out_h, rc)
    bg[y:y + h] = _resize(frame[:, left:left + cw], out_w, h, cv2, interp)
    return bg


def blurred_bg(frame: np.ndarray, out_w: int, out_h: int, rc: ReframeCfg, cv2) -> np.ndarray:
    """Centre region of the frame with the output's shape, blurred (cheaply, at 1/8 size) and dimmed."""
    h, w = frame.shape[:2]
    bw = min(w, round(h * out_w / out_h))
    bh = min(h, round(bw * out_h / out_w))
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    small = cv2.resize(frame[y0:y0 + bh, x0:x0 + bw], (max(1, out_w // 8), max(1, out_h // 8)),
                       interpolation=cv2.INTER_AREA)
    k = max(1, rc.fit_blur // 8) * 2 + 1
    small = cv2.GaussianBlur(small, (k, k), 0)
    bg = cv2.resize(small, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    return (bg.astype(np.float32) * rc.fit_dim).astype(np.uint8)


class Framer:
    """Draws one output frame: the layout (compose) into the canvas window; with a tweet card, over a
    blurred copy of the frame and under the card (its transparent window gives the video rounded corners)."""

    def __init__(self, cv: Canvas, meta: VideoMeta, rc: ReframeCfg, cv2, interp=None):
        self.cv, self.meta, self.rc, self.cv2, self.interp = cv, meta, rc, cv2, interp
        self.card = None
        if cv.framed and cv.card is not None:
            rgba = cv2.imread(str(cv.card), cv2.IMREAD_UNCHANGED)
            if rgba is not None and rgba.shape[:2] == (cv.H, cv.W) and rgba.shape[2] == 4:
                a = rgba[..., 3]
                ys, xs = np.nonzero(a)
                if len(ys):  # blend only the card's bounding box
                    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                    al = a[y0:y1, x0:x1, None].astype(np.uint16)
                    pre = rgba[y0:y1, x0:x1, :3].astype(np.uint16) * al
                    self.card = (np.s_[y0:y1, x0:x1], 255 - al, pre)

    def __call__(self, frame: np.ndarray, shot: Shot | None, i: int) -> np.ndarray:
        cv = self.cv
        if not cv.framed:
            return compose(frame, shot, i, cv.W, cv.H, self.meta, self.rc, self.cv2, self.interp)
        x, y, w, h = cv.win
        out = blurred_bg(frame, cv.W, cv.H, self.rc, self.cv2)
        out[y:y + h, x:x + w] = compose(frame, shot, i, w, h, self.meta, self.rc, self.cv2, self.interp)
        if self.card is not None:
            sl, inv, pre = self.card
            out[sl] = ((out[sl].astype(np.uint16) * inv + pre + 127) // 255).astype(np.uint8)
        return out


def compose_fit(frame: np.ndarray, out_w: int, out_h: int, rc: ReframeCfg, cv2, interp=None) -> np.ndarray:
    """Whole frame, full width, over a blurred and dimmed fill of itself."""
    h, w = frame.shape[:2]
    bg = blurred_bg(frame, out_w, out_h, rc, cv2)
    fg_h = round(out_w * h / w / 2) * 2
    fg = _resize(frame, out_w, fg_h, cv2, interp)
    y = int(np.clip(round(rc.fit_y * out_h - fg_h / 2), 0, out_h - fg_h))
    bg[y:y + fg_h] = fg
    return bg


def split_geometry(shot: Shot, sw: int, sh: int, out_w: int, out_h: int, meta: VideoMeta, rc: ReframeCfg
                   ) -> tuple[int, list[tuple[int, int, int, int]], tuple[int, int, int, int]]:
    """(cam panel height, cam source crops (px), screen source crop (px)) for a SPLIT shot."""
    panel_h = int(round(split_panel_frac(shot.cams, meta.width, meta.height, rc, out_w / out_h) * out_h / 2) * 2)
    n = len(shot.cams)
    slot_w = out_w // n
    cams = []
    for k, (x0, y0, x1, y1) in enumerate(shot.cams):
        fx = shot.face_x[k] if k < len(shot.face_x) else 0.5
        fy = shot.face_y[k] if k < len(shot.face_y) else 0.45
        # inset by 1.5% so the cam's own border/rounded corners don't show
        ix, iy = 0.015 * (x1 - x0), 0.015 * (y1 - y0)
        box = (x0 + ix, y0 + iy, x1 - ix, y1 - iy)
        w_k = out_w - slot_w * (n - 1) if k == n - 1 else slot_w
        # a short, wide panel keeps a strip of the cam: centre it a bit above the face so the head isn't cut
        fy = max(0.0, fy - 0.12)
        a, b, c, d = crop_to_aspect(box[0] * sw, box[1] * sh, box[2] * sw, box[3] * sh, w_k / panel_h, fx, fy)
        cams.append((int(a), int(b), max(int(a) + 2, int(c)), max(int(b) + 2, int(d))))

    screen = screen_crop(shot.cams, sw, sh, out_w / (out_h - panel_h))
    return panel_h, cams, screen


def edited_screen(frame: np.ndarray, shot: Shot, i: int, area_w: int, area_h: int, rc: ReframeCfg, cv2,
                  interp=None) -> np.ndarray | None:
    """The gameplay area of a facecam shot whose window was set in the editor (None = automatic): the
    window fills the area, or (wider) sits full width over a blurred copy of the frame."""
    sh, sw = frame.shape[:2]
    win = screen_window(shot, i, sw, sh, area_w / area_h)
    if win is None:
        return None
    (gl, gt, gr, gb), e = win
    y, h = screen_band(e, area_w, area_h)
    if h >= area_h:
        return np.array(_resize(frame[gt:gb, gl:gr], area_w, area_h, cv2, interp), copy=True)
    out = blurred_bg(frame, area_w, area_h, rc, cv2)
    out[y:y + h] = _resize(frame[gt:gb, gl:gr], area_w, h, cv2, interp)
    return out


def compose_split(frame: np.ndarray, shot: Shot, out_w: int, out_h: int, meta: VideoMeta, rc: ReframeCfg,
                  cv2, interp=None, i: int = 0) -> np.ndarray:
    """Facecam(s) on top, the screen underneath."""
    sh, sw = frame.shape[:2]
    panel_h, cams, (gl, gt, gr, gb) = split_geometry(shot, sw, sh, out_w, out_h, meta, rc)
    out = np.empty((out_h, out_w, 3), np.uint8)
    n = len(cams)
    slot_w = out_w // n
    for k, (a, b, c, d) in enumerate(cams):
        x = slot_w * k
        w_k = out_w - slot_w * (n - 1) if k == n - 1 else slot_w
        out[:panel_h, x:x + w_k] = _resize(frame[b:d, a:c], w_k, panel_h, cv2, interp)
    screen = edited_screen(frame, shot, i, out_w, out_h - panel_h, rc, cv2, interp)
    out[panel_h:] = screen if screen is not None else _resize(frame[gt:gb, gl:gr], out_w, out_h - panel_h, cv2, interp)
    return out


def _hex_bgr(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)


def _rounded_mask(w: int, h: int, r: int, cv2) -> np.ndarray:
    """Anti-aliased rounded rectangle, float32 0-1 (drawn at 4x and shrunk)."""
    s = 4
    m = np.zeros((h * s, w * s), np.uint8)
    r = max(0, min(r, w // 2, h // 2)) * s
    if r:
        for cx, cy in ((r, r), (w * s - r - 1, r), (r, h * s - r - 1), (w * s - r - 1, h * s - r - 1)):
            cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    cv2.rectangle(m, (r, 0), (w * s - r - 1, h * s - 1), 255, -1)
    cv2.rectangle(m, (0, r), (w * s - 1, h * s - r - 1), 255, -1)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


@lru_cache(maxsize=16)
def _pip_layers(w: int, h: int, radius: int, border: int, shadow: float, pad: int):
    """Alpha layers for one cam size, on a canvas padded by `pad` px: (shadow, border, cam)."""
    import cv2

    W, H = w + 2 * pad, h + 2 * pad
    cam = np.zeros((H, W), np.float32)
    cam[pad:pad + h, pad:pad + w] = _rounded_mask(w, h, radius, cv2)
    ring = np.zeros((H, W), np.float32)
    if border:
        ring[pad - border:pad + h + border, pad - border:pad + w + border] = _rounded_mask(
            w + 2 * border, h + 2 * border, radius + border, cv2)
    outer = np.maximum(ring, cam)
    sh = np.zeros((H, W), np.float32)
    if shadow > 0:
        off = max(1, pad // 3)
        sh[off:, :] = outer[:H - off, :]  # cast downwards
        k = max(3, (pad // 2) * 2 + 1)
        sh = cv2.GaussianBlur(sh, (k, k), 0) * shadow
    return sh[..., None], ring[..., None], cam[..., None]


def compose_pip(frame: np.ndarray, shot: Shot, out_w: int, out_h: int, rc: ReframeCfg, cv2, interp=None,
                i: int = 0) -> np.ndarray:
    """Gameplay/screen fills the frame; the facecam sits in a top corner (rounded, bordered, shadowed)."""
    sh, sw = frame.shape[:2]
    (gl, gt, gr, gb), cams = pip_geometry(shot, sw, sh, out_w, out_h, rc)
    by, bh = pip_band(out_w, out_h, rc)
    edited = edited_screen(frame, shot, i, out_w, out_h, rc, cv2, interp)
    if edited is not None:
        out = edited
    elif bh >= out_h:
        out = _resize(frame[gt:gb, gl:gr], out_w, out_h, cv2, interp)
        out = np.array(out, copy=True)  # _resize may hand back a view of the decoded frame
    else:  # zoomed out: the gameplay full width, over a blurred and dimmed fill of the same crop
        screen = frame[gt:gb, gl:gr]
        small = cv2.resize(screen, (max(1, out_w // 8), max(1, out_h // 8)), interpolation=cv2.INTER_AREA)
        k = max(1, rc.fit_blur // 8) * 2 + 1
        small = cv2.GaussianBlur(small, (k, k), 0)
        out = cv2.resize(small, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        out = (out.astype(np.float32) * rc.fit_dim).astype(np.uint8)
        out[by:by + bh] = _resize(screen, out_w, bh, cv2, interp)
    scale = out_w / 1080
    border = round(rc.pip_border * scale)
    pad = border + max(4, round(18 * scale))
    color = np.array(_hex_bgr(rc.pip_border_color), np.float32)
    for (a, b, c, d), (x, y, w, h) in cams:
        shadow, ring, alpha = _pip_layers(w, h, round(rc.pip_radius * w), border, rc.pip_shadow, pad)
        cam = np.zeros((h + 2 * pad, w + 2 * pad, 3), np.float32)
        cam[pad:pad + h, pad:pad + w] = _resize(frame[b:d, a:c], w, h, cv2, interp)
        # clip the padded layer to the canvas
        X0, Y0 = x - pad, y - pad
        ox0, oy0 = max(0, -X0), max(0, -Y0)
        ox1 = min(cam.shape[1], out_w - X0)
        oy1 = min(cam.shape[0], out_h - Y0)
        if ox1 <= ox0 or oy1 <= oy0:
            continue
        region = out[Y0 + oy0:Y0 + oy1, X0 + ox0:X0 + ox1].astype(np.float32)
        sl = np.s_[oy0:oy1, ox0:ox1]
        region *= 1 - shadow[sl]
        region = region * (1 - ring[sl]) + color * ring[sl]
        region = region * (1 - alpha[sl]) + cam[sl] * alpha[sl]
        out[Y0 + oy0:Y0 + oy1, X0 + ox0:X0 + ox1] = np.clip(region + 0.5, 0, 255).astype(np.uint8)
    return out


def render_debug(src: Path, dst: Path, c: Clip, meta: VideoMeta, plan: ReframePlan) -> None:
    """16:9 debug video: face boxes, the 9:16 window and each shot's mode."""
    import cv2

    from .reframe import crop_width

    h = 360
    w = round(h * meta.width / meta.height / 2) * 2
    ff = ffmpeg.require("ffmpeg")
    dec = subprocess.Popen(
        [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{c.start:.3f}", "-i", str(src.resolve()),
         "-t", f"{c.duration:.3f}", "-map", "0:v:0", "-vf", f"scale={w}:{h}", "-pix_fmt", "bgr24",
         "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
         "-r", f"{meta.fps:.5f}", "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-pix_fmt", "yuv420p", str(dst)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    samples = {s.f: s for s in plan.samples}
    cw = crop_width(meta.width, meta.height) * w
    colors = {"track": (80, 220, 80), "fit": (255, 160, 60), "center": (60, 160, 255), "split": (220, 80, 220)}
    last = None
    i = 0
    try:
        while len(buf := dec.stdout.read(w * h * 3)) == w * h * 3:
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
            shot = plan.shot_at(i)
            last = samples.get(i, last)
            for f in (last.faces if last else []):
                x0, y0 = int((f[0] - f[2] / 2) * w), int((f[1] - f[3] / 2) * h)
                ignored = any(abs(f[0] - o[0]) < 0.015 and abs(f[1] - o[1]) < 0.015 for o in plan.overlays)
                cv2.rectangle(frame, (x0, y0), (x0 + int(f[2] * w), y0 + int(f[3] * h)),
                              (0, 0, 255) if ignored else (255, 255, 255), 1)  # red = overlay, ignored
            col = colors.get(shot.mode, (255, 255, 255))
            if shot.mode == "fit":
                cv2.rectangle(frame, (1, 1), (w - 2, h - 2), col, 3)
            elif shot.mode == "split":
                for x0, y0, x1, y1 in shot.cams:
                    cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), col, 3)
            else:
                x = shot.x[min(i - shot.start, len(shot.x) - 1)] if shot.x else 0.5
                left = int(round(x * w - cw / 2))
                cv2.rectangle(frame, (left, 0), (left + int(cw), h - 1), col, 3)
            if i == shot.start:
                cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 6)  # red flash = detected cut
            cv2.putText(frame, f"{shot.mode.upper()} {shot.why}", (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
            enc.stdin.write(frame.tobytes())
            i += 1
        enc.stdin.close()
        enc.wait()
    finally:
        dec.stdout.close()
        dec.wait()
