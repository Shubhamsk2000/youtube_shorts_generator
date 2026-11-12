"""Environment checks used by `shorts doctor` and GET /api/system."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass

from .config import Config
from common.sysinfo import available_ram_gb, free_disk_gb


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


def run_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    ff = shutil.which("ffmpeg")
    checks.append(Check("ffmpeg", bool(ff), ff or "missing: sudo apt install -y ffmpeg"))
    checks.append(Check("ffprobe", bool(shutil.which("ffprobe")), "comes with ffmpeg"))
    if ff:
        buildconf = subprocess.run([ff, "-hide_banner", "-buildconf"], capture_output=True, text=True).stdout
        encoders = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        checks.append(Check("ffmpeg libx264", "libx264" in encoders, "needed for rendering"))
        checks.append(Check("ffmpeg libass", "libass" in buildconf, "needed for burned-in captions"))

    if shutil.which("fc-match"):
        font = cfg.captions.font
        got = subprocess.run(["fc-match", "-f", "%{family} %{style}", font], capture_output=True, text=True).stdout
        found = all(part.lower() in got.lower() for part in font.split())
        checks.append(Check("Caption font", found, got if found else f"'{font}' not installed (got {got})",
                            required=False))

    for mod in ("cv2", "numpy"):
        try:
            __import__(mod)
            checks.append(Check(mod, True, "face tracking"))
        except ImportError as e:
            checks.append(Check(mod, False, f"{e}: pip install -e ."))

    runtimes = [r for r in ("deno", "node") if shutil.which(r)]
    checks.append(Check("JS runtime", bool(runtimes), ", ".join(runtimes) or "install node or deno"))

    for mod in ("yt_dlp", "faster_whisper"):
        try:
            __import__(mod)
            checks.append(Check(mod, True))
        except ImportError as e:
            checks.append(Check(mod, False, str(e)))

    disk = free_disk_gb(cfg.workspace())
    checks.append(Check("Free disk", disk >= 6, f"{disk:.1f} GB free (a 4K source ≈ 1-2 GB)", required=False))
    ram = available_ram_gb()
    checks.append(Check("Available RAM", ram >= 1.5, f"{ram:.1f} GB", required=False))
    checks.append(Check("Gemini API key", bool(os.getenv("GEMINI_API_KEY")),
                        "set" if os.getenv("GEMINI_API_KEY") else "missing in .env: AI moment finding falls back to rough offline picks", required=False))
    return checks


def summary(cfg: Config) -> dict:
    checks = run_checks(cfg)
    return {
        "ready": all(c.ok for c in checks if c.required),
        "disk_free_gb": round(free_disk_gb(cfg.workspace()), 1),
        "ram_available_gb": round(available_ram_gb(), 1),
        "checks": [asdict(c) for c in checks],
    }
