"""Stage: each picked clip gets its own small video file, sources/<clip_id>.mkv.

Reframe, render and export read a clip's frames from that file, never from the full download, so:
  - "clips" mode (long streams): only the AUDIO of the whole video is downloaded (transcribe + select need
    nothing else); the video of each pick is downloaded here, a minute or so per clip, instead of ~100 GB.
  - "full" mode: each clip is cut out of the local download (stream copy, no quality loss), and the full
    download is deleted afterwards (download.keep_source: false). Only the shorts are kept.

Timing. The transcript's timeline is audio.wav. A downloaded range never starts exactly where it was asked
to (it starts on a stream fragment or keyframe, and a live stream's fragments drift from their nominal
2-5 s by minutes over a day), so its exact position is measured: the transcript audio of the clip is
located inside the downloaded file's audio (cross-correlation). A cut from the local file keeps the
source's timestamps instead (-copyts), which gives the position exactly.

index.json maps clip id -> file + `offset` (source time at t=0 of the file). resolve() turns a clip into
(file, the clip on that file's timeline); jobs made before this stage fall back to the full download.
"""
from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from ..config import Config
from ..models import Clip, Selection, VideoMeta, load_json, save_json
from common import ffmpeg
from common.progress import Reporter
from common.sysinfo import free_disk_gb

SOURCES_DIR = "sources"
INDEX = "index.json"
SR = 16000
ENV_HOP = 80                 # envelope bin: 5 ms at 16 kHz (coarse search)
MIN_SCORE = 0.5              # normalised correlation needed to trust a measured position
PROBE_MIN_S = 60.0           # audio probe reaches at least this far on each side of the clip...
PROBE_DRIFT = 0.004          # ...plus this share of the position (fragments drift ~0.2 % over a stream)
PROBE_MAX_S = 420.0
MARGIN_S = 8.0               # extra seconds downloaded on each side once the position is known
MAX_ATTEMPTS = 3
INFO_TTL_S = 30 * 60         # YouTube media URLs expire after a few hours: re-resolve after this


class SourceError(RuntimeError):
    pass


class SourceEntry(BaseModel):
    file: str                  # inside sources/
    offset: float              # source time (transcript timeline) at t=0 of the file
    duration: float            # of the file
    aligned: bool = True       # False: position estimated (silent audio), may be off by a fragment
    score: float | None = None
    how: str = "cut"           # "cut" (from the local download) | "download"


class SourceIndex(BaseModel):
    clips: dict[str, SourceEntry] = {}
    calib: list[list[float]] = []  # [source time, error] of measured downloads: corrects the next estimate


def sources_dir(job_dir: Path) -> Path:
    return job_dir / SOURCES_DIR


def load_index(job_dir: Path) -> SourceIndex:
    p = sources_dir(job_dir) / INDEX
    if p.exists():
        try:
            return load_json(SourceIndex, p)
        except Exception:  # noqa: BLE001  (a broken index only costs re-fetching)
            pass
    return SourceIndex()


def save_index(job_dir: Path, idx: SourceIndex) -> None:
    sources_dir(job_dir).mkdir(exist_ok=True)
    save_json(idx, sources_dir(job_dir) / INDEX)


def full_source(job_dir: Path, meta: VideoMeta) -> Path | None:
    """The full video download, if it is (still) on disk. Clips mode never has one."""
    p = job_dir / meta.file
    return p if meta.mode == "full" and not meta.source_deleted and p.exists() else None


def need_range(c: Clip, meta: VideoMeta, cfg: Config) -> tuple[float, float]:
    pad = cfg.download.clip_pad_s
    return max(0.0, c.start - pad), min(meta.duration, c.end + pad) if meta.duration else c.end + pad


def covers(e: SourceEntry, c: Clip, tol: float = 0.05) -> bool:
    return e.offset <= c.start + tol and e.offset + e.duration >= c.end - tol


def shifted(c: Clip, offset: float) -> Clip:
    """The clip on a file's timeline whose t=0 is source time `offset`."""
    return c.model_copy(update={"start": c.start - offset, "end": c.end - offset})


