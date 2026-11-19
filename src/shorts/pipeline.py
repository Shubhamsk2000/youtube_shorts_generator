"""Stage order + runner shared by the CLI and the web server."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from common.progress import Hooks
from .stages import captions, download, reframe, render, select, sources, transcribe
from common.lock import job_lock


@dataclass(frozen=True)
class StageDef:
    key: str
    label: str
    implemented: bool
    weight: float = 1.0   # share of a typical run's wall time (overall progress is weighted by it)


# weights: measured on a 2 vCPU server, 20-40 min source, ~15 clips (transcription dominates)
STAGES = [
    StageDef("download", "Download", True, 0.10),
    StageDef("transcribe", "Transcribe", True, 0.38),
    StageDef("select", "Find moments", True, 0.10),
    StageDef("sources", "Clip video", True, 0.04),
    StageDef("reframe", "Reframe to 9:16", True, 0.14),
    StageDef("captions", "Pacing & captions", True, 0.03),
    StageDef("render", "Render previews", True, 0.21),
]
WEIGHTS = {s.key: s.weight for s in STAGES}


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

        hooks.stage_start("sources")
        summary = sources.run(job_dir, meta, cfg, report=hooks.reporter("sources"))
        hooks.stage_done("sources", summary)

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
