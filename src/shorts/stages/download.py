"""Stage 1: fetch the source video (YouTube URL or local file) into workspace/<id>/."""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..models import VideoInfo, VideoMeta, load_json, save_json
from common.progress import Reporter
from common import ffmpeg
from common.sysinfo import free_disk_gb
from common.paths import resolve

META = "meta.json"
INFO = "info.json"


class DownloadError(RuntimeError):
    pass


@dataclass
class JobRef:
    id: str
    dir: Path
    source: str
    title: str
    duration: float | None
    is_youtube: bool


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip()))


def _local_id(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem)[:40]
    digest = hashlib.sha1(f"{path.resolve()}:{path.stat().st_size}".encode()).hexdigest()[:8]
    return f"local-{stem}-{digest}"


def _ydl_opts(cfg: Config) -> dict:
    d = cfg.download
    h = d.max_height
    opts: dict = {
        # best video up to max_height + best audio; fall back to a progressive file
        "format": f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
        # resolution first, then fps, then codec no "better" than prefer_codec (avoids slow AV1)
        "format_sort": [f"res:{h}", "fps", f"vcodec:{d.prefer_codec}", "acodec"],
        "merge_output_format": "mkv",  # mkv holds any vcodec/acodec combo without re-encoding
        "noplaylist": True,
        "live_from_start": True,  # live streams: grab from the beginning (cut at "now", see _stop_at_now)
        "quiet": True,
        "noprogress": True,
        "js_runtimes": {"deno": {}, "node": {}},  # YouTube needs a JS runtime for signatures
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 8,  # live / DASH streams come in ~5 s pieces: fetch several at once
    }
    if d.cookies_from_browser:
        opts["cookiesfrombrowser"] = (d.cookies_from_browser,)
    cookies = resolve(d.cookies_file)
    if cookies.is_file():
        opts["cookiefile"] = str(cookies)
    return opts


def _estimate_size(info: dict, live_last_s: float = 0) -> int:
    fmts = info.get("requested_formats") or [info]
    size = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in fmts)
    if not size and (secs := info.get("duration") or _live_elapsed(info, live_last_s)):
        # live / post-live streams report no size: bitrate (kbit/s) × length
        size = int(sum(f.get("tbr") or 0 for f in fmts) * 1000 / 8 * secs)
    return size


def _live_status(info: dict) -> str | None:
    return info.get("live_status") or ("is_live" if info.get("is_live") else None)


def _reject_upcoming(info: dict) -> None:
    if _live_status(info) == "is_upcoming":
        raise DownloadError("That stream hasn't started yet. Paste it again once it is live or has ended.")


def _stop_at_now(fragments, n_last: int = 0):
    """Wrap yt-dlp's live-from-start fragment generator so it ends at the live edge instead of
    following the broadcast forever. The first batch it yields runs from the stream's start to
    "now" (every fragment carries that batch's fragment_count); anything after that is new live data.
    n_last > 0: only the last n_last fragments of that batch."""
    def sq_of(frag) -> int | None:
        sq = parse_qs(urlparse(frag.get("url") or "").query).get("sq")
        return int(sq[0]) if sq else None

    def gen(ctx):
        head = None
        for frag in fragments(ctx):
            count = frag.get("fragment_count")
            if head is None:
                head = count
            elif count != head:
                return  # a later poll: we're past the snapshot point
            sq = sq_of(frag)
            if n_last and head is not None and sq is not None and sq < head - n_last:
                continue  # before the window we want
            yield frag
            if sq is not None and head is not None and sq >= head - 1:
                return
    return gen


def _snapshot_live(info: dict, last_s: float = 0) -> None:
    for f in info.get("requested_formats") or [info]:
        if callable(f.get("fragments")):
            frag_s = f.get("target_duration") or 5.0
            f["fragments"] = _stop_at_now(f["fragments"], math.ceil(last_s / frag_s) if last_s else 0)


def _live_elapsed(info: dict, last_s: float = 0) -> float:
    start = info.get("release_timestamp") or info.get("timestamp")
    # YouTube keeps only the last 120 h of a live stream
    secs = min(max(time.time() - start, 0), 120 * 3600) if start else 0
    return min(secs, last_s) if last_s and secs else (last_s or secs)


