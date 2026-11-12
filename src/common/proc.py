"""Run memory-heavy stages in a child process, relay its progress, track its peak RAM."""
from __future__ import annotations

import multiprocessing as mp
import queue
from dataclasses import dataclass
from typing import Callable

import psutil


@dataclass
class ProcStats:
    peak_rss_mb: float


class StageProcessError(RuntimeError):
    pass


def run_isolated(fn: Callable, *args, on_message: Callable[[tuple], None] | None = None) -> ProcStats:
    """Run fn(*args, q) in a fresh process. Anything the child puts on q is passed to on_message."""
    ctx = mp.get_context("spawn")  # fresh interpreter: no inherited memory, clean exit
    q = ctx.Queue()
    p = ctx.Process(target=fn, args=(*args, q), daemon=True)
    p.start()
    peak = 0
    ps = psutil.Process(p.pid)

    def drain(timeout: float) -> None:
        try:
            msg = q.get(timeout=timeout)
            while True:
                if on_message:
                    on_message(msg)
                msg = q.get_nowait()
        except queue.Empty:
            pass

    try:
        while p.is_alive():
            try:
                peak = max(peak, ps.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            drain(0.5)
        p.join()
        drain(0.05)
    except BaseException:
        p.terminate()
        p.join()
        raise
    if p.exitcode != 0:
        raise StageProcessError(f"{getattr(fn, '__name__', fn)} failed (exit code {p.exitcode})")
    return ProcStats(peak_rss_mb=peak / 1e6)
