"""Anime API (mounted by the Studio server under /api/anime).

Its tasks (pipeline, re-renders, exports) go through the Studio's shared runner, one at a time.
Job state lives in data/anime/workspace/<id>/job.json; episodes made with the CLI show up too.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from common.lock import job_lock, lock_owner
from common.runner import SerialRunner
from common.paths import DATA_DIR, MODELS_DIR, resolve
from common.progress import Hooks, Reporter
from common.sysinfo import available_ram_gb, free_disk_gb

from . import export as exporter
from . import pipeline
from .config import Config, Style, load_config, save_local
from .models import EpisodeMeta, Selection, Subs, load_json, save_json
from .stages import edit, ingest, render, select, signals, watch

JOB_FILE = "job.json"
REVIEWS = "reviews.json"
EPISODES = DATA_DIR / "anime" / "episodes"
AVATAR = DATA_DIR / "anime" / "avatar.png"
VIDEO_EXTS = {".mkv", ".mp4", ".webm", ".mov", ".avi", ".m4v"}

JobStatus = Literal["queued", "running", "done", "failed", "interrupted"]
StageStatus = Literal["pending", "running", "done", "failed"]


class Options(BaseModel):
    style: Style = "clip"
    count: int = 5
    audio: Literal["auto", "eng", "jpn"] = "auto"   # auto = English dub if the file has one
    captions: bool = True


class StageState(BaseModel):
    key: str
    label: str
    status: StageStatus = "pending"
    progress: float | None = None
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ExportState(BaseModel):
    status: Literal["queued", "running", "done", "failed"]
    progress: float | None = None
    message: str | None = None
    dir: str | None = None
    files: dict[str, str] = {}      # pick id -> exported mp4
    queue: list[str] = []
    current: str | None = None


class JobState(BaseModel):
    id: str
    source: str
    title: str
    duration: float | None = None
    status: JobStatus = "queued"
    error: str | None = None
    task: Literal["pipeline", "render"] | None = None
    created_at: str
    updated_at: str
    stages: list[StageState]
    options: Options = Options()
    audio_lang: str | None = None
    export: ExportState | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_stages() -> list[StageState]:
    return [StageState(key=s.key, label=s.label) for s in pipeline.STAGES]


def job_cfg(base: Config, o: Options) -> Config:
    """The config one job runs with: the yaml settings plus the options chosen in the UI."""
    cfg = base.model_copy(deep=True)
    cfg.style = o.style
    cfg.select.count = o.count
    cfg.captions.enabled = o.captions
    langs = {"eng": ["eng", "en"], "jpn": ["jpn", "ja"]}.get(o.audio)
    if langs:
        cfg.subs.audio_languages = [*langs, *(x for x in cfg.subs.audio_languages if x not in langs)]
    return cfg


class JobStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.jobs: dict[str, JobState] = {}
        self._last_save: dict[str, float] = {}
        self._load()

    def dir(self, job_id: str) -> Path:
        return self.cfg.workspace() / job_id

    def _load(self) -> None:
        ws = self.cfg.workspace()
        if not ws.exists():
            return
        for d in ws.iterdir():
            try:
                if (d / JOB_FILE).exists():
                    job = load_json(JobState, d / JOB_FILE)
                    if job.status in ("queued", "running"):  # the server stopped mid-task
                        job.status, job.task = "interrupted", None
                        for s in job.stages:
                            if s.status == "running":
                                s.status, s.message = "pending", "Interrupted"
                    if job.export and job.export.status in ("queued", "running"):
                        job.export.status, job.export.message = "failed", "Interrupted: export again"
                elif (d / ingest.META).exists():
                    job = _job_from_cli_dir(d)
                else:
                    continue
                self.jobs[job.id] = job
            except Exception:  # noqa: BLE001  one broken job must not hide the others
                traceback.print_exc()

    def get(self, job_id: str) -> JobState | None:
        with self.lock:
            j = self.jobs.get(job_id)
            return j.model_copy(deep=True) if j else None

    def all(self) -> list[JobState]:
        with self.lock:
            return sorted((j.model_copy(deep=True) for j in self.jobs.values()), key=lambda j: j.created_at,
                          reverse=True)

    def put(self, job: JobState) -> None:
        with self.lock:
            self.jobs[job.id] = job
            self._save(job, True)

    def update(self, job_id: str, *, stage: str | None = None, force_save: bool = True, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            target = next(s for s in job.stages if s.key == stage) if stage else job
            for k, v in fields.items():
                setattr(target, k, v)
            job.updated_at = _now()
            self._save(job, force_save)

    def remove(self, job_id: str) -> None:
        with self.lock:
            self.jobs.pop(job_id, None)

    def _save(self, job: JobState, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_save.get(job.id, 0) < 2:
            return
        self._last_save[job.id] = now
        d = self.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        save_json(job, d / JOB_FILE)


STAGE_FILES = {"ingest": ingest.SUBS, "signals": signals.OUT, "select": select.OUT, "watch": watch.OUT,
               "edit": edit.OUT}


def _job_from_cli_dir(d: Path) -> JobState:
    meta = load_json(EpisodeMeta, d / ingest.META)
    stages = _fresh_stages()
    for s in stages:
        if s.key in STAGE_FILES and (d / STAGE_FILES[s.key]).exists():
            s.status, s.progress, s.message = "done", 1.0, "Made with the CLI"
    if (d / render.PREVIEW_DIR).exists() and any((d / render.PREVIEW_DIR).glob("*.mp4")):
        stages[-1].status, stages[-1].progress, stages[-1].message = "done", 1.0, "Made with the CLI"
    style = "clip"
    if (d / select.OUT).exists():
        style = load_json(Selection, d / select.OUT).style
    created = datetime.fromtimestamp((d / ingest.META).stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    return JobState(id=meta.id, source=meta.source, title=meta.title, duration=meta.duration, created_at=created,
                    updated_at=created, stages=stages, audio_lang=meta.audio_lang, options=Options(style=style),
                    status="done" if all(s.status == "done" for s in stages) else "interrupted")


class StoreHooks(Hooks):
    def __init__(self, store: JobStore, job_id: str):
        self.store, self.job_id = store, job_id

    def stage_start(self, stage: str) -> None:
        self.store.update(self.job_id, stage=stage, status="running", progress=None, message=None,
                          started_at=_now(), finished_at=None)

    def stage_done(self, stage: str, message: str | None = None) -> None:
        self.store.update(self.job_id, stage=stage, status="done", progress=1.0, message=message,
                          finished_at=_now())
        if stage == "ingest":
            meta = load_json(EpisodeMeta, self.store.dir(self.job_id) / ingest.META)
            self.store.update(self.job_id, duration=meta.duration, audio_lang=meta.audio_lang)

    def reporter(self, stage: str) -> Reporter:
        def report(fraction: float | None = None, message: str | None = None) -> None:
            fields = {"progress": fraction}
            if message:
                fields["message"] = message
            self.store.update(self.job_id, stage=stage, force_save=False, **fields)
        return report


class Worker:
    """One task at a time (pipeline runs, re-renders after an edit, exports)."""

    def __init__(self, store: JobStore, runner: SerialRunner, cfg_path: Path | None):
        self.store, self.runner, self.cfg_path = store, runner, cfg_path
        self.current: str | None = None

    def cfg(self, job_id: str) -> Config:
        return job_cfg(load_config(self.cfg_path), self.store.jobs[job_id].options)

    def submit(self, job_id: str, *, reselect: bool = False) -> None:
        self.store.update(job_id, status="queued", error=None, task="pipeline")
        self.runner.submit("anime", job_id, "pipeline", lambda: self._handle("run", job_id, reselect))

    def submit_render(self, job_id: str) -> None:
        """After a hook/title edit or a captions toggle: redo the edit list and the affected previews."""
        job = self.store.get(job_id)
        if job and job.status in ("queued", "running"):
            return  # the running pipeline renders with the new values anyway
        for key in ("edit", "render"):
            self.store.update(job_id, stage=key, status="pending", progress=None)
        self.store.update(job_id, status="queued", error=None, task="render")
        self.runner.submit("anime", job_id, "render", lambda: self._handle("render", job_id, False))

    def submit_export(self, job_id: str, ids: list[str]) -> None:
        with self.store.lock:
            ex = self.store.jobs[job_id].export
            if ex is not None and ex.status in ("queued", "running"):
                ex.queue = list(dict.fromkeys([*ex.queue, *(i for i in ids if i != ex.current)]))
                self.store.update(job_id)
                return
            self.store.update(job_id, export=ExportState(
                status="queued", queue=list(dict.fromkeys(ids)), message="Waiting for the worker…",
                files=ex.files if ex else {}, dir=ex.dir if ex else None))
        self.runner.submit("anime", job_id, "export", lambda: self._handle("export", job_id, False))

    def _handle(self, kind: str, job_id: str, flag: bool) -> None:
        if job_id not in self.store.jobs:
            return
        self.current = job_id
        try:
            if kind == "export":
                self._export(job_id)
            else:
                self._run(job_id, kind, flag)
        finally:
            self.current = None

    def _run(self, job_id: str, kind: str, reselect: bool) -> None:
        job = self.store.get(job_id)
        if not job or job.status != "queued":
            return
        self.store.update(job_id, status="running")
        hooks = StoreHooks(self.store, job_id)
        try:
            cfg = self.cfg(job_id)
            if kind == "render":
                d = self.store.dir(job_id)
                meta = load_json(EpisodeMeta, d / ingest.META)
                with job_lock(d):
                    for key, fn in (("edit", lambda: edit.run(d, meta, cfg)[1]),
                                    ("render", lambda: render.run(d, meta, cfg, report=hooks.reporter("render")))):
                        hooks.stage_start(key)
                        hooks.stage_done(key, fn())
            else:
                pipeline.run(job.source, cfg, hooks, style=cfg.style, reselect=reselect)
            self.store.update(job_id, status="done", task=None)
        except Exception as e:  # noqa: BLE001  shown in the UI
            traceback.print_exc()
            running = next((s for s in self.store.get(job_id).stages if s.status == "running"), None)
            if running:
                self.store.update(job_id, stage=running.key, status="failed", finished_at=_now())
            self.store.update(job_id, status="failed", task=None, error=str(e) or type(e).__name__)

    def _export(self, job_id: str) -> None:
        store = self.store

        def ex() -> ExportState:
            return store.jobs[job_id].export

        with store.lock:
            ex().status = "running"
            store.update(job_id)
        n = 0
        try:
            cfg = self.cfg(job_id)
            d = store.dir(job_id)
            meta = load_json(EpisodeMeta, d / ingest.META)
            while True:
                with store.lock:
                    if not ex().queue:
                        break
                    pid = ex().queue.pop(0)
                    ex().current, ex().progress = pid, 0.0
                    left = len(ex().queue)
                    store.update(job_id)

                def report(fraction=None, message=None, left=left):
                    with store.lock:
                        ex().progress = fraction
                        if message:
                            ex().message = message + (f" · {left} more queued" if left else "")
                    store.update(job_id, force_save=False)

                with job_lock(d):
                    res = exporter.run(d, meta, cfg, ids=[pid], report=report)
                n += 1
                with store.lock:
                    ex().dir = str(res.dir)
                    if res.files:
                        ex().files = {**ex().files, pid: str(res.files[0])}
                    store.update(job_id)
            with store.lock:
                e = ex()
                e.status, e.progress, e.current = "done", 1.0, None
                e.message = f"{n} short{'s' * (n != 1)} exported to {e.dir}"
                store.update(job_id)
        except Exception as err:  # noqa: BLE001
            traceback.print_exc()
            with store.lock:
                e = ex()
                e.status, e.current, e.queue = "failed", None, []
                e.message = str(err) or type(err).__name__
                store.update(job_id)


# ---------------------------------------------------------------- API bodies

class CreateJob(BaseModel):
    source: str                 # file name in data/anime/episodes, or an absolute path
    options: Options = Options()


class ReselectBody(BaseModel):
    style: Style | None = None
    count: int | None = None


class PickEdit(BaseModel):
    tweet: str | None = None
    title: str | None = None
    review: Literal["approved", "rejected", "none"] | None = None
    song: str | None = None     # song edits: a music library id, "" = let the app choose


class MoodEdit(BaseModel):
    mood: Literal["hype", "epic", "sad", "chill", "funny"]


class OptionsEdit(BaseModel):
    captions: bool


class ExportBody(BaseModel):
    pick_ids: list[str] | None = None   # None = every approved pick


class CardEdit(BaseModel):
    name: str | None = None
    handle: str | None = None
    theme: Literal["dark", "light"] | None = None
    verified: bool | None = None
    footer: Literal["date", "none"] | None = None
    remove_avatar: bool = False


def _reviews(d: Path) -> dict[str, str]:
    f = d / REVIEWS
    return json.loads(f.read_text()) if f.exists() else {}


def _safe_name(name: str) -> str:
    name = Path(name).name
    if not name or name.startswith(".") or Path(name).suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(422, f"Expected a video file ({', '.join(sorted(VIDEO_EXTS))})")
    return re.sub(r"[/\\\x00]", "_", name)


def make_router(runner: SerialRunner, cfg_path: Path | None = None) -> APIRouter:
    base = load_config(cfg_path)
    store = JobStore(base)
    worker = Worker(store, runner, cfg_path)
    EPISODES.mkdir(parents=True, exist_ok=True)
    router = APIRouter()

    def job_or_404(job_id: str) -> JobState:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown episode")
        return job

    def idle_or_409(job: JobState) -> None:
        if job.status in ("queued", "running") or worker.current == job.id:
            raise HTTPException(409, "This episode is busy, wait for it to finish")

    # --- system / settings

    @router.get("/system")
    def system():
        import os

        from common import ffmpeg as ff

        cfg = load_config(cfg_path)
        checks = []

        def check(name: str, ok: bool, detail: str, required: bool = True):
            checks.append({"name": name, "ok": ok, "detail": detail, "required": required})

        try:
            ff.require("ffmpeg")
            check("ffmpeg", True, "found")
        except ff.FFmpegError as e:
            check("ffmpeg", False, str(e))
        check("Gemini API key", bool(os.getenv("GEMINI_API_KEY", "").strip()),
              "set" if os.getenv("GEMINI_API_KEY") else "add GEMINI_API_KEY to .env (picks fall back to signals only)",
              required=False)
        hub = MODELS_DIR.parent / "huggingface" / "hub"
        has_en = any(hub.glob(f"models--*faster-whisper-{cfg.subs.dub_model}")) if hub.exists() else False
        check("Dub transcription", has_en, f"Whisper {cfg.subs.dub_model} "
              + ("ready" if has_en else "downloads on first English-dub episode"), required=False)
        disk = free_disk_gb(EPISODES)
        check("Free disk", disk > 3, f"{disk:.1f} GB free")
        return {"ready": all(c["ok"] for c in checks if c["required"]), "disk_free_gb": round(disk, 1),
                "ram_available_gb": round(available_ram_gb(), 1), "checks": checks,
                "episodes_dir": str(EPISODES)}

    @router.get("/settings")
    def get_settings():
        cfg = load_config(cfg_path)
        c = cfg.card
        avatar = resolve(c.avatar) if c.avatar else None
        return {"card": {"name": c.name, "handle": c.handle, "theme": c.theme, "verified": c.verified,
                         "footer": c.footer, "has_avatar": bool(avatar and avatar.exists())},
                "defaults": Options(style=cfg.style, count=cfg.select.count, captions=cfg.captions.enabled)}

    @router.put("/settings/card")
    def put_card(body: CardEdit):
        vals = body.model_dump(exclude_none=True, exclude={"remove_avatar"})
        if "handle" in vals:
            vals["handle"] = "@" + vals["handle"].strip().lstrip("@")
        if body.remove_avatar:
            vals["avatar"] = None
        save_local("card", vals)
        return get_settings()

    @router.put("/settings/avatar")
    async def put_avatar(request: Request):
        from PIL import Image

        data = await request.body()
        if len(data) > 15_000_000:
            raise HTTPException(413, "Image too large (max 15 MB)")
        try:
            im = Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            raise HTTPException(422, "Not an image")
        side = min(im.size)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                      (im.width + side) // 2, (im.height + side) // 2)).resize((512, 512), Image.LANCZOS)
        AVATAR.parent.mkdir(parents=True, exist_ok=True)
        im.save(AVATAR)
        save_local("card", {"avatar": str(AVATAR.relative_to(DATA_DIR.parent))})
        return get_settings()

    @router.get("/settings/card/preview")
    def card_preview(text: str = "nobody was ready for what he said next", v: str = ""):
        from PIL import Image

        from .card import layout, render_card

        cfg = load_config(cfg_path)
        lay = layout(cfg.card, 1080, 1920, 1920, 1080, text[:200])
        tmp = DATA_DIR.parent / ".cache" / "tmp" / "card-preview.png"
        render_card(cfg.card, lay, tmp)
        im = Image.open(tmp)
        x, y, w, h = lay.card
        pad = 40
        crop = im.crop((max(0, x - pad), max(0, y - pad), min(im.width, x + w + pad), min(im.height, y + h + pad)))
        bg = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        vx, vy, vw, vh = lay.video
        ox, oy = max(0, x - pad), max(0, y - pad)
        bg.paste((60, 64, 82, 255), (vx - ox, vy - oy, vx - ox + vw, vy - oy + vh))
        bg.alpha_composite(crop)
        buf = BytesIO()
        bg.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    # --- episodes

    @router.get("/episodes")
    def episodes():
        out = []
        for f in sorted(EPISODES.iterdir(), key=lambda p: p.name.lower()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.endswith(".part"):
                jid = ingest.job_id(f.resolve())
                out.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1),
                            "job_id": jid if jid in store.jobs else None})
        return {"dir": str(EPISODES), "episodes": out}

    @router.put("/episodes/{name}")
    async def upload_episode(name: str, request: Request):
        """Raw upload (the browser streams the file body): saved into data/anime/episodes/."""
        name = _safe_name(name)
        dst = EPISODES / name
        if dst.exists():
            raise HTTPException(409, f"{name} is already in the episodes folder")
        if free_disk_gb(EPISODES) < 2:
            raise HTTPException(507, "Less than 2 GB of free disk space")
        tmp = dst.with_name(dst.name + ".part")
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)
        return {"name": name, "size_mb": round(dst.stat().st_size / 1e6, 1)}

    # --- music library (song edits)

    @router.get("/music")
    def music_list():
        from . import music

        uses = music.usage()
        return {"dir": str(music.MUSIC_DIR),
                "tracks": [{"id": t.id, "title": t.title, "duration": t.duration, "bpm": t.bpm, "mood": t.mood,
                            "mood_source": t.mood_source, "genre": t.genre, "uses": uses.get(t.id, 0)}
                           for t in sorted(music.library(), key=lambda t: t.title.lower())]}

    @router.put("/music/{name}")
    async def music_upload(name: str, request: Request, mood: str | None = None):
        """Raw upload of one song into data/anime/music/ (into <mood>/ when a mood is given), analysed at once."""
        from fastapi.concurrency import run_in_threadpool

        from . import music

        name = Path(name).name
        if not name or name.startswith(".") or Path(name).suffix.lower() not in music.AUDIO_EXTS:
            raise HTTPException(422, f"Expected an audio file ({', '.join(sorted(music.AUDIO_EXTS))})")
        folder = music.MUSIC_DIR / mood if mood in music.MOODS else music.MUSIC_DIR
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / name
        if dst.exists():
            raise HTTPException(409, f"{name} is already in the library")
        tmp = dst.with_name("." + dst.name + ".part")
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
                    if f.tell() > 60_000_000:
                        raise HTTPException(413, "Song too large (max 60 MB)")
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)
        cfg = load_config(cfg_path)
        tracks = await run_in_threadpool(lambda: music.library(
            tag_with_gemini=cfg.music.tag_with_gemini, models=[cfg.select.model, *cfg.select.fallback_models]))
        rel = dst.relative_to(music.MUSIC_DIR).as_posix()
        t = next((t for t in tracks if t.id == rel), None)
        if t is None:
            dst.unlink(missing_ok=True)
            raise HTTPException(422, "Couldn't find a steady beat in that file")
        return {"id": t.id, "title": t.title, "bpm": t.bpm, "mood": t.mood, "mood_source": t.mood_source}

    @router.put("/music/mood/{track_id:path}")
    def music_mood(track_id: str, body: MoodEdit):
        from . import music

        music.set_mood(track_id, body.mood)
        return {"id": track_id, "mood": body.mood}

    @router.get("/music/file/{track_id:path}")
    def music_file(track_id: str):
        from . import music

        f = (music.MUSIC_DIR / track_id).resolve()
        if music.MUSIC_DIR.resolve() not in f.parents or not f.is_file():
            raise HTTPException(404, "No such song")
        return FileResponse(f)

    @router.delete("/music/{track_id:path}", status_code=204)
    def music_delete(track_id: str):
        from . import music

        f = (music.MUSIC_DIR / track_id).resolve()
        if music.MUSIC_DIR.resolve() not in f.parents or not f.is_file():
            raise HTTPException(404, "No such song")
        f.unlink()
        music.library()  # drops it from the index

    # --- jobs

    @router.get("/jobs", response_model=list[JobState])
    def list_jobs():
        return store.all()

    @router.post("/jobs", response_model=JobState)
    def create_job(body: CreateJob):
        src = Path(body.source)
        src = src if src.is_absolute() else EPISODES / src.name
        if not src.is_file():
            raise HTTPException(404, f"No such file: {src}")
        jid = ingest.job_id(src.resolve())
        existing = store.get(jid)
        if existing:
            if existing.status in ("queued", "running"):
                return existing
            if existing.options.audio != body.options.audio:  # another audio track: start over
                shutil.rmtree(store.dir(jid), ignore_errors=True)
                store.remove(jid)
            else:
                store.update(jid, options=body.options)
                worker.submit(jid, reselect=existing.options.style != body.options.style
                              or existing.options.count != body.options.count)
                return store.get(jid)
        now = _now()
        store.put(JobState(id=jid, source=str(src.resolve()), title=src.stem, created_at=now, updated_at=now,
                           stages=_fresh_stages(), options=body.options))
        worker.submit(jid)
        return store.get(jid)

    @router.get("/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str):
        return job_or_404(job_id)

    @router.post("/jobs/{job_id}/retry", response_model=JobState)
    def retry(job_id: str):
        idle_or_409(job_or_404(job_id))
        worker.submit(job_id)
        return store.get(job_id)

    @router.post("/jobs/{job_id}/reselect", response_model=JobState)
    def reselect(job_id: str, body: ReselectBody):
        """Ask Gemini again (optionally another style / count)."""
        job = job_or_404(job_id)
        idle_or_409(job)
        opts = job.options.model_copy(update=body.model_dump(exclude_none=True))
        store.update(job_id, options=opts)
        for key in ("select", "watch", "edit", "render"):
            store.update(job_id, stage=key, status="pending", progress=None, message=None,
                         started_at=None, finished_at=None)
        worker.submit(job_id, reselect=True)
        return store.get(job_id)

    @router.put("/jobs/{job_id}/options", response_model=JobState)
    def set_options(job_id: str, body: OptionsEdit):
        job = job_or_404(job_id)
        idle_or_409(job)
        store.update(job_id, options=job.options.model_copy(update={"captions": body.captions}))
        if (store.dir(job_id) / edit.OUT).exists() or (store.dir(job_id) / watch.OUT).exists():
            worker.submit_render(job_id)
        return store.get(job_id)

    @router.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        """Removes the work files and previews; never the episode itself or exported shorts."""
        job = job_or_404(job_id)
        idle_or_409(job)
        if owner := lock_owner(store.dir(job_id)):
            raise HTTPException(409, f"Busy in the terminal (PID {owner})")
        store.remove(job_id)
        d = store.dir(job_id).resolve()
        if d.parent == base.workspace().resolve() and d.is_dir():
            shutil.rmtree(d)

    @router.get("/jobs/{job_id}/picks")
    def picks(job_id: str):
        job = job_or_404(job_id)
        d = store.dir(job_id)
        if not (d / edit.OUT).exists():
            raise HTTPException(404, "No edits yet")
        cfg = job_cfg(load_config(cfg_path), job.options)
        sel = load_json(Selection, d / edit.OUT)
        reviews = _reviews(d)
        files = job.export.files if job.export else {}
        out = []
        for p in sel.picks:
            name = render.output_name(p, cfg, cfg.preview)
            ready = (d / render.PREVIEW_DIR / name).exists()
            options = []
            if p.style == "song":
                from . import music

                options = [{"id": t.id, "title": t.title, "mood": t.mood, "bpm": t.bpm, "why": why}
                           for t, _, why in music.rank(music.library(), p.kind or "hype", cfg.music.min_s)[:6]]
            out.append({
                **p.model_dump(exclude={"segments"}),
                "song_options": options,
                "duration": round(p.duration, 1),
                "segments": [{**s.model_dump(), "duration": round(s.duration, 2)} for s in p.segments],
                "review": reviews.get(p.id), "ready": ready,
                "url": f"/api/anime/jobs/{job_id}/picks/{p.id}/video?v={name[-10:-4]}" if ready else None,
                "final_url": f"/api/anime/jobs/{job_id}/picks/{p.id}/final" if p.id in files and Path(files[p.id]).exists()
                else None,
            })
        meta = load_json(EpisodeMeta, d / ingest.META)
        subs = load_json(Subs, d / ingest.SUBS) if (d / ingest.SUBS).exists() else None
        sig = load_json(signals.Signals, d / signals.OUT) if (d / signals.OUT).exists() else None
        return {"style": sel.style, "source": sel.source, "model": sel.model, "note": sel.note, "picks": out,
                "episode": {"audio": meta.audio_lang, "subs": subs.source if subs else None,
                            "lines": len(subs.cues) if subs else 0,
                            "excluded": [{"start": a, "end": b, "what": w} for a, b, w in sig.excluded] if sig else []}}

    def _pick_file(job_id: str, pick_id: str) -> tuple[Path, str]:
        job = job_or_404(job_id)
        d = store.dir(job_id)
        if not (d / edit.OUT).exists():
            raise HTTPException(404, "No edits yet")
        p = next((p for p in load_json(Selection, d / edit.OUT).picks if p.id == pick_id), None)
        if not p:
            raise HTTPException(404, "Unknown pick")
        cfg = job_cfg(load_config(cfg_path), job.options)
        return d / render.PREVIEW_DIR / render.output_name(p, cfg, cfg.preview), f"{p.rank:02d}-{exporter.slug(p.title)}"

    @router.get("/jobs/{job_id}/picks/{pick_id}/video")
    def pick_video(job_id: str, pick_id: str):
        f, name = _pick_file(job_id, pick_id)
        if not f.exists():
            raise HTTPException(404, "Preview not rendered yet")
        return FileResponse(f, media_type="video/mp4", filename=f"{name}-preview.mp4", content_disposition_type="inline")

    @router.get("/jobs/{job_id}/picks/{pick_id}/final")
    def pick_final(job_id: str, pick_id: str):
        job = job_or_404(job_id)
        f = Path((job.export.files if job.export else {}).get(pick_id, ""))
        if not f.is_file():
            raise HTTPException(404, "Not exported yet")
        return FileResponse(f, media_type="video/mp4", filename=f.name)

    @router.put("/jobs/{job_id}/picks/{pick_id}")
    def edit_pick(job_id: str, pick_id: str, body: PickEdit):
        job = job_or_404(job_id)
        d = store.dir(job_id)
        _pick_file(job_id, pick_id)  # 404s
        if body.review is not None:
            with store.lock:
                r = _reviews(d)
                if body.review == "none":
                    r.pop(pick_id, None)
                else:
                    r[pick_id] = body.review
                (d / REVIEWS).write_text(json.dumps(r, indent=1))
        if body.song is not None:
            idle_or_409(job)
            from . import music

            if body.song and body.song not in {t.id for t in music.library()}:
                raise HTTPException(404, "That song isn't in the music library")
            edit.save_override(d, pick_id, song=body.song)
            worker.submit_render(job_id)
        if body.tweet is not None or body.title is not None:
            idle_or_409(job)
            tweet = select.clean_tweet(body.tweet) if body.tweet is not None else None
            if body.tweet is not None and not tweet:
                raise HTTPException(422, "The hook text can't be empty")
            edit.save_override(d, pick_id, tweet=tweet,
                               title=body.title.strip()[:100] if body.title is not None else None)
            worker.submit_render(job_id)
        return {"ok": True}

    @router.post("/jobs/{job_id}/export", response_model=JobState)
    def export(job_id: str, body: ExportBody):
        job_or_404(job_id)
        d = store.dir(job_id)
        if not (d / edit.OUT).exists():
            raise HTTPException(409, "No edits yet")
        ids = [p.id for p in load_json(Selection, d / edit.OUT).picks]
        if body.pick_ids is None:
            r = _reviews(d)
            wanted = [i for i in ids if r.get(i) == "approved"]
            if not wanted:
                raise HTTPException(409, "Approve at least one short first")
        else:
            wanted = [i for i in body.pick_ids if i in ids]
            if not wanted:
                raise HTTPException(404, "Unknown pick")
        worker.submit_export(job_id, wanted)
        return store.get(job_id)

    @router.get("/jobs/{job_id}/source")
    def source(job_id: str):
        job = job_or_404(job_id)
        f = Path(job.source)
        if not f.is_file():
            raise HTTPException(404, "Episode file is gone")
        return FileResponse(f)

    router.store, router.worker = store, worker  # read by the Studio's queue view
    return router
