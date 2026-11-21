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

    ws = cfg.workspace()
    if ws.is_symlink() and not ws.exists():  # workspace on a data disk that isn't mounted
        checks.append(Check("Workspace disk", False, f"{ws} -> {os.readlink(ws)} is missing: mount the data disk "
                                                     "(sudo mount -a)"))
    disk = disk_free(cfg)
    checks.append(Check("Free disk", disk >= 6, f"{disk:.1f} GB free (long streams: audio ≈ 70 MB/h + clips "
                                                 "≈ 2-3 GB, see download.mode)", required=False))
    ram = available_ram_gb()
    checks.append(Check("Available RAM", ram >= 1.5, f"{ram:.1f} GB", required=False))
    from common.llm import load_llm_config
    keyed = [f"{p.name} ({len(p.keys())} key{'s' * (len(p.keys()) != 1)})"
             for p in load_llm_config().providers.values() if p.api_key_env and p.has_key()]
    checks.append(Check("AI API keys", bool(keyed), ", ".join(keyed) if keyed else
                        "none in .env (GEMINI_API_KEY or NVIDIA_API_KEY): moments can't be picked", required=False))
    return checks


def disk_free(cfg: Config) -> float:
    """Free space where projects live (the workspace: downloads, transcripts, renders). It may be its own
    disk (data disk symlinked in); the pipeline's own space checks use this one too."""
    return free_disk_gb(cfg.workspace())


def disks(cfg: Config) -> list[dict]:
    """Each distinct disk Studio writes to, with what it holds."""
    import os
    places = [("Projects", cfg.workspace()), ("Uploads", cfg.workspace().parent / "uploads"),
              ("Exports", cfg.export.out_dir())]
    out: dict[int, dict] = {}
    for label, p in places:
        q = p
        while not q.exists() and q != q.parent:
            q = q.parent
        try:
            dev = os.stat(q).st_dev
        except OSError:
            continue
        if dev in out:
            out[dev]["holds"].append(label)
        else:
            out[dev] = {"holds": [label], "path": str(q.resolve()), "free_gb": round(free_disk_gb(q), 1)}
    return list(out.values())


def summary(cfg: Config) -> dict:
    checks = run_checks(cfg)
    return {
        "ready": all(c.ok for c in checks if c.required),
        "disk_free_gb": round(disk_free(cfg), 1),
        "ram_available_gb": round(available_ram_gb(), 1),
        "checks": [asdict(c) for c in checks],
    }
