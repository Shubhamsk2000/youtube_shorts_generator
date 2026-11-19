"""What surrounds the video on the 9:16 canvas (reframe.background).

  blur   the layout fills the whole canvas (blurred background only where the video doesn't reach)
  tweet  the layout is drawn into the video window of a tweet card (Studio → Settings → Channel card,
         the `card:` section), over a blurred, dimmed copy of the frame; the clip's hook text is the tweet

`canvas()` is the single source of truth for the content window, used by the captions (positions) and
the render (drawing).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import Clip

CARDS_DIR = "cards"


@dataclass(frozen=True)
class Canvas:
    W: int
    H: int
    win: tuple[int, int, int, int]   # x, y, w, h of the area the layout is drawn into
    card: Path | None = None         # full-canvas RGBA overlay (tweet), transparent over the window

    @property
    def framed(self) -> bool:
        return self.win != (0, 0, self.W, self.H)

    def y(self, frac: float) -> int:
        """A height inside the window (fraction of the window) -> px on the canvas."""
        return round(self.win[1] + frac * self.win[3])


def tweet_text(c: Clip) -> str:
    return (c.hook_text or c.title or "").strip()


def _avatar_digest(cc) -> str:
    from common.paths import resolve
    if not cc.avatar:
        return "none"
    p = resolve(cc.avatar)
    return hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "missing"


def canvas_key(cfg: Config, c: Clip) -> str:
    """Everything outside the reframe settings that changes the canvas (part of the render cache key)."""
    if cfg.reframe.background != "tweet":
        return "blur"
    cc = cfg.card
    return json.dumps([cc.model_dump(mode="json"), _avatar_digest(cc), tweet_text(c)], sort_keys=True)


def canvas(cfg: Config, W: int, H: int, c: Clip, job_dir: Path | None = None) -> Canvas:
    """Geometry for a W x H output; with job_dir, the tweet card image is drawn (cached) as well."""
    rc = cfg.reframe
    if rc.background != "tweet":
        return Canvas(W, H, (0, 0, W, H))
    from ..card import layout, render_card

    cc = cfg.card
    a = max(0.3, min(rc.tweet_window_aspect, 2.0))
    lay = layout(cc, W, H, round(1000 * a), 1000, tweet_text(c), max_video_ratio=1 / a, fit=True)
    png = None
    if job_dir is not None:
        key = hashlib.sha1(f"{canvas_key(cfg, c)}|{W}x{H}|{a}".encode()).hexdigest()[:10]
        png = job_dir / CARDS_DIR / f"{c.id}-{key}.png"
        if not png.exists():
            render_card(cc, lay, png)
    return Canvas(W, H, lay.video, png)
