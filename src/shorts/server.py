"""Shorts API (mounted by the Studio server under /api/shorts): job store, worker, REST routes."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import export as exporter
from . import editor, health, pipeline, settings
from common import ffmpeg
from common.paths import DATA_DIR, resolve
from common.sysinfo import free_disk_gb

from .config import LAYOUT_FILE, Config, load_config, load_job_layout, save_local, with_job_layout
from .models import Focus, Selection, Transcript, VideoMeta, load_json, save_json
from common.progress import Hooks, Reporter
from .stages import captions, download, reframe, render, select, sources, transcribe
from common.runner import SerialRunner
from common.lock import job_lock, lock_owner

JOB_FILE = "job.json"
REVIEWS_FILE = exporter.REVIEWS_FILE
UPLOADS = DATA_DIR / "shorts" / "uploads"
AVATAR = DATA_DIR / "shorts" / "avatar.png"   # the channel card picture (Settings → Channel card)
UPLOAD_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

JobStatus = Literal["queued", "running", "done", "failed", "interrupted"]
StageStatus = Literal["pending", "running", "done", "failed", "planned"]


class StageState(BaseModel):
    key: str
    label: str
    status: StageStatus
    progress: float | None = None
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    eta_s: float | None = None     # running: estimated seconds left in this stage (from its own pace)
    weight: float = 1.0            # share of a typical run (overall progress is weighted by it)


class ExportState(BaseModel):
    status: Literal["queued", "running", "done", "failed"]
    progress: float | None = None
    message: str | None = None
    dir: str | None = None
    files: list[str] = []
    finished_at: str | None = None
    queue: list[str] = []          # clip ids waiting to be exported
    current: str | None = None     # clip id being exported now


class JobState(BaseModel):
    id: str
    source: str
    title: str
    duration: float | None = None
    is_youtube: bool = False
    status: JobStatus = "queued"
    error: str | None = None
    created_at: str
    updated_at: str
    stages: list[StageState]
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    export: ExportState | None = None
    stopping: bool = False         # Stop was pressed; takes effect at the stage's next progress update


class JobCancelled(Exception):
    """Raised from a progress callback when the job was stopped."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_stages() -> list[StageState]:
    return [StageState(key=s.key, label=s.label, status="pending" if s.implemented else "planned", weight=s.weight)
            for s in pipeline.STAGES]


