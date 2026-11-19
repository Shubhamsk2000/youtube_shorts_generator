"""Studio's Settings page: a curated set of configs/shorts.yaml keys that can be changed from the UI.

Values are saved as overrides in configs/shorts.local.yaml (shorts.yaml stays as the documented base). Every
change is validated by building the full Config first, so a bad value can never break the next job.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Config, load_config, read_local, write_local


@dataclass(frozen=True)
class Field:
    key: str                       # "section.name" in shorts.yaml
    label: str
    type: str                      # "bool" | "int" | "float" | "choice" | "color"
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    options: list[dict] = field(default_factory=list)   # choice: [{value, label}]
    applies: str = "render"        # what a change needs: "new" (new videos / re-pick) | "render" (re-render)


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    help: str
    fields: list[Field]


def _opts(*pairs: tuple[str, str]) -> list[dict]:
    return [{"value": v, "label": lab} for v, lab in pairs]


GROUPS: list[Group] = [
    Group("select", "Moment picking", "How the AI chooses clips. Applies to new videos and to Re-pick.", [
        Field("select.max_clips", "Most clips per video", "int", "Upper limit; fewer are kept when fewer pass the checks.",
              1, 60, 1, applies="new"),
        Field("select.min_clip_s", "Shortest clip", "float", "", 5, 60, 1, "s", applies="new"),
        Field("select.max_clip_s", "Longest clip", "float", "YouTube Shorts allow up to 3 min; 30-50 s performs best.",
              10, 180, 1, "s", applies="new"),
        Field("select.min_score", "Minimum score", "float", "Picks scored below this (0-10) are dropped. Low on purpose: "
              "you review every clip.", 0, 10, 0.5, applies="new"),
        Field("select.min_hook_score", "Minimum hook score", "float", "Drops clips whose opening won't stop the scroll.",
              0, 10, 0.5, applies="new"),
        Field("select.refine", "Editor pass", "bool", "A second AI pass compares all picks, tightens the hooks and "
              "writes the on-screen text.", applies="new"),
        Field("select.use_heatmap", "Use “Most replayed”", "bool", "YouTube's replay graph as a signal, when the "
              "video has one.", applies="new"),
        Field("select.trim_promo", "Cut plugs at the end", "bool", "“Go check it out”, sponsors, “link in the "
              "description”.", applies="new"),
    ]),
    Group("captions", "Captions", "Word-by-word subtitles burned into the video.", [
        Field("captions.enabled", "Captions", "bool"),
        Field("captions.uppercase", "UPPERCASE", "bool"),
        Field("captions.max_words", "Words on screen", "int", "At a time.", 1, 6, 1),
        Field("captions.size", "Text size", "int", "On a 1080×1920 frame.", 40, 160, 2, "px"),
        Field("captions.position", "Vertical position", "float", "Bottom edge of the text, from the top.",
              0.3, 0.85, 0.01, "%"),
        Field("captions.text_color", "Text colour", "color"),
        Field("captions.highlight_color", "Current word", "color"),
        Field("captions.emphasis_color", "Emphasis words", "color"),
        Field("captions.emphasize_numbers", "Highlight numbers & money", "bool"),
        Field("captions.offset_ms", "Timing offset", "int", "Negative shows captions earlier.", -500, 500, 10, "ms"),
    ]),
    Group("hook", "Hook banner", "The text box at the top during the first seconds.", [
        Field("hook.enabled", "Hook banner", "bool"),
        Field("hook.auto", "Skip when the first line says it", "bool", "No banner when the spoken opening already "
              "states the premise."),
        Field("hook.duration_s", "On screen for", "float", "", 1, 8, 0.1, "s"),
        Field("hook.size", "Text size", "int", "", 40, 120, 2, "px"),
        Field("hook.text_color", "Text colour", "color"),
        Field("hook.box_color", "Box colour", "color"),
    ]),
    Group("edit", "Editing", "Pacing and the opening.", [
        Field("pacing.enabled", "Tighten pauses", "bool", "Jump-cuts silent gaps between words."),
        Field("pacing.min_gap_s", "Cut pauses longer than", "float", "", 0.2, 2, 0.05, "s"),
        Field("pacing.keep_gap_s", "…down to", "float", "", 0, 1, 0.05, "s"),
        Field("cold_open.enabled", "Cold open", "bool", "Open on a flash of the payoff, then cut to the start. "
              "Off by default: out of context it often reads as a stray fragment."),
    ]),
    Group("layout", "Layout defaults", "New videos start with these; each video can override them in its Layout panel.", [
        Field("reframe.screen_layout", "Facecam + gameplay", "choice", "",
              options=_opts(("split", "Stacked"), ("pip", "Corner cam"), ("track", "Ignore cam"))),
        Field("reframe.smart_layout", "Smart layout", "bool", "Per-shot framing: gameplay-only shots are shown whole."),
        Field("reframe.background", "Background", "choice", "",
              options=_opts(("blur", "Blur"), ("tweet", "Tweet card"))),
    ]),
    Group("output", "Export & audio", "Full-quality files for upload.", [
        Field("export.crf", "Quality (CRF)", "int", "Lower = better quality, bigger file. 18 is visually lossless.",
              14, 30, 1, applies="export"),
        Field("export.preset", "Encoder speed", "choice", "Slower = smaller file at the same quality.",
              options=_opts(("veryfast", "Very fast"), ("faster", "Faster"), ("fast", "Fast"), ("medium", "Medium"),
                            ("slow", "Slow")), applies="export"),
        Field("export.sharpen", "Sharpening", "float", "", 0, 1.5, 0.05, applies="export"),
        Field("export.max_fps", "Frame rate cap", "choice", "",
              options=_opts(("0", "Same as source"), ("30", "30 fps"), ("60", "60 fps")), applies="export"),
        Field("audio.loudness_lufs", "Loudness", "float", "−14 LUFS is what Shorts, TikTok and Reels play at.",
              -24, -8, 0.5, "LUFS"),
    ]),
    Group("download", "Download", "How the source video is fetched. Applies to new videos.", [
        Field("download.max_height", "Max source quality", "choice", "",
              options=_opts(("720", "720p"), ("1080", "1080p"), ("1440", "1440p"), ("2160", "4K")), applies="new"),
        Field("download.mode", "Long videos", "choice", "Clips only: just the audio of the whole video, then only "
              "the picked clips' video. Needed for streams that would not fit on disk.",
              options=_opts(("auto", "Auto"), ("full", "Whole video"), ("clips", "Clips only")), applies="new"),
        Field("download.live_last_min", "Live streams: last", "float", "Only the last N minutes of a live stream "
              "(0 = from the start).", 0, 720, 5, "min", applies="new"),
    ]),
]

FIELDS: dict[str, Field] = {f.key: f for g in GROUPS for f in g.fields}


class SettingsError(ValueError):
    pass


def _get(data: dict, key: str) -> Any:
    sec, name = key.split(".", 1)
    return (data.get(sec) or {}).get(name)


def _value(cfg: Config, key: str) -> Any:
    sec, name = key.split(".", 1)
    v = getattr(getattr(cfg, sec), name)
    return str(int(v)) if FIELDS[key].type == "choice" and isinstance(v, (int, float)) else v


def view(cfg_path: Path | None = None, local: Path | None = None) -> dict:
    from . import config
    local = local or config.LOCAL
    cur = load_config(cfg_path, local=local)
    base = load_config(cfg_path, local=None)
    over = read_local(local)
    return {"groups": [{
        "id": g.id, "label": g.label, "help": g.help,
        "fields": [{**asdict(f), "value": _value(cur, f.key), "default": _value(base, f.key),
                    "custom": _get(over, f.key) is not None} for f in g.fields],
    } for g in GROUPS]}


def _coerce(f: Field, v: Any) -> Any:
    if f.type == "bool":
        if not isinstance(v, bool):
            raise SettingsError(f"{f.label}: expected on/off")
        return v
    if f.type in ("int", "float"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SettingsError(f"{f.label}: expected a number")
        if (f.min is not None and v < f.min) or (f.max is not None and v > f.max):
            raise SettingsError(f"{f.label}: must be between {f.min:g} and {f.max:g}")
        return int(round(v)) if f.type == "int" else round(float(v), 4)
    if f.type == "color":
        s = str(v).strip()
        if not (len(s) == 7 and s.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in s[1:])):
            raise SettingsError(f"{f.label}: expected a colour like #FFE600")
        return s.upper()
    if f.type == "choice":
        allowed = {o["value"] for o in f.options}
        if str(v) not in allowed:
            raise SettingsError(f"{f.label}: choose one of {', '.join(sorted(allowed))}")
        return int(v) if str(v).isdigit() else str(v)
    raise SettingsError(f"{f.label}: unsupported")


def update(changes: dict[str, Any], cfg_path: Path | None = None, local: Path | None = None) -> dict:
    """{"captions.size": 96, "hook.enabled": false, "select.max_clips": null (= back to shorts.yaml)}"""
    from .config import merge
    import yaml
    from common.paths import CONFIG_DIR
    from . import config

    local = local or config.LOCAL
    data = read_local(local)
    for key, v in changes.items():
        f = FIELDS.get(key)
        if not f:
            raise SettingsError(f"Unknown setting: {key}")
        sec, name = key.split(".", 1)
        section = dict(data.get(sec) or {})
        if v is None:
            section.pop(name, None)
        else:
            section[name] = _coerce(f, v)
        data[sec] = section
    # cross-field checks + full validation before anything is written
    base_path = cfg_path or CONFIG_DIR / "shorts.yaml"
    base = (yaml.safe_load(base_path.read_text()) if base_path.exists() else {}) or {}
    try:
        cfg = Config.model_validate(merge(base, data))
    except ValidationError as e:
        raise SettingsError(e.errors()[0].get("msg", str(e))) from None
    if cfg.select.min_clip_s >= cfg.select.max_clip_s:
        raise SettingsError("Shortest clip must be shorter than the longest clip")
    if cfg.pacing.keep_gap_s >= cfg.pacing.min_gap_s:
        raise SettingsError("Pauses must be cut down to less than the length that triggers a cut")
    write_local(data, local)
    return view(cfg_path, local)
