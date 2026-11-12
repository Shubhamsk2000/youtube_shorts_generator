"""Stage 4: decide where the 9:16 window goes, shot by shot -> reframe/<clip_id>.json.

One streaming pass over each clip at low resolution:
  * camera cuts are found from how much a 64x36 thumbnail changes between frames (pixels + layout;
    colour histograms miss cuts between two angles of the same scene);
  * faces are detected (OpenCV YuNet) ~6x per second and on the first frame of every shot.
Each shot then gets ONE mode, so the framing never flickers mid-shot:
  TRACK   one face dominates, or the whole group fits in a 9:16 window -> follow it (smoothed)
  FIT     faces spread wider than 9:16 -> whole frame over a blurred copy of itself
  CENTER  no reliable face -> centre crop
  SPLIT   a facecam box over gameplay / a screen -> cam on top, the screen underneath
          (cam boxes are found as rectangles whose border edges stay put while the content moves)
At a cut the crop jumps (that's what a cut looks like); within a shot it is locked when the
subject barely moves, otherwise smoothed and speed-limited.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from common.paths import MODELS_DIR

from ..config import Config, ReframeCfg
from ..models import Clip, FaceSample, ReframePlan, Selection, Shot, VideoMeta, load_json, save_json
from common.progress import Reporter
from common.ffmpeg import require
from .select import OUT as SELECTION

REFRAME_DIR = "reframe"
VERSION = 2
MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def plan_path(job_dir: Path, clip: Clip) -> Path:
    return job_dir / REFRAME_DIR / f"{clip.id}.json"


def plan_key(clip: Clip, rc: ReframeCfg) -> str:
    raw = json.dumps([VERSION, round(clip.start, 3), round(clip.end, 3), rc.model_dump()], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def crop_width(src_w: int, src_h: int) -> float:
    """Width of the full-height 9:16 window, as a fraction of the frame width."""
    return min(1.0, src_h * 9 / 16 / src_w)


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    out_dir = job_dir / REFRAME_DIR
    out_dir.mkdir(exist_ok=True)
    sel = load_json(Selection, job_dir / SELECTION)
    rc = cfg.reframe
    if not rc.enabled:
        for f in out_dir.glob("*.json"):
            f.unlink()
        return "Disabled: centre crop"

    wanted, todo = set(), []
    for c in sel.clips:
        p = plan_path(job_dir, c)
        wanted.add(p.name)
        if not (p.exists() and load_json(ReframePlan, p).key == plan_key(c, rc)):
            todo.append(c)
    for f in out_dir.iterdir():
        if f.name not in wanted and f.suffix == ".json":
            f.unlink()

    if todo:
        det = _detector(rc)
        total = sum(c.duration for c in todo)
        done = 0.0
        for i, c in enumerate(todo):
            def prog(frac: float, c=c, i=i) -> None:
                report((done + frac * c.duration) / total, f"Clip {i + 1}/{len(todo)}: tracking faces…")
            plan = analyze(job_dir / meta.file, c, meta, rc, det, prog)
            save_json(plan, plan_path(job_dir, c))
            done += c.duration

    counts = {"track": 0, "fit": 0, "center": 0, "split": 0}
    for c in sel.clips:
        for s in load_json(ReframePlan, plan_path(job_dir, c)).shots:
            counts[s.mode] += 1
    n = sum(counts.values())
    split = f", {counts['split']} facecam split" if counts["split"] else ""
    return (f"{len(sel.clips)} clips · {n} shots: {counts['track']} tracked, {counts['fit']} fit, "
            f"{counts['center']} centre{split}" + ("" if todo else " · cached"))


# ---------------------------------------------------------------- analysis (I/O)

def ensure_model() -> Path:
    if MODEL.exists():
        return MODEL
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL.with_suffix(".part")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as r:
        tmp.write_bytes(r.read())
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != MODEL_SHA256:
        tmp.unlink()
        raise RuntimeError("Downloaded face model failed its checksum")
    tmp.replace(MODEL)
    return MODEL


def _detector(rc: ReframeCfg):
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    import cv2

    return cv2.FaceDetectorYN.create(str(ensure_model()), "", (320, 320), rc.face_score, 0.3, 50)


def analyze(src: Path, clip: Clip, meta: VideoMeta, rc: ReframeCfg, det, on_progress) -> ReframePlan:
    import cv2

    h = rc.analysis_height
    w = round(h * meta.width / meta.height / 2) * 2
    det.setInputSize((w, h))
    proc = subprocess.Popen(
        [require("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-ss", f"{clip.start:.3f}", "-i", str(src), "-t", f"{clip.duration:.3f}", "-map", "0:v:0",
         "-vf", f"scale={w}:{h}:flags=fast_bilinear", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    step = max(1, round(meta.fps / rc.sample_fps))
    est_frames = max(1, round(clip.duration * meta.fps))
    cuts = [0]
    samples: list[FaceSample] = []
    edges: list[tuple[int, np.ndarray]] = []  # (frame, packed edge map) per sample: finds facecam borders
    prev_small = prev_gray = None
    i = 0
    try:
        size = w * h * 3
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            is_cut = False
            if prev_small is not None:
                is_cut = cut_score(small, prev_small, gray, prev_gray) > rc.cut_threshold
            if is_cut and i - cuts[-1] >= 2:  # ignore 1-frame flashes
                cuts.append(i)
            if i % step == 0 or i == cuts[-1]:
                _, faces = det.detect(frame)
                if rc.screen_layout == "split":
                    edges.append((i, np.packbits(cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 40, 120) > 0)))
                samples.append(FaceSample(f=i, faces=[
                    [round(float((f[0] + f[2] / 2) / w), 4), round(float((f[1] + f[3] / 2) / h), 4),
                     round(float(f[2] / w), 4), round(float(f[3] / h), 4), round(float(f[-1]), 3)]
                    for f in (faces if faces is not None else [])
                ]))
            prev_small, prev_gray = small, gray
            i += 1
            if i % 15 == 0:
                on_progress(min(i / est_frames, 1.0))
    finally:
        proc.stdout.close()
        proc.wait()
    if i == 0:
        raise RuntimeError(f"Could not decode any frames for clip {clip.id}")

    overlays = find_overlays(samples, cuts)
    cams: dict[int, list[list[float]]] = {}
    if edges:
        cuts, cams = detect_cams(edges, (h, w), samples, cuts, overlays, rc)
    shots = plan_shots(cuts, i, samples, meta.fps, crop_width(meta.width, meta.height), rc, overlays, cams)
    return ReframePlan(key=plan_key(clip, rc), fps=meta.fps, n_frames=i, src_w=meta.width, src_h=meta.height,
                       shots=shots, samples=samples, overlays=overlays)


# ---------------------------------------------------------------- decisions (pure)

def cut_score(small: np.ndarray, prev_small: np.ndarray, gray: np.ndarray, prev_gray: np.ndarray) -> float:
    """Mean pixel change (0-1) plus a small layout term (1 - correlation of the grey thumbnails).

    Measured on a MrBeast clip: true cuts >= 0.27, ordinary frames (incl. fast motion) <= 0.24.
    """
    diff = float(np.mean(np.abs(small.astype(np.int16) - prev_small.astype(np.int16)))) / 255
    a, b = gray - gray.mean(), prev_gray - prev_gray.mean()
    corr = float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum() + 1e-6))
    return diff + 0.1 * (1 - corr)

def find_overlays(samples: list[FaceSample], cuts: list[int], tol: float = 0.012) -> list[list[float]]:
    """Faces on on-screen graphics (e.g. a scoreboard with headshots): pixel-static across camera cuts.

    A real head always moves a little and changes at a cut; a printed one doesn't. A face counts as an
    overlay if it sits in the same spot (std < 0.4% of the frame) in >= 30% of samples, across >= 2 shots.
    """
    if len(samples) < 6 or len(cuts) < 2:
        return []
    clusters: list[dict] = []
    for s in samples:
        shot = sum(c <= s.f for c in cuts) - 1
        for f in s.faces:
            for c in clusters:
                if abs(c["cx"] - f[0]) < tol and abs(c["cy"] - f[1]) < tol and abs(c["w"] - f[2]) < tol:
                    c["pts"].append(f)
                    c["shots"].add(shot)
                    break
            else:
                clusters.append({"cx": f[0], "cy": f[1], "w": f[2], "pts": [f], "shots": {shot}})
    out = []
    for c in clusters:
        pts = np.array(c["pts"])
        if (len(pts) >= 0.3 * len(samples) and len(c["shots"]) >= 2
                and pts[:, 0].std() < 0.004 and pts[:, 1].std() < 0.004 and pts[:, 2].std() < 0.004):
            out.append([round(float(v), 4) for v in pts[:, :4].mean(axis=0)])
    return out


def _is_overlay(f: list[float], overlays: list[list[float]], tol: float = 0.015) -> bool:
    return any(abs(f[0] - o[0]) < tol and abs(f[1] - o[1]) < tol and abs(f[2] - o[2]) < tol for o in overlays)

def classify(faces: list[list[float]], crop_w: float, rc: ReframeCfg,
             overlays: list[list[float]] | None = None) -> tuple[str, float | None]:
    """One sample -> ("track", x) | ("fit", None) | ("none", None)."""
    faces = [f for f in faces if f[3] >= rc.min_face_h and not _is_overlay(f, overlays or [])]
    if not faces:
        return "none", None
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    top = faces[0][2] * faces[0][3]
    group = [f for f in faces if f[2] * f[3] >= rc.group_ratio * top]
    x0 = min(f[0] - f[2] / 2 for f in group)
    x1 = max(f[0] + f[2] / 2 for f in group)
    if x1 - x0 <= crop_w * 0.85:  # everyone who matters fits (with some room for shoulders)
        return "track", (x0 + x1) / 2
    if len(faces) == 1 or top >= rc.dominance * faces[1][2] * faces[1][3]:
        return "track", faces[0][0]
    return "fit", None


def plan_shots(cuts: list[int], n_frames: int, samples: list[FaceSample], fps: float, crop_w: float,
               rc: ReframeCfg, overlays: list[list[float]] | None = None,
               cams: dict[int, list[list[float]]] | None = None) -> list[Shot]:
    bounds = list(zip(cuts, cuts[1:] + [n_frames]))
    shots: list[Shot] = []
    for k, (a, b) in enumerate(bounds):
        ss = [s for s in samples if a <= s.f < b]
        if cams and cams.get(k):
            shots.append(split_shot(a, b, cams[k], ss))
            continue
        kinds = [(s.f, *classify(s.faces, crop_w, rc, overlays)) for s in ss]
        n = max(1, len(kinds))
        n_none = sum(k == "none" for _, k, _ in kinds)
        n_fit = sum(k == "fit" for _, k, _ in kinds)
        track = [(f, x) for f, k, x in kinds if k == "track"]
        if not kinds or n_none / n >= rc.no_face_fraction:
            shots.append(Shot(start=a, end=b, mode="center", x=[0.5] * (b - a), why="no reliable face"))
        elif n_fit / n >= rc.fit_fraction or not track:
            shots.append(Shot(start=a, end=b, mode="fit", why="faces spread wider than 9:16"))
        else:
            xs = crop_path([f - a for f, _ in track], [x for _, x in track], b - a, fps, crop_w, rc)
            static = len(set(xs)) == 1
            shots.append(Shot(start=a, end=b, mode="track", x=xs,
                              why="subject locked" if static else "following subject"))
    return shots


def crop_path(frames: list[int], xs: list[float], n: int, fps: float, crop_w: float, rc: ReframeCfg) -> list[float]:
    """Per-frame crop centre for one shot: locked if the subject barely moves, else smoothed + speed-limited."""
    lo = math.ceil(crop_w / 2 * 1e4) / 1e4  # rounded inward: values are stored with 4 decimals
    hi = math.floor((1 - crop_w / 2) * 1e4) / 1e4
    xs_arr = np.array(xs, dtype=float)
    if xs_arr.max() - xs_arr.min() <= rc.static_tolerance:
        x = float(np.clip(np.median(xs_arr), lo, hi))
        return [round(x, 4)] * n
    path = np.interp(np.arange(n), frames, xs_arr)  # holds the first/last value at the ends
    sigma = max(1.0, rc.smooth_s * fps / 2)
    k = np.arange(-int(3 * sigma), int(3 * sigma) + 1)
    kernel = np.exp(-(k ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    padded = np.pad(path, len(k) // 2, mode="edge")
    path = np.convolve(padded, kernel, mode="valid")
    vmax = rc.max_speed / fps
    for i in range(1, n):  # speed limit: no whip-pans
        path[i] = path[i - 1] + np.clip(path[i] - path[i - 1], -vmax, vmax)
    return [round(float(v), 4) for v in np.clip(path, lo, hi)]


# ---------------------------------------------------------------- facecam detection (pure)

def _line_maps(P: np.ndarray, thr: float) -> tuple[np.ndarray, np.ndarray]:
    """Cumulative sums of 'steady vertical line' / 'steady horizontal line' pixels.

    A border wobbles by a pixel between frames, so persistence is summed across a 3 px band first.
    """
    import cv2

    H, W = P.shape
    ev = cv2.filter2D(P, -1, np.ones((1, 3), np.float32), borderType=cv2.BORDER_CONSTANT) >= thr
    eh = cv2.filter2D(P, -1, np.ones((3, 1), np.float32), borderType=cv2.BORDER_CONSTANT) >= thr
    ch = np.zeros((H, W + 1), np.int32)
    ch[:, 1:] = np.cumsum(eh, 1)
    cv = np.zeros((H + 1, W), np.int32)
    cv[1:] = np.cumsum(ev, 0)
    return ch, cv


def _peaks(idx: np.ndarray, cov: np.ndarray, keep: int = 10) -> list[int]:
    """Collapse runs of adjacent candidate lines to their strongest member; keep the best few."""
    out: list[tuple[float, int]] = []
    run: list[int] = []
    for j, x in enumerate(idx):
        if run and x != idx[run[-1]] + 1:
            best = max(run, key=lambda r: cov[r])
            out.append((cov[best], int(idx[best])))
            run = []
        run.append(j)
    if run:
        best = max(run, key=lambda r: cov[r])
        out.append((cov[best], int(idx[best])))
    return [x for _, x in sorted(out, reverse=True)[:keep]]


def find_cam_box(P: np.ndarray, face: tuple[float, float, float, float], thr: float,
                 cover: float = 0.85) -> tuple[int, int, int, int] | None:
    """Largest steady rectangle around a face (x, y, w, h in px) -> (x0, y0, x1, y1) px, or None.

    P is edge persistence (0-1 per pixel). Each side must be a steady line along >= `cover` of its
    length (ignoring 10% at each end for rounded corners).
    """
    H, W = P.shape
    ch, cv = _line_maps(P.astype(np.float32), thr)
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    ya, yb = max(0, int(cy - 0.3 * fh)), min(H, int(cy + 0.3 * fh))
    xa, xb = max(0, int(cx - 0.3 * fw)), min(W, int(cx + 0.3 * fw))
    if yb <= ya or xb <= xa:
        return None

    def cols(lo, hi):
        idx = np.arange(max(0, int(lo)), min(W, int(hi)))
        c = (cv[yb, idx] - cv[ya, idx]) / (yb - ya)
        return _peaks(idx[c >= cover], c[c >= cover])

    def rows(lo, hi):
        idx = np.arange(max(0, int(lo)), min(H, int(hi)))
        c = (ch[idx, xb] - ch[idx, xa]) / (xb - xa)
        return np.array(_peaks(idx[c >= cover], c[c >= cover]), int)

    lefts, rights = cols(cx - 3.5 * fw, cx - 0.4 * fw), cols(cx + 0.4 * fw, cx + 3.5 * fw)
    tops, bots = rows(cy - 3 * fh, cy - 0.3 * fh), rows(cy + 0.3 * fh, cy + 3 * fh)
    if not (lefts and rights and len(tops) and len(bots)):
        return None
    best, best_area = None, 0
    for l in lefts:
        for r in rights:
            w = r - l
            if w > 0.6 * W or w < max(1.3 * fw, 0.12 * W):
                continue
            m = int(0.1 * w)
            a, b = l + m, r - m
            ts = tops[(ch[tops, b] - ch[tops, a]) >= cover * (b - a)]
            bs = bots[(ch[bots, b] - ch[bots, a]) >= cover * (b - a)]
            for t in ts:
                for bo in bs:
                    h = bo - t
                    if h > 0.75 * H or h < max(1.15 * fh, 0.2 * H) or not 0.5 <= w / h <= 2.5 or w * h <= best_area:
                        continue
                    mm = int(0.1 * h)
                    need = cover * (h - 2 * mm)
                    if cv[bo - mm, l] - cv[t + mm, l] >= need and cv[bo - mm, r] - cv[t + mm, r] >= need:
                        best, best_area = (int(l), int(t), int(r), int(bo)), w * h
    return best


def border_cover(P: np.ndarray, box: tuple[int, int, int, int], thr: float, slack: int = 3) -> float:
    """How much of a box's border (weakest side) is a steady line in P; each side may sit `slack` px off."""
    return _cover(*_line_maps(P.astype(np.float32), thr), box, slack)


