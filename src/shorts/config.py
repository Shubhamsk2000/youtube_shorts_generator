from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from common.paths import CONFIG_DIR, resolve


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DownloadCfg(_Strict):
    max_height: int = 2160
    prefer_codec: str = "vp9"
    cookies_from_browser: str | None = None
    cookies_file: Path = Path("data/shorts/cookies.txt")  # used only if it exists
    min_free_disk_gb: float = 3.0
    live_last_min: float = 0.0   # live streams: only the last N minutes up to now (0 = from the start)
    # How the source is fetched:
    #   "full"  = download the whole video (as before)
    #   "clips" = download only the AUDIO of the whole video (for transcribe + select), then only the video
    #             of the picked clips (each its own small file). For long streams that would never fit on disk.
    #   "auto"  = "full" while the whole video is at most full_max_gb (and fits the disk), else "clips"
    mode: Literal["auto", "full", "clips"] = "auto"
    full_max_gb: float = 6.0
    clip_pad_s: float = 3.0      # clip video files reach this far beyond the clip on each side
    keep_source: bool = False    # False: delete the full download once every clip has its own video file
                                 # (YouTube only; uploaded local files are never deleted)
    max_hours: float = 36.0      # longest source accepted (the 16 kHz audio of 37 h hits the 4 GB WAV limit)


class TranscribeCfg(_Strict):
    model: str = "large-v3-turbo"
    compute_type: str = "int8"
    threads: int = 0             # 0 = all CPU cores
    beam_size: int = 5
    vad: bool = True
    min_word_prob: float = 0.0
    title_hints: bool = True   # pass the video title / channel to Whisper so names and terms are spelled right
    chunk_min: float = 30.0    # audio longer than 1.5x this is transcribed in chunks of this length, each saved
                               # as it finishes, so an interrupted multi-hour stream resumes where it stopped


class SentenceCfg(_Strict):
    max_words: int = 20
    max_gap_s: float = 1.0


class SelectCfg(_Strict):
    model: str = "gemini-2.5-flash"
    fallback_models: list[str] = ["gemini-flash-latest", "gemini-2.5-flash-lite"]
    window_min: float = 20.0     # transcript sent to Gemini in windows of about this length
    overlap_min: float = 2.0     # windows overlap so a moment on a boundary isn't lost
    min_clip_s: float = 15.0
    max_clip_s: float = 50.0
    min_score: float = 4.0       # deliberately low: a human reviews the picks
    min_hook_score: float = 5.0  # after the editor pass: drop clips whose opening won't stop the scroll
    min_standalone_score: float = 5.0  # ...or that need the rest of the video to make sense
    trim_lead_fillers: bool = True  # start on the first real word ("And now we're back" -> "we're back")
    trim_promo: bool = True      # cut trailing plugs ("go check it out", sponsors, "link in the description")
    loop_bonus: float = 0.3      # score bonus when the ending leads back into the opening (clean replay)
    max_clips: int = 25
    max_overlap_s: float = 2.0   # two picks sharing more than this are duplicates
    temperature: float = 0.2
    timeout_s: float = 180.0
    max_retries: int = 4         # per model, on 429/503/timeouts
    min_interval_s: float = 4.0  # spacing between calls (free-tier per-minute limits)
    offline_fallback: bool = False  # every AI failed: pick rough moments with a heuristic (off = stop with Retry)
    refine: bool = True          # 2nd Gemini pass: compare all picks, tighten hooks, write on-screen text
    use_heatmap: bool = True     # YouTube "Most replayed" data as a signal (when the video has it)
    use_audio: bool = True       # loudness + reactions (crash, screams, laughter) from the audio as signals
    loud_top_fraction: float = 0.12  # share of lines marked "loud" for the picker
    burst_min_s: float = 0.75    # loud non-speech right after a line must last this long to count as a reaction
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
    # facecam over gameplay/screen: "pip" (gameplay fills the frame, cam in a top corner),
    # "split" (cam panel on top, screen underneath) or "track" (ignore the cam box, follow faces)
    screen_layout: Literal["pip", "split", "track"] = "pip"
    cam_edge_persistence: float = 0.35  # how steady a cam box border must be (0-1) to count as a facecam overlay
    detect_text: bool = True        # find burned-in text (the source's own captions, graphics) so ours avoid it
    text_fps: float = 1.0           # how often to look for it
    split_cam_min: float = 0.20     # SPLIT shots: cam panel height, min/max fraction of the output height
    split_cam_max: float = 0.30     # (sized to the cam's shape in between; min = max fixes it)
    pip_corner: Literal["auto", "left", "right"] = "auto"  # PIP shots: top corner for the cam (auto = the side it's on in the source)
    pip_width: float = 0.42         # cam width, fraction of the output width
    pip_max_h: float = 0.28         # ...and at most this fraction of the output height
    pip_top: float = 0.075          # top edge, fraction of the height (clear of the Shorts top bar)
    pip_margin: float = 0.035       # gap to the side edge, fraction of the width
    pip_radius: float = 0.07        # corner rounding, fraction of the cam width
    pip_border: int = 6             # px on a 1080-wide canvas (0 = none)
    pip_border_color: str = "#FFFFFF"
    pip_shadow: float = 0.45        # drop-shadow strength (0 = none)
    pip_screen_aspect: float = 0.5625  # gameplay width/height: 0.5625 = fills the 9:16 frame; wider (0.625 = 10:16,
                                       # 0.6875 = 11:16) zooms out, full width over a blurred copy of itself
    # smart layout: picks the framing per shot. Gameplay-only shots of a stream (no face, while other shots
    # have the facecam) show the whole frame over the background instead of a narrow centre crop, and a
    # blip of under smart_blip_s between two shots with the same facecam keeps the facecam layout
    smart_layout: bool = True
    smart_blip_s: float = 1.2
    # what is around the video: "blur" = a blurred, dimmed copy of the frame (only visible where the video
    # doesn't fill the frame); "tweet" = the video inside a tweet card (Channel card in Studio) over the blur,
    # with the hook text as the tweet
    background: Literal["blur", "tweet"] = "blur"
    tweet_window_aspect: float = 0.8   # tweet card video window, width/height (shrunk to fit the card on screen)

    # settings that only change how a plan is drawn, not the analysis: excluded from the plan cache key
    RENDER_ONLY: ClassVar[set[str]] = {"fit_y", "fit_blur", "fit_dim", "split_cam_min", "split_cam_max",
                                        "pip_corner", "pip_width", "pip_max_h", "pip_top", "pip_margin",
                                        "pip_radius", "pip_border", "pip_border_color", "pip_shadow",
                                        "pip_screen_aspect", "smart_layout", "smart_blip_s", "background",
                                        "tweet_window_aspect"}


