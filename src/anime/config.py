"""Settings for the anime edits app (configs/anime.yaml). Unknown keys are rejected."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from common.paths import CONFIG_DIR, resolve

Style = Literal["clip", "montage", "song"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicCfg(_Strict):
    """Song edits (AMV style): your songs in data/anime/music/, cut on their beats."""
    min_s: float = 40.0              # length of the song section used (whole bars)
    max_s: float = 60.0
    tag_with_gemini: bool = True     # Gemini listens once to name each song's mood (else folder name / a guess)
    drop_cut_beats: int = 1          # cut every N beats where the song is loudest...
    mid_cut_beats: int = 2           # ...every N beats in the middle...
    calm_cut_beats: int = 4          # ...and every N beats (one bar) where it is calm
    min_shot_s: float = 0.55         # no shot shorter than this (a fast song cuts every 2 beats instead of 1)


class SubsCfg(_Strict):
    languages: list[str] = ["eng", "en"]   # preferred subtitle track languages, in order
    audio_languages: list[str] = ["eng", "en", "jpn", "ja"]  # preferred audio track: English dub first
    # English audio (dubs): the subtitle track rarely matches what the dub actors say, so transcribe the
    # audio itself (word timings -> word-by-word captions). "track" = use the subtitle track anyway.
    dub_captions: Literal["transcribe", "track"] = "transcribe"
    dub_model: str = "small.en"      # already downloaded by the shorts app
    # ASS styles whose name contains one of these are signs/songs/credits, not dialogue
    drop_styles: list[str] = ["sign", "song", "op", "ed", "kara", "title", "note", "insert", "credit", "lyric"]
    whisper_fallback: bool = True    # no text subtitles -> Whisper translates the Japanese audio to English
    whisper_model: str = "small"     # multilingual model (~480 MB, downloaded once into .cache/)
    compute_type: str = "int8"
    threads: int = 4


class SignalsCfg(_Strict):
    analysis_width: int = 128        # frames are shrunk to this for cut/motion analysis
    cut_threshold: float = 0.09      # mean pixel change (0-1) that can count as a cut...
    cut_ratio: float = 3.5           # ...if it is also this many times the local average
    min_shot_s: float = 0.25
    skip_op_ed: bool = True          # never pick from the opening/ending songs or the next-episode preview
    op_ed_len_s: float = 90.0        # typical OP/ED length (used when there are no chapters)


class SelectCfg(_Strict):
    model: str = "gemini-3.8-flash"
    fallback_models: list[str] = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    count: int = 5                   # shorts per episode
    min_clip_s: float = 20.0         # clip style: one complete scene...
    target_clip_s: float = 60.0      # ...about this long when the scene supports it (never padded)
    max_clip_s: float = 90.0
    montage_min_s: float = 40.0      # montage style: several beats cut together, at least this long...
    montage_max_s: float = 60.0      # ...and at most this long (extra beats are added if Gemini's run short)
    watch: bool = True               # Gemini watches a small proxy of every pick, then rescores and trims it
    watch_height: int = 270          # proxy resolution sent to Gemini (small = fewer tokens)
    temperature: float = 0.4
    timeout_s: float = 150.0
    max_retries: int = 4
    min_interval_s: float = 4.0
    offline_fallback: bool = True    # if Gemini is unreachable, pick by action/loudness only


class EditCfg(_Strict):
    snap_s: float = 0.8              # move a cut point onto a nearby shot change (cleaner cuts)
    auto_punch: bool = True          # montage: zoom punch on the loudest hit of each beat
    punch_zoom: float = 0.16         # zoom-punch strength (0.16 = 16 % bigger)
    punch_s: float = 0.35
    shake_px: float = 10             # camera-shake amplitude, on a 1080-wide canvas
    transition_s: float = 0.25       # flash / zoom / whip / fade length
    end_freeze_s: float = 0.0        # hold the last frame this long (0 = off)


class CardCfg(_Strict):
    theme: Literal["dark", "light"] = "dark"
    name: str = "Anime Edits"        # your channel's display name
    handle: str = "@animeedits"      # your channel's handle
    avatar: Path | None = None       # square image (png/jpg); none = initial in a coloured circle
    verified: bool = True
    footer: Literal["date", "none"] = "date"
    font: str = "Inter"
    text_size: int = 46              # tweet text, on a 1080x1920 canvas
    max_lines: int = 4
    center_y: float = 0.44           # vertical centre of the card (fraction of the height)


class CaptionCfg(_Strict):
    enabled: bool = True             # dialogue subtitles under the card
    font: str = "Inter Black"
    size: int = 54
    color: str = "#FFFFFF"
    word_by_word: bool = True        # transcribed audio (dubs): a few words at a time, current word highlighted
    highlight_color: str = "#FFE600"
    max_words: int = 3
    max_chars: int = 18
    outline: float = 5
    gap: int = 48                    # space between the card and the captions (px at 1080 wide)


class AudioCfg(_Strict):
    loudness_lufs: float = -14.0
    true_peak: float = -1.5


class RenderCfg(_Strict):
    width: int = 1080
    height: int = 1920
    crf: int = 18
    preset: str = "faster"
    audio_bitrate: str = "192k"
    max_fps: float = 0               # 0 = keep the episode frame rate (usually 23.976)
    bg_blur: int = 24                # blurred background strength
    bg_dim: float = 0.55             # blurred background brightness


class Config(_Strict):
    workspace_dir: Path = Path("data/anime/workspace")
    export_dir: Path = Path("data/anime/exports")
    style: Style = "clip"
    subs: SubsCfg = SubsCfg()
    signals: SignalsCfg = SignalsCfg()
    select: SelectCfg = SelectCfg()
    edit: EditCfg = EditCfg()
    music: MusicCfg = MusicCfg()
    card: CardCfg = CardCfg()
    captions: CaptionCfg = CaptionCfg()
    audio: AudioCfg = AudioCfg()
    preview: RenderCfg = RenderCfg(width=540, height=960, crf=28, preset="ultrafast", audio_bitrate="96k")
    export: RenderCfg = RenderCfg()

    def workspace(self) -> Path:
        return resolve(self.workspace_dir)

    def exports(self) -> Path:
        return resolve(self.export_dir)


LOCAL = CONFIG_DIR / "anime.local.yaml"  # written by the web UI (channel card etc.), wins over anime.yaml


def load_config(path: Path | None = None, *, local: Path | None = LOCAL) -> Config:
    path = path or CONFIG_DIR / "anime.yaml"
    data = (yaml.safe_load(path.read_text()) if path.exists() else {}) or {}
    if local and local.exists():
        data = merge(data, yaml.safe_load(local.read_text()) or {})
    return Config.model_validate(data)


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def save_local(section: str, values: dict) -> None:
    """Persist UI settings (one config section) to configs/anime.local.yaml."""
    data = (yaml.safe_load(LOCAL.read_text()) if LOCAL.exists() else {}) or {}
    data[section] = {**(data.get(section) or {}), **values}
    tmp = LOCAL.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    tmp.replace(LOCAL)
