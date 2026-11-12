"""Per-job lock file so the CLI and the web server never touch the same job at once."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psutil

LOCK = ".lock"


class JobBusyError(RuntimeError):
    pass


def lock_owner(job_dir: Path) -> int | None:
    """PID of another live process working on this job, else None (stale locks are ignored)."""
    try:
        pid = int((job_dir / LOCK).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    if pid == os.getpid() or not psutil.pid_exists(pid):
        return None
    return pid


@contextmanager
def job_lock(job_dir: Path):
    job_dir.mkdir(parents=True, exist_ok=True)
    owner = lock_owner(job_dir)
    if owner:
        raise JobBusyError(f"This video is already being processed by another process (PID {owner}).")
    path = job_dir / LOCK
    path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
