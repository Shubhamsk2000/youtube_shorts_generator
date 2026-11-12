from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from common.paths import setup_env

from .config import Config, load_config

setup_env()
app = typer.Typer(add_completion=False, help="Turn anime episodes into vertical edit shorts (tweet-card style).")
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to a config file (default: configs/anime.yaml)")
StyleOpt = typer.Option(None, "--style", "-s", help="clip (one scene) or montage (several beats); default from config")


@app.command()
def make(episode: Path = typer.Argument(..., help="Episode file (.mkv / .mp4)"), style: str = StyleOpt,
         count: int = typer.Option(None, "--count", "-n", help="How many shorts"),
         again: bool = typer.Option(False, "--again", help="Ask Gemini again instead of reusing its picks"),
         config: Path = ConfigOpt) -> None:
    """Episode -> subtitles, signals, Gemini picks, edits and quick previews."""
    from common.progress import ConsoleHooks

    from . import pipeline

    cfg = _cfg(config, count)
    job_dir = _guard(lambda: pipeline.run(str(episode), cfg, ConsoleHooks(), style=_style(style), reselect=again))
    _show(job_dir, cfg)
    console.print(f"[bold green]done[/] → previews in {job_dir / 'previews'}")
    console.print(f"next: [bold]anime export {job_dir.name}[/] (all) or [bold]--rank 1 --rank 3[/]")


@app.command("list")
def list_edits(job: str, config: Path = ConfigOpt) -> None:
    """Show the edits of a processed episode."""
    cfg = _cfg(config)
    _show(_job_dir(job, cfg), cfg)


@app.command()
def export(job: str, rank: list[int] = typer.Option(None, "--rank", "-r", help="Export these ranks (repeatable)"),
           config: Path = ConfigOpt) -> None:
    """Render full-quality 1080x1920 files + a .txt (title, description, hashtags) per short."""
    from common.lock import job_lock
    from common.progress import ConsoleHooks

    from . import export as ex
    from .models import EpisodeMeta, load_json

    cfg = _cfg(config)
    job_dir = _job_dir(job, cfg)
    meta = load_json(EpisodeMeta, job_dir / "meta.json")
    hooks = ConsoleHooks()

    def work():
        with job_lock(job_dir):
            return ex.run(job_dir, meta, cfg, ranks=rank or None, report=hooks.reporter("export"))

    res = _guard(work)
    for f in res.files:
        console.print(f"  {f}")
    console.print(f"[bold green]exported[/] {len(res.files)} shorts → {res.dir}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the Studio web UI (both apps): http://127.0.0.1:8000."""
    from studio.cli import serve as studio_serve

    studio_serve(host=host, port=port, shorts_config=None, anime_config=None)


@app.command()
def card(text: str = typer.Argument("nobody was ready for what he said next"),
         out: Path = typer.Option(Path("card-preview.png")), config: Path = ConfigOpt) -> None:
    """Render the tweet card alone (to check your name/handle/avatar/theme)."""
    from PIL import Image

    from .card import layout, render_card

    cfg = _cfg(config)
    lay = layout(cfg.card, 1080, 1920, 1920, 1080, text)
    render_card(cfg.card, lay, out)
    bg = Image.new("RGBA", (1080, 1920), (40, 44, 52, 255))
    x, y, w, h = lay.video
    bg.paste((90, 90, 110, 255), (x, y, x + w, y + h))
    bg.alpha_composite(Image.open(out))
    bg.convert("RGB").save(out)
    console.print(f"card preview → {out.resolve()}")


def _cfg(path: Path | None, count: int | None = None) -> Config:
    cfg = load_config(path)
    if count:
        cfg.select.count = count
    return cfg


def _style(s: str | None):
    if s is None:
        return None
    if s not in ("clip", "montage"):
        console.print("[red]--style must be clip or montage[/]")
        raise typer.Exit(2)
    return s


def _job_dir(job: str, cfg: Config) -> Path:
    p = Path(job)
    if p.is_file():  # the episode itself
        from .stages.ingest import job_id
        p = cfg.workspace() / job_id(p.resolve())
    d = p if p.is_dir() else cfg.workspace() / job
    if not (d / "meta.json").exists():
        console.print(f"[red]No processed episode at {d}[/] (run `anime make <episode>` first)")
        raise typer.Exit(1)
    return d


def _show(job_dir: Path, cfg: Config) -> None:
    from .models import Selection, load_json
    from .stages.edit import OUT

    f = job_dir / OUT
    if not f.exists():
        return
    sel = load_json(Selection, f)
    if sel.note:
        console.print(f"[yellow]{sel.note}[/]")
    for p in sel.picks:
        eye = "👁 " if p.watched else "  "
        segs = ", ".join(f"{int(s.start // 60)}:{s.start % 60:04.1f}+{s.duration:.1f}s" for s in p.segments)
        console.print(f" {p.rank:>2}. [bold]{p.score:4.1f}[/] {eye}{p.kind:<9} {p.duration:5.1f}s  [bold]{p.title}[/]")
        console.print(f"      [dim]card:[/] {p.tweet}")
        console.print(f"      [dim]{segs}[/]")


def _guard(fn):
    """Show pipeline errors as one clean message instead of a traceback."""
    from common.ffmpeg import FFmpegError
    from common.llm import LLMError
    from common.lock import JobBusyError
    from common.proc import StageProcessError

    from .export import ExportError
    from .stages.ingest import IngestError

    try:
        return fn()
    except (IngestError, FFmpegError, StageProcessError, JobBusyError, LLMError, ExportError) as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted, rerun the same command to resume[/]")
        sys.exit(130)


if __name__ == "__main__":
    app()
