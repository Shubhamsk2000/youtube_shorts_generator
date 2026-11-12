"""The Studio server: mounts the shorts and anime APIs, runs their heavy tasks through one shared runner,
and serves the single web UI (web/studio)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from common.paths import DATA_DIR, WEB_DIR
from common.runner import SerialRunner
from common.sysinfo import available_ram_gb, free_disk_gb

from . import __version__

WEB_DIST = WEB_DIR / "studio" / "dist"


def create_app(shorts_cfg: Path | None = None, anime_cfg: Path | None = None) -> FastAPI:
    from anime.server import make_router as anime_router
    from shorts.server import make_router as shorts_router

    runner = SerialRunner()
    shorts = shorts_router(runner, shorts_cfg)
    anime = anime_router(runner, anime_cfg)
    app = FastAPI(title="Studio", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(shorts, prefix="/api/shorts", tags=["shorts"])
    app.include_router(anime, prefix="/api/anime", tags=["anime"])
    stores = {"shorts": shorts.store, "anime": anime.store}

    def title(app_name: str, job_id: str) -> str:
        job = stores[app_name].get(job_id)
        return job.title if job else job_id

    @app.get("/api/status")
    def status():
        """Small and cheap: the nav and footer poll it."""
        snap = runner.snapshot()
        for t in [snap["current"], *snap["queued"]]:
            if t:
                t["title"] = title(t["app"], t["job_id"])
        return {
            "version": __version__,
            "queue": snap,
            "disk_free_gb": round(free_disk_gb(DATA_DIR), 1),
            "ram_available_gb": round(available_ram_gb(), 1),
            "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        }

    @app.get("/api/recent")
    def recent(limit: int = 12):
        """The newest jobs of both apps, for the home page."""
        rows = []
        for name, store in stores.items():
            for j in store.all():
                rows.append({"app": name, "id": j.id, "title": j.title, "status": j.status,
                             "created_at": j.created_at, "duration": j.duration,
                             "progress": _progress(j.stages)})
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

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


def _progress(stages) -> float:
    real = [s for s in stages if s.status != "planned"]
    if not real:
        return 0.0
    return round(sum(1.0 if s.status == "done" else (s.progress or 0) if s.status == "running" else 0
                     for s in real) / len(real), 3)