def _cover(ch: np.ndarray, cv: np.ndarray, box: tuple[int, int, int, int], slack: int = 3) -> float:
    H, W = ch.shape[0], cv.shape[1]
    l, t, r, b = box
    mw, mh = int(0.1 * (r - l)), int(0.1 * (b - t))
    xa, xb, ya, yb = l + mw, r - mw, t + mh, b - mh
    if xb <= xa or yb <= ya:
        return 0.0

    def hline(y0: int) -> float:
        ys = np.arange(max(0, y0 - slack), min(H, y0 + slack + 1))
        return float(((ch[ys, xb] - ch[ys, xa]) / (xb - xa)).max())

    def vline(x0: int) -> float:
        xs = np.arange(max(0, x0 - slack), min(W, x0 + slack + 1))
        return float(((cv[yb, xs] - cv[ya, xs]) / (yb - ya)).max())

    return min(hline(t), hline(b), vline(l), vline(r))


def _outside_steady(P: np.ndarray, box: tuple[int, int, int, int], thr: float) -> float:
    """Share of steady edge pixels outside the box: high = a static scene, not a cam over moving content."""
    m = np.ones(P.shape, bool)
    m[box[1]:box[3], box[0]:box[2]] = False
    return float((P[m] >= thr).mean())


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def _shot_of(f: int, cuts: list[int]) -> int:
    return sum(c <= f for c in cuts) - 1