def resolve(job_dir: Path, meta: VideoMeta, c: Clip, cfg: Config | None = None,
            report: Reporter | None = None) -> tuple[Path, Clip]:
    """(file to decode, clip on that file's timeline). With cfg, a missing clip file is fetched first."""
    e = load_index(job_dir).clips.get(c.id)
    if e and (sources_dir(job_dir) / e.file).exists() and covers(e, c):
        return sources_dir(job_dir) / e.file, shifted(c, e.offset)
    full = full_source(job_dir, meta)
    if full:
        return full, c
    if cfg is None:
        raise SourceError(f"No video for clip {c.id}: run the pipeline again to fetch it")
    ensure(job_dir, meta, cfg, [c], report)
    return resolve(job_dir, meta, c)


# ---------------------------------------------------------------- the stage

def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, report: Reporter | None = None) -> str:
    report = report or (lambda fraction=None, message=None: None)
    sel = load_json(Selection, job_dir / "selection.json")
    idx = load_index(job_dir)
    wanted = {c.id for c in sel.clips}
    stale = [k for k in idx.clips if k not in wanted]
    for k in stale:  # clips from an earlier selection
        (sources_dir(job_dir) / idx.clips.pop(k).file).unlink(missing_ok=True)
    if stale:
        save_index(job_dir, idx)
    if sources_dir(job_dir).exists():  # leftovers of an interrupted fetch
        for f in sources_dir(job_dir).iterdir():
            if f.name != INDEX and f.name not in {e.file for e in idx.clips.values()}:
                f.unlink(missing_ok=True)

    n_new, approx = ensure(job_dir, meta, cfg, sel.clips, report)
    freed = cleanup_source(job_dir, meta, cfg, sel)
    idx = load_index(job_dir)
    mb = sum((sources_dir(job_dir) / e.file).stat().st_size for e in idx.clips.values()
             if (sources_dir(job_dir) / e.file).exists()) / 1e6
    how = "downloaded" if meta.mode == "clips" or meta.source_deleted else "cut"
    msg = f"{len(sel.clips)} clip videos · {mb:.0f} MB" + (f" · {n_new} {how}" if n_new else " · cached")
    if approx:
        msg += f" · {approx} placed by estimate (silent audio)"
    if freed:
        msg += f" · full download deleted ({freed / 1e9:.1f} GB freed)"
    return msg


def ensure(job_dir: Path, meta: VideoMeta, cfg: Config, clips: list[Clip],
           report: Reporter | None = None) -> tuple[int, int]:
    """Make sure every clip has a file covering it. Returns (files made, of which placed by estimate)."""
    report = report or (lambda fraction=None, message=None: None)
    idx = load_index(job_dir)
    todo = [c for c in clips if not ((e := idx.clips.get(c.id)) and (sources_dir(job_dir) / e.file).exists()
                                     and covers(e, c) and trusted(job_dir, e, c))]
    if not todo:
        return 0, 0
    sources_dir(job_dir).mkdir(exist_ok=True)
    for c in todo:
        if c.id in idx.clips:  # its video is replaced: what was analysed / rendered from the old one goes too
            forget_derived(job_dir, c)
    full = full_source(job_dir, meta)
    fetcher = None if full else RangeFetcher(meta, cfg)
    if fetcher and not meta.source_url:
        raise SourceError("The source video is gone and this job has no URL to fetch it from again")
    approx = 0
    t0 = time.monotonic()
    for i, c in enumerate(todo):
        el = time.monotonic() - t0
        eta = f" · ~{(el / i * (len(todo) - i)) / 60:.0f} min left" if i >= 2 else ""
        report(i / len(todo), f"Clip {i + 1}/{len(todo)}: {'cutting' if full else 'downloading'} "
                              f"{_hms(c.start)}–{_hms(c.end)}{eta}")
        a, b = need_range(c, meta, cfg)
        if full:
            e = cut_local(full, a, b, sources_dir(job_dir) / f"{c.id}.mkv")
        else:
            e = fetcher.fetch(job_dir, c, a, b, idx)
        approx += not e.aligned
        idx.clips[c.id] = e
        save_index(job_dir, idx)  # after every clip: an interruption keeps what is done
    report(1.0, None)
    return len(todo), approx


