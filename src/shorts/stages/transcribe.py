"""Stage 2: local Whisper (faster-whisper, CPU int8) -> transcript.json with word timing.

The model runs in a child process so its ~1 GB is fully returned to the OS afterwards.
"""
from __future__ import annotations

import math
import os
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
PARTS_DIR = "transcript_parts"  # long audio: one file per finished chunk (resume after an interruption)
OVERLAP_S = 5.0                 # each chunk hears this much of its neighbours, so no word is cut in half


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
        if not (job_dir / meta.file).exists():
            raise StageProcessError("The downloaded source is gone (it is deleted once the clips are cut) and "
                                    "audio.wav is missing: delete this job and make it again")
        report(None, "Extracting audio…")
        ffmpeg.extract_audio(job_dir / meta.file, audio)
    if force:
        import shutil
        shutil.rmtree(job_dir / PARTS_DIR, ignore_errors=True)

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
                             cfg.model_dump(mode="json"), _hints(job_dir, meta, cfg), on_message=on_message)
    except StageProcessError as e:
        raise StageProcessError(errors[0] if errors else str(e)) from e
    elapsed = time.monotonic() - t0

    import shutil
    shutil.rmtree(job_dir / PARTS_DIR, ignore_errors=True)  # merged into transcript.json
    t = load_json(Transcript, out)
    summary = (f"{_n_words(t)} words · {elapsed / 60:.1f} min "
               f"({meta.duration / max(elapsed, 1e-6):.1f}× realtime) · peak RAM {stats.peak_rss_mb:.0f} MB")
    return t, summary


def _n_words(t: Transcript) -> int:
    return sum(len(s.words) for s in t.sentences)


def _hints(job_dir: Path, meta: VideoMeta, cfg: Config) -> str | None:
    """Title + channel as Whisper hotwords: names, brands and made-up words get spelled the way the video does."""
    if not cfg.transcribe.title_hints:
        return None
    from .download import INFO
    from ..models import VideoInfo
    channel = ""
    if (job_dir / INFO).exists():
        try:
            channel = load_json(VideoInfo, job_dir / INFO).channel
        except Exception:  # noqa: BLE001
            pass
    # punctuated on purpose: with large-v3-turbo a punctuated hint also keeps the transcript punctuated
    text = " ".join(f"{x.strip().rstrip('.')}." for x in (meta.title, channel) if x and x.strip())
    return text[:200] or None


def _transcribe_worker(audio: str, out: str, duration: float, cfg_dict: dict, hints: str | None, q) -> None:
    try:
        _transcribe(audio, out, duration, Config.model_validate(cfg_dict), q, hints)
    except Exception as e:  # report cleanly to parent instead of dumping a traceback
        q.put(("error", f"{type(e).__name__}: {e}"))
        raise SystemExit(1)


def _transcribe(audio: str, out: str, duration: float, cfg: Config, q, hints: str | None = None) -> None:
    from faster_whisper import WhisperModel  # heavy import only in the child

    tc = cfg.transcribe
    q.put(("progress", None, f"Loading Whisper {tc.model}…"))
    model = WhisperModel(tc.model, device="cpu", compute_type=tc.compute_type,
                         cpu_threads=tc.threads or (os.cpu_count() or 4))

    chunk_s = tc.chunk_min * 60
    if duration > chunk_s * 1.5:
        words, language = _transcribe_chunked(model, audio, Path(out).parent / PARTS_DIR, duration, tc, hints, q)
    else:
        words, language = _transcribe_span(model, audio, 0.0, duration, tc, hints, q, duration, 0.0)

    words = merge_fragments(words)
    sentences = group_sentences(words,cfg.sentences.max_words, cfg.sentences.max_gap_s)
    save_json(
        Transcript(language=language, model=tc.model, duration=duration, sentences=sentences),
        Path(out),
    )


def _decode_opts(tc, hints: str | None) -> dict:
    return dict(
        language="en",
        word_timestamps=True,
        beam_size=tc.beam_size,
        vad_filter=tc.vad,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,  # avoids runaway repetition loops on music/noise
        hotwords=hints,
    )


