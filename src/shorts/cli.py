from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

from .config import PROJECT_ROOT, load_config

load_dotenv(PROJECT_ROOT / ".env")
# Keep model downloads (Whisper via Hugging Face) and yt-dlp's cache inside the project folder.
os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_ROOT / ".cache"))
os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
app = typer.Typer(add_completion=False, help="Turn long YouTube videos into vertical shorts.")
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to config.yaml")


@app.command()
def doctor(config: Path = ConfigOpt) -> None:
    """Check that all external tools and resources are in place."""
    from .health import run_checks

    checks = run_checks(load_config(config))
    for c in checks:
        mark = "[green]✔[/]" if c.ok else ("[red]✘[/]" if c.required else "[yellow]![/]")
        console.print(f" {mark} {c.name:<18} {c.detail}")
    ok = all(c.ok for c in checks if c.required)
    console.print("[green]All required checks passed.[/]" if ok else "[red]Fix the ✘ items above.[/]")
    raise typer.Exit(0 if ok else 1)


@app.command()
def download(source: str, config: Path = ConfigOpt, force: bool = False) -> None:
    """Download a YouTube URL (or register a local file) into the workspace."""
    from .progress import ConsoleHooks
    from .stages import download as dl

    cfg = load_config(config)
    hooks = ConsoleHooks()
    job_dir, meta = _guard(lambda: dl.run(source, cfg, force=force, report=hooks.reporter("download")))
    hooks.stage_done("download", dl.describe(meta))
    console.print(f"job dir: {job_dir}")


@app.command()
def transcribe(job: str, config: Path = ConfigOpt, force: bool = False) -> None:
    """Transcribe a downloaded job (by video id or job dir)."""
    from .models import VideoMeta, load_json
    from .progress import ConsoleHooks
    from .stages import transcribe as tr

    cfg = load_config(config)
    job_dir = _job_dir(job, cfg)
    meta = load_json(VideoMeta, job_dir / "meta.json")
    hooks = ConsoleHooks()
    _, summary = _guard(_locked(job_dir, lambda: tr.run(job_dir, meta, cfg, force=force,
                                                        report=hooks.reporter("transcribe"))))
    hooks.stage_done("transcribe", summary)


@app.command()
def select(job: str, config: Path = ConfigOpt, force: bool = typer.Option(False, help="Ask Gemini again")) -> None:
    """Find moments in a transcribed job, reframe and caption them, render previews."""
    from .models import VideoMeta, load_json
    from .progress import ConsoleHooks
    from .stages import captions as cp
    from .stages import reframe as rf
    from .stages import render as rd
    from .stages import select as sl

    cfg = load_config(config)
    job_dir = _job_dir(job, cfg)
    meta = load_json(VideoMeta, job_dir / "meta.json")
    hooks = ConsoleHooks()

    def work():
        sel, summary = sl.run(job_dir, meta, cfg, force=force, report=hooks.reporter("select"))
        hooks.stage_done("select", summary)
        if sel.note:
            console.print(f"[yellow]{sel.note}[/]")
        for c in sel.clips:
            console.print(f" {c.rank:>2}. [bold]{c.score:4.1f}[/] {c.start / 60:5.1f}m +{c.duration:4.1f}s  {c.title}")
        hooks.stage_done("reframe", rf.run(job_dir, meta, cfg, report=hooks.reporter("reframe")))
        hooks.stage_done("captions", cp.run(job_dir, cfg, report=hooks.reporter("captions")))
        hooks.stage_done("render", rd.run(job_dir, meta, cfg, report=hooks.reporter("render")))

    _guard(_locked(job_dir, work))