def trusted(job_dir: Path, e: SourceEntry, c: Clip) -> bool:
    """A file placed by estimate is only kept for a silent clip (nothing to measure). Jobs made before every
    spoken clip was measured could keep a guess that was minutes off: the footage of another moment under
    this clip's captions. Those are fetched again."""
    if e.aligned or e.how != "download":
        return True
    wav = job_dir / "audio.wav"
    return not wav.exists() or _rms_db(read_wav(wav, c.start, c.end)) < -50


def forget_derived(job_dir: Path, c: Clip) -> None:
    """The face analysis and preview made from a clip's old video file (its frames are no longer these)."""
    from .reframe import plan_path
    from .render import CLIPS_DIR
    plan_path(job_dir, c).unlink(missing_ok=True)
    for f in (job_dir / CLIPS_DIR).glob(f"{c.id}-*.mp4"):
        f.unlink(missing_ok=True)


def cleanup_source(job_dir: Path, meta: VideoMeta, cfg: Config, sel: Selection) -> int:
    """Delete the full download (video, or the clips-mode audio) once audio.wav and every clip file exist.
    Uploaded local files are never deleted: they are the user's own (and only a hard link here)."""
    if cfg.download.keep_source or meta.source_deleted or not meta.source_url or not sel.clips:
        return 0  # no picks yet (e.g. the AI was unavailable): keep it for the next attempt
    if not (job_dir / "audio.wav").exists():
        return 0
    idx = load_index(job_dir)
    if not all((e := idx.clips.get(c.id)) and (sources_dir(job_dir) / e.file).exists() for c in sel.clips):
        return 0
    src = job_dir / meta.file
    freed = src.stat().st_size if src.exists() else 0
    src.unlink(missing_ok=True)
    meta.source_deleted = True
    save_json(meta, job_dir / "meta.json")
    return freed


# ---------------------------------------------------------------- local cut (exact)

def cut_local(src: Path, a: float, b: float, dst: Path) -> SourceEntry:
    """Stream-copy [a, b] out of the local download. -copyts keeps the source's timestamps, so the file's
    first timestamp tells exactly where it starts (the cut begins on the keyframe before `a`)."""
    _check_disk(dst.parent, (b - a) * _bytes_per_s(src))
    tmp = dst.with_name(dst.stem + ".tmp.mkv")
    ffmpeg.run(["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-copyts", "-i", str(src),
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "disabled", str(tmp)])
    src_start = ffmpeg.start_time(src)
    start = ffmpeg.start_time(tmp)
    # a -copyts Matroska reports its duration from timestamp 0, not from its first frame: measure the end
    end = ffmpeg.end_time(tmp)
    tmp.replace(dst)
    return SourceEntry(file=dst.name, offset=round(start - src_start, 4), duration=round(end - start, 4), how="cut")


def _bytes_per_s(src: Path) -> float:
    try:
        d = ffmpeg.duration(src)
        return src.stat().st_size / d if d > 0 else 2e6
    except Exception:  # noqa: BLE001
        return 2e6


def _check_disk(where: Path, need_bytes: float, cfg: Config | None = None) -> None:
    keep = cfg.download.min_free_disk_gb if cfg else 1.0
    free = free_disk_gb(where)
    if free - need_bytes / 1e9 < keep:
        raise SourceError(f"Not enough disk for the clip video: need ≈{need_bytes / 1e9:.2f} GB, "
                          f"free {free:.1f} GB (keeping {keep} GB free). Delete old jobs to make room.")


# ---------------------------------------------------------------- downloaded ranges (measured)

