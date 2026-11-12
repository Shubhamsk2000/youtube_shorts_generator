"""Stage order + runner shared by the CLI (and later the web server)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from common.lock import job_lock
from common.progress import Hooks

from .config import Config, Style
from .stages import edit, ingest, render, select, signals, watch


@dataclass(frozen=True)
class StageDef:
    key: str
    label: str


STAGES = [
    StageDef("ingest", "Episode & subtitles"),
    StageDef("signals", "Shots, motion & loudness"),
    StageDef("select", "Find moments"),
    StageDef("watch", "Gemini watches the picks"),
    StageDef("edit", "Cut points & effects"),
    StageDef("render", "Render previews"),
]


def run(source: str, cfg: Config, hooks: Hooks, *, style: Style | None = None, reselect: bool = False) -> Path:
    """Episode file -> previews. Finished stages are cached; reselect=True asks Gemini again."""
    job_dir = cfg.workspace() / ingest.job_id(Path(source).expanduser().resolve())
    with job_lock(job_dir):
        hooks.stage_start("ingest")
        job_dir, meta, msg = ingest.run(source, cfg, report=hooks.reporter("ingest"))
        hooks.stage_done("ingest", msg)

        hooks.stage_start("signals")
        _, msg = signals.run(job_dir, meta, cfg, report=hooks.reporter("signals"))
        hooks.stage_done("signals", msg)

        hooks.stage_start("select")
        _, msg = select.run(job_dir, meta, cfg, style=style, force=reselect, report=hooks.reporter("select"))
        hooks.stage_done("select", msg)

        hooks.stage_start("watch")
        _, msg = watch.run(job_dir, meta, cfg, force=reselect, report=hooks.reporter("watch"))
        hooks.stage_done("watch", msg)

        hooks.stage_start("edit")
        _, msg = edit.run(job_dir, meta, cfg, report=hooks.reporter("edit"))
        hooks.stage_done("edit", msg)

        hooks.stage_start("render")
        msg = render.run(job_dir, meta, cfg, report=hooks.reporter("render"))
        hooks.stage_done("render", msg)
    return job_dir