@app.command("debug-reframe")
def debug_reframe(job: str, rank: int = typer.Argument(1, help="Clip rank (1 = best)"), config: Path = ConfigOpt) -> None:
    """Render a 16:9 debug video of one clip: face boxes, the 9:16 window, shot modes, cuts (red)."""
    from .models import ReframePlan, Selection, VideoMeta, load_json
    from .stages import reframe as rf
    from .stages import render as rd

    cfg = load_config(config)
    job_dir = _job_dir(job, cfg)
    meta = load_json(VideoMeta, job_dir / "meta.json")
    clip = next((c for c in load_json(Selection, job_dir / "selection.json").clips if c.rank == rank), None)
    if not clip:
        console.print(f"[red]No clip with rank {rank}[/]")
        raise typer.Exit(1)
    p = rf.plan_path(job_dir, clip)
    if not p.exists():
        console.print("[red]No reframe plan yet: run `shorts select` first[/]")
        raise typer.Exit(1)
    plan = load_json(ReframePlan, p)
    out = job_dir / rf.REFRAME_DIR / f"{clip.id}.debug.mp4"
    rd.render_debug(job_dir / meta.file, out, clip, meta, plan)
    for s in plan.shots:
        console.print(f" {s.start / plan.fps:6.2f}s  {s.mode:<6} {s.why}")
    console.print(f"[green]debug video:[/] {out}")


@app.command()
def export(job: str, config: Path = ConfigOpt,
           all_clips: bool = typer.Option(False, "--all", help="Export every clip, not just the approved ones"),
           rank: list[int] = typer.Option(None, "--rank", "-r", help="Export these clip ranks (repeatable)")) -> None:
    """Render approved clips at full quality into exports/<video>/ (with title/description/hashtags)."""
    from . import export as ex
    from .models import Selection, VideoMeta, load_json
    from .progress import ConsoleHooks

    cfg = load_config(config)
    job_dir = _job_dir(job, cfg)
    meta = load_json(VideoMeta, job_dir / "meta.json")
    ids = None
    if all_clips or rank:
        clips = load_json(Selection, job_dir / "selection.json").clips
        ids = [c.id for c in clips if all_clips or c.rank in rank]
    hooks = ConsoleHooks()
    res = _guard(_locked(job_dir, lambda: ex.run(job_dir, meta, cfg, clip_ids=ids, report=hooks.reporter("export"))))
    for f in res.files:
        console.print(f"  {f}")
    console.print(f"[bold green]exported[/] {len(res.files)} clips → {res.dir}")


@app.command()
def make(source: str, config: Path = ConfigOpt) -> None:
    """Run the full pipeline (stages implemented so far) on a URL or local file."""
    from . import pipeline
    from .progress import ConsoleHooks

    cfg = load_config(config)
    job_dir = _guard(lambda: pipeline.run(source, cfg, ConsoleHooks()))
    console.print(f"[bold green]done[/] → {job_dir}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, config: Path = ConfigOpt) -> None:
    """Start the web UI + API (http://127.0.0.1:8000)."""
    import uvicorn

    from .server import create_app

    console.print(f"[bold]shorts[/] UI → http://{host}:{port}")
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")


def _job_dir(job: str, cfg) -> Path:
    p = Path(job)
    d = p if p.is_dir() else cfg.workspace() / job
    if not (d / "meta.json").exists():
        console.print(f"[red]No downloaded job at {d}[/]")
        raise typer.Exit(1)
    return d


def _locked(job_dir: Path, fn):
    """Same per-job lock the pipeline uses, so the CLI and `shorts serve` never work on one job at once."""
    from .utils.lock import job_lock

    def run():
        with job_lock(job_dir):
            return fn()
    return run


def _guard(fn):
    """Show pipeline errors as one clean message instead of a traceback."""
    from .export import ExportError
    from .stages.download import DownloadError
    from .utils.ffmpeg import FFmpegError
    from .utils.lock import JobBusyError
    from .llm import LLMError
    from .utils.proc import StageProcessError

    try:
        return fn()
    except (DownloadError, FFmpegError, StageProcessError, JobBusyError, LLMError, ExportError) as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted, rerun the same command to resume[/]")
        sys.exit(130)


if __name__ == "__main__":
    app()
