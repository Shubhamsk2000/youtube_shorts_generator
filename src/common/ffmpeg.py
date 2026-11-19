from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def require(binary: str = "ffmpeg") -> str:
    path = shutil.which(binary)
    if not path:
        raise FFmpegError(f"'{binary}' not found on PATH. Install it: sudo apt install -y ffmpeg")
    return path


def run(args: list[str]) -> None:
    proc = subprocess.run(
        [require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip()[-2000:])


def run_with_progress(args: list[str], duration: float, on_progress, cwd: Path | None = None) -> None:
    """Like run(), but calls on_progress(fraction) as ffmpeg encodes (via -progress)."""
    proc = subprocess.Popen(
        [require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostats", "-y",
         "-progress", "pipe:1", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
    )
    try:
        for line in proc.stdout:  # key=value lines
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    on_progress(min(int(line.split("=", 1)[1]) / 1e6 / duration, 1.0))
                except ValueError:
                    pass
        err = proc.stderr.read()
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise FFmpegError(err.strip()[-2000:])


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [require("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip())
    return json.loads(proc.stdout)


def video_info(path: Path) -> dict:
    """Duration, size, fps and codec of the first video stream."""
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return {
        "duration": float(info["format"].get("duration") or v.get("duration") or 0),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(fps, 3),
        "vcodec": v.get("codec_name"),
    }


def duration(path: Path) -> float:
    """Container duration in seconds (also for audio-only files)."""
    info = probe(path)
    d = info["format"].get("duration")
    if d is None:
        d = max((float(s.get("duration") or 0) for s in info.get("streams", [])), default=0)
    return float(d or 0)


def start_time(path: Path) -> float:
    """First timestamp of the file (0 for most; a -copyts cut keeps its source's)."""
    return float(probe(path)["format"].get("start_time") or 0)


def end_time(path: Path) -> float:
    """Timestamp where the last packet ends (any stream): exact for files whose duration field isn't."""
    proc = subprocess.run(
        [require("ffprobe"), "-v", "error", "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip())
    end = 0.0
    for line in proc.stdout.splitlines():
        parts = line.split(",")
        try:
            end = max(end, float(parts[0]) + (float(parts[1]) if len(parts) > 1 and parts[1] not in ("", "N/A") else 0))
        except ValueError:
            continue
    return end


def extract_audio(src: Path, dst: Path, sample_rate: int = 16000) -> None:
    """Mono 16 kHz PCM WAV, what Whisper expects."""
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    run(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(dst)
