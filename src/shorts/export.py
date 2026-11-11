"""Final export: approved clips at full quality -> exports/<video title>/NN-title.mp4 + NN-title.txt.

Same framing/captions/edits as the previews, but 1080x1920, a high-quality encode, light sharpening
and two-pass loudness normalisation. Renders are cached in workspace/<id>/final/, so exporting again
only renders clips that changed; the .txt next to each video holds the title, description and
hashtags ready to paste into the upload form.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .models import Clip, Selection, VideoMeta, load_json
from .progress import Reporter
from .stages import render
from .stages.select import OUT as SELECTION

REVIEWS_FILE = "reviews.json"
FINAL_DIR = "final"


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    dir: Path
    files: list[str] = field(default_factory=list)
    rendered: int = 0


def load_reviews(job_dir: Path) -> dict[str, str]:
    try:
        return json.loads((job_dir / REVIEWS_FILE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def slug(s: str, n: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-") or "clip"


def export_dir(cfg: Config, meta: VideoMeta) -> Path:
    return cfg.export.out_dir() / f"{slug(meta.title, 60)}-{meta.id[:11]}"


def upload_text(c: Clip, meta: VideoMeta) -> str:
    tags = " ".join(c.hashtags) if c.hashtags else "#shorts"
    src = ""
    if meta.source_url:
        sep = "&" if "?" in meta.source_url else "?"
        src = f"\nFull video: {meta.source_url}{sep}t={int(c.start)}s" if "youtu" in meta.source_url else ""
    return (f"TITLE\n{c.title}\n\n"
            f"DESCRIPTION\n{(c.description or c.title).strip()}{src}\n\n{tags}\n\n"
            f"HOOK TEXT (on screen)\n{c.hook_text or c.title}\n\n"
            f"--\nscore {c.score:g} · {c.duration:.0f}s · source {int(c.start // 60)}:{int(c.start % 60):02d}\n")


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, clip_ids: list[str] | None = None,
        report: Reporter | None = None) -> ExportResult:
    """Export the approved clips (or `clip_ids`). Raises ExportError when there is nothing to export."""
    report = report or (lambda fraction=None, message=None: None)
    if not (job_dir / SELECTION).exists():
        raise ExportError("No clips yet: run the pipeline first")
    sel = load_json(Selection, job_dir / SELECTION)
    if clip_ids is None:
        reviews = load_reviews(job_dir)
        clips = [c for c in sel.clips if reviews.get(c.id) == "approved"]
        if not clips:
            raise ExportError("No approved clips: approve some clips first")
    else:
        wanted = set(clip_ids)
        clips = [c for c in sel.clips if c.id in wanted]
        if not clips:
            raise ExportError("None of those clips exist")

    ec = cfg.export
    cache = job_dir / FINAL_DIR
    cache.mkdir(exist_ok=True)
    out = export_dir(cfg, meta)
    out.mkdir(parents=True, exist_ok=True)
    names = {c.id: render.clip_file(job_dir, c, cfg, ec) for c in clips}
    keep = set(names.values())
    for f in cache.iterdir():  # older renders of these clips (settings, framing or captions changed)
        if f.suffix == ".mp4" and f.name not in keep and f.name.split("-")[0] in names:
            f.unlink()

    todo = [c for c in clips if not (cache / names[c.id]).exists()]
    total = sum(c.duration for c in todo) or 1.0
    done = 0.0
    t0 = time.monotonic()
    res = ExportResult(dir=out)
    for i, c in enumerate(clips):
        dst = cache / names[c.id]
        if not dst.exists():
            def prog(f: float, c=c, i=i) -> None:
                frac = (done + f * c.duration) / total
                el = time.monotonic() - t0
                eta = f" · ~{(el / frac - el) / 60:.0f} min left" if frac > 0.05 else ""
                report(frac, f"Clip {i + 1}/{len(clips)}: {c.title[:48]}{eta}")

            prog(0.0)
            render.render_clip(job_dir, meta, c, cfg, ec, dst, prog, two_pass_loudness=True)
            done += c.duration
            res.rendered += 1
        base = f"{c.rank:02d}-{slug(c.title)}"
        final = out / f"{base}.mp4"
        final.unlink(missing_ok=True)
        try:
            os.link(dst, final)  # same disk: no copy
        except OSError:
            shutil.copy2(dst, final)
        (out / f"{base}.txt").write_text(upload_text(c, meta))
        res.files.append(final.name)
    report(1.0, f"{len(res.files)} clips exported")
    return res