class CaptionCfg(_Strict):
    enabled: bool = True
    font: str = "Inter Black"
    size: int = 92               # on a 1080x1920 canvas (scales with the output)
    uppercase: bool = True
    max_words: int = 2
    max_chars: int = 16          # per line, before the line breaks early
    pause_s: float = 0.35        # a pause longer than this starts a new line
    min_word_s: float = 0.08     # words Whisper timed as ~0 s get at least this long
    position: float = 0.6        # bottom edge of the text, as a fraction of the height
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    emphasis_color: str = "#4ADE80"
    emphasize_numbers: bool = True  # money and numbers ("$10,000", "100") always in the emphasis colour
    avoid_text: bool = True      # move a caption line above/below burned-in text it would cover
    text_margin: int = 24        # px of air between our caption and the source's text (1080x1920 canvas)
    safe_top: float = 0.30       # captions never move above this (hook banner, Shorts search bar)...
    safe_bottom: float = 0.78    # ...or below this (title, buttons)
    outline: float = 7
    shadow: float = 3
    pop_scale: float = 1.08      # active word grows this much
    offset_ms: int = 0           # shift all captions; negative = earlier


class HookCfg(_Strict):
    enabled: bool = True         # on-screen hook text at the top for the first seconds
    auto: bool = True            # skip it when the spoken first line already states the premise
    duration_s: float = 2.8
    font: str = "Inter Black"
    size: int = 70               # on a 1080x1920 canvas
    position: float = 0.17       # vertical centre, fraction of the height (below the Shorts top bar)
    text_color: str = "#000000"
    box_color: str = "#FFFFFF"
    max_chars_per_line: int = 18


class ColdOpenCfg(_Strict):
    # open on a flash of the payoff, then hard-cut to the start. Off by default: out of context it reads as a
    # stray fragment of another moment (bad editing) more often than as a tease
    enabled: bool = False
    duration_s: float = 0.7      # length of a wordless reaction flash (crash, scream, laugh)
    min_phrase_s: float = 0.4    # a spoken flash is a whole phrase with a pause on both sides, this long...
    max_phrase_s: float = 1.2    # ...at most
    min_lead_s: float = 6.0      # only when the payoff is at least this far into the clip (else it's not a tease)
    when: str = "always"         # "always" | "no_premise": only when the spoken opening doesn't state the premise


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


