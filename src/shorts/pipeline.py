"""Stage order + runner shared by the CLI and the web server."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .progress import Hooks
from .stages import captions, download, reframe, render, select, transcribe
from .utils.lock import job_lock


@dataclass(frozen=True)
class StageDef:
    key: str
    label: str
    implemented: bool


STAGES = [
    StageDef("download", "Download", True),
    StageDef("transcribe", "Transcribe", True),
    StageDef("select", "Find moments", True),
    StageDef("reframe", "Reframe to 9:16", True),
    StageDef("captions", "Pacing & captions", True),
    StageDef("render", "Render previews", True),
]


def run(source: str, cfg: Config, hooks: Hooks, job: download.JobRef | None = None) -> Path:
    job = job or download.resolve_job(source, cfg)
    with job_lock(job.dir):
        hooks.stage_start("download")
        job_dir, meta = download.run(source, cfg, report=hooks.reporter("download"), job=job)
        hooks.stage_done("download", download.describe(meta))

        hooks.stage_start("transcribe")
        _, summary = transcribe.run(job_dir, meta, cfg, report=hooks.reporter("transcribe"))
        hooks.stage_done("transcribe", summary)

        hooks.stage_start("select")
        _, summary = select.run(job_dir, meta, cfg, report=hooks.reporter("select"))
        hooks.stage_done("select", summary)

        hooks.stage_start("reframe")
        summary = reframe.run(job_dir, meta, cfg, report=hooks.reporter("reframe"))
        hooks.stage_done("reframe", summary)

        hooks.stage_start("captions")
        summary = captions.run(job_dir, cfg, report=hooks.reporter("captions"))
        hooks.stage_done("captions", summary)

        hooks.stage_start("render")
        summary = render.run(job_dir, meta, cfg, report=hooks.reporter("render"))
        hooks.stage_done("render", summary)
    return job_dir
