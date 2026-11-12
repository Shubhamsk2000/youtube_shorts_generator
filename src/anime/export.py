"""Final export: full-quality shorts -> data/anime/exports/<episode>/NN-title.mp4 + NN-title.txt."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from common.progress import Reporter

from .config import Config
from .models import EpisodeMeta, Selection, Subs, load_json
from .stages.edit import OUT as SELECTION
from .stages.ingest import SUBS
from .stages.render import render_pick


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    dir: Path
    files: list[Path] = field(default_factory=list)


def slug(text: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "short"


def run(job_dir: Path, meta: EpisodeMeta, cfg: Config, *, ranks: list[int] | None = None,
        ids: list[str] | None = None, report: Reporter | None = None) -> ExportResult:
    report = report or (lambda fraction=None, message=None: None)
    sel = load_json(Selection, job_dir / SELECTION)
    picks = [p for p in sel.picks if (ranks is None or p.rank in ranks) and (ids is None or p.id in ids)]
    if not picks:
        raise ExportError("No matching edits to export")
    out_dir = cfg.exports() / meta.id
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = load_json(Subs, job_dir / SUBS).cues
    res = ExportResult(dir=out_dir)
    for i, p in enumerate(picks):
        base = out_dir / f"{p.rank:02d}-{slug(p.title)}"
        report(i / len(picks), f"Exporting {i + 1}/{len(picks)}: {p.title[:50]}")
        render_pick(job_dir, meta, p, cues, cfg, cfg.export, base.with_suffix(".mp4"),
                    lambda f, i=i: report((i + f) / len(picks), None))
        uniq: dict[str, str] = {}  # dedupe case-insensitively, Gemini's tags first
        for h in [*p.hashtags, "anime", "animeedit", "shorts"]:
            uniq.setdefault(h.lower(), h)
        tags = " ".join(f"#{h}" for h in uniq.values() if h)
        base.with_suffix(".txt").write_text(
            f"TITLE\n{p.title}\n\nDESCRIPTION\n{p.description}\n\n{tags}\n\nHOOK (on the card)\n{p.tweet}\n\n"
            + (f"MUSIC\n{p.song.title} (your library: {p.song.id})\n\n" if p.song else "")
            + f"SOURCE\n{Path(meta.source).name} · "
            + ", ".join(f"{_mmss(s.start)}-{_mmss(s.end)}" for s in p.segments) + "\n"
        )
        if p.song:
            from . import music

            music.mark_used(p.song.id)  # spreads songs across your uploads
        res.files.append(base.with_suffix(".mp4"))
    return res


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"