class CardCfg(_Strict):
    """The tweet card around the video (reframe.background: tweet). YOUR channel, never someone else's account."""
    theme: Literal["dark", "light"] = "dark"
    name: str = "Your Channel"       # display name
    handle: str = "@yourchannel"
    avatar: Path | None = None       # square image (png/jpg); none = initial in a coloured circle
    verified: bool = True
    footer: Literal["date", "none"] = "date"
    font: str = "Inter"
    text_size: int = 46              # tweet text, on a 1080x1920 canvas
    max_lines: int = 4
    center_y: float = 0.44           # vertical centre of the card (fraction of the height)


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
    cold_open: ColdOpenCfg = ColdOpenCfg()
    pacing: PacingCfg = PacingCfg()
    audio: AudioCfg = AudioCfg()
    export: ExportCfg = ExportCfg()
    card: CardCfg = CardCfg()

    def workspace(self) -> Path:
        return resolve(self.workspace_dir)


LOCAL = CONFIG_DIR / "shorts.local.yaml"  # written by Studio's Settings page; wins over shorts.yaml (git-ignored)


_DEFAULT = object()


def load_config(path: Path | None = None, *, local=_DEFAULT) -> Config:
    """configs/shorts.yaml (or `path`) with the UI's overrides from configs/shorts.local.yaml on top
    (local=None: shorts.yaml only)."""
    path = path or CONFIG_DIR / "shorts.yaml"
    local = LOCAL if local is _DEFAULT else local
    data = (yaml.safe_load(path.read_text()) if path.exists() else {}) or {}
    if local is not None and local.exists():
        data = merge(data, yaml.safe_load(local.read_text()) or {})
    return Config.model_validate(data)


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def read_local(local: Path | None = None) -> dict:
    local = local or LOCAL
    try:
        data = yaml.safe_load(local.read_text()) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def write_local(data: dict, local: Path | None = None) -> None:
    """Replace configs/shorts.local.yaml (empty sections dropped), atomically."""
    local = local or LOCAL
    data = {k: v for k, v in data.items() if v not in ({}, None)}
    tmp = local.with_suffix(".tmp")
    tmp.write_text("# Saved by Studio (Settings / Channel card). Wins over shorts.yaml. Delete a line to go back.\n"
                   + (yaml.safe_dump(data, sort_keys=False, allow_unicode=True) if data else ""))
    tmp.replace(local)


def save_local(section: str, values: dict, local: Path | None = None) -> None:
    """Merge values into one section of configs/shorts.local.yaml."""
    data = read_local(local)
    data[section] = {**(data.get(section) or {}), **values}
    write_local(data, local)


def migrate_legacy_card(local: Path | None = None) -> bool:
    """The channel card used to live in configs/anime.local.yaml (the removed anime app). Carry it over once,
    with its avatar (data/anime/avatar.png -> data/shorts/avatar.png). True when something was migrated."""
    import shutil

    old = CONFIG_DIR / "anime.local.yaml"
    if not old.exists() or (read_local(local).get("card")):
        return False
    card = (yaml.safe_load(old.read_text()) or {}).get("card") or {}
    if not isinstance(card, dict) or not card:
        return False
    if card.get("avatar"):
        src = resolve(Path(card["avatar"]))
        dst = resolve(Path("data/shorts/avatar.png"))
        if src.exists() and src != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            card["avatar"] = "data/shorts/avatar.png"
        elif not src.exists():
            card["avatar"] = None
    allowed = set(CardCfg.model_fields)
    save_local("card", {k: v for k, v in card.items() if k in allowed}, local)
    return True


# ---------------------------------------------------------------- per-video layout (Studio's Layout panel)

LAYOUT_FILE = "layout.json"   # workspace/<id>/layout.json: overrides of the reframe keys below, for one video
LAYOUT_KEYS = ("screen_layout", "split_cam_min", "split_cam_max", "smart_layout", "background")


def load_job_layout(job_dir: Path) -> dict:
    try:
        import json
        data = json.loads((job_dir / LAYOUT_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in LAYOUT_KEYS} if isinstance(data, dict) else {}


def with_job_layout(cfg: Config, job_dir: Path) -> Config:
    """cfg with this video's layout overrides applied (idempotent; unchanged when it has none)."""
    over = load_job_layout(job_dir)
    if not over:
        return cfg
    rc = ReframeCfg.model_validate({**cfg.reframe.model_dump(), **over})
    return cfg.model_copy(update={"reframe": rc})
