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
from .models import Clip, ReframePlan, VideoMeta
from .stages import captions, framing, pacing, sources

EDITOR_DIR = "editor"
PROXY_HEIGHT = 540
PROXY_VERSION = 2
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def n_frames(plan: ReframePlan | None, meta: VideoMeta, c: Clip) -> int:
    return max(plan.n_frames if plan else 0, round(c.duration * meta.fps), 1)


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
        "shots": [{"start": s.start, "end": s.end, "mode": s.mode, "why": s.why, "x": s.x, "cams": s.cams}
                  for s in shots],
        "faces": [{"f": s.f, "faces": [f[:4] for f in s.faces]} for s in (auto.samples if auto else [])],
        "keys": [k.model_dump() for k in fr.keys] if fr else [],
        "keep": keep if cfg.pacing.enabled else None,
        "flash": flash,
        "layout": {
            "out_w": 1080, "out_h": 1920, "fit_y": rc.fit_y, "fit_dim": rc.fit_dim,
            "screen_layout": rc.screen_layout, "background": rc.background,
            "max_aspect": round(framing.max_aspect(meta.width, meta.height), 4),
        },
        "hook": {
            "enabled": cfg.hook.enabled and rc.background != "tweet", "auto": cfg.hook.auto,
            "text": captions.hook_text(c), "position": cfg.hook.position, "duration_s": cfg.hook.duration_s,
            "size": cfg.hook.size, "max_chars_per_line": cfg.hook.max_chars_per_line,
        },
        "captions": {"enabled": cfg.captions.enabled, "position": cfg.captions.position, "size": cfg.captions.size},
    }


def proxy_path(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config) -> tuple[Path, Path, Clip]:
    """(proxy file, source file, the clip on the source's timeline). Named by range + source: a new pick or
    fresh clip video makes a new proxy."""
    src, lc = sources.resolve(job_dir, meta, c, cfg)
    key = f"{src.name}:{src.stat().st_size}:{lc.start:.3f}:{lc.end:.3f}:{PROXY_HEIGHT}:{PROXY_VERSION}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return job_dir / EDITOR_DIR / f"{c.id}-{digest}.mp4", src, lc


def ensure_proxy(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config) -> Path:
    dst, src, lc = proxy_path(job_dir, meta, c, cfg)
    with _locks_guard:
        lock = _locks.setdefault(str(dst), threading.Lock())
    with lock:
        if dst.exists():
            return dst
        dst.parent.mkdir(exist_ok=True)
        for old in dst.parent.glob(f"{c.id}-*.mp4"):  # an earlier range of this clip
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


def save_keys(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config, keys: list[framing.Key]) -> framing.Framing:
    cfg = with_job_layout(cfg, job_dir)
    auto = framing.analysis(job_dir, c, cfg)
    fr = framing.Framing(keys=framing.clean(keys, n_frames(auto, meta, c), meta.width, meta.height))
    framing.save(job_dir, c, fr)
    return fr
