"""Stage 2: local Whisper (faster-whisper, CPU int8) -> transcript.json with word timing.

The model runs in a child process so its ~1 GB is fully returned to the OS afterwards.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..config import Config
from ..models import Transcript, VideoMeta, Word, load_json, save_json
from common.progress import Reporter
from ..text import group_sentences, merge_fragments
from common import ffmpeg
from common.proc import StageProcessError, run_isolated

OUT = "transcript.json"
AUDIO = "audio.wav"


def run(job_dir: Path, meta: VideoMeta, cfg: Config, *, force: bool = False,
        report: Reporter | None = None) -> tuple[Transcript, str]:
    """Returns (transcript, human summary)."""
    report = report or (lambda fraction=None, message=None: None)
    out = job_dir / OUT
    if out.exists() and not force:
        t = load_json(Transcript, out)
        return t, f"{_n_words(t)} words · cached"

    audio = job_dir / AUDIO
    if not audio.exists() or force:
        report(None, "Extracting audio…")
        ffmpeg.extract_audio(job_dir / meta.file, audio)

    errors: list[str] = []

    def on_message(msg: tuple) -> None:
        kind, *rest = msg
        if kind == "progress":
            report(*rest)
        elif kind == "error":
            errors.append(rest[0])

    t0 = time.monotonic()
    try:
        stats = run_isolated(_transcribe_worker, str(audio), str(out), meta.duration,
                             cfg.model_dump(mode="json"), on_message=on_message)
    except StageProcessError as e:
        raise StageProcessError(errors[0] if errors else str(e)) from e
    elapsed = time.monotonic() - t0

    t = load_json(Transcript, out)
    summary = (f"{_n_words(t)} words · {elapsed / 60:.1f} min "
               f"({meta.duration / max(elapsed, 1e-6):.1f}× realtime) · peak RAM {stats.peak_rss_mb:.0f} MB")
    return t, summary


def _n_words(t: Transcript) -> int:
    return sum(len(s.words) for s in t.sentences)


def _transcribe_worker(audio: str, out: str, duration: float, cfg_dict: dict, q) -> None:
    try:
        _transcribe(audio, out, duration, Config.model_validate(cfg_dict), q)
    except Exception as e:  # report cleanly to parent instead of dumping a traceback
        q.put(("error", f"{type(e).__name__}: {e}"))
        raise SystemExit(1)


def _transcribe(audio: str, out: str, duration: float, cfg: Config, q) -> None:
    from faster_whisper import WhisperModel  # heavy import only in the child

    tc = cfg.transcribe
    q.put(("progress", None, f"Loading Whisper {tc.model}…"))
    model = WhisperModel(tc.model, device="cpu", compute_type=tc.compute_type, cpu_threads=tc.threads)

    segments, info = model.transcribe(
        audio,
        language="en",
        word_timestamps=True,
        beam_size=tc.beam_size,
        vad_filter=tc.vad,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,  # avoids runaway repetition loops on music/noise
    )

    words: list[Word] = []
    t0 = time.monotonic()
    for seg in segments:  # generator: decoding happens here, streaming
        for w in seg.words or []:
            text = w.word.strip()
            if not text or w.probability < tc.min_word_prob:
                continue
            words.append(Word(text=text, start=round(w.start, 3), end=round(w.end, 3),
                              prob=round(w.probability, 3)))
        frac = min(seg.end / max(duration, 1), 0.999)
        el = time.monotonic() - t0
        eta = el / frac - el if frac > 0.02 else None
        eta_s = f" · ~{eta / 60:.0f} min left" if eta else ""
        q.put(("progress", frac, f"[{_fmt(seg.end)}] {seg.text.strip()[:60]}{eta_s}"))

    words = merge_fragments(words)
    sentences = group_sentences(words,cfg.sentences.max_words, cfg.sentences.max_gap_s)
    save_json(
        Transcript(language=info.language, model=tc.model, duration=duration, sentences=sentences),
        Path(out),
    )


def _fmt(s: float) -> str:
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"