def detect_cams(edges: list[tuple[int, np.ndarray]], shape: tuple[int, int], samples: list[FaceSample],
                cuts: list[int], overlays: list[list[float]], rc: ReframeCfg
                ) -> tuple[list[int], dict[int, list[list[float]]]]:
    """Find facecam boxes; return (cuts, {shot index: cam boxes [x0, y0, x1, y1] as fractions}).

    Boxes are found on edge persistence (whole clip, then per shot for cams that move or zoom in).
    Presence is then checked on every sample frame, and shots are split where a cam appears or
    disappears: a cut from gameplay to an animation is easy to miss, a cam border is not.
    """
    H, W = shape
    thr = rc.cam_edge_persistence
    frames = [f for f, _ in edges]
    unpack = lambda e: np.unpackbits(e, count=H * W).reshape(H, W).astype(np.float32)  # noqa: E731
    shot_sum: dict[int, np.ndarray] = {}
    shot_n: dict[int, int] = {}
    total = np.zeros((H, W), np.float32)
    for f, e in edges:
        m = unpack(e)
        k = _shot_of(f, cuts)
        shot_sum[k] = shot_sum.get(k, 0) + m
        shot_n[k] = shot_n.get(k, 0) + 1
        total += m
    P_all = total / len(edges)

    def face_px(f) -> tuple[float, float, float, float]:
        return ((f[0] - f[2] / 2) * W, (f[1] - f[3] / 2) * H, f[2] * W, f[3] * H)

    def usable(f) -> bool:
        return f[3] >= rc.min_face_h * 0.6 and not _is_overlay(f, overlays)

    boxes: list[tuple[int, int, int, int]] = []

    def consider(P, f, t=thr) -> None:
        box = find_cam_box(P, face_px(f), t)
        if box and _outside_steady(P, box, thr) <= 0.08 and not any(_iou(box, b) > 0.8 for b in boxes):
            boxes.append(box)

    # 1) cams that stay put: faces that keep showing up in one spot, on whole-clip persistence
    clusters: list[dict] = []
    for s in samples:
        for f in s.faces:
            if not usable(f):
                continue
            for c in clusters:
                if abs(c["cx"] - f[0]) < 0.05 and abs(c["cy"] - f[1]) < 0.05:
                    c["faces"].append(f)
                    break
            else:
                clusters.append({"cx": f[0], "cy": f[1], "faces": [f]})
    for c in sorted(clusters, key=lambda c: -len(c["faces"]))[:6]:
        if len(c["faces"]) >= 3:
            consider(P_all, list(np.median(np.array(c["faces"])[:, :4], axis=0)))
    # no cam that persists across the clip -> not a facecam video: never split (court lines,
    # doors and on-screen graphics make rectangles around faces in ordinary footage)
    if not boxes:
        return cuts, {}
    # 2) cams that move or grow (editors zoom the cam in for reactions): per shot
    for k, n in shot_n.items():
        if n < 4:
            continue
        P = shot_sum[k] / n
        seen: list = []
        for s in samples:
            if _shot_of(s.f, cuts) != k:
                continue
            for f in s.faces:
                if usable(f) and len(seen) < 6 and not any(
                        abs(f[0] - g[0]) < 0.03 and abs(f[1] - g[1]) < 0.03 for g in seen):
                    seen.append(f)
                    consider(P, f)
    if not boxes:
        return cuts, {}

    # 3) which cams are on screen in each sample frame (a border shows up as an edge in every frame)
    def presence() -> list[tuple]:
        out_states = []
        for _, e in edges:
            ch, cv = _line_maps(unpack(e), 0.5)
            on = [b for b in boxes if _cover(ch, cv, b) >= 0.75]
            on.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
            keep: list = []
            for b in on:  # a zoomed and a normal version of one cam: keep the bigger
                if not any(_iou(b, x) > 0.3 for x in keep):
                    keep.append(b)
            out_states.append(tuple(sorted(keep[:2])))
        return out_states

    by_frame = {s.f: s for s in samples}

    def validated(states: list[tuple]) -> list[tuple]:
        """Drop boxes that rarely hold a face: a facecam shows its person most of the time."""
        good = set()
        for b in boxes:
            on = [frames[j] for j, st in enumerate(states) if b in st]
            with_face = sum(1 for f in on if f in by_frame and any(
                b[0] <= x[0] * W <= b[2] and b[1] <= x[1] * H <= b[3] for x in by_frame[f].faces))
            if on and with_face >= 0.5 * len(on):
                good.add(b)
        return [tuple(b for b in st if b in good) for st in states]

    states = validated(presence())
    global_boxes = {b for st in states for b in st}
    if sum(1 for st in states if st) < 0.2 * len(states) or not global_boxes:
        return cuts, {}  # a cam that's barely on screen: not worth changing the layout
    # 4) stretches with faces but no known cam: a cam that moved for a moment? search just there
    runs, cur = [], []
    for j, st in enumerate(states):
        if not st and (not cur or _shot_of(frames[j], cuts) == _shot_of(frames[cur[-1]], cuts)):
            cur.append(j)
        else:
            if len(cur) >= 4:
                runs.append(cur)
            cur = [] if st else [j]
    if len(cur) >= 4:
        runs.append(cur)
    n_boxes = len(boxes)
    for run in runs:
        P = sum(unpack(edges[j][1]) for j in run) / len(run)
        seen: list = []
        for j in run:
            for f in (by_frame[frames[j]].faces if frames[j] in by_frame else []):
                if usable(f) and len(seen) < 4 and not any(
                        abs(f[0] - g[0]) < 0.03 and abs(f[1] - g[1]) < 0.03 for g in seen):
                    seen.append(f)
                    consider(P, f)
    if len(boxes) > n_boxes:
        states = validated(presence())

    # smooth: a state must hold for a few samples, so one noisy frame can't flip the layout
    sm = list(states)
    for j in range(len(states)):
        win = [states[x] for x in range(max(0, j - 2), min(len(states), j + 3))
               if _shot_of(frames[x], cuts) == _shot_of(frames[j], cuts)]
        sm[j] = max(set(win), key=lambda st: (win.count(st), st == states[j]))

    new_cuts = list(cuts)
    for j in range(1, len(sm)):
        if sm[j] != sm[j - 1] and _shot_of(frames[j], cuts) == _shot_of(frames[j - 1], cuts):
            new_cuts.append(frames[j])
    new_cuts = sorted(set(new_cuts))
    seg_states, seg_len = [], []
    for k in range(len(new_cuts)):
        votes = [sm[j] for j, f in enumerate(frames) if _shot_of(f, new_cuts) == k]
        seg_states.append(max(set(votes), key=votes.count) if votes else ())
        seg_len.append(len(votes))
    # a blip without a cam between cam segments is the cam animating (zooming in/out): keep the split
    for k, st in enumerate(seg_states):
        if not st and seg_len[k] <= 4:
            prev = seg_states[k - 1] if k > 0 else ()
            nxt = seg_states[k + 1] if k + 1 < len(seg_states) else ()
            seg_states[k] = prev or nxt
    out: dict[int, list[list[float]]] = {}
    for k, st in enumerate(seg_states):
        if st:
            out[k] = [[round(b[0] / W, 4), round(b[1] / H, 4), round(b[2] / W, 4), round(b[3] / H, 4)] for b in st]
    return new_cuts, out


