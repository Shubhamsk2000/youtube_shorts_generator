"""Stage 6: 9:16 render of every selected clip (previews), also used for the final export.

Frames are decoded by ffmpeg at the source height (never above the output height), laid out per
frame in numpy and resized ONCE to the output size (Lanczos), then piped into a second ffmpeg that
sharpens lightly, burns in captions and muxes the audio:
  TRACK/CENTER  moving 9:16 crop window
  FIT           whole frame over a blurred copy of itself
  SPLIT         facecam on top, the gameplay/screen underneath
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
from pathlib import Path

import numpy as np

from ..config import AudioCfg, Config, ExportCfg, PreviewCfg, ReframeCfg
from ..models import Clip, ReframePlan, Selection, Shot, VideoMeta, load_json
from common.progress import Reporter
from common import ffmpeg
from .captions import ass_path
from .pacing import edits_path, kept_duration, load_keep
from .reframe import plan_path, split_panel_frac
from .select import OUT as SELECTION

CLIPS_DIR = "clips"
RenderCfg = PreviewCfg | ExportCfg


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "none"


def clip_file(job_dir: Path, clip: Clip, cfg: Config, rs: RenderCfg | None = None) -> str:
    """Cache name: changes whenever the range, settings, framing, captions or edits change."""
    rs = rs or cfg.preview
    key = json.dumps([round(clip.start, 3), round(clip.end, 3), rs.model_dump(mode="json"),
                      cfg.audio.model_dump(), _digest(plan_path(job_dir, clip)), _digest(ass_path(job_dir, clip)),
                      _digest(edits_path(job_dir, clip)), "v3"], sort_keys=True)
    return f"{clip.id}-{hashlib.sha1(key.encode()).hexdigest()[:6]}.mp4"


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    sel = load_json(Selection, job_dir / SELECTION)
    pc = cfg.preview
    out_dir = job_dir / CLIPS_DIR
    out_dir.mkdir(exist_ok=True)

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
    ass = ass_path(job_dir, c)
    ass = ass if (cfg.captions.enabled or cfg.hook.enabled) and ass.exists() else None
    plan_p = plan_path(job_dir, c)
    plan = load_json(ReframePlan, plan_p) if cfg.reframe.enabled and plan_p.exists() else None
    keep = load_keep(job_dir, c) if cfg.pacing.enabled else None
    src = job_dir / meta.file
    measured = None
    if two_pass_loudness:
        on_progress(0.0)
        measured = measure_loudness(src, c, keep, cfg.audio)
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    _render(src, tmp, c, meta, rs, plan, cfg.reframe, cfg.audio, keep, ass, measured, on_progress)
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


def measure_loudness(src: Path, c: Clip, keep, ac: AudioCfg) -> dict | None:
    """First loudnorm pass: measure the clip so the second pass can normalise linearly (no pumping)."""
    chain = ",".join([*_pre_audio_filters(keep, ac),
                      f"loudnorm=I={ac.loudness_lufs}:TP={ac.true_peak}:LRA=11:print_format=json"])
    proc = subprocess.run(
        [ffmpeg.require("ffmpeg"), "-hide_banner", "-nostdin", "-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}",
         "-i", str(src.resolve()), "-vn", "-map", "0:a:0?", "-af", chain, "-f", "null", "-"],
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


def _audio_args(c: Clip, keep, ac: AudioCfg, bitrate: str, measured: dict | None) -> list[str]:
    dur = kept_duration(keep, c.duration)
    fade = min(0.15, dur / 4)
    ln = f"loudnorm=I={ac.loudness_lufs}:TP={ac.true_peak}:LRA=11"
    if measured:
        ln += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
               f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
               f":offset={measured['target_offset']}:linear=true")
    chain = [*_pre_audio_filters(keep, ac), ln, "aresample=48000",
             "afade=t=in:d=0.03", f"afade=t=out:st={dur - fade:.3f}:d={fade:.3f}"]
    return ["-af", ",".join(chain), "-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-ac", "2"]


def _video_args(rs: RenderCfg) -> list[str]:
    return ["-c:v", "libx264", "-preset", rs.preset, "-crf", str(rs.crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart"]


# ---------------------------------------------------------------- video

def _kept_frames(n_hint: int, fps: float, keep) -> callable:
    if not keep:
        return lambda i: True
    return lambda i: any(a <= (i + 0.5) / fps < b for a, b in keep)


def _render(src: Path, tmp: Path, c: Clip, meta: VideoMeta, rs: RenderCfg, plan: ReframePlan | None,
            rc: ReframeCfg, ac: AudioCfg, keep, ass: Path | None, measured: dict | None, on_progress) -> None:
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
    dec = subprocess.Popen(
        [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{c.start:.3f}", "-i", str(src.resolve()),
         "-t", f"{c.duration:.3f}", "-map", "0:v:0", *(["-vf", ",".join(vf_dec)] if vf_dec else []),
         "-fps_mode", "passthrough", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    vf = ["setsar=1"]
    if rs.sharpen > 0:
        vf.insert(0, f"unsharp=5:5:{rs.sharpen:.2f}:5:5:0")
    if ass:
        vf.append(f"subtitles=filename={ass.name}")  # run from the captions dir: no path escaping
    err = tempfile.TemporaryFile()
    enc = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", f"{out_fps:.5f}", "-i", "pipe:0",
         "-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", str(src.resolve()),
         "-map", "0:v", "-map", "1:a:0?", "-vf", ",".join(vf),
         *_audio_args(c, keep, ac, rs.audio_bitrate, measured), *_video_args(rs), "-shortest", str(tmp.resolve())],
        stdin=subprocess.PIPE, stderr=err, cwd=ass.parent if ass else None,
    )
    size = sw * sh * 3
    n_est = max(1, round(c.duration * meta.fps))
    kept = _kept_frames(n_est, meta.fps, keep)
    interp = cv2.INTER_LINEAR if rs.fast else None
    i = 0  # source frame index (the plan and the edits count source frames)
    try:
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            if kept(i):
                frame = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
                out = compose(frame, plan.shot_at(i) if plan else None, i, out_w, out_h, meta, rc, cv2, interp)
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
    if shot is not None and shot.mode == "split" and shot.cams:
        return compose_split(frame, shot, out_w, out_h, meta, rc, cv2, interp)
    cw = min(sw, round(sh * out_w / out_h))
    x = 0.5
    if shot is not None and shot.x:
        x = shot.x[min(i - shot.start, len(shot.x) - 1)]
    left = int(np.clip(round(x * sw - cw / 2), 0, sw - cw))
    ch = min(sh, round(cw * out_h / out_w))
    top = (sh - ch) // 2
    return _resize(frame[top:top + ch, left:left + cw], out_w, out_h, cv2, interp)


def compose_fit(frame: np.ndarray, out_w: int, out_h: int, rc: ReframeCfg, cv2, interp=None) -> np.ndarray:
    """Whole frame, full width, over a blurred and dimmed fill of itself."""
    h, w = frame.shape[:2]
    # background: centre 9:16 region, blurred cheaply at 1/8 size
    bw = min(w, round(h * out_w / out_h))
    x0 = (w - bw) // 2
    small = cv2.resize(frame[:, x0:x0 + bw], (max(1, out_w // 8), max(1, out_h // 8)), interpolation=cv2.INTER_AREA)
    k = max(1, rc.fit_blur // 8) * 2 + 1
    small = cv2.GaussianBlur(small, (k, k), 0)
    bg = cv2.resize(small, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    bg = (bg.astype(np.float32) * rc.fit_dim).astype(np.uint8)
    fg_h = round(out_w * h / w / 2) * 2
    fg = _resize(frame, out_w, fg_h, cv2, interp)
    y = int(np.clip(round(rc.fit_y * out_h - fg_h / 2), 0, out_h - fg_h))
    bg[y:y + fg_h] = fg
    return bg


def _crop_to_aspect(x0: float, y0: float, x1: float, y1: float, aspect: float, fx: float, fy: float
                    ) -> tuple[float, float, float, float]:
    """Largest sub-box of (x0,y0,x1,y1) with width/height = aspect, centred on (fx, fy) where possible."""
    w, h = x1 - x0, y1 - y0
    if w / h > aspect:  # too wide: trim the sides
        nw = h * aspect
        left = float(np.clip(x0 + fx * w - nw / 2, x0, x1 - nw))
        return left, y0, left + nw, y1
    nh = w / aspect
    top = float(np.clip(y0 + fy * h - nh / 2, y0, y1 - nh))
    return x0, top, x1, top + nh


def split_geometry(shot: Shot, sw: int, sh: int, out_w: int, out_h: int, meta: VideoMeta, rc: ReframeCfg
                   ) -> tuple[int, list[tuple[int, int, int, int]], tuple[int, int, int, int]]:
    """(cam panel height, cam source crops (px), screen source crop (px)) for a SPLIT shot."""
    panel_h = int(round(split_panel_frac(shot.cams, meta.width, meta.height, rc) * out_h / 2) * 2)
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
        a, b, c, d = _crop_to_aspect(box[0] * sw, box[1] * sh, box[2] * sw, box[3] * sh, w_k / panel_h, fx, fy)
        cams.append((int(a), int(b), max(int(a) + 2, int(c)), max(int(b) + 2, int(d))))

    gh = out_h - panel_h
    aspect = out_w / gh
    cw, ch = round(sh * aspect), sh
    if cw > sw:
        cw, ch = sw, round(sw / aspect)
    # the screen crop: as central as possible, but don't show the cam twice
    best_left, best_cost = (sw - cw) // 2, None
    for shift in np.linspace(-0.25, 0.25, 21):
        left = int(np.clip(round((0.5 + shift) * sw - cw / 2), 0, sw - cw))
        overlap = sum(max(0, min(left + cw, x1 * sw) - max(left, x0 * sw)) for x0, _, x1, _ in shot.cams)
        cost = overlap * 4 + abs(shift) * sw
        if best_cost is None or cost < best_cost:
            best_left, best_cost = left, cost
    top = (sh - ch) // 2
    return panel_h, cams, (best_left, top, best_left + cw, top + ch)


def compose_split(frame: np.ndarray, shot: Shot, out_w: int, out_h: int, meta: VideoMeta, rc: ReframeCfg,
                  cv2, interp=None) -> np.ndarray:
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
    out[panel_h:] = _resize(frame[gt:gb, gl:gr], out_w, out_h - panel_h, cv2, interp)
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