def _transcribe_span(model, audio, offset: float, length: float, tc, hints, q, total: float, done_before: float,
                     t0: float | None = None) -> tuple[list[Word], str]:
    """Transcribe `audio` (a path, or samples starting at source time `offset`); word times on the source
    timeline. Progress is reported against the whole video (done_before + this span)."""
    segments, info = model.transcribe(audio, **_decode_opts(tc, hints))
    words: list[Word] = []
    t0 = t0 if t0 is not None else time.monotonic()
    for seg in segments:  # generator: decoding happens here, streaming
        for w in seg.words or []:
            text = w.word.strip()
            if not text or w.probability < tc.min_word_prob:
                continue
            words.append(Word(text=text, start=round(w.start + offset, 3), end=round(w.end + offset, 3),
                              prob=round(w.probability, 3)))
        at = seg.end + offset
        frac = min(at / max(total, 1), 0.999)
        el = time.monotonic() - t0
        run_frac = (at - done_before) / max(total - done_before, 1)  # ETA from this run's own speed
        eta = el / run_frac - el if run_frac > 0.02 else None
        eta_s = f" · ~{eta / 60:.0f} min left" if eta else ""
        q.put(("progress", frac, f"[{_fmt(at)}] {seg.text.strip()[:60]}{eta_s}"))
    return words, info.language


def chunk_spans(duration: float, chunk_s: float) -> list[tuple[float, float]]:
    n = max(1, math.ceil(duration / chunk_s - 1e-9))
    return [(k * chunk_s, min(duration, (k + 1) * chunk_s)) for k in range(n)]


def stitch(parts: list[list[Word]], tol: float = 0.1) -> list[Word]:
    """Chunk outputs in order -> one word list. Neighbouring chunks overlap, so a word near a seam can come
    out of both: a word that starts before the previous kept word has ended is the same word again."""
    out: list[Word] = []
    for ws in parts:
        for w in ws:
            if out and w.start < out[-1].end - tol:
                continue
            out.append(w)
    return out


def _parts_key(tc, hints: str | None, chunk_s: float) -> str:
    import hashlib
    import json
    raw = json.dumps([tc.model, tc.compute_type, tc.beam_size, tc.vad, tc.min_word_prob, hints, chunk_s, OVERLAP_S])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _transcribe_chunked(model, audio: str, parts_dir: Path, duration: float, tc, hints, q) -> tuple[list[Word], str]:
    import json

    from .sources import read_wav

    chunk_s = tc.chunk_min * 60
    key = _parts_key(tc, hints, chunk_s)
    parts_dir.mkdir(exist_ok=True)
    spans = chunk_spans(duration, chunk_s)
    parts: list[list[Word]] = []
    language = "en"
    t0 = time.monotonic()
    first_new = None
    for k, (s, e) in enumerate(spans):
        p = parts_dir / f"{k:04d}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if data.get("key") == key:
                    parts.append([Word(**w) for w in data["words"]])
                    language = data.get("language", language)
                    q.put(("progress", min(e / max(duration, 1), 0.999), f"[{_fmt(e)}] chunk {k + 1}/{len(spans)} · cached"))
                    continue
            except (ValueError, KeyError, TypeError):
                pass
        if first_new is None:
            first_new, t0 = s, time.monotonic()
        a = max(0.0, s - OVERLAP_S)
        x = read_wav(Path(audio), a, min(duration, e + OVERLAP_S))
        ws, language = _transcribe_span(model, x, a, e - a, tc, hints, q, duration, first_new, t0) if len(x) else ([], language)
        last = k == len(spans) - 1
        ws = [w for w in ws if w.start >= s and (last or w.start < e)]  # each word from the chunk it starts in
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "start": s, "end": e, "language": language,
                                   "words": [w.model_dump() for w in ws]}))
        tmp.replace(p)
        parts.append(ws)
    return stitch(parts), language


def _fmt(s: float) -> str:
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"