def resolve_job(source: str, cfg: Config) -> JobRef:
    """Identify the job (id, title) without downloading media. Raises DownloadError."""
    if is_url(source):
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({**_ydl_opts(cfg), "no_warnings": True}) as ydl:
                info = ydl.extract_info(source, download=False, process=False)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(_clean_ytdlp_error(e)) from e
        if info.get("_type") == "playlist":
            raise DownloadError("That's a playlist link. Paste a single video URL.")
        _reject_upcoming(info)
        jid, title = info["id"], info.get("title") or info["id"]
        if _live_status(info) == "is_live":
            # each paste of a live link is its own snapshot (up to that moment), so a later paste,
            # or the finished VOD, doesn't reuse an older, shorter download
            jid = f"{jid}-live{time.strftime('%Y%m%d-%H%M')}"
            last = f"last {cfg.download.live_last_min:g} min " if cfg.download.live_last_min else ""
            title = f"{title} (live, {last}until {time.strftime('%H:%M')})"
        return JobRef(
            id=jid,
            dir=cfg.workspace() / jid,
            source=source,
            title=title,
            duration=info.get("duration"),
            is_youtube=info.get("extractor_key", "").lower().startswith("youtube"),
        )
    path = Path(source).expanduser()
    if not path.is_file():
        raise DownloadError(f"Not a URL and no such file: {source}")
    jid = _local_id(path)
    return JobRef(jid, cfg.workspace() / jid, str(path), path.stem, None, False)


def run(source: str, cfg: Config, *, force: bool = False, report: Reporter | None = None,
        job: JobRef | None = None) -> tuple[Path, VideoMeta]:
    report = report or (lambda fraction=None, message=None: None)
    job = job or resolve_job(source, cfg)
    job.dir.mkdir(parents=True, exist_ok=True)
    meta_path = job.dir / META

    if meta_path.exists() and not force:
        meta = load_json(VideoMeta, meta_path)
        if (job.dir / meta.file).exists() or meta.source_deleted:
            report(1.0, f"Already downloaded ({meta.height}p)")
            return job.dir, meta

    if is_url(source):
        meta = _download_youtube(source, job, cfg, report)
    else:
        meta = _copy_local(Path(source).expanduser(), job)

    save_json(meta, meta_path)
    return job.dir, meta


def describe(meta: VideoMeta) -> str:
    msg = f"{meta.width}×{meta.height} @ {meta.fps:g}fps · {meta.vcodec}"
    if meta.mode == "clips":
        msg += f" · {meta.duration / 3600:.1f} h: audio only, clip video fetched per clip"
    if meta.height < 1440:
        msg += " · low-res source, crops will be upscaled"
    return msg


def _fmt_size(f: dict, secs: float) -> int:
    return int(f.get("filesize") or f.get("filesize_approx") or (f.get("tbr") or 0) * 1000 / 8 * secs)


def plan_mode(info: dict, cfg: Config, free_gb: float, live_last_s: float = 0) -> tuple[str, str]:
    """("full" | "clips", why) for this video, or DownloadError when even the audio-first route can't fit.

    full  = the whole video + its 16 kHz WAV.   clips = the audio + WAV + the clip videos (a budget for
    max_clips + 10 picks). auto takes "full" only while the video is at most full_max_gb and fits."""
    d = cfg.download
    live = _live_status(info) == "is_live"
    secs = (info.get("duration") or 0) if not live else _live_elapsed(info, live_last_s)
    secs = secs or _live_elapsed(info, live_last_s)
    if secs > d.max_hours * 3600:
        raise DownloadError(
            f"That video is {secs / 3600:.0f} h long; the limit is download.max_hours ({d.max_hours:g} h)."
            + (" Set download.live_last_min in configs/shorts.yaml to take only the end of the stream." if live
               else ""))
    fmts = info.get("requested_formats") or [info]
    full = _estimate_size(info, live_last_s)
    wav = secs * SR_BYTES
    audio = sum(_fmt_size(f, secs) for f in fmts if f.get("vcodec") in (None, "none")) or int(secs * 16_000)
    video_bps = sum((f.get("tbr") or 0) for f in fmts if f.get("vcodec") not in (None, "none")) * 1000 / 8
    sel = cfg.select
    clips = (sel.max_clips + 10) * (sel.max_clip_s + 2 * d.clip_pad_s + 2 * 8 + 4) * (video_bps or 1e6)
    keep = d.min_free_disk_gb

    def fits(n: float) -> bool:
        return free_gb - n / 1e9 >= keep

    gb = lambda n: f"{n / 1e9:.1f} GB"  # noqa: E731
    if d.mode == "full" or (d.mode == "auto" and full <= d.full_max_gb * 1e9 and fits(full + wav + clips)):
        if not fits(full + wav):
            raise DownloadError(
                f"Not enough disk: download ≈{gb(full)} (+ {gb(wav)} audio for Whisper), free {free_gb:.1f} GB, "
                f"must keep {keep} GB free. Set download.mode: auto in configs/shorts.yaml (downloads only the "
                "audio + the clips), delete old jobs, or lower download.max_height.")
        return "full", f"whole video ≈{gb(full)}"
    need = audio + wav + clips
    if not fits(need):
        raise DownloadError(
            f"Not enough disk even for audio + clips: audio ≈{gb(audio)}, Whisper audio {gb(wav)}, clip videos "
            f"up to ≈{gb(clips)}; free {free_gb:.1f} GB, must keep {keep} GB free. Delete old jobs or lower "
            "download.max_height" + (" (or set download.live_last_min)." if live else "."))
    why = (f"whole video would be ≈{gb(full)}: downloading the audio (≈{gb(audio)}) now and only the "
           "picked clips' video later")
    return "clips", why


