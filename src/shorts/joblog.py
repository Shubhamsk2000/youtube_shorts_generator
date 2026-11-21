"""A project's activity log, workspace/<id>/log.jsonl: what the pipeline did, step by step (the Console).

One JSON object per line: {"i": seq, "t": iso time, "level": info|ok|warn|error, "stage": key|null, "msg": text}.
Progress messages are logged when they say something new: a message that only changes its numbers
("Downloading 41%" -> "42%") is logged at most every PROGRESS_EVERY_S seconds. Every line is also printed, so
`journalctl -u studio` has the same story.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = "log.jsonl"
MAX_BYTES = 1_500_000        # then the oldest ~40% is dropped
PROGRESS_EVERY_S = 15.0
LEVELS = ("info", "ok", "warn", "error")

_lock = threading.Lock()
_next: dict[str, int] = {}                      # log file -> next sequence number
_last: dict[tuple[str, str | None], tuple[str, float]] = {}  # (file, stage) -> (normalised msg, time)

_WARN = re.compile(r"unavailable|retry|retrying|rate.?limit|quota|timed? ?out|overload|invalid|skipp|estimate|"
                   r"falling back|fallback|offline|trying |cancel|stopp|not found|missing|slow", re.I)
_ERROR = re.compile(r"\bfailed\b|\berror\b|crash|exception|traceback|refused|couldn't|could not", re.I)


def level_of(msg: str) -> str:
    """A progress message's level, from its words (providers and stages report plain text)."""
    if _ERROR.search(msg):
        return "error" if not re.search(r"trying|retry|keeping|using", msg, re.I) else "warn"
    return "warn" if _WARN.search(msg) else "info"


def _path(job_dir: Path) -> Path:
    return job_dir / LOG


def _seq(p: Path) -> int:
    key = str(p)
    if key not in _next:
        n = -1
        if p.exists():
            try:
                with p.open("rb") as f:
                    f.seek(max(0, p.stat().st_size - 4096))
                    for line in f.read().splitlines()[::-1]:
                        try:
                            n = int(json.loads(line)["i"])
                            break
                        except (ValueError, KeyError, json.JSONDecodeError):
                            continue
            except OSError:
                pass
        _next[key] = n + 1
    return _next[key]


def _trim(p: Path) -> None:
    lines = p.read_bytes().splitlines(keepends=True)
    p.write_bytes(b"".join(lines[int(len(lines) * 0.4):]))


def log(job_dir: Path, msg: str, level: str = "info", stage: str | None = None) -> None:
    msg = (msg or "").strip()
    if not msg or not job_dir.exists():
        return
    level = level if level in LEVELS else "info"
    p = _path(job_dir)
    with _lock:
        i = _seq(p)
        entry = {"i": i, "t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": level,
                 "stage": stage, "msg": msg[:2000]}
        try:
            with p.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _next[str(p)] = i + 1
            if p.stat().st_size > MAX_BYTES:
                _trim(p)
        except OSError:
            return
        _last[(str(p), stage)] = (_norm(msg), time.monotonic())
    print(f"[{job_dir.name}] {level.upper():5} {stage or '-':10} {msg}", flush=True)


def _norm(msg: str) -> str:
    return re.sub(r"\d+(?:[.,:]\d+)*", "#", msg)


def progress(job_dir: Path, stage: str, msg: str | None) -> None:
    """A stage's progress message: logged when it is new, rate limited when only its numbers move."""
    if not msg:
        return
    prev = _last.get((str(_path(job_dir)), stage))
    if prev and prev[0] == _norm(msg) and time.monotonic() - prev[1] < PROGRESS_EVERY_S:
        return
    log(job_dir, msg, level_of(msg), stage)


def read(job_dir: Path, after: int = -1, limit: int = 500) -> dict:
    """Entries with i > after (the newest `limit` of them), and the last sequence number."""
    p = _path(job_dir)
    out: list[dict] = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("i", -1) > after:
                out.append(e)
    truncated = len(out) > limit
    out = out[-limit:]
    return {"entries": out, "last": out[-1]["i"] if out else after, "truncated": truncated}

