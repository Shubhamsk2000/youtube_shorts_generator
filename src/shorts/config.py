from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from common.paths import CONFIG_DIR, resolve


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DownloadCfg(_Strict):
    max_height: int = 2160
    prefer_codec: str = "vp9"
    cookies_from_browser: str | None = None
    min_free_disk_gb: float = 3.0


class TranscribeCfg(_Strict):
    model: str = "small.en"
    compute_type: str = "int8"
    threads: int = 4
    beam_size: int = 5
    vad: bool = True
    min_word_prob: float = 0.0


class SentenceCfg(_Strict):
    max_words: int = 30
    max_gap_s: float = 1.0


class SelectCfg(_Strict):
    model: str = "gemini-2.5-flash"
    fallback_models: list[str] = ["gemini-flash-latest", "gemini-2.5-flash-lite"]
    window_min: float = 20.0     # transcript sent to Gemini in windows of about this length
    overlap_min: float = 2.0     # windows overlap so a moment on a boundary isn't lost
    min_clip_s: float = 15.0
    max_clip_s: float = 60.0
    min_score: float = 4.0       # deliberately low: a human reviews the picks
    max_clips: int = 25
    max_overlap_s: float = 2.0   # two picks sharing more than this are duplicates
    temperature: float = 0.2
    timeout_s: float = 180.0
    max_retries: int = 4         # per model, on 429/503/timeouts
    min_interval_s: float = 4.0  # spacing between calls (free-tier per-minute limits)
    offline_fallback: bool = True  # if Gemini is unreachable, pick moments with a heuristic
    refine: bool = True          # 2nd Gemini pass: compare all picks, tighten hooks, write on-screen text
    use_heatmap: bool = True     # YouTube "Most replayed" data as a signal (when the video has it)
    heat_bonus: float = 1.5      # score bonus for clips viewers replayed most (0 = off)


class ReframeCfg(_Strict):
    enabled: bool = True            # false = plain center crop
    sample_fps: float = 6.0         # face detection rate (plus the first frame of every shot)
    analysis_height: int = 360      # frames are downscaled to this for detection
    face_score: float = 0.7         # YuNet confidence threshold
    min_face_h: float = 0.05        # ignore faces smaller than this fraction of the frame height
    cut_threshold: float = 0.25     # cut score (pixel change + layout change) that counts as a camera cut
    group_ratio: float = 0.35       # faces at least this big (vs the largest) count as "the group"
    dominance: float = 1.5          # a face this much bigger than the next one is "the subject"
    fit_fraction: float = 0.5       # shots where >= this share of samples can't fit 9:16 -> FIT
    no_face_fraction: float = 0.6   # shots with no face in >= this share of samples -> CENTER
    static_tolerance: float = 0.05  # subject moves less than this (fraction of width) -> locked crop
    smooth_s: float = 0.5           # smoothing window of a moving crop
    max_speed: float = 0.5          # crop moves at most this fraction of the frame width per second
    fit_y: float = 0.42             # FIT shots: vertical centre of the full frame (keeps captions clear)
    fit_blur: int = 31              # FIT shots: background blur strength
    fit_dim: float = 0.55           # FIT shots: background brightness
    screen_layout: str = "split"    # facecam over gameplay/screen: "split" (cam top, screen bottom) or "track"
    cam_edge_persistence: float = 0.35  # how steady a cam box border must be (0-1) to count as a facecam overlay
    split_cam_min: float = 0.30     # SPLIT shots: cam panel height, min/max fraction of the output height
    split_cam_max: float = 0.45


class CaptionCfg(_Strict):
    enabled: bool = True
    font: str = "Inter Black"
    size: int = 92               # on a 1080x1920 canvas (scales with the output)
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 16          # per line, before the line breaks early
    pause_s: float = 0.35        # a pause longer than this starts a new line
    min_word_s: float = 0.08     # words Whisper timed as ~0 s get at least this long
    position: float = 0.68       # bottom edge of the text, as a fraction of the height
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    emphasis_color: str = "#4ADE80"
    outline: float = 7
    shadow: float = 3
    pop_scale: float = 1.08      # active word grows this much
    offset_ms: int = 0           # shift all captions; negative = earlier


class HookCfg(_Strict):
    enabled: bool = True         # on-screen hook text at the top for the first seconds
    duration_s: float = 2.8
    font: str = "Inter Black"
    size: int = 70               # on a 1080x1920 canvas
    position: float = 0.17       # vertical centre, fraction of the height (below the Shorts top bar)
    text_color: str = "#000000"
    box_color: str = "#FFFFFF"
    max_chars_per_line: int = 18


class PacingCfg(_Strict):
    enabled: bool = True         # tighten pauses between words (jump cuts)
    min_gap_s: float = 0.55      # only pauses longer than this are shortened
    keep_gap_s: float = 0.2      # ...down to this much air
    max_gap_s: float = 3.0       # longer pauses are kept: usually something is happening on screen
    quiet_ratio: float = 0.35    # a pause is cut only if it is this quiet vs the speech (no reactions/action)


class AudioCfg(_Strict):
    loudness_lufs: float = -14.0  # Shorts / TikTok / Reels playback level
    true_peak: float = -1.5
    highpass_hz: int = 70         # remove rumble (0 = off)


class PreviewCfg(_Strict):
    """Quick review renders: deliberately low quality so they're fast. Exports are full quality."""
    width: int = 540
    height: int = 960
    crf: int = 28
    preset: str = "ultrafast"
    audio_bitrate: str = "96k"
    sharpen: float = 0.0
    max_fps: float = 30          # 60 fps sources are previewed at 30 (half the work)
    fast: bool = True            # bilinear resizing instead of Lanczos


class ExportCfg(_Strict):
    dir: Path = Path("data/shorts/exports")  # finished uploads land in <dir>/<video title>/
    width: int = 1080
    height: int = 1920
    crf: int = 18
    preset: str = "faster"       # x264 speed; "medium"/"slow" = smaller file, much slower
    audio_bitrate: str = "192k"
    sharpen: float = 0.45        # light unsharp after upscaling (0 = off)
    max_fps: float = 0           # 0 = keep the source frame rate
    fast: bool = False

    def out_dir(self) -> Path:
        return resolve(self.dir)


class Config(_Strict):
    workspace_dir: Path = Path("data/shorts/workspace")
    download: DownloadCfg = DownloadCfg()
    transcribe: TranscribeCfg = TranscribeCfg()
    sentences: SentenceCfg = SentenceCfg()
    select: SelectCfg = SelectCfg()
    reframe: ReframeCfg = ReframeCfg()
    captions: CaptionCfg = CaptionCfg()
    preview: PreviewCfg = PreviewCfg()
    hook: HookCfg = HookCfg()
    pacing: PacingCfg = PacingCfg()
    audio: AudioCfg = AudioCfg()
    export: ExportCfg = ExportCfg()

    def workspace(self) -> Path:
        return resolve(self.workspace_dir)


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_DIR / "shorts.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Config.model_validate(data or {})
