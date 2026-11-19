"""Manual framing from the clip editor -> framing/<clip_id>.json, applied on top of the reframe plan.

The analysis (reframe/<clip_id>.json) is never changed: "reset to auto" just removes keys. Each key pins the
window at one source frame of the clip:

    x  centre of the window, fraction of the source width; null = keep following the automatic framing (face
       tracking), so a key can widen the window without freezing it
    a  window shape, width/height: the output's own shape (9:16) fills the screen like an automatic crop; a wider
       window (13:16, 1:1 ... up to the whole 16:9 frame) is scaled to the short's full width and shown over a
       blurred copy of the frame

A shot (between two camera cuts) with keys becomes "manual": x and a ease between its keys and hold before the
first / after the last. Shots without keys keep their automatic framing. At a cut the window jumps, as always.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..config import Config
from ..models import Clip, ReframePlan, Shot, VideoMeta, load_json, save_json
from .reframe import effective_plan, plan_path

FRAMING_DIR = "framing"
OUT_ASPECT = 9 / 16
MAX_KEYS = 2000


class Key(BaseModel):
    f: int        # source frame inside the clip
    x: float | None = None  # window centre, fraction of the source width; None = the automatic path
    a: float = OUT_ASPECT  # window width / height


class Framing(BaseModel):
    keys: list[Key] = []


def framing_path(job_dir: Path, clip: Clip) -> Path:
    return job_dir / FRAMING_DIR / f"{clip.id}.json"


def load(job_dir: Path, clip: Clip) -> Framing | None:
    p = framing_path(job_dir, clip)
    if not p.exists():
        return None
    try:
        fr = load_json(Framing, p)
    except Exception:  # noqa: BLE001  (a broken file = automatic framing)
        return None
    return fr if fr.keys else None


def save(job_dir: Path, clip: Clip, fr: Framing) -> None:
    p = framing_path(job_dir, clip)
    if not fr.keys:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(exist_ok=True)
    save_json(fr, p)


def max_aspect(src_w: int, src_h: int) -> float:
    """The widest window there is: the whole frame."""
    return src_w / max(1, src_h)


def window_width(a: float, src_w: int, src_h: int) -> float:
    """Window width as a fraction of the source width."""
    return min(1.0, a * src_h / max(1, src_w))


def clean(keys: list[Key], n_frames: int, src_w: int, src_h: int) -> list[Key]:
    """Sorted, one key per frame (the last one wins), values clamped to what can be drawn."""
    if len(keys) > MAX_KEYS:
        raise ValueError(f"Too many keyframes (max {MAX_KEYS})")
    hi_a = max(OUT_ASPECT, max_aspect(src_w, src_h))
    by_f: dict[int, Key] = {}
    for k in keys:
        if not (0 <= k.f < max(1, n_frames)):
            raise ValueError(f"Keyframe at frame {k.f} is outside the clip (0-{n_frames - 1})")
        a = round(min(max(k.a, OUT_ASPECT), hi_a), 4)
        half = window_width(a, src_w, src_h) / 2
        x = None if k.x is None else round(min(max(k.x, half), 1 - half), 4)
        by_f[k.f] = Key(f=k.f, x=x, a=a)
    return [by_f[f] for f in sorted(by_f)]


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)  # smoothstep: no sudden start or stop between keys


def interpolate(keys: list[Key], frames: range, auto_x=lambda f: 0.5,
                src_w: int = 16, src_h: int = 9) -> tuple[list[float], list[float]]:
    """Per-frame (x, a) over `frames` from keys sorted by frame (all inside or around the range).
    A key without x uses the automatic path at that frame (auto_x), so tracking continues under it."""
    xs, as_ = [], []
    j = 0
    for f in frames:
        while j + 1 < len(keys) and keys[j + 1].f <= f:
            j += 1
        k0 = keys[j]
        x0 = auto_x(f) if k0.x is None else k0.x
        if f <= k0.f or j + 1 >= len(keys):
            x, a = x0, k0.a
        else:
            k1 = keys[j + 1]
            t = _ease((f - k0.f) / (k1.f - k0.f))
            x1 = auto_x(f) if k1.x is None else k1.x
            x, a = x0 + (x1 - x0) * t, k0.a + (k1.a - k0.a) * t
        half = window_width(a, src_w, src_h) / 2  # a wider window has less room to move
        xs.append(round(min(max(x, half), 1 - half), 4))
        as_.append(round(a, 4))
    return xs, as_


def apply(plan: ReframePlan, fr: Framing | None) -> ReframePlan:
    """The plan with every shot that has keys replaced by its manual window path."""
    if not fr or not fr.keys or not plan.shots:
        return plan
    keys = sorted(fr.keys, key=lambda k: k.f)
    shots: list[Shot] = []
    last = len(plan.shots) - 1
    for n, s in enumerate(plan.shots):
        end = max(s.end, keys[-1].f + 1) if n == last else s.end  # keys past the analysed frames: last shot
        mine = [k for k in keys if s.start <= k.f < end]
        if not mine:
            shots.append(s)
            continue
        path = s.x if s.x and s.mode in ("track", "center", "manual") else None

        def auto_x(f: int, s=s, path=path) -> float:
            return path[min(max(f - s.start, 0), len(path) - 1)] if path else 0.5

        xs, as_ = interpolate(mine, range(s.start, end), auto_x, plan.src_w, plan.src_h)
        shots.append(Shot(start=s.start, end=end, mode="manual", x=xs, a=as_, why="set in the editor"))
    return plan.model_copy(update={"shots": shots})


def base_plan(meta: VideoMeta, clip: Clip) -> ReframePlan:
    """A plan for a clip that was never analysed (reframe off): one centred shot."""
    n = max(1, round(clip.duration * meta.fps))
    return ReframePlan(key="manual", fps=meta.fps, n_frames=n, src_w=meta.width, src_h=meta.height,
                       shots=[Shot(start=0, end=n, mode="center", x=[0.5] * n, why="centre crop")])


def analysis(job_dir: Path, clip: Clip, cfg: Config) -> ReframePlan | None:
    """The automatic framing: the saved analysis with the smart layout applied (None = centre crop)."""
    p = plan_path(job_dir, clip)
    if not (cfg.reframe.enabled and p.exists()):
        return None
    return effective_plan(load_json(ReframePlan, p), cfg.reframe)


def load_plan(job_dir: Path, clip: Clip, cfg: Config, meta: VideoMeta) -> ReframePlan | None:
    """What is drawn: analysis -> smart layout -> manual overrides. None = plain centre crop."""
    plan = analysis(job_dir, clip, cfg)
    fr = load(job_dir, clip)
    if fr is None:
        return plan
    return apply(plan if plan is not None else base_plan(meta, clip), fr)
