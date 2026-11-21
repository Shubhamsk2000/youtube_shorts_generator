"""The clip editor's backend: what the page needs to draw a clip, and its 16:9 proxy video.

The proxy is the clip's own range, re-encoded small (H.264, short keyframe interval) so every browser can play
and scrub it. It keeps the source frame rate and starts at the clip's first frame, so frame n of the proxy is
frame n of the reframe plan, the overrides and the renderer.
"""
from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path

from common import ffmpeg

from .config import Config, with_job_layout
from .models import Clip, ReframePlan, Transcript, VideoMeta, load_json
from .stages import captions, framing, pacing, sources
from .stages.reframe import pip_geometry
from .stages.render import split_geometry

EDITOR_DIR = "editor"
PROXY_HEIGHT = 540
PROXY_VERSION = 2
REVIEW_PAD_S = 5.0   # the trim panel shows this much before and after the clip
MIN_CLIP_S = 3.0
MAX_CLIP_S = 180.0   # Shorts can be up to 3 minutes
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def n_frames(plan: ReframePlan | None, meta: VideoMeta, c: Clip) -> int:
    return max(plan.n_frames if plan else 0, round(c.duration * meta.fps), 1)


def cam_view(s, meta: VideoMeta, rc) -> dict | None:
    """A facecam shot as the live preview draws it, on the 1080x1920 frame (fractions): the gameplay area
    (top, height), the automatic gameplay crop (centre x, shape), and each cam tile (source box -> output box)."""
    if s.mode != "split" or not s.cams or rc.screen_layout == "track":
        return None
    W, H, sw, sh = 1080, 1920, meta.width, meta.height
    if rc.screen_layout == "pip":
        (gl, gt, gr, gb), cams = pip_geometry(s, sw, sh, W, H, rc)
        tiles = [{"src": [a / sw, b / sh, c / sw, d / sh], "dst": [x / W, y / H, w / W, h / H]}
                 for (a, b, c, d), (x, y, w, h) in cams]
        return {"area": [0.0, 1.0], "screen_x": (gl + gr) / 2 / sw, "screen_a": (gr - gl) / max(1, gb - gt),
                "tiles": tiles, "pip": True}
    panel_h, cams, (gl, gt, gr, gb) = split_geometry(s, sw, sh, W, H, meta, rc)
    n = len(cams)
    slot = W // n
    tiles = [{"src": [a / sw, b / sh, c / sw, d / sh],
              "dst": [slot * k / W, 0.0, ((W - slot * (n - 1)) if k == n - 1 else slot) / W, panel_h / H]}
             for k, (a, b, c, d) in enumerate(cams)]
    return {"area": [panel_h / H, 1 - panel_h / H], "screen_x": (gl + gr) / 2 / sw,
            "screen_a": (gr - gl) / max(1, gb - gt), "tiles": tiles, "pip": False}


def view(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config) -> dict:
    """Everything the editor draws: the automatic framing per shot, the manual keys, faces, removed pauses."""
    cfg = with_job_layout(cfg, job_dir)
    auto = framing.analysis(job_dir, c, cfg)
    n = n_frames(auto, meta, c)
    shots = auto.shots if auto else framing.base_plan(meta, c).shots
    keep, flash = pacing.load_edits(job_dir, c)
    fr = framing.load(job_dir, c)
    rc = cfg.reframe
    return {
        "fps": meta.fps,
        "n_frames": n,
        "src_w": meta.width,
        "src_h": meta.height,
        "analysed": auto is not None,
        "shots": [{"start": s.start, "end": s.end, "mode": s.mode, "why": s.why, "x": s.x, "cams": s.cams,
                   "cam_view": cam_view(s, meta, rc)} for s in shots],
        "faces": [{"f": s.f, "faces": [f[:4] for f in s.faces]} for s in (auto.samples if auto else [])],
        "keys": [k.model_dump() for k in fr.keys] if fr else [],
        "keep_cam": fr.keep_cam if fr else True,
        "cam": fr.cam.model_dump() if fr and fr.cam else None,
        "keep": keep if cfg.pacing.enabled else None,
        "flash": flash,
        "layout": {
            "out_w": 1080, "out_h": 1920, "fit_y": rc.fit_y, "fit_dim": rc.fit_dim,
            "screen_layout": rc.screen_layout, "background": rc.background,
            "max_aspect": round(framing.max_aspect(meta.width, meta.height), 4),
        },
        "hook": {
            "enabled": cfg.hook.enabled and rc.background != "tweet", "auto": cfg.hook.auto,
            "text": captions.hook_text(c), "position": cfg.hook.position, "y": c.hook_y,
            "duration_s": cfg.hook.duration_s,
            "size": cfg.hook.size, "max_chars_per_line": cfg.hook.max_chars_per_line,
        },
        "captions": {"enabled": cfg.captions.enabled, "position": cfg.captions.position, "size": cfg.captions.size},
    }


