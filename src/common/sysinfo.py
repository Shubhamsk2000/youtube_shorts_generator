from __future__ import annotations

import shutil
from pathlib import Path

import psutil


def free_disk_gb(path: Path) -> float:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def available_ram_gb() -> float:
    return psutil.virtual_memory().available / 1e9