class RangeFetcher:
    """Downloads time ranges of the source from YouTube and measures where each one really starts."""

    def __init__(self, meta: VideoMeta, cfg: Config):
        self.meta, self.cfg = meta, cfg
        self._info: dict | None = None
        self._at = 0.0

    def info(self) -> dict:
        if self._info is None or time.monotonic() - self._at > INFO_TTL_S:
            from .download import extract_for_ranges
            self._info = extract_for_ranges(self.meta, self.cfg)
            self._at = time.monotonic()
        return self._info

    def fetch(self, job_dir: Path, c: Clip, a: float, b: float, idx: SourceIndex) -> SourceEntry:
        from . import download as dl

        sdir = sources_dir(job_dir)
        wav = job_dir / "audio.wav"
        ref = read_wav(wav, c.start, c.end) if wav.exists() else np.zeros(0, np.float32)
        silent = _rms_db(ref) < -50
        err = _calib_error(idx.calib, c.start)
        fragmented = dl.is_fragmented(self.info())

        def probe(reach: float) -> float | None:
            """Download just the audio around the clip (±reach) and measure the error. None: not found."""
            p = sdir / f"{c.id}.probe"
            try:
                got, r0 = dl.download_range(self.info(), self.cfg, a - err - reach, b - err + reach, p,
                                            audio_only=True)
                found = locate(ref, decode_audio(got))
            finally:
                for f in sdir.glob(f"{c.id}.probe*"):
                    f.unlink(missing_ok=True)
            if not found:
                return None
            measured = (c.start - found[0]) - r0
            idx.calib.append([round(c.start, 2), round(measured, 3)])
            return measured

        wide = False  # the widest probe (PROBE_MAX_S) has been tried for this clip
        if fragmented and not silent:
            # cheap first: the audio around the clip, wide enough for any drift, to measure the error
            reach = min(PROBE_MAX_S, max(PROBE_MIN_S, PROBE_DRIFT * c.start))
            if idx.calib:
                reach = min(reach, PROBE_MIN_S + 0.001 * min(abs(c.start - t) for t, _ in idx.calib))
            wide = reach >= PROBE_MAX_S
            if (m := probe(reach)) is not None:
                err = m

        e = None
        for attempt in range(MAX_ATTEMPTS + 1):
            margin = MARGIN_S * (attempt + 1) + (0 if fragmented else 2)
            tmp = sdir / f"{c.id}.part.mkv"
            est = (b - a + 2 * margin) * dl.bytes_per_s(self.info())
            _check_disk(sdir, est, self.cfg)
            got, r0 = dl.download_range(self.info(), self.cfg, a - err - margin, b - err + margin, tmp)
            dur = ffmpeg.duration(got)
            found = locate(ref, decode_audio(got)) if not silent else None
            if found:
                offset, score = c.start - found[0], found[1]
                e = SourceEntry(file=f"{c.id}.mkv", offset=round(offset, 4), duration=round(dur, 4),
                                score=round(score, 3), how="download")
                idx.calib.append([round(c.start, 2), round(offset - r0, 3)])
                err = offset - r0
            elif silent:  # nothing to match: trust the estimate; the margin absorbs a small error
                e = SourceEntry(file=f"{c.id}.mkv", offset=round(r0 + err, 4), duration=round(dur, 4),
                                aligned=False, how="download")
            else:
                # The clip's speech isn't in what came back: the download is somewhere else entirely (e.g. the
                # served video and the transcript's audio are minutes apart). Never keep a guess: it would show
                # other footage under these captions. Search wide around it, then download again, corrected.
                got.unlink(missing_ok=True)
                if wide:
                    break
                wide = True
                if (m := probe(PROBE_MAX_S)) is None:
                    break
                err = m
                continue
            if e.offset <= a + 0.05 and e.offset + e.duration >= b - 0.05 or not e.aligned:
                got.replace(sdir / e.file)
                return e
            got.unlink(missing_ok=True)  # measured, but it doesn't cover the clip: ask again, corrected
        if e is None:
            raise SourceError(f"The video at {_hms(a)}–{_hms(b)} doesn't match the transcript's audio (searched "
                              f"±{PROBE_MAX_S / 60:.0f} min): the clip can't be placed. Try Retry; if it keeps "
                              f"failing, the source changed since it was transcribed: start the project again")
        raise SourceError(f"Couldn't fetch video covering {_hms(a)}–{_hms(b)} after {MAX_ATTEMPTS} tries")


