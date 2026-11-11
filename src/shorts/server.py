"""FastAPI backend: job queue + REST API + serves the built React app."""
from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import export as exporter
from . import health, pipeline
from .config import PROJECT_ROOT, Config, load_config
from .models import Selection, Transcript, VideoMeta, load_json, save_json
from .progress import Hooks, Reporter
from .stages import captions, download, reframe, render, select, transcribe
from .utils.lock import job_lock, lock_owner

JOB_FILE = "job.json"
REVIEWS_FILE = exporter.REVIEWS_FILE
WEB_DIST = PROJECT_ROOT / "web" / "dist"

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_stages() -> list[StageState]:
    return [StageState(key=s.key, label=s.label, status="pending" if s.implemented else "planned")
            for s in pipeline.STAGES]


class JobStore:
    """In-memory job states, persisted to workspace/<id>/job.json."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.jobs: dict[str, JobState] = {}
        self._last_save: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        ws = self.cfg.workspace()
        if not ws.exists():
            return
        for d in ws.iterdir():
            if not d.is_dir():
                continue
            try:
                if (d / JOB_FILE).exists():
                    job = load_json(JobState, d / JOB_FILE)
                    job.stages = _merge_stage_defs(job.stages)
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
                    job = _job_from_cli_dir(d)
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
        s.label = sd.label
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
    rf = by["reframe"]
    if rf.status == "pending" and (not cfg.reframe.enabled or all(
            reframe.plan_path(d, c).exists() for c in sel.clips)):
        rf.status, rf.progress = "done", 1.0
        rf.message = f"{len(sel.clips)} clips reframed" if cfg.reframe.enabled else "Disabled: centre crop"
    cap = by["captions"]
    if cap.status == "pending" and (not cfg.captions.enabled or
                                    all(captions.ass_path(d, c).exists() for c in sel.clips)):
        cap.status, cap.progress = "done", 1.0
        cap.message = f"{len(sel.clips)} clips captioned" if cfg.captions.enabled else "Disabled in config.yaml"
    ren = by["render"]
    if ren.status == "pending" and cap.status == "done" and rf.status == "done" and all(
            (d / render.CLIPS_DIR / render.clip_file(d, c, cfg)).exists() for c in sel.clips):
        ren.status, ren.progress = "done", 1.0
        ren.message = f"{len(sel.clips)} previews"


def _job_from_cli_dir(d: Path) -> JobState:
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
    return JobState(
        id=meta.id, source=meta.source_url or meta.file, title=meta.title, duration=meta.duration,
        is_youtube=bool(meta.source_url), status="done" if all(s.status in ("done", "planned") for s in stages) else "interrupted",
        created_at=created, updated_at=created, stages=stages,
        width=meta.width, height=meta.height, fps=meta.fps,
    )


class StoreHooks(Hooks):
    def __init__(self, store: JobStore, job_id: str):
        self.store, self.job_id = store, job_id

    def stage_start(self, stage: str) -> None:
        self.store.update(self.job_id, stage=stage, status="running", progress=None,
                          message=None, started_at=_now(), finished_at=None)

    def stage_done(self, stage: str, message: str | None = None) -> None:
        self.store.update(self.job_id, stage=stage, status="done", progress=1.0,
                          message=message, finished_at=_now())
        if stage == "download":
            meta = load_json(VideoMeta, self.store.cfg.workspace() / self.job_id / download.META)
            self.store.update(self.job_id, width=meta.width, height=meta.height, fps=meta.fps,
                              duration=meta.duration, title=meta.title)

    def reporter(self, stage: str) -> Reporter:
        def report(fraction: float | None = None, message: str | None = None) -> None:
            fields = {"progress": fraction}
            if message:
                fields["message"] = message
            self.store.update(self.job_id, stage=stage, force_save=False, **fields)
        return report


class Worker:
    """One job at a time: the pipeline is CPU and RAM bound."""

    def __init__(self, store: JobStore, cfg: Config, cfg_path: Path | None = None):
        self.store, self.cfg, self.cfg_path = store, cfg, cfg_path
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.current: str | None = None
        threading.Thread(target=self._loop, daemon=True, name="pipeline-worker").start()

    def submit(self, job_id: str) -> None:
        self.store.update(job_id, status="queued", error=None)
        self.q.put(("run", job_id))

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
        self.q.put(("export", job_id))

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
            traceback.print_exc()
            with store.lock:
                e = ex()
                e.status, e.current, e.queue, e.finished_at = "failed", None, [], _now()
                e.message = str(err) or type(err).__name__
                store.update(job_id)
        finally:
            self.current = None

    def _loop(self) -> None:
        while True:
            kind, job_id = self.q.get()
            if kind == "export":
                self._export(job_id)
                continue
            job = self.store.get(job_id)
            if not job or job.status != "queued":
                continue
            self.current = job_id
            self.store.update(job_id, status="running")
            try:  # pick up config.yaml edits without restarting the server
                self.cfg = load_config(self.cfg_path)
            except Exception as e:
                self.store.update(job_id, status="failed", error=f"config.yaml is invalid: {e}")
                self.current = None
                continue
            hooks = StoreHooks(self.store, job_id)
            try:
                ref = download.JobRef(job.id, self.cfg.workspace() / job.id, job.source,
                                      job.title, job.duration, job.is_youtube)
                pipeline.run(job.source, self.cfg, hooks, job=ref)
                self.store.update(job_id, status="done")
            except Exception as e:
                traceback.print_exc()
                running = next((s for s in self.store.get(job_id).stages if s.status == "running"), None)
                if running:
                    self.store.update(job_id, stage=running.key, status="failed", finished_at=_now())
                self.store.update(job_id, status="failed", error=str(e) or type(e).__name__)
            finally:
                self.current = None


# ---------------------------------------------------------------- API

class CreateJob(BaseModel):
    url: str


class ExportBody(BaseModel):
    clip_ids: list[str] | None = None  # None = all approved clips


class ReviewBody(BaseModel):
    review: Literal["approved", "rejected"] | None


_load_reviews = exporter.load_reviews


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "clip"


def create_app(cfg_path: Path | None = None) -> FastAPI:
    cfg = load_config(cfg_path)
    store = JobStore(cfg)
    worker = Worker(store, cfg, cfg_path)
    app = FastAPI(title="shorts", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store, app.state.worker = store, worker

    @app.get("/api/system")
    def system():
        return health.summary(cfg)

    @app.get("/api/jobs", response_model=list[JobState])
    def list_jobs():
        return store.all()

    @app.post("/api/jobs", response_model=JobState)
    def create_job(body: CreateJob):
        url = body.url.strip()
        if not download.is_url(url):
            raise HTTPException(422, "Paste a full video URL (https://…)")
        try:
            ref = download.resolve_job(url, cfg)
        except download.DownloadError as e:
            raise HTTPException(422, str(e))
        existing = store.get(ref.id)
        if existing:
            if existing.status in ("failed", "interrupted"):
                worker.submit(ref.id)  # resume: finished stages are cached on disk
            return store.get(ref.id)
        now = _now()
        store.put(JobState(id=ref.id, source=url, title=ref.title, duration=ref.duration,
                           is_youtube=ref.is_youtube, created_at=now, updated_at=now,
                           stages=_fresh_stages()))
        worker.submit(ref.id)
        return store.get(ref.id)

    @app.get("/api/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str):
        return _job_or_404(store, job_id)

    @app.post("/api/jobs/{job_id}/retry", response_model=JobState)
    def retry_job(job_id: str):
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running"):
            raise HTTPException(409, "Job is already in progress")
        worker.submit(job_id)
        return store.get(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running") or worker.current == job_id:
            raise HTTPException(409, "Can't delete a job while it's processing")
        if owner := lock_owner(cfg.workspace() / job_id):
            raise HTTPException(409, f"Can't delete: this video is being processed from the terminal (PID {owner})")
        store.remove(job_id)
        d = (cfg.workspace() / job_id).resolve()
        if d.parent == cfg.workspace().resolve() and d.is_dir():
            shutil.rmtree(d)

    @app.get("/api/jobs/{job_id}/transcript")
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

    @app.get("/api/jobs/{job_id}/clips")
    def get_clips(job_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / select.OUT).exists():
            raise HTTPException(404, "No moments found yet")
        sel = load_json(Selection, d / select.OUT)
        reviews = _load_reviews(d)
        live = load_config(cfg_path)
        clips = []
        for c in sel.clips:
            f = render.clip_file(d, c, live)
            ready = (d / render.CLIPS_DIR / f).exists()
            hd = d / exporter.FINAL_DIR / render.clip_file(d, c, live, live.export)
            clips.append({**c.model_dump(), "duration": round(c.duration, 2),
                          "review": reviews.get(c.id), "ready": ready,
                          "url": f"/api/jobs/{job_id}/clips/{c.id}/video?v={f[-10:-4]}" if ready else None,
                          "final_url": f"/api/jobs/{job_id}/clips/{c.id}/final?v={hd.stem[-6:]}" if hd.exists() else None})
        return {"source": sel.source, "model": sel.model, "note": sel.note, "candidates": sel.candidates,
                "rejected": sel.rejected, "clips": clips}

    @app.get("/api/jobs/{job_id}/clips/{clip_id}/video")
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

    @app.put("/api/jobs/{job_id}/clips/{clip_id}/review")
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

    @app.post("/api/jobs/{job_id}/export", response_model=JobState)
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
        return store.get(job_id)

    @app.get("/api/jobs/{job_id}/clips/{clip_id}/final")
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

    @app.post("/api/jobs/{job_id}/reselect", response_model=JobState)
    def reselect_job(job_id: str):
        """Ask Gemini again (e.g. after prompt changes): redo moments -> reframe -> captions -> previews."""
        job = _job_or_404(store, job_id)
        if job.status in ("queued", "running") or worker.current == job_id:
            raise HTTPException(409, "Job is already in progress")
        d = cfg.workspace() / job_id
        if not (d / transcribe.OUT).exists():
            raise HTTPException(409, "Not transcribed yet")
        (d / select.OUT).unlink(missing_ok=True)
        for key in ("select", "reframe", "captions", "render"):
            store.update(job_id, stage=key, status="pending", progress=None, message=None,
                         started_at=None, finished_at=None)
        worker.submit(job_id)
        return store.get(job_id)

    @app.get("/api/jobs/{job_id}/source")
    def get_source(job_id: str):
        _job_or_404(store, job_id)
        d = cfg.workspace() / job_id
        if not (d / download.META).exists():
            raise HTTPException(404, "Not downloaded yet")
        meta = load_json(VideoMeta, d / download.META)
        return FileResponse(d / meta.file)

    # --- the built React app (npm run build in web/) ---
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if not WEB_DIST.exists():
            raise HTTPException(404, "UI not built. Run: cd web && npm install && npm run build")
        f = (WEB_DIST / path).resolve()
        if path and f.is_file() and WEB_DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")

    return app


def _job_or_404(store: JobStore, job_id: str) -> JobState:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job