def split_shot(a: int, b: int, cams: list[list[float]], ss: list[FaceSample]) -> Shot:
    """SPLIT shot: remember where the face sits inside each cam so the top panel can frame it."""
    fx, fy = [], []
    for cam in cams:
        x0, y0, x1, y1 = cam
        inside = [f for s in ss for f in s.faces if x0 <= f[0] <= x1 and y0 <= f[1] <= y1]
        if inside:
            fx.append(round(float(np.median([(f[0] - x0) / (x1 - x0) for f in inside])), 3))
            fy.append(round(float(np.median([(f[1] - y0) / (y1 - y0) for f in inside])), 3))
        else:
            fx.append(0.5)
            fy.append(0.45)
    return Shot(start=a, end=b, mode="split", cams=cams, face_x=fx, face_y=fy, why="facecam over screen")


def split_panel_frac(cams: list[list[float]], src_w: int, src_h: int, rc: ReframeCfg) -> float:
    """Height of the cam panel in a SPLIT layout, as a fraction of the output height.

    Sized so the cam is cropped as little as possible, within [split_cam_min, split_cam_max].
    """
    if not cams:
        return rc.split_cam_min
    aspects = [(c[2] - c[0]) * src_w / max(1e-6, (c[3] - c[1]) * src_h) for c in cams]
    slot = 9 / len(cams)  # output is 9 wide x 16 tall
    want = slot / (sum(aspects) / len(aspects)) / 16
    return float(np.clip(want, rc.split_cam_min, rc.split_cam_max))
