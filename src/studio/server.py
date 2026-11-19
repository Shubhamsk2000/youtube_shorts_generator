"""The Studio server: mounts the shorts API, runs its heavy tasks through one background runner,
and serves the web UI (web/studio)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from common.paths import WEB_DIR
from common.runner import SerialRunner
from common.sysinfo import available_ram_gb

from . import __version__

WEB_DIST = WEB_DIR / "studio" / "dist"


SHORTS_AI_TASKS = ["shorts.select", "shorts.refine"]


class ModelPick(BaseModel):
    provider: str | None = None   # None = back to configs/llm.yaml
    model: str | None = None


def _shorts_models(shorts_cfg: Path | None) -> list[str]:
    from shorts.config import load_config
    sc = load_config(shorts_cfg).select
    return [sc.model, *sc.fallback_models]


def _ai_route(shorts_cfg: Path | None) -> dict:
    """Which provider/model picks shorts moments right now (configs/llm.yaml, re-read on change)."""
    from common.llm import route_info
    from shorts.config import load_config

    try:
        sc = load_config(shorts_cfg).select
        return route_info("shorts.select", caller_models=[sc.model, *sc.fallback_models])
    except Exception as e:  # noqa: BLE001  (status must never fail)
        return {"provider": None, "error": str(e), "key_set": False}


def create_app(shorts_cfg: Path | None = None) -> FastAPI:
    from shorts.config import migrate_legacy_card
    from shorts.health import disk_free
    from shorts.server import make_router as shorts_router

    try:
        migrate_legacy_card()  # channel card from the removed anime app's settings, once
    except Exception as e:  # noqa: BLE001
        print(f"channel card migration skipped: {e}")

    runner = SerialRunner()
    shorts = shorts_router(runner, shorts_cfg)
    app = FastAPI(title="Studio", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(shorts, prefix="/api/shorts", tags=["shorts"])

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, exc: RequestValidationError):
        """One readable sentence for the UI instead of pydantic's error list."""
        errs = exc.errors()
        first = errs[0] if errs else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
        msg = first.get("msg", "Invalid request")
        return JSONResponse({"detail": f"{where}: {msg}" if where else msg, "errors": errs[:5]}, status_code=422)

    @app.exception_handler(Exception)
    async def crashed(_: Request, exc: Exception):
        import traceback
        traceback.print_exception(exc)
        return JSONResponse({"detail": f"Server error: {str(exc) or type(exc).__name__}"}, status_code=500)

    def title(job_id: str) -> str:
        job = shorts.store.get(job_id)
        return job.title if job else job_id

    @app.get("/api/status")
    def status():
        """Small and cheap: the nav polls it."""
        snap = runner.snapshot()
        for t in [snap["current"], *snap["queued"]]:
            if t:
                t["title"] = title(t["job_id"])
        return {
            "version": __version__,
            "queue": snap,
            "disk_free_gb": round(disk_free(shorts.store.cfg), 1),
            "ram_available_gb": round(available_ram_gb(), 1),
            "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "ai": _ai_route(shorts_cfg),
        }

    @app.get("/api/ai/shorts")
    def ai_models():
        """The model picker: what can pick shorts moments, and what does now."""
        from common.llm import model_choices, picked_model
        return {"choices": model_choices(_shorts_models(shorts_cfg)), "current": _ai_route(shorts_cfg),
                "picked": picked_model(SHORTS_AI_TASKS[0])}

    @app.put("/api/ai/shorts")
    def set_ai_model(pick: ModelPick):
        from common.llm import LLMError, set_task_model
        try:
            set_task_model(SHORTS_AI_TASKS, pick.provider, pick.model)
        except LLMError as e:
            raise HTTPException(400, str(e)) from None
        return ai_models()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Unknown API route")
        if not WEB_DIST.exists():
            raise HTTPException(404, "UI not built. Run: cd web && npm install && npm run build")
        f = (WEB_DIST / path).resolve()
        if path and f.is_file() and WEB_DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")

    return app