class JobStore:
    """In-memory job states, persisted to workspace/<id>/job.json."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.jobs: dict[str, JobState] = {}
        self._last_save: dict[str, float] = {}
        self._cli_ids: set[str] = set()  # jobs made by the CLI (no job.json): re-read on every scan
        self._last_scan = 0.0
        self._load()

    def rescan(self) -> None:
        """Pick up jobs the CLI created or advanced since startup (throttled)."""
        if time.monotonic() - self._last_scan < 2:
            return
        with self.lock:
            self._load(only_cli=True)

    def _load(self, only_cli: bool = False) -> None:
        self._last_scan = time.monotonic()
        ws = self.cfg.workspace()
        if not ws.exists():
            return
        for d in ws.iterdir():
            if not d.is_dir():
                continue
            if only_cli and d.name in self.jobs and (d.name not in self._cli_ids or (d / JOB_FILE).exists()):
                self._cli_ids.discard(d.name)  # the server's own jobs (or a CLI job it took over) are live in memory
                continue
            try:
                if (d / JOB_FILE).exists():
                    job = load_json(JobState, d / JOB_FILE)
                    job.stages = _merge_stage_defs(job.stages)
                    job.stopping = False
                    if job.status in ("queued", "running"):  # server died mid-job
                        job.status = "interrupted"
                        for s in job.stages:
                            if s.status == "running":
                                s.status, s.message = "pending", "Interrupted"
                    if job.export and job.export.status in ("queued", "running"):
                        job.export.status, job.export.message = "failed", "Interrupted: export again"
                    _reconcile(job, d, self.cfg)
                    if job.status == "done" and any(s.status == "pending" for s in job.stages):
                        job.status = "interrupted"  # finished before newer stages shipped: offer Resume
                    elif job.status == "interrupted" and all(s.status in ("done", "planned") for s in job.stages):
                        job.status = "done"
                elif (d / download.META).exists():  # created by the CLI
                    job = _job_from_cli_dir(d, self.cfg)
                    self._cli_ids.add(job.id)
                else:
                    continue
                self.jobs[job.id] = job
            except Exception:
                traceback.print_exc()

    def get(self, job_id: str) -> JobState | None:
        with self.lock:
            j = self.jobs.get(job_id)
            return j.model_copy(deep=True) if j else None

    def all(self) -> list[JobState]:
        with self.lock:
            return sorted((j.model_copy(deep=True) for j in self.jobs.values()),
                          key=lambda j: j.created_at, reverse=True)

    def put(self, job: JobState) -> None:
        with self.lock:
            self.jobs[job.id] = job
            self._save(job, force=True)

    def update(self, job_id: str, *, stage: str | None = None, force_save: bool = True, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            target = next(s for s in job.stages if s.key == stage) if stage else job
            for k, v in fields.items():
                setattr(target, k, v)
            job.updated_at = _now()
            self._save(job, force=force_save)

    def remove(self, job_id: str) -> None:
        with self.lock:
            self.jobs.pop(job_id, None)

    def _save(self, job: JobState, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_save.get(job.id, 0) < 2:
            return  # progress ticks: persist at most every 2s
        self._last_save[job.id] = now
        d = self.cfg.workspace() / job.id
        d.mkdir(parents=True, exist_ok=True)
        save_json(job, d / JOB_FILE)


def _merge_stage_defs(saved: list[StageState]) -> list[StageState]:
    """Keep saved progress but follow the current stage list (new stages appear automatically)."""
    by_key = {s.key: s for s in saved}
    out = []
    for sd in pipeline.STAGES:
        s = by_key.get(sd.key) or StageState(key=sd.key, label=sd.label, status="pending")
        s.label, s.weight, s.eta_s = sd.label, sd.weight, None
        if not sd.implemented:
            s.status = "planned"
        elif s.status == "planned":
            s.status = "pending"
        out.append(s)
    return out


def _reconcile(job: JobState, d: Path, cfg: Config) -> None:
    """Mark pending stages done when their output is already on disk (e.g. produced by the CLI)."""
    by = {s.key: s for s in job.stages}
    if by["select"].status != "done" or not (d / select.OUT).exists():
        return
    sel = load_json(Selection, d / select.OUT)
    src = by.get("sources")
    if src and src.status == "pending" and (d / download.META).exists():
        meta = load_json(VideoMeta, d / download.META)
        idx = sources.load_index(d)
        have = all((e := idx.clips.get(c.id)) and (sources.sources_dir(d) / e.file).exists() for c in sel.clips)
        if have or sources.full_source(d, meta):  # older jobs read their clips from the full download
            src.status, src.progress = "done", 1.0
            src.message = f"{len(sel.clips)} clip videos" if have else "From the full download"
    rf = by["reframe"]
    if rf.status == "pending" and (not cfg.reframe.enabled or all(
            reframe.plan_path(d, c).exists() for c in sel.clips)):
        rf.status, rf.progress = "done", 1.0
        rf.message = f"{len(sel.clips)} clips reframed" if cfg.reframe.enabled else "Disabled: centre crop"
    cap = by["captions"]
    if cap.status == "pending" and (not cfg.captions.enabled or
                                    all(captions.ass_path(d, c).exists() for c in sel.clips)):
        cap.status, cap.progress = "done", 1.0
        cap.message = f"{len(sel.clips)} clips captioned" if cfg.captions.enabled else "Disabled in configs/shorts.yaml"
    ren = by["render"]
    if ren.status == "pending" and cap.status == "done" and rf.status == "done" and all(
            (d / render.CLIPS_DIR / render.clip_file(d, c, cfg)).exists() for c in sel.clips):
        ren.status, ren.progress = "done", 1.0
        ren.message = f"{len(sel.clips)} previews"


def _job_from_cli_dir(d: Path, cfg: Config) -> JobState:
    meta = load_json(VideoMeta, d / download.META)
    stages = _fresh_stages()
    stages[0].status = "done"
    stages[0].message = download.describe(meta)
    has_t = (d / transcribe.OUT).exists()
    if has_t:
        stages[1].status = "done"
        stages[1].message = "Transcribed via CLI"
    if (d / select.OUT).exists():
        stages[2].status = "done"
        stages[2].message = select.describe(load_json(Selection, d / select.OUT), cached=True)
    created = datetime.fromtimestamp((d / download.META).stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    job = JobState(
        id=meta.id, source=meta.source_url or meta.file, title=meta.title, duration=meta.duration,
        is_youtube=bool(meta.source_url), status="interrupted",
        created_at=created, updated_at=created, stages=stages,
        width=meta.width, height=meta.height, fps=meta.fps,
    )
    _reconcile(job, d, cfg)  # reframe / captions / render outputs already on disk
    if lock_owner(d):  # the CLI is working on it right now
        job.status = "running"
        if cur := next((s for s in job.stages if s.status == "pending"), None):
            cur.status, cur.message = "running", "Running from the command line…"
    elif all(s.status in ("done", "planned") for s in job.stages):
        job.status = "done"
    return job


class StoreHooks(Hooks):
    def __init__(self, store: JobStore, job_id: str, cancelled=lambda: False):
        self.store, self.job_id, self.cancelled = store, job_id, cancelled
        self._t0: dict[str, float] = {}

    def _check(self) -> None:
        if self.cancelled():
            raise JobCancelled()

    def stage_start(self, stage: str) -> None:
        self._check()
        self._t0[stage] = time.monotonic()
        self.store.update(self.job_id, stage=stage, status="running", progress=None, eta_s=None,
                          message=None, started_at=_now(), finished_at=None)

    def stage_done(self, stage: str, message: str | None = None) -> None:
        self.store.update(self.job_id, stage=stage, status="done", progress=1.0, eta_s=None,
                          message=message, finished_at=_now())
        if stage == "download":
            meta = load_json(VideoMeta, self.store.cfg.workspace() / self.job_id / download.META)
            self.store.update(self.job_id, width=meta.width, height=meta.height, fps=meta.fps,
                              duration=meta.duration, title=meta.title)

    def reporter(self, stage: str) -> Reporter:
        def report(fraction: float | None = None, message: str | None = None) -> None:
            self._check()
            fields: dict = {"progress": None if fraction is None else max(0.0, min(1.0, fraction))}
            el = time.monotonic() - self._t0.get(stage, time.monotonic())
            f = fields["progress"]
            # an estimate only once there is enough of the stage to go on (early rates are noisy)
            fields["eta_s"] = round(el * (1 - f) / f) if f is not None and f >= 0.03 and el >= 5 else None
            if message:
                fields["message"] = message
            self.store.update(self.job_id, stage=stage, force_save=False, **fields)
        return report


class Worker:
    """One job at a time: the pipeline is CPU and RAM bound."""

    def __init__(self, store: JobStore, cfg: Config, runner: SerialRunner, cfg_path: Path | None = None):
        self.store, self.cfg, self.cfg_path, self.runner = store, cfg, cfg_path, runner
        self.current: str | None = None
        self._cancel: set[str] = set()       # pipeline runs asked to stop
        self._again: set[str] = set()        # a clip changed while the job was rendering: run once more after
        self._cancel_export: set[str] = set()

    def submit(self, job_id: str) -> None:
        self._cancel.discard(job_id)
        self.runner.drop("shorts", job_id, "pipeline")  # already waiting: keep one queued run, not two
        self.store.update(job_id, status="queued", error=None, stopping=False)
        self.runner.submit("shorts", job_id, "pipeline", lambda: self._handle("run", job_id))

    def rerender(self, job_id: str) -> None:
        """Re-render previews whose inputs changed (a clip edit). A running job renders again when it's done;
        a queued one already will."""
        job = self.store.get(job_id)
        if job and (job.status == "running" or self.current == job_id):
            self._again.add(job_id)
            return
        self.store.update(job_id, stage="render", status="pending", progress=None, eta_s=None, message=None,
                          started_at=None, finished_at=None)
        if not job or job.status != "queued":
            self.submit(job_id)

    def stop(self, job_id: str) -> str:
        """Stop a job's pipeline run (queued: removed at once; running: at its next progress update).
        Returns "stopped" | "stopping" | "idle"."""
        job = self.store.get(job_id)
        if not job:
            return "idle"
        if job.status == "queued" and self.runner.drop("shorts", job_id, "pipeline"):
            self._mark_stopped(job_id)
            return "stopped"
        if job.status in ("queued", "running"):
            self._cancel.add(job_id)
            self.store.update(job_id, stopping=True)
            return "stopping"
        return "idle"

    def stop_export(self, job_id: str) -> str:
        job = self.store.get(job_id)
        ex = job.export if job else None
        if not ex or ex.status not in ("queued", "running"):
            return "idle"
        if ex.status == "queued" and self.runner.drop("shorts", job_id, "export"):
            with self.store.lock:
                e = self.store.jobs[job_id].export
                e.status, e.queue, e.current, e.message, e.finished_at = "failed", [], None, "Export cancelled", _now()
                self.store.update(job_id)
            return "stopped"
        self._cancel_export.add(job_id)
        with self.store.lock:
            self.store.jobs[job_id].export.queue = []
            self.store.jobs[job_id].export.message = "Cancelling after this step…"
            self.store.update(job_id)
        return "stopping"

    def _mark_stopped(self, job_id: str) -> None:
        job = self.store.get(job_id)
        running = next((s for s in job.stages if s.status == "running"), None) if job else None
        if running:
            self.store.update(job_id, stage=running.key, status="pending", progress=None, eta_s=None,
                              message="Stopped: resume to continue from here", finished_at=None)
        self.store.update(job_id, status="interrupted", error=None, stopping=False)

    def submit_export(self, job_id: str, clip_ids: list[str]) -> None:
        """Queue clips for HD export; clicks while an export runs are added to the same queue."""
        with self.store.lock:
            ex = self.store.jobs[job_id].export
            busy = ex is not None and ex.status in ("queued", "running")
            if busy:
                ex.queue = list(dict.fromkeys([*ex.queue, *(c for c in clip_ids if c != ex.current)]))
                self.store.update(job_id)
                return
            self.store.update(job_id, export=ExportState(
                status="queued", queue=list(dict.fromkeys(clip_ids)), message="Waiting for the worker…",
                files=ex.files if ex else [], dir=ex.dir if ex else None))
        self.runner.submit("shorts", job_id, "export", lambda: self._handle("export", job_id))

    def _export(self, job_id: str) -> None:
        store = self.store

        def ex() -> ExportState:
            return store.jobs[job_id].export

        self.current = job_id
        with store.lock:
            ex().status = "running"
            store.update(job_id)
        n_done = 0
        try:
            self.cfg = load_config(self.cfg_path)
            d = self.cfg.workspace() / job_id
            meta = load_json(VideoMeta, d / download.META)
            while True:
                with store.lock:
                    if not ex().queue:
                        break
                    cid = ex().queue.pop(0)
                    ex().current, ex().progress = cid, 0.0
                    left = len(ex().queue)
                    store.update(job_id)

                def report(fraction=None, message=None, left=left):
                    if job_id in self._cancel_export:
                        raise JobCancelled()
                    with store.lock:
                        ex().progress = fraction
                        if message:
                            ex().message = message + (f" · {left} more queued" if left else "")
                    store.update(job_id, force_save=False)

                with job_lock(d):
                    res = exporter.run(d, meta, self.cfg, clip_ids=[cid], report=report)
                n_done += 1
                with store.lock:
                    ex().dir = str(res.dir)
                    ex().files = list(dict.fromkeys([*ex().files, *res.files]))
                    store.update(job_id)
            with store.lock:
                e = ex()
                e.status, e.progress, e.current, e.finished_at = "done", 1.0, None, _now()
                e.message = f"{n_done} clip{'s' * (n_done != 1)} exported"
                store.update(job_id)
        except Exception as err:
            cancelled = job_id in self._cancel_export
            if not cancelled:
                traceback.print_exc()
            with store.lock:
                e = ex()
                e.status, e.current, e.queue, e.finished_at = "failed", None, [], _now()
                e.message = (f"Export cancelled · {n_done} finished" if cancelled
                             else _friendly(err))
                store.update(job_id)
        finally:
            self._cancel_export.discard(job_id)
            self.current = None

    def _handle(self, kind: str, job_id: str) -> None:
        if kind == "export":
            self._export(job_id)
            return
        job = self.store.get(job_id)
        if not job or job.status != "queued":
            return
        self.current = job_id
        self.store.update(job_id, status="running")
        try:  # pick up configs/shorts.yaml edits without restarting the server
            self.cfg = load_config(self.cfg_path)
        except Exception as e:
            self.store.update(job_id, status="failed", error=f"configs/shorts.yaml is invalid: {e}")
            self.current = None
            return
        hooks = StoreHooks(self.store, job_id, cancelled=lambda: job_id in self._cancel)
        try:
            ref = download.JobRef(job.id, self.cfg.workspace() / job.id, job.source,
                                  job.title, job.duration, job.is_youtube)
            pipeline.run(job.source, self.cfg, hooks, job=ref)
            self.store.update(job_id, status="done", stopping=False)
            if job_id in self._again:  # an edit landed after the render had passed that clip
                self._again.discard(job_id)
                self.store.update(job_id, stage="render", status="pending", progress=None, eta_s=None,
                                  message=None, started_at=None, finished_at=None)
                self.submit(job_id)
        except Exception as e:
            if job_id in self._cancel:  # stopped (the stop may surface wrapped in another error)
                self._mark_stopped(job_id)
                return
            traceback.print_exc()
            running = next((s for s in self.store.get(job_id).stages if s.status == "running"), None)
            if running:
                self.store.update(job_id, stage=running.key, status="failed", eta_s=None, finished_at=_now(),
                                  message=_friendly(e))
            self.store.update(job_id, status="failed", error=_friendly(e), stopping=False)
        finally:
            self._cancel.discard(job_id)
            self._again.discard(job_id)  # failed or stopped: Retry renders everything that's stale anyway
            self.current = None


def _friendly(e: BaseException) -> str:
    """One readable line (plus the tool's own message) instead of a bare exception name."""
    msg = (str(e) or "").strip()
    name = type(e).__name__
    hints = {
        "LLMError": "The AI provider failed",
        "DownloadError": "Download failed",
        "FFmpegError": "ffmpeg failed",
        "StageProcessError": "A processing step crashed (often out of memory)",
        "JobBusyError": "This video is busy",
        "SourceError": "Couldn't get the clip's video",
        "ExportError": "Export failed",
        "MemoryError": "Ran out of memory",
        "OSError": "File system error",
    }
    if name in hints and not msg.lower().startswith(hints[name].lower()):
        return f"{hints[name]}: {msg}" if msg else hints[name]
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "Disk full: free some space (delete old projects) and resume"
    return msg or name


# ---------------------------------------------------------------- API

class CreateJob(BaseModel):
    url: str
    focus: str = ""                                   # optional note: what the AI should look for
    focus_mode: Literal["prefer", "only"] = "prefer"


class FocusBody(BaseModel):
    text: str
    mode: Literal["prefer", "only"] = "prefer"
    repick: bool = True                               # pick the moments again now (when the job is idle)


class JobStats(BaseModel):
    clips: int = 0
    approved: int = 0
    rejected: int = 0
    exported: int = 0              # clips with an HD file
    clip_seconds: float = 0.0      # total length of all clips
    picked_by: str | None = None   # "gemini" | "heuristic"


class JobView(JobState):
    stats: JobStats = JobStats()
    focus: Focus | None = None     # the focus note in effect for the next pick
    picked_focus: Focus | None = None  # the note the current picks were made with
    progress: float = 0.0          # 0-1, stages weighted by their typical share of the run
    server_time: str = ""          # lets the browser show elapsed times without clock skew


class ClipEdit(BaseModel):
    title: str | None = None
    hook_text: str | None = None
    show_hook: bool | None = None
    description: str | None = None
    hashtags: list[str] | None = None


class FramingKey(BaseModel):
    f: int
    x: float | None = None    # None = keep following the automatic framing
    a: float = 9 / 16


class FramingBody(BaseModel):
    keys: list[FramingKey]
    keep_cam: bool = True     # facecam shots: the keys set only the gameplay window, the cam stays


class BulkReview(BaseModel):
    clip_ids: list[str]
    review: Literal["approved", "rejected"] | None


class CardEdit(BaseModel):
    name: str | None = None
    handle: str | None = None
    theme: Literal["dark", "light"] | None = None
    verified: bool | None = None
    footer: Literal["date", "none"] | None = None
    remove_avatar: bool = False


class ExportBody(BaseModel):
    clip_ids: list[str] | None = None  # None = all approved clips


class ReviewBody(BaseModel):
    review: Literal["approved", "rejected"] | None


class LayoutBody(BaseModel):
    screen_layout: Literal["split", "pip", "track"]
    cam_size: float | None = None          # None = auto (sized to the cam, 20-30%); else a fraction 0.15-0.45
    background: Literal["blur", "tweet"]
    smart_layout: bool


def _layout_view(cfg: Config, job_dir: Path) -> dict:
    rc = with_job_layout(cfg, job_dir).reframe
    lo, hi = sorted((rc.split_cam_min, rc.split_cam_max))
    return {"screen_layout": rc.screen_layout, "cam_size": lo if abs(hi - lo) < 1e-6 else None,
            "cam_range": [lo, hi], "background": rc.background, "smart_layout": rc.smart_layout,
            "custom": bool(load_job_layout(job_dir))}


_load_reviews = exporter.load_reviews
_stats_cache: dict[str, tuple[tuple, JobStats]] = {}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def job_stats(d: Path) -> JobStats:
    """Clip counts for one job (cached until selection, reviews or HD files change)."""
    sel_p, rev_p, fin = d / select.OUT, d / REVIEWS_FILE, d / exporter.FINAL_DIR
    key = (_mtime(sel_p), _mtime(rev_p), _mtime(fin))
    hit = _stats_cache.get(d.name)
    if hit and hit[0] == key:
        return hit[1]
    st = JobStats()
    if sel_p.exists():
        try:
            sel = load_json(Selection, sel_p)
            reviews = _load_reviews(d)
            ids = {c.id for c in sel.clips}
            hd = {f.name.split("-")[0] for f in fin.iterdir()   # finished renders only, not a stopped .tmp.mp4
                  if f.suffix == ".mp4" and not f.name.endswith(".tmp.mp4")} if fin.is_dir() else set()
            st = JobStats(clips=len(sel.clips), approved=sum(reviews.get(i) == "approved" for i in ids),
                          rejected=sum(reviews.get(i) == "rejected" for i in ids), exported=len(ids & hd),
                          clip_seconds=round(sum(c.duration for c in sel.clips), 1), picked_by=sel.source)
        except Exception:  # noqa: BLE001  (a half-written file: counts come next poll)
            pass
    _stats_cache[d.name] = (key, st)
    return st


def weighted_progress(stages: list[StageState]) -> float:
    real = [s for s in stages if s.status != "planned"]
    total = sum(s.weight for s in real)
    if not total:
        return 0.0
    got = sum(s.weight * (1.0 if s.status == "done" else (s.progress or 0) if s.status == "running" else 0)
              for s in real)
    return round(got / total, 4)


def _dir_size(p: Path) -> int:
    """Bytes under p; hard links (exports share the HD renders' data) are counted once."""
    seen: set[tuple[int, int]] = set()
    total = 0
    stack = [p]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if (st.st_dev, st.st_ino) not in seen:
                                seen.add((st.st_dev, st.st_ino))
                                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _picked_focus(d: Path) -> Focus | None:
    """The focus note of the current picks (read from selection.json without loading every clip)."""
    p = d / select.OUT
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text()).get("focus")
        return Focus(**raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _same_focus_text(f: Focus | None, text: str, mode: str) -> bool:
    return bool(f) and f.text == re.sub(r"\s+", " ", text).strip()[:select.FOCUS_MAX] and f.mode == mode


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "clip"


def make_router(runner: SerialRunner, cfg_path: Path | None = None) -> APIRouter:
    cfg = load_config(cfg_path)
    store = JobStore(cfg)
    worker = Worker(store, cfg, runner, cfg_path)
    router = APIRouter()

    @router.get("/system")
    def system():
        return health.summary(cfg)

    def view(job: JobState) -> JobView:
        d = cfg.workspace() / job.id
        return JobView(**job.model_dump(), stats=job_stats(d), progress=weighted_progress(job.stages),
                       server_time=_now(), focus=select.load_focus(d), picked_focus=_picked_focus(d))

    @router.get("/jobs", response_model=list[JobView])
    def list_jobs():
        store.rescan()
        return [view(j) for j in store.all()]

    def start_job(source: str, focus: str = "", focus_mode: str = "prefer") -> JobState:
        """URL or local file -> job (an existing one is resumed instead of duplicated). A focus note is saved
        with it; on an existing finished job a different note picks the moments again."""
        try:
            ref = download.resolve_job(source, cfg)
        except download.DownloadError as e:
            raise HTTPException(422, str(e))
        existing = store.get(ref.id)
        if existing:
            if focus.strip() and not _same_focus_text(select.load_focus(ref.dir), focus, focus_mode):
                select.save_focus(ref.dir, focus, focus_mode)
                if existing.status not in ("queued", "running") and worker.current != ref.id \
                        and (ref.dir / transcribe.OUT).exists():
                    repick(ref.id)
                    return store.get(ref.id)
            if existing.status in ("failed", "interrupted"):
                worker.submit(ref.id)  # resume: finished stages are cached on disk
            return store.get(ref.id)
        select.save_focus(ref.dir, focus, focus_mode)
        now = _now()
        store.put(JobState(id=ref.id, source=ref.source, title=ref.title, duration=ref.duration,
                           is_youtube=ref.is_youtube, created_at=now, updated_at=now,
                           stages=_fresh_stages()))
        worker.submit(ref.id)
        return store.get(ref.id)

    @router.post("/jobs", response_model=JobView)
    def create_job(body: CreateJob):
        url = body.url.strip()
        if not download.is_url(url):
            raise HTTPException(422, "Paste a full video URL (https://…)")
        return view(start_job(url, body.focus, body.focus_mode))

    @router.put("/uploads/{name}", response_model=JobView)
    async def upload_video(name: str, request: Request, focus: str = "",
                           focus_mode: Literal["prefer", "only"] = "prefer"):
        """The browser streams a local video file as the request body; it is saved to data/shorts/uploads/
        (the job hard-links it, so no second copy on disk) and processed like a downloaded video."""
        name = Path(name).name
        suffix = Path(name).suffix.lower()
        if not name or name.startswith(".") or suffix not in UPLOAD_EXTS:
            raise HTTPException(422, f"Expected a video file ({', '.join(sorted(UPLOAD_EXTS))})")
        size = int(request.headers.get("content-length") or 0)
        if free_disk_gb(UPLOADS) < max(3.0, size / 1e9 * 2 + 1):  # the upload + renders need room
            raise HTTPException(507, "Not enough free disk space for this video")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        dst = UPLOADS / name
        if dst.exists() and dst.stat().st_size == size:
            return view(start_job(str(dst), focus, focus_mode))  # same file again: reuse it (and its job)
        n = 2
        while dst.exists():
            dst = UPLOADS / f"{Path(name).stem} ({n}){suffix}"
            n += 1
        tmp = dst.with_name(dst.name + ".part")
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
            if size and tmp.stat().st_size != size:
                raise HTTPException(400, "Upload incomplete, try again")
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)
        try:
            ffmpeg.video_info(dst)
        except Exception:
            dst.unlink(missing_ok=True)
            raise HTTPException(422, "That file isn't a playable video")
        return view(await run_in_threadpool(start_job, str(dst), focus, focus_mode))

    @router.get("/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str):
        store.rescan()
        return view(_job_or_404(store, job_id))

    @router.post("/jobs/{job_id}/stop", response_model=JobView)
    def stop_job(job_id: str):
        """Stop processing. Finished stages stay cached: Resume continues from the stopped stage."""
        job = _job_or_404(store, job_id)
        if job.status not in ("queued", "running"):
            raise HTTPException(409, "This video isn't processing")
        worker.stop(job_id)
        return view(store.get(job_id))

    @router.post("/jobs/{job_id}/export/stop", response_model=JobView)
    def stop_export(job_id: str):
        job = _job_or_404(store, job_id)
        if worker.stop_export(job_id) == "idle":
            raise HTTPException(409, "No export is running")
        return view(store.get(job_id))

    @router.post("/jobs/{job_id}/retry", response_model=JobView)
    def retry_job(job_id: str):
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running"):
            raise HTTPException(409, "Job is already in progress")
        worker.submit(job_id)
        return view(store.get(job_id))

    @router.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, exports: bool = False):
        """Delete the project's working files; with exports=true also its exported HD clips."""
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running") or worker.current == job_id:
            raise HTTPException(409, "Can't delete a job while it's processing")
        if owner := lock_owner(cfg.workspace() / job_id):
            raise HTTPException(409, f"Can't delete: this video is being processed from the terminal (PID {owner})")
        if exports and (cfg.workspace() / job_id / download.META).exists():
            try:
                out = exporter.export_dir(load_config(cfg_path), load_json(VideoMeta, cfg.workspace() / job_id / download.META))
                if out.is_dir() and out.resolve().parent == load_config(cfg_path).export.out_dir().resolve():
                    shutil.rmtree(out)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"Couldn't delete the exported files: {_friendly(e)}") from None
        store.remove(job_id)
        _stats_cache.pop(job_id, None)
        d = (cfg.workspace() / job_id).resolve()
        if d.parent == cfg.workspace().resolve() and d.is_dir():
            shutil.rmtree(d)

    @router.get("/jobs/{job_id}/transcript")
    def get_transcript(job_id: str):
        _job_or_404(store, job_id)
        path = cfg.workspace() / job_id / transcribe.OUT
        if not path.exists():
            raise HTTPException(404, "Not transcribed yet")
        t = load_json(Transcript, path)
        return {
            "model": t.model,
            "word_count": sum(len(s.words) for s in t.sentences),
            "sentences": [{"id": s.id, "start": s.start, "end": s.end, "text": s.text}
                          for s in t.sentences],
        }

    @router.get("/jobs/{job_id}/clips")
    def get_clips(job_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(404, "No moments found yet")
        sel = load_json(Selection, d / select.OUT)
        reviews = _load_reviews(d)
        live = load_config(cfg_path)
        meta = load_json(VideoMeta, d / download.META) if (d / download.META).exists() else None
        clips = []
        for c in sel.clips:
            f = render.clip_file(d, c, live)
            ready = (d / render.CLIPS_DIR / f).exists()
            hd = d / exporter.FINAL_DIR / render.clip_file(d, c, live, live.export)
            clips.append({**c.model_dump(), "duration": round(c.duration, 2),
                          "review": reviews.get(c.id), "ready": ready,
                          "url": f"/api/shorts/jobs/{job_id}/clips/{c.id}/video?v={f[-10:-4]}" if ready else None,
                          "final_url": f"/api/shorts/jobs/{job_id}/clips/{c.id}/final?v={hd.stem[-6:]}" if hd.exists() else None,
                          "upload_text": exporter.upload_text(c, meta) if meta else ""})
        return {"source": sel.source, "model": sel.model, "note": sel.note, "candidates": sel.candidates,
                "rejected": sel.rejected, "clips": clips}

    @router.get("/jobs/{job_id}/clips/{clip_id}/video")
    def get_clip_video(job_id: str, clip_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(404, "No clips")
        clip = next((c for c in load_json(Selection, d / select.OUT).clips if c.id == clip_id), None)
        f = d / render.CLIPS_DIR / render.clip_file(d, clip, load_config(cfg_path)) if clip else None
        if not f or not f.exists():
            raise HTTPException(404, "Preview not rendered yet")
        return FileResponse(f, media_type="video/mp4",
                            filename=f"{clip.rank:02d}-{_slug(clip.title)}.mp4", content_disposition_type="inline")

    @router.put("/jobs/{job_id}/clips/{clip_id}/review")
    def set_review(job_id: str, clip_id: str, body: ReviewBody):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        with store.lock:
            reviews = _load_reviews(d)
            if body.review is None:
                reviews.pop(clip_id, None)
            else:
                reviews[clip_id] = body.review
            tmp = d / (REVIEWS_FILE + ".tmp")
            tmp.write_text(json.dumps(reviews, indent=1))
            tmp.replace(d / REVIEWS_FILE)
        return {"id": clip_id, "review": body.review}

    @router.post("/jobs/{job_id}/export", response_model=JobView)
    def export_job(job_id: str, body: ExportBody):
        """HD export of the given clips (default: all approved ones). Adds to a running export's queue."""
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(409, "No clips yet")
        sel = load_json(Selection, d / select.OUT)
        ids = {c.id for c in sel.clips}
        if body.clip_ids is None:
            reviews = _load_reviews(d)
            wanted = [c.id for c in sel.clips if reviews.get(c.id) == "approved"]
            if not wanted:
                raise HTTPException(409, "Approve at least one clip first")
        else:
            wanted = [c for c in body.clip_ids if c in ids]
            if not wanted:
                raise HTTPException(404, "Unknown clip")
        worker.submit_export(job_id, wanted)
        return view(store.get(job_id))

    @router.get("/jobs/{job_id}/clips/{clip_id}/final")
    def get_clip_final(job_id: str, clip_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        clip = next((c for c in load_json(Selection, d / select.OUT).clips if c.id == clip_id), None) \
            if (d / select.OUT).exists() else None
        live = load_config(cfg_path)
        f = d / exporter.FINAL_DIR / render.clip_file(d, clip, live, live.export) if clip else None
        if not f or not f.exists():
            raise HTTPException(404, "Not exported yet")
        return FileResponse(f, media_type="video/mp4", filename=f"{clip.rank:02d}-{_slug(clip.title)}-hd.mp4")

    @router.get("/jobs/{job_id}/layout")
    def get_layout(job_id: str):
        _job_or_404(store, job_id)
        return _layout_view(load_config(cfg_path), cfg.workspace() / job_id)

    @router.put("/jobs/{job_id}/layout")
    def set_layout(job_id: str, body: LayoutBody):
        """This video's framing (facecam layout, cam size, background); re-renders its previews. Moments,
        transcript and face analysis are reused (only switching to/from "track" redoes the analysis)."""
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running") or worker.current == job_id:
            raise HTTPException(409, "Job is in progress: change the layout when it is done")
        d = cfg.workspace() / job_id
        base = load_config(cfg_path).reframe
        data = {"screen_layout": body.screen_layout, "background": body.background,
                "smart_layout": body.smart_layout}
        if body.cam_size is None:  # auto: back to the shorts.yaml range
            data["split_cam_min"], data["split_cam_max"] = base.split_cam_min, base.split_cam_max
        else:
            v = min(max(body.cam_size, 0.15), 0.45)
            data["split_cam_min"] = data["split_cam_max"] = round(v, 3)
        with store.lock:
            tmp = d / (LAYOUT_FILE + ".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(d / LAYOUT_FILE)
        if (d / select.OUT).exists():
            for key in ("reframe", "captions", "render"):
                store.update(job_id, stage=key, status="pending", progress=None, message=None,
                             started_at=None, finished_at=None)
            worker.submit(job_id)
        return _layout_view(load_config(cfg_path), d)

    def repick(job_id: str) -> None:
        d = cfg.workspace() / job_id
        (d / select.OUT).unlink(missing_ok=True)
        for key in ("select", "sources", "reframe", "captions", "render"):
            store.update(job_id, stage=key, status="pending", progress=None, eta_s=None, message=None,
                         started_at=None, finished_at=None)
        worker.submit(job_id)

    @router.post("/jobs/{job_id}/reselect", response_model=JobView)
    def reselect_job(job_id: str):
        """Ask Gemini again (e.g. after prompt changes): redo moments -> reframe -> captions -> previews."""
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running") or worker.current == job_id:
            raise HTTPException(409, "Job is already in progress")
        if not (cfg.workspace() / job_id / transcribe.OUT).exists():
            raise HTTPException(409, "Not transcribed yet")
        repick(job_id)
        return view(store.get(job_id))

    @router.put("/jobs/{job_id}/focus", response_model=JobView)
    def set_focus(job_id: str, body: FocusBody):
        """Change (or clear, with empty text) what the AI looks for. With repick, the moments are picked again
        right away when the job is idle and transcribed; otherwise the note applies to the next pick."""
        job = _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        busy = job.status in ("queued", "running") or worker.current == job_id
        if busy and (d / select.OUT).exists():
            raise HTTPException(409, "This video is processing: change the focus when it's done (or stop it first)")
        select.save_focus(d, body.text, body.mode)
        if body.repick and not busy and (d / transcribe.OUT).exists():
            repick(job_id)
        return view(store.get(job_id))

    @router.get("/jobs/{job_id}/source")
    def get_source(job_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / download.META).exists():
            raise HTTPException(404, "Not downloaded yet")
        meta = load_json(VideoMeta, d / download.META)
        if meta.mode != "full" or meta.source_deleted or not (d / meta.file).exists():
            raise HTTPException(404, "The full video isn't kept for this job: only each clip's own video")
        return FileResponse(d / meta.file)

    def write_reviews(d: Path, reviews: dict) -> None:
        tmp = d / (REVIEWS_FILE + ".tmp")
        tmp.write_text(json.dumps(reviews, indent=1))
        tmp.replace(d / REVIEWS_FILE)

    @router.put("/jobs/{job_id}/reviews")
    def set_reviews(job_id: str, body: BulkReview):
        """Approve / reject / clear several clips at once."""
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(409, "No clips yet")
        ids = {c.id for c in load_json(Selection, d / select.OUT).clips}
        wanted = [c for c in body.clip_ids if c in ids]
        with store.lock:
            reviews = _load_reviews(d)
            for cid in wanted:
                if body.review is None:
                    reviews.pop(cid, None)
                else:
                    reviews[cid] = body.review
            write_reviews(d, reviews)
        return {"updated": len(wanted), "review": body.review}

    def resubmit_render(job_id: str, stages=("reframe", "captions", "render")) -> None:
        for key in stages:
            store.update(job_id, stage=key, status="pending", progress=None, eta_s=None, message=None,
                         started_at=None, finished_at=None)
        worker.submit(job_id)

    def renders_only_or_409(job: JobState, what: str) -> None:
        """Clip edits are fine while the job is only (re-)rendering previews; not while it picks or frames."""
        busy = job.status in ("queued", "running") or worker.current == job.id
        if busy and any(st.status not in ("done", "planned") for st in job.stages if st.key != "render"):
            raise HTTPException(409, f"This video is processing: {what} when it's done (or stop it first)")

    def idle_or_409(job: JobState, what: str) -> None:
        if job.status in ("queued", "running") or worker.current == job.id:
            raise HTTPException(409, f"This video is processing: {what} when it's done (or stop it first)")

    @router.patch("/jobs/{job_id}/clips/{clip_id}")
    def edit_clip(job_id: str, clip_id: str, body: ClipEdit):
        """Edit a clip's title, on-screen hook, description or hashtags. Changes that show in the video (hook
        text / banner on or off) re-render that clip's preview; existing HD files are re-exported on demand."""
        job = _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(409, "No clips yet")
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(422, "Nothing to change")
        for k in ("title", "hook_text", "description"):
            if k in changes:
                changes[k] = changes[k].strip()
        if "title" in changes and not changes["title"]:
            raise HTTPException(422, "The title can't be empty")
        if len(changes.get("title", "")) > 100:
            raise HTTPException(422, "Titles are limited to 100 characters on YouTube")
        if len(changes.get("hook_text", "")) > 120:
            raise HTTPException(422, "Keep the hook under 120 characters: it has to be read in 3 seconds")
        if len(changes.get("description", "")) > 4500:
            raise HTTPException(422, "Description is too long (YouTube's limit is 5000 characters)")
        if "hashtags" in changes:
            tags = []
            for t in changes["hashtags"]:
                t = re.sub(r"[^\w]", "", t.strip().lstrip("#"), flags=re.UNICODE)
                if t and f"#{t}" not in tags:
                    tags.append(f"#{t}")
            changes["hashtags"] = tags[:15]
        with store.lock:
            sel = load_json(Selection, d / select.OUT)
            clip = next((c for c in sel.clips if c.id == clip_id), None)
            if not clip:
                raise HTTPException(404, "Unknown clip")
            visual_before = (captions.hook_text(clip), clip.show_hook)
            for k, v in changes.items():
                setattr(clip, k, v)
            visual = (captions.hook_text(clip), clip.show_hook) != visual_before
            if visual:
                renders_only_or_409(job, "change the on-screen text")
            save_json(sel, d / select.OUT)
        rerender = False
        if visual:
            live = with_job_layout(load_config(cfg_path), d)
            try:
                captions.refresh_clip(d, live, clip)
                rerender = True
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"Saved, but the captions couldn't be rebuilt: {_friendly(e)}") from None
            worker.rerender(job_id)
        return {"clip": {**clip.model_dump(), "duration": round(clip.duration, 2)}, "rerender": rerender}

    def clip_or_404(d: Path, clip_id: str):
        if not (d / select.OUT).exists():
            raise HTTPException(409, "No clips yet")
        sel = load_json(Selection, d / select.OUT)
        clip = next((c for c in sel.clips if c.id == clip_id), None)
        if not clip:
            raise HTTPException(404, "Unknown clip")
        return sel, clip

    def meta_or_409(d: Path) -> VideoMeta:
        if not (d / download.META).exists():
            raise HTTPException(409, "Not downloaded yet")
        return load_json(VideoMeta, d / download.META)

    @router.get("/jobs/{job_id}/clips/{clip_id}/editor")
    def get_editor(job_id: str, clip_id: str):
        """What the clip editor draws: automatic framing per shot, manual keys, faces, removed pauses."""
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        sel, clip = clip_or_404(d, clip_id)
        meta = meta_or_409(d)
        live = load_config(cfg_path)
        order = sorted(sel.clips, key=lambda c: c.rank)
        k = next(i for i, c in enumerate(order) if c.id == clip_id)
        f = render.clip_file(d, clip, live)
        ready = (d / render.CLIPS_DIR / f).exists()
        return {
            **editor.view(d, meta, clip, live),
            "clip": {**clip.model_dump(), "duration": round(clip.duration, 2),
                     "review": _load_reviews(d).get(clip.id), "ready": ready,
                     "url": f"/api/shorts/jobs/{job_id}/clips/{clip.id}/video?v={f[-10:-4]}" if ready else None},
            "prev": order[k - 1].id if k > 0 else None,
            "next": order[k + 1].id if k + 1 < len(order) else None,
            "index": k + 1,
            "count": len(order),
            "proxy_url": f"/api/shorts/jobs/{job_id}/clips/{clip.id}/proxy",
        }

    @router.get("/jobs/{job_id}/clips/{clip_id}/proxy")
    async def get_proxy(job_id: str, clip_id: str):
        """The clip's full 16:9 frame as a small mp4 for the editor (made on first open, then cached)."""
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        _, clip = clip_or_404(d, clip_id)
        meta = meta_or_409(d)
        try:
            p = await run_in_threadpool(editor.ensure_proxy, d, meta, clip, load_config(cfg_path))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Couldn't prepare the editor video: {_friendly(e)}") from None
        return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=86400"})

    @router.post("/jobs/{job_id}/clips/{clip_id}/proxy", status_code=204)
    async def warm_proxy(job_id: str, clip_id: str):
        """Build the editor video ahead of time (the page warms the next clip's while you edit this one)."""
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        _, clip = clip_or_404(d, clip_id)
        meta = meta_or_409(d)
        try:
            await run_in_threadpool(editor.ensure_proxy, d, meta, clip, load_config(cfg_path))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Couldn't prepare the editor video: {_friendly(e)}") from None

    @router.put("/jobs/{job_id}/clips/{clip_id}/framing")
    def set_framing(job_id: str, clip_id: str, body: FramingBody):
        """Save the window keys from the editor (empty list = automatic again) and re-render that clip."""
        job = _job_or_404(store, job_id)
        renders_only_or_409(job, "change the framing")
        d = cfg.workspace() / job_id
        _, clip = clip_or_404(d, clip_id)
        meta = meta_or_409(d)
        live = with_job_layout(load_config(cfg_path), d)
        before = render.clip_file(d, clip, live)
        try:
            fr = editor.save_keys(d, meta, clip, live, [editor.framing.Key(**k.model_dump()) for k in body.keys],
                                  body.keep_cam)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        try:
            captions.refresh_clip(d, live, clip)  # captions move off burned-in text through the new window
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Saved, but the captions couldn't be rebuilt: {_friendly(e)}") from None
        rerender = render.clip_file(d, clip, live) != before
        if rerender:
            worker.rerender(job_id)
        return {"keys": [k.model_dump() for k in fr.keys], "keep_cam": fr.keep_cam, "rerender": rerender}

    @router.post("/jobs/{job_id}/rerender", response_model=JobView)
    def rerender(job_id: str):
        """Rebuild framing, captions and previews with the current settings (moments and transcript are kept)."""
        job = _job_or_404(store, job_id)
        idle_or_409(job, "re-render")
        if not (cfg.workspace() / job_id / select.OUT).exists():
            raise HTTPException(409, "No clips yet")
        resubmit_render(job_id, ("sources", "reframe", "captions", "render"))
        return view(store.get(job_id))

    @router.get("/jobs/{job_id}/exports.zip")
    def download_zip(job_id: str):
        """Every exported clip of this video (mp4 + upload text) as one zip, streamed (no temp copy on disk)."""
        from fastapi.responses import StreamingResponse
        import zipfile

        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / download.META).exists():
            raise HTTPException(404, "Nothing exported yet")
        out = exporter.export_dir(load_config(cfg_path), load_json(VideoMeta, d / download.META))
        files = sorted(f for f in out.iterdir() if f.suffix in (".mp4", ".txt")) if out.is_dir() else []
        if not files:
            raise HTTPException(404, "Nothing exported yet: export some clips first")

        class Sink:
            def __init__(self):
                self.buf = bytearray()

            def write(self, b):
                self.buf += b
                return len(b)

            def flush(self):
                pass

        def gen():
            sink = Sink()
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as z:  # mp4 doesn't compress
                for f in files:
                    with z.open(f.name, "w", force_zip64=f.stat().st_size > 2**31) as zf, f.open("rb") as src:
                        while chunk := src.read(1 << 20):
                            zf.write(chunk)
                            if len(sink.buf) >= 1 << 20:
                                yield bytes(sink.buf)
                                sink.buf.clear()
                    yield bytes(sink.buf)
                    sink.buf.clear()
            yield bytes(sink.buf)

        return StreamingResponse(gen(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{out.name}.zip"'})

    @router.get("/storage")
    def storage():
        """Disk used per project (workspace + its exports) and in total. Walks the folders: call on demand."""
        live = load_config(cfg_path)
        rows = []
        for j in store.all():
            d = live.workspace() / j.id
            ws = _dir_size(d) if d.is_dir() else 0
            ex = 0
            if (d / download.META).exists():
                try:
                    out = exporter.export_dir(live, load_json(VideoMeta, d / download.META))
                    ex = _dir_size(out) if out.is_dir() else 0
                except Exception:  # noqa: BLE001
                    pass
            rows.append({"id": j.id, "title": j.title, "status": j.status, "created_at": j.created_at,
                         "workspace_bytes": ws, "exports_bytes": ex})
        rows.sort(key=lambda r: r["workspace_bytes"] + r["exports_bytes"], reverse=True)
        up = _dir_size(UPLOADS) if UPLOADS.is_dir() else 0
        return {"projects": rows, "uploads_bytes": up,
                "total_bytes": sum(r["workspace_bytes"] + r["exports_bytes"] for r in rows) + up,
                "disk_free_gb": round(health.disk_free(live), 1)}

    # --- settings (configs/shorts.local.yaml)

    @router.get("/settings")
    def get_settings():
        return settings.view(cfg_path)

    @router.put("/settings")
    def put_settings(changes: dict[str, Any]):
        try:
            return settings.update(changes, cfg_path)
        except settings.SettingsError as e:
            raise HTTPException(422, str(e)) from None

    def card_view() -> dict:
        c = load_config(cfg_path).card
        avatar = resolve(c.avatar) if c.avatar else None
        return {"name": c.name, "handle": c.handle, "theme": c.theme, "verified": c.verified,
                "footer": c.footer, "has_avatar": bool(avatar and avatar.exists())}

    @router.get("/settings/card")
    def get_card():
        return card_view()

    @router.put("/settings/card")
    def put_card(body: CardEdit):
        vals = body.model_dump(exclude_none=True, exclude={"remove_avatar"})
        if "name" in vals:
            vals["name"] = vals["name"].strip()
            if not vals["name"] or len(vals["name"]) > 50:
                raise HTTPException(422, "Display name: 1-50 characters")
        if "handle" in vals:
            h = vals["handle"].strip().lstrip("@")
            if not re.fullmatch(r"[\w.\-]{1,30}", h):
                raise HTTPException(422, "Handle: letters, numbers, dots, dashes or underscores (max 30)")
            vals["handle"] = "@" + h
        if body.remove_avatar:
            vals["avatar"] = None
            AVATAR.unlink(missing_ok=True)
        save_local("card", vals)
        return card_view()

    @router.put("/settings/avatar")
    async def put_avatar(request: Request):
        from io import BytesIO

        from PIL import Image, UnidentifiedImageError

        data = await request.body()
        if not data:
            raise HTTPException(422, "Empty file")
        if len(data) > 15_000_000:
            raise HTTPException(413, "Image too large (max 15 MB)")
        try:
            im = Image.open(BytesIO(data))
            im.load()
            im = im.convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(422, "That file isn't an image Studio can read (use PNG or JPG)") from None
        side = min(im.size)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                      (im.width + side) // 2, (im.height + side) // 2)).resize((512, 512), Image.LANCZOS)
        AVATAR.parent.mkdir(parents=True, exist_ok=True)
        im.save(AVATAR)
        save_local("card", {"avatar": str(AVATAR.relative_to(DATA_DIR.parent))})
        return card_view()

    @router.get("/settings/card/preview")
    def card_preview(text: str = "nobody was ready for what he said next", v: str = ""):
        from io import BytesIO

        from fastapi.responses import Response
        from PIL import Image

        from .card import layout, render_card

        cc = load_config(cfg_path).card
        lay = layout(cc, 1080, 1920, 1920, 1080, text[:200])
        tmp = DATA_DIR.parent / ".cache" / "tmp" / f"card-preview-{threading.get_ident()}.png"
        render_card(cc, lay, tmp)
        im = Image.open(tmp)
        x, y, w, h = lay.card
        pad = 40
        ox, oy = max(0, x - pad), max(0, y - pad)
        crop = im.crop((ox, oy, min(im.width, x + w + pad), min(im.height, y + h + pad)))
        bg = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        vx, vy, vw, vh = lay.video
        bg.paste((60, 64, 82, 255), (vx - ox, vy - oy, vx - ox + vw, vy - oy + vh))
        bg.alpha_composite(crop)
        tmp.unlink(missing_ok=True)
        buf = BytesIO()
        bg.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    router.store, router.worker = store, worker  # read by the Studio's queue view
    return router


def _job_or_404(store: JobStore, job_id: str) -> JobState:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job