SR_BYTES = 32_000  # 16 kHz mono 16-bit WAV, per second


def _download_youtube(url: str, job: JobRef, cfg: Config, report: Reporter) -> VideoMeta:
    import yt_dlp

    files: dict[str, tuple[int, int]] = {}
    expected = {"total": 0}

    def hook(d: dict) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            files[d["filename"]] = (d.get("downloaded_bytes") or 0, total)
            done = sum(b for b, _ in files.values())
            whole = max(expected["total"], sum(t for _, t in files.values()), 1)
            speed = d.get("speed") or 0
            left = f" · ~{(whole - done) / speed / 60:.0f} min left" if speed > 0 and whole > done else ""
            report(min(done / whole, 0.99),
                   f"{expected.get('what', '')}{done / 1e6:.0f} / {whole / 1e6:.0f} MB · {speed / 1e6:.1f} MB/s{left}")
        elif d["status"] == "finished":
            report(None, "Merging audio and video…" if expected.get("mode") == "full" else "Finishing…")

    base = _ydl_opts(cfg)
    try:
        with yt_dlp.YoutubeDL({**base, "outtmpl": str(job.dir / "source.%(ext)s"), "progress_hooks": [hook]}) as ydl:
            report(None, "Picking best format…")
            info = ydl.extract_info(url, download=False)
            _reject_upcoming(info)
            live = _live_status(info) == "is_live"
            last_s = cfg.download.live_last_min * 60 if live else 0
            mode, why = plan_mode(info, cfg, free_disk_gb(job.dir), last_s)
            expected["mode"] = mode
            vfmt = next((f for f in info.get("requested_formats") or [info] if f.get("vcodec") not in (None, "none")), info)
            afmt = next((f for f in info.get("requested_formats") or [] if f.get("vcodec") in (None, "none")), None)
            if mode == "full":
                if live:
                    _snapshot_live(info, last_s)
                need = _estimate_size(info, last_s)
                expected["total"] = need
                if live:
                    mins = _live_elapsed(info, last_s) / 60
                    what = f"the last {mins:.0f} min" if last_s else f"from the start up to now (≈{mins:.0f} min)"
                    report(0.0, f"Live stream: downloading {what} at {info.get('height') or '?'}p ≈{need / 1e9:.2f} GB")
                else:
                    report(0.0, f"Downloading {info.get('height') or '?'}p ≈{need / 1e9:.2f} GB")
                ydl.process_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e

    if mode == "clips":
        for f in job.dir.glob("source.*"):  # partial full download from an earlier attempt
            f.unlink(missing_ok=True)
        src = _download_audio(url, job, cfg, report, hook, expected, live, last_s, why, afmt)
        save_json(info_from_ytdlp(info), job.dir / INFO)
        dur = ffmpeg.duration(src)
        fps = float(vfmt.get("fps") or 30)
        return VideoMeta(id=job.id, title=job.title, source_url=url, file=src.name, mode="clips",
                         duration=dur, width=int(vfmt.get("width") or 1920), height=int(vfmt.get("height") or 1080),
                         fps=round(fps, 3), vcodec=(vfmt.get("vcodec") or "").split(".")[0] or None,
                         video_format=vfmt.get("format_id"), audio_format=(afmt or {}).get("format_id"))

    save_json(info_from_ytdlp(info), job.dir / INFO)
    for f in job.dir.glob(f"{AUDIO_SRC}.*"):  # an earlier audio-only attempt (settings changed since)
        f.unlink(missing_ok=True)
    candidates = [p for p in job.dir.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if not candidates:
        raise DownloadError("yt-dlp finished but no source file was found")
    src = max(candidates, key=lambda p: p.stat().st_size)
    meta = _meta_from_file(src, job.id, job.title, url)
    meta.video_format = vfmt.get("format_id")
    meta.audio_format = (afmt or {}).get("format_id")
    return meta


AUDIO_SRC = "source_audio"


def _download_audio(url: str, job: JobRef, cfg: Config, report: Reporter, hook, expected: dict,
                    live: bool, last_s: float, why: str, afmt: dict | None) -> Path:
    """Clips mode: the whole soundtrack only (what transcribe + select read)."""
    import yt_dlp

    # the same audio stream the clip videos will carry, so their audio lines up sample for sample
    fmt = f"{afmt['format_id']}/ba[ext=m4a]/ba" if afmt else "ba[ext=m4a]/ba"
    opts = {**_ydl_opts(cfg), "format": fmt, "format_sort": [], "outtmpl": str(job.dir / f"{AUDIO_SRC}.%(ext)s"),
            "progress_hooks": [hook]}
    opts.pop("merge_output_format", None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("vcodec") not in (None, "none"):
                raise DownloadError("This video has no separate audio stream, so it can't be fetched clip by "
                                    "clip. Set download.mode: full in configs/shorts.yaml.")
            if live:
                _snapshot_live(info, last_s)
            need = _estimate_size(info, last_s)
            expected["total"], expected["what"] = need, "Audio: "
            report(0.0, f"Long video, {why}")
            ydl.process_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e
    got = [p for p in job.dir.glob(f"{AUDIO_SRC}.*") if p.suffix not in {".part", ".ytdl"}]
    if not got:
        raise DownloadError("yt-dlp finished but no audio file was found")
    return max(got, key=lambda p: p.stat().st_size)


# ---------------------------------------------------------------- time ranges (clip videos)

def extract_for_ranges(meta: VideoMeta, cfg: Config) -> dict:
    """Fresh format info (media URLs expire) for fetching clip ranges, on the same streams as the job."""
    import yt_dlp

    h = cfg.download.max_height
    fmt = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    if meta.video_format and meta.audio_format:
        fmt = f"{meta.video_format}+{meta.audio_format}/{fmt}"
    try:
        with yt_dlp.YoutubeDL({**_ydl_opts(cfg), "format": fmt, "no_warnings": True}) as ydl:
            info = ydl.extract_info(meta.source_url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e
    _reject_upcoming(info)
    return info


def _formats(info: dict) -> list[dict]:
    return info.get("requested_formats") or [info]


def is_fragmented(info: dict) -> bool:
    """Live / post-live streams come as numbered fragments (no byte ranges): ranges are fragment slices."""
    return all(f.get("fragments") is not None for f in _formats(info))


def bytes_per_s(info: dict) -> float:
    return sum((f.get("tbr") or 0) for f in _formats(info)) * 1000 / 8 or 1.5e6


def frag_starts(fmt: dict, info: dict) -> tuple[list[float] | None, float]:
    """(start time of each fragment, nominal fragment length). Real per-fragment durations when the manifest
    has them, else evenly spread over the stream's duration (fragments are nominally 1-5 s)."""
    frags = fmt.get("fragments")
    nominal = float(fmt.get("target_duration") or info.get("target_duration") or 5.0)
    if callable(frags):
        return None, nominal
    n = len(frags)
    if n and all(f.get("duration") for f in frags[: min(n, 3)]) and all(f.get("duration") for f in frags):
        starts, t = [], 0.0
        for f in frags:
            starts.append(t)
            t += float(f["duration"])
        return starts, nominal
    step = (info.get("duration") or 0) / n if n and info.get("duration") else nominal
    return [i * step for i in range(n)], step


def frag_slice(starts: list[float], x0: float, x1: float) -> tuple[int, int]:
    """Indices [i0, i1] of the fragments that cover [x0, x1]."""
    import bisect
    i0 = max(0, bisect.bisect_right(starts, max(x0, 0.0)) - 1)
    i1 = max(i0, min(len(starts) - 1, bisect.bisect_left(starts, x1)))
    return i0, i1


def _sq_window(fragments, i0: int, i1: int):
    """Live (still broadcasting): yt-dlp's fragment generator, limited to sequence numbers i0..i1."""
    def sq_of(frag) -> int | None:
        sq = parse_qs(urlparse(frag.get("url") or "").query).get("sq")
        return int(sq[0]) if sq else None

    def gen(ctx):
        for frag in fragments(ctx):
            sq = sq_of(frag)
            if sq is None or sq < i0:
                continue
            yield frag
            if sq >= i1:
                return
    return gen


def download_range(info: dict, cfg: Config, x0: float, x1: float, dst: Path, *,
                   audio_only: bool = False) -> tuple[Path, float]:
    """Download about [x0, x1] (seconds on the stream's own clock) to dst.<ext>. Returns (file, the nominal
    time its first fragment starts at). The real position is measured by the caller (sources.locate)."""
    import yt_dlp

    x0 = max(0.0, x0)
    for f in dst.parent.glob(dst.name + ".*"):
        f.unlink(missing_ok=True)
    opts = {**_ydl_opts(cfg), "outtmpl": str(dst) + ".%(ext)s", "progress_hooks": []}
    fmts = _formats(info)
    if audio_only:
        fmts = [f for f in fmts if f.get("vcodec") in (None, "none")] or fmts[-1:]
    try:
        if is_fragmented(info):
            r0 = None
            new_fmts = []
            for f in fmts:
                starts, step = frag_starts(f, info)
                if starts is None:  # still live: sequence numbers are fragment indices
                    i0, i1 = int(x0 // step), int(x1 // step) + 1
                    new_fmts.append({**f, "fragments": _sq_window(f["fragments"], i0, i1)})
                    r0 = i0 * step if r0 is None else r0
                else:
                    i0, i1 = frag_slice(starts, x0, x1)
                    new_fmts.append({**f, "fragments": list(f["fragments"][i0: i1 + 1])})
                    r0 = starts[i0] if r0 is None else r0
            one = dict(info)
            if len(new_fmts) == 1:
                one.pop("requested_formats", None)
                one.update(new_fmts[0])
            else:
                one["requested_formats"] = new_fmts
            one.pop("requested_downloads", None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.process_info(one)
        else:  # ordinary video: ffmpeg reads just the range over HTTP
            fmt = "+".join(f["format_id"] for f in fmts)
            opts.update({"format": fmt, "download_ranges": yt_dlp.utils.download_range_func(None, [(x0, x1)]),
                         "force_keyframes_at_cuts": False})
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(info.get("webpage_url") or info["original_url"], download=True)
            r0 = x0
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e
    got = [p for p in dst.parent.glob(dst.name + ".*") if p.suffix not in {".part", ".ytdl"}]
    if not got:
        raise DownloadError("yt-dlp finished but the clip file is missing")
    path = max(got, key=lambda p: p.stat().st_size)
    return path, float(r0 or 0.0)


def _clean_ytdlp_error(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).removeprefix("ERROR: ").strip()
    if re.search(r"sign in|not a bot|cookies", msg, re.I):
        msg += (" (Export YouTube cookies to data/shorts/cookies.txt (on a server: scripts/sync-settings.sh "
                "copies it), or set download.cookies_from_browser in configs/shorts.yaml; "
                "and update yt-dlp: pip install -U 'yt-dlp[default]')")
    return msg


def _copy_local(path: Path, job: JobRef) -> VideoMeta:
    dst = job.dir / f"source{path.suffix.lower()}"
    if not dst.exists():
        try:
            dst.hardlink_to(path)  # no extra disk use when on the same filesystem
        except OSError:
            shutil.copy2(path, dst)
    return _meta_from_file(dst, job.id, path.stem, None)


def _meta_from_file(src: Path, vid: str, title: str, url: str | None) -> VideoMeta:
    info = ffmpeg.video_info(src)
    return VideoMeta(id=vid, title=title, source_url=url, file=src.name, **info)


def info_from_ytdlp(info: dict) -> VideoInfo:
    return VideoInfo(
        description=(info.get("description") or "")[:3000],
        channel=info.get("channel") or info.get("uploader") or "",
        tags=[str(t) for t in (info.get("tags") or [])][:30],
        chapters=[{"start_time": c.get("start_time"), "end_time": c.get("end_time"), "title": c.get("title")}
                  for c in (info.get("chapters") or [])],
        heatmap=[{"start_time": h.get("start_time"), "end_time": h.get("end_time"), "value": h.get("value")}
                 for h in (info.get("heatmap") or [])],
    )


def load_info(job_dir: Path, meta: VideoMeta, cfg: Config) -> VideoInfo:
    """Extra YouTube metadata; fetched once (no media download) for jobs made before it was saved."""
    p = job_dir / INFO
    if p.exists():
        return load_json(VideoInfo, p)
    info = VideoInfo()
    if meta.source_url:
        try:
            import yt_dlp

            with yt_dlp.YoutubeDL({**_ydl_opts(cfg), "no_warnings": True}) as ydl:
                info = info_from_ytdlp(ydl.extract_info(meta.source_url, download=False))
        except Exception:  # noqa: BLE001  (nice to have; never fail the pipeline over it)
            return info
    save_json(info, p)
    return info