def _calib_error(calib: list[list[float]], t: float) -> float:
    """Interpolated download error at source time t from earlier measurements (0 without any)."""
    if not calib:
        return 0.0
    pts = sorted((p[0], p[1]) for p in calib)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return float(np.interp(t, xs, ys))


# ---------------------------------------------------------------- audio alignment (pure parts tested)

def decode_audio(path: Path) -> np.ndarray:
    """Mono 16 kHz float32 on the file's own timeline: sample 0 = t 0 (a late-starting audio track is padded)."""
    proc = subprocess.run(
        [ffmpeg.require("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-map", "0:a:0", "-vn", "-ac", "1", "-af", f"aresample={SR}:async=1:first_pts=0",
         "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return np.zeros(0, np.float32)
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768


def read_wav(wav: Path, t0: float, t1: float) -> np.ndarray:
    with wave.open(str(wav)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a, b = max(0, int(t0 * sr)), min(n, int(t1 * sr))
        if b <= a or w.getsampwidth() != 2:
            return np.zeros(0, np.float32)
        w.setpos(a)
        x = np.frombuffer(w.readframes(b - a), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    if sr != SR:  # never in practice (the pipeline writes 16 kHz); keep it correct anyway
        x = np.interp(np.arange(0, len(x), sr / SR), np.arange(len(x)), x).astype(np.float32)
    return x


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)) if len(x) else -120.0


def _envelope(x: np.ndarray, hop: int = ENV_HOP) -> np.ndarray:
    k = len(x) // hop
    e = np.sqrt(np.mean(x[:k * hop].reshape(k, hop) ** 2, axis=1) + 1e-10)
    return np.log(e + 1e-4)


def _ncc_valid(t: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of template t at every position of s (len(s) >= len(t))."""
    n, m = len(t), len(s)
    t = t - t.mean()
    tn = float(np.sqrt(np.sum(t.astype(np.float64) ** 2)))
    if tn < 1e-6:  # flat template (silence): matches nothing
        return np.zeros(max(m - n + 1, 0))
    size = 1 << int(np.ceil(np.log2(n + m)))
    corr = np.fft.irfft(np.fft.rfft(s, size) * np.conj(np.fft.rfft(t, size)), size)[: m - n + 1]
    c1 = np.concatenate([[0.0], np.cumsum(s, dtype=np.float64)])
    c2 = np.concatenate([[0.0], np.cumsum(s.astype(np.float64) ** 2)])
    win_sum = c1[n:] - c1[:-n]
    win_sq = c2[n:] - c2[:-n]
    var = np.maximum(win_sq - win_sum ** 2 / n, 1e-12)
    return corr / (tn * np.sqrt(var))


def locate(template: np.ndarray, search: np.ndarray) -> tuple[float, float] | None:
    """Where `template` (transcript audio) starts inside `search` (a download's audio), in seconds, plus the
    match score; None when it isn't clearly there. Coarse on 5 ms loudness envelopes (robust, cheap over many
    minutes), then sample-exact on the waveform around that."""
    if len(template) < SR * 2 or len(search) < len(template) or _rms_db(template) < -50:
        return None
    te, se = _envelope(template), _envelope(search)
    if len(se) < len(te):
        return None
    coarse = _ncc_valid(te, se)
    k = int(np.argmax(coarse))
    if coarse[k] < MIN_SCORE:
        return None
    # fine: waveform correlation within ±2 envelope bins of the coarse match
    lo = max(0, (k - 2) * ENV_HOP)
    hi = min(len(search) - len(template), (k + 2) * ENV_HOP)
    seg = search[lo: hi + len(template)]
    fine = _ncc_valid(template, seg)
    j = int(np.argmax(fine))
    score = float(fine[j])
    if score < MIN_SCORE:  # waveforms differ (another audio codec): the envelope match is still right to 5 ms
        return k * ENV_HOP / SR, float(coarse[k])
    return (lo + j) / SR, score


def _hms(s: float) -> str:
    s = max(0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"
