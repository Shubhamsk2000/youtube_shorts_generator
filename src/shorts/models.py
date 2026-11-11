"""Data contracts passed between pipeline stages (each is saved as JSON)."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class VideoMeta(BaseModel):
    id: str
    title: str
    source_url: str | None = None
    duration: float
    width: int
    height: int
    fps: float
    vcodec: str | None = None
    file: str  # filename inside the job dir


class Word(BaseModel):
    text: str
    start: float
    end: float
    prob: float


class Sentence(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[Word]


class Transcript(BaseModel):
    language: str
    model: str
    duration: float
    sentences: list[Sentence]

    def all_words(self) -> list[Word]:
        return [w for s in self.sentences for w in s.words]


class ClipScores(BaseModel):
    hook: float        # do the first ~3s make you stay?
    standalone: float  # understandable without the rest of the video
    payoff: float      # does it land / resolve?
    energy: float      # emotion, stakes, surprise

    def overall(self) -> float:
        return round(0.3 * self.hook + 0.25 * self.standalone + 0.25 * self.payoff + 0.2 * self.energy, 1)


class Clip(BaseModel):
    id: str             # stable hash of the time range: same range -> same id across reruns
    rank: int
    start: float        # seconds in the source (padded, snapped to sentence edges)
    end: float
    start_sentence: int
    end_sentence: int
    title: str
    hook: str
    reason: str
    scores: ClipScores
    score: float
    emphasis_words: list[str] = []
    text: str
    hook_text: str = ""          # on-screen hook overlay (first seconds)
    description: str = ""        # upload description
    hashtags: list[str] = []
    heat: float | None = None    # YouTube "Most replayed" level of this part, 0-1 (None = no data)

    @property
    def duration(self) -> float:
        return self.end - self.start


class Selection(BaseModel):
    source: str              # "gemini" or "heuristic"
    model: str | None = None
    note: str | None = None  # why the heuristic was used, etc.
    calls: int = 0
    candidates: int = 0      # picks before validation/dedupe
    rejected: dict[str, int] = {}
    clips: list[Clip]


class VideoInfo(BaseModel):
    """Extra YouTube metadata that helps pick moments (all optional)."""
    description: str = ""
    channel: str = ""
    tags: list[str] = []
    chapters: list[dict] = []    # {start_time, end_time, title}
    heatmap: list[dict] = []     # "Most replayed": {start_time, end_time, value 0-1}


class FaceSample(BaseModel):
    f: int                         # frame index within the clip
    faces: list[list[float]]       # [cx, cy, w, h, score], fractions of the frame


class Shot(BaseModel):
    start: int                     # first frame (inclusive)
    end: int                       # last frame (exclusive)
    mode: str                      # "track" | "fit" | "center" | "split"
    x: list[float] | None = None   # crop centre per frame (fraction of width); None for fit/split
    why: str = ""
    cams: list[list[float]] = []   # split: facecam boxes [x0, y0, x1, y1] (fractions), shown on top
    face_y: list[float] = []       # split: face centre y inside each cam (fraction of the cam box)
    face_x: list[float] = []       # split: face centre x inside each cam


class ReframePlan(BaseModel):
    key: str                       # cache key (clip range + settings)
    fps: float
    n_frames: int
    src_w: int
    src_h: int
    shots: list[Shot]
    samples: list[FaceSample] = []
    overlays: list[list[float]] = []  # faces on on-screen graphics, ignored (cx, cy, w, h)

    def shot_at(self, frame: int) -> Shot:
        for s in self.shots:
            if s.start <= frame < s.end:
                return s
        return self.shots[-1]


def save_json(model: BaseModel, path: Path) -> None:
    """Atomic write so an interrupted run never leaves a half-written stage file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=1))
    tmp.replace(path)


def load_json(cls: type[T], path: Path) -> T:
    return cls.model_validate_json(path.read_text())
