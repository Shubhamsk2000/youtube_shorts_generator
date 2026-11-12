"""Data passed between the anime pipeline stages (each saved as JSON in the job folder)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from common.jsonio import load_json, save_json  # noqa: F401  (re-exported)

Transition = Literal["cut", "flash", "zoom", "whip", "fade", "glitch"]
Effect = Literal["zoom_punch", "shake", "slowmo", "freeze_end", "flash_in"]
Style = Literal["clip", "montage", "song"]


class EpisodeMeta(BaseModel):
    id: str
    title: str
    source: str                  # absolute path of the episode (it is not copied)
    duration: float
    width: int
    height: int
    fps: str                     # exact rate as ffmpeg writes it, e.g. "24000/1001"
    audio_index: int = 0         # which audio track (original Japanese voices if found)
    audio_lang: str | None = None


class Word(BaseModel):
    text: str
    start: float
    end: float


class Cue(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = []       # only for transcribed audio: enables word-by-word captions


class Subs(BaseModel):
    source: str                  # "track 2 (eng, ass)", "file x.srt", "whisper small", "none"
    cues: list[Cue] = []
    song_ranges: list[tuple[float, float]] = []  # OP/ED lyrics found in the subtitle styles


class Signals(BaseModel):
    """Per-bin intensity of the episode, used to spot action and to place cuts and punches."""
    bin_s: float
    loudness: list[float]        # dB per bin
    motion: list[float]          # 0-1 pixel change per bin (cuts excluded)
    cuts: list[float]            # shot-change times (s)
    excluded: list[tuple[float, float, str]] = []  # opening / ending / preview: never picked


class Segment(BaseModel):
    start: float
    end: float
    transition_in: Transition = "cut"   # how this segment starts (ignored for the first one)
    effects: list[Effect] = []
    punch_at: float | None = None       # source time of the zoom punch
    intensity: int | None = None        # song edits: 1 (calm) - 5 (biggest hit), from Gemini
    out_start: float | None = None      # song edits: where this shot starts in the finished short (on a beat)

    @property
    def duration(self) -> float:
        return self.end - self.start


class SongRef(BaseModel):
    id: str                             # file in data/anime/music/
    title: str
    start: float                        # the part of the song used
    end: float
    bpm: float
    mood: str
    auto: bool = True                   # chosen by the app (False = you picked it)

    @property
    def duration(self) -> float:
        return self.end - self.start


class Pick(BaseModel):
    id: str
    rank: int
    style: Style
    score: float
    kind: str = ""                      # fight / emotional / comedy / reveal / hype / dialogue
    title: str                          # upload title
    tweet: str                          # text on the tweet card (the hook)
    description: str = ""
    hashtags: list[str] = []
    why: str = ""
    segments: list[Segment]
    watched: bool = False               # Gemini watched it (vs text + signals only)
    song: SongRef | None = None         # song edits: the soundtrack, cut to its beats

    @property
    def duration(self) -> float:
        if self.song:
            return self.song.duration
        return sum(s.duration for s in self.segments)


class Selection(BaseModel):
    source: Literal["gemini", "heuristic"]
    model: str | None = None
    style: Style
    picks: list[Pick]
    note: str | None = None
