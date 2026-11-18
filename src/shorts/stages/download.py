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
        if (job.dir / meta.file).exists():
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
    if meta.height < 1440:
        msg += " · low-res source, crops will be upscaled"
    return msg


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
            report(min(done / whole, 0.99),
                   f"{done / 1e6:.0f} / {whole / 1e6:.0f} MB · {speed / 1e6:.1f} MB/s")
        elif d["status"] == "finished":
            report(None, "Merging audio and video…")

    opts = {**_ydl_opts(cfg), "outtmpl": str(job.dir / "source.%(ext)s"), "progress_hooks": [hook]}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            report(None, "Picking best format…")
            info = ydl.extract_info(url, download=False)
            _reject_upcoming(info)
            live = _live_status(info) == "is_live"
            last_s = cfg.download.live_last_min * 60 if live else 0
            if live:
                _snapshot_live(info, last_s)
            need = _estimate_size(info, last_s)
            expected["total"] = need
            free = free_disk_gb(job.dir)
            if free - need / 1e9 < cfg.download.min_free_disk_gb:
                raise DownloadError(
                    f"Not enough disk: download ≈{need / 1e9:.1f} GB, free {free:.1f} GB, "
                    f"must keep {cfg.download.min_free_disk_gb} GB free. Delete old jobs or "
                    "lower download.max_height in configs/shorts.yaml"
                    + (" (or set download.live_last_min to grab only the end of the stream)." if live else ".")
                )
            if live:
                mins = _live_elapsed(info, last_s) / 60
                what = f"the last {mins:.0f} min" if last_s else f"from the start up to now (≈{mins:.0f} min)"
                report(0.0, f"Live stream: downloading {what} "
                            f"at {info.get('height') or '?'}p ≈{need / 1e9:.2f} GB")
            else:
                report(0.0, f"Downloading {info.get('height') or '?'}p ≈{need / 1e9:.2f} GB")
            ydl.process_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e

    save_json(info_from_ytdlp(info), job.dir / INFO)
    candidates = [p for p in job.dir.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if not candidates:
        raise DownloadError("yt-dlp finished but no source file was found")
    src = max(candidates, key=lambda p: p.stat().st_size)
    return _meta_from_file(src, job.id, job.title, url)


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
