"""Progress reporting shared by the CLI (prints) and the web server (updates job state)."""
from __future__ import annotations

import time
from typing import Protocol

from rich.console import Console

console = Console()


class Reporter(Protocol):
    def __call__(self, fraction: float | None = None, message: str | None = None) -> None: ...


class Hooks:
    """Pipeline lifecycle callbacks. Base class = no-op."""

    def stage_start(self, stage: str) -> None: ...
    def stage_done(self, stage: str, message: str | None = None) -> None: ...
    def reporter(self, stage: str) -> Reporter:
        return lambda fraction=None, message=None: None


class ConsoleHooks(Hooks):
    def reporter(self, stage: str) -> Reporter:
        last = {"t": 0.0, "msg": None}

        def report(fraction: float | None = None, message: str | None = None) -> None:
            now = time.monotonic()
            # throttle progress spam, but always show new messages
            if message == last["msg"] and now - last["t"] < 5:
                return
            last.update(t=now, msg=message)
            pct = f"{fraction * 100:5.1f}% " if fraction is not None else ""
            console.print(f"[dim]{stage}:[/] {pct}{message or ''}")

        return report

    def stage_done(self, stage: str, message: str | None = None) -> None:
        console.print(f"[green]{stage}:[/] {message or 'done'}")