def proxy_path(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config, kind: str = "edit") -> tuple[Path, Path, Clip]:
    """(proxy file, source file, the clip on the source's timeline). Named by range + source: a new pick or
    fresh clip video makes a new proxy. kind "trim" = the review range's proxy, kept apart from the editor's."""
    src, lc = sources.resolve(job_dir, meta, c, cfg)
    key = f"{src.name}:{src.stat().st_size}:{lc.start:.3f}:{lc.end:.3f}:{PROXY_HEIGHT}:{PROXY_VERSION}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    stem = c.id if kind == "edit" else f"{c.id}.{kind}"
    return job_dir / EDITOR_DIR / f"{stem}-{digest}.mp4", src, lc


def ensure_proxy(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config, kind: str = "edit") -> Path:
    dst, src, lc = proxy_path(job_dir, meta, c, cfg, kind)
    with _locks_guard:
        lock = _locks.setdefault(str(dst), threading.Lock())
    with lock:
        if dst.exists():
            return dst
        dst.parent.mkdir(exist_ok=True)
        stem = dst.name.rsplit("-", 1)[0]
        for old in dst.parent.glob(f"{stem}-*.mp4"):  # an earlier range of this clip
            old.unlink(missing_ok=True)
        h = min(PROXY_HEIGHT, meta.height)
        h -= h % 2
        tmp = dst.with_name(dst.stem + ".tmp.mp4")
        # 50/60 fps sources: every 2nd frame is plenty to position a window (the page maps time -> source frame)
        vf = ([f"fps={meta.fps / 2:.5f}"] if meta.fps > 35 else []) + [f"scale=-2:{h}"]
        fps = meta.fps / 2 if meta.fps > 35 else meta.fps
        gop = max(1, round(fps / 2))  # a keyframe every half second: scrubbing lands fast
        cmd = [ffmpeg.require("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{lc.start:.3f}", "-i", str(src), "-t", f"{lc.duration:.3f}",
               "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(vf),
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "fastdecode", "-crf", "26", "-g", str(gop),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise ffmpeg.FFmpegError(res.stderr.strip()[-1500:] or "proxy encode failed")
        tmp.replace(dst)
        return dst


# ---------------------------------------------------------------- trim: the clip with 5 s on both sides

def review_range(meta: VideoMeta, c: Clip) -> tuple[float, float]:
    """Source seconds the trim panel shows: the clip plus REVIEW_PAD_S each side (inside the video)."""
    end = min(meta.duration, c.end + REVIEW_PAD_S) if meta.duration else c.end + REVIEW_PAD_S
    return round(max(0.0, c.start - REVIEW_PAD_S), 3), round(end, 3)


def review_clip(meta: VideoMeta, c: Clip) -> Clip:
    """The clip widened to its review range (same id: its source file is widened to cover it)."""
    a, b = review_range(meta, c)
    return c.model_copy(update={"start": a, "end": b})


def ensure_review_proxy(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config) -> Path:
    """A small mp4 of the review range (fetches the extra seconds first if the clip's video doesn't have them)."""
    return ensure_proxy(job_dir, meta, review_clip(meta, c), cfg, kind="trim")


def trim_view(job_dir: Path, meta: VideoMeta, c: Clip) -> dict:
    """What the trim panel draws: the range, and the transcript around it (to judge the context)."""
    a, b = review_range(meta, c)
    t = load_json(Transcript, job_dir / "transcript.json")
    lines = [{"id": s.id, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
              "words": [[round(w.start, 2), round(w.end, 2), w.text.strip()] for w in s.words]}
             for s in t.sentences if s.end > a and s.start < b]
    return {"start": c.start, "end": c.end, "review": [a, b], "pad_s": REVIEW_PAD_S,
            "min_s": MIN_CLIP_S, "max_s": MAX_CLIP_S, "sentences": lines}


def trimmed(t: Transcript, meta: VideoMeta, c: Clip, start: float, end: float) -> Clip:
    """The clip with a new start/end (source seconds); sentences and text follow. Raises ValueError."""
    start, end = round(start, 3), round(end, 3)
    if start < 0 or (meta.duration and end > meta.duration + 0.05):
        raise ValueError("The new range goes past the video")
    if end - start < MIN_CLIP_S:
        raise ValueError(f"A short has to be at least {MIN_CLIP_S:g} seconds")
    if end - start > MAX_CLIP_S:
        raise ValueError(f"Shorts are limited to {MAX_CLIP_S / 60:g} minutes")
    inside = [s for s in t.sentences if s.end > start and s.start < end]
    if not inside:
        raise ValueError("Nothing is said in that range")
    words = [w.text.strip() for s in inside for w in s.words if w.end > start and w.start < end]
    payoff = c.payoff_sentence if c.payoff_sentence is not None and \
        inside[0].id <= c.payoff_sentence <= inside[-1].id else None
    return c.model_copy(update={"start": start, "end": end, "start_sentence": inside[0].id,
                                "end_sentence": inside[-1].id, "text": " ".join(words), "payoff_sentence": payoff})


def apply_trim(job_dir: Path, meta: VideoMeta, old: Clip, new: Clip) -> int:
    """Keep the editor's work on the new range: window keys move with the footage (keys now outside the clip
    are dropped), and the old range's framing analysis + pauses are removed so they're redone. Returns the
    number of keys dropped."""
    from .stages.reframe import plan_path
    fr = framing.load(job_dir, old)
    dropped = 0
    if fr is not None and fr.keys:
        shift = round((old.start - new.start) * meta.fps)
        n = max(1, round(new.duration * meta.fps))
        # a key on the first frame stays there (it sets the window from the start, e.g. "this shape for the whole
        # clip"); of the keys now before the start, the last one moves onto it, so the window it set is kept
        keys = sorted((k.model_copy(update={"f": 0 if k.f == 0 else k.f + shift}) for k in fr.keys), key=lambda k: k.f)
        before = [k for k in keys if k.f < 0]
        kept = [k for k in keys if 0 <= k.f < n]
        if before and not (kept and kept[0].f == 0):
            kept.insert(0, before[-1].model_copy(update={"f": 0}))
        dropped = len(fr.keys) - len(kept)
        framing.save(job_dir, new, fr.model_copy(update={"keys": kept}))
    plan_path(job_dir, old).unlink(missing_ok=True)
    pacing.edits_path(job_dir, old).unlink(missing_ok=True)
    return dropped


def save_keys(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config, keys: list[framing.Key],
              keep_cam: bool = True, cam: framing.CamBox | None = None) -> framing.Framing:
    cfg = with_job_layout(cfg, job_dir)
    auto = framing.analysis(job_dir, c, cfg)
    fr = framing.Framing(keys=framing.clean(keys, n_frames(auto, meta, c), meta.width, meta.height),
                         keep_cam=keep_cam, cam=framing.clean_cam(cam))
    framing.save(job_dir, c, fr)
    return fr
