"""Stage 1: fetch the source video (YouTube URL or local file) into workspace/<id>/."""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

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
        "quiet": True,
        "noprogress": True,
        "js_runtimes": {"deno": {}, "node": {}},  # YouTube needs a JS runtime for signatures
        "retries": 10,
        "fragment_retries": 10,
    }
    if d.cookies_from_browser:
        opts["cookiesfrombrowser"] = (d.cookies_from_browser,)
    cookies = resolve(d.cookies_file)
    if cookies.is_file():
        opts["cookiefile"] = str(cookies)
    return opts


def _estimate_size(info: dict) -> int:
    fmts = info.get("requested_formats") or [info]
    return sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in fmts)


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
        return JobRef(
            id=info["id"],
            dir=cfg.workspace() / info["id"],
            source=source,
            title=info.get("title") or info["id"],
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
            need = _estimate_size(info)
            expected["total"] = need
            free = free_disk_gb(job.dir)
            if free - need / 1e9 < cfg.download.min_free_disk_gb:
                raise DownloadError(
                    f"Not enough disk: download ≈{need / 1e9:.1f} GB, free {free:.1f} GB, "
                    f"must keep {cfg.download.min_free_disk_gb} GB free. Delete old jobs or "
                    "lower download.max_height in configs/shorts.yaml."
                )
            report(0.0, f"Downloading {info.get('height') or '?'}p ≈{need / 1e9:.2f} GB")
            ydl.process_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_ytdlp_error(e)) from e

    save_json(info_from_ytdlp(info), job.dir / INFO)
    candidates = [p for p in job.dir.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if not candidates:
        raise DownloadError("yt-dlp finished but no source file was found")
    src = max(candidates, key=lambda p: p.stat().st_size)
    return _meta_from_file(src, info["id"], info.get("title") or info["id"], url)


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
