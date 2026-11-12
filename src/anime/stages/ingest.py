"""Stage 1: register an episode and get its dialogue as timed cues.

The episode file is never copied (episodes are big): the job stores its absolute path.
Audio: the first track in subs.audio_languages (English dub if there is one, else Japanese).
Dialogue:
  English audio (dub)  Whisper transcribes the audio itself, with word timings (subs.dub_captions):
                       dub scripts differ from the subtitle track, and captions must match the voices
  other audio          1. a text subtitle track inside the file (srt / ass / ssa / webvtt)
                       2. a subtitle file next to the episode: <name>.en.srt, <name>.ass, ...
                       3. Whisper translating the audio to English (subs.whisper_fallback)
Image subtitles (PGS/VobSub, common on Blu-ray rips) can't be read as text, so they fall through to 3.
ASS signs, song lyrics and credits are dropped; song lines mark where the OP/ED is.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from common import ffmpeg
from common.proc import StageProcessError, run_isolated
from common.progress import Reporter

from ..config import Config, SubsCfg
from ..models import Cue, EpisodeMeta, Subs, Word, load_json, save_json

META = "meta.json"
SUBS = "subs.json"
AUDIO = "audio.wav"
ENGLISH = {"eng", "en", "english"}
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
SIDE_EXTS = (".ass", ".ssa", ".srt", ".vtt")


class IngestError(RuntimeError):
    pass


def job_id(src: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-")[:48] or "episode"
    return f"{slug}-{hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:4]}"


def run(source: str, cfg: Config, *, report: Reporter | None = None) -> tuple[Path, EpisodeMeta, str]:
    report = report or (lambda fraction=None, message=None: None)
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise IngestError(f"No such file: {src}")
    job_dir = cfg.workspace() / job_id(src)
    job_dir.mkdir(parents=True, exist_ok=True)

    meta_p = job_dir / META
    if meta_p.exists():
        meta = load_json(EpisodeMeta, meta_p)
    else:
        report(None, "Reading the episode…")
        meta = _probe(src, job_dir.name, cfg.subs)
        save_json(meta, meta_p)

    subs_p = job_dir / SUBS
    if subs_p.exists():
        subs = load_json(Subs, subs_p)
        return job_dir, meta, f"{_describe(meta)} · {len(subs.cues)} subtitle lines · cached"
    subs = _subs(src, job_dir, meta, cfg, report)
    save_json(subs, subs_p)
    return job_dir, meta, f"{_describe(meta)} · {len(subs.cues)} subtitle lines from {subs.source}"


def _describe(m: EpisodeMeta) -> str:
    return f"{m.duration / 60:.1f} min {m.width}×{m.height} · audio: {m.audio_lang or 'track ' + str(m.audio_index)}"


def _probe(src: Path, jid: str, sc: SubsCfg) -> EpisodeMeta:
    info = ffmpeg.probe(src)
    streams = info["streams"]
    v = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")),
             None)
    if v is None:
        raise IngestError("The file has no video stream")
    audios = [s for s in streams if s["codec_type"] == "audio"]
    if not audios:
        raise IngestError("The file has no audio stream")
    a_idx = _prefer([_lang(s) for s in audios], sc.audio_languages)
    fps = v.get("avg_frame_rate") or v.get("r_frame_rate") or "24000/1001"
    if fps in ("0/0", "0/1"):
        fps = "24000/1001"
    title = (info["format"].get("tags") or {}).get("title") or src.stem
    return EpisodeMeta(
        id=jid, title=title, source=str(src), duration=float(info["format"]["duration"]),
        width=int(v["width"]), height=int(v["height"]), fps=fps,
        audio_index=a_idx, audio_lang=_lang(audios[a_idx]),
    )


def _lang(stream: dict) -> str | None:
    return (stream.get("tags") or {}).get("language")


def _prefer(langs: list[str | None], wanted: list[str]) -> int:
    for w in wanted:
        for i, lang in enumerate(langs):
            if lang and lang.lower() == w.lower():
                return i
    return 0


# ---------------------------------------------------------------- subtitles

def is_english_audio(meta: EpisodeMeta) -> bool:
    return (meta.audio_lang or "").lower() in ENGLISH


def _subs(src: Path, job_dir: Path, meta: EpisodeMeta, cfg: Config, report: Reporter) -> Subs:
    sc = cfg.subs
    if is_english_audio(meta) and sc.dub_captions == "transcribe":
        return _whisper(src, job_dir, meta, sc, report, english=True)
    streams = [s for s in ffmpeg.probe(src)["streams"] if s["codec_type"] == "subtitle"]
    text = [s for s in streams if s.get("codec_name") in TEXT_SUB_CODECS]
    if text:
        i = _prefer([_lang(s) for s in text], sc.languages)
        # "signs & songs" tracks only carry on-screen text: prefer a full dialogue track
        full = [j for j, s in enumerate(text) if not re.search(r"sign|song", (s.get("tags") or {}).get("title", ""), re.I)]
        if i not in full and full:
            i = next((j for j in full if _lang(text[j]) == _lang(text[i])), full[0])
        s = text[i]
        report(None, f"Extracting subtitle track {s['index']} ({_lang(s) or '?'}, {s['codec_name']})…")
        ext = "ass" if s["codec_name"] in ("ass", "ssa") else "srt"
        out = job_dir / f"subs.{ext}"
        ffmpeg.run(["-i", str(src), "-map", f"0:{s['index']}", "-c:s", ext, str(out)])
        return _parse(out, sc, f"track {s['index']} ({_lang(s) or '?'}, {s['codec_name']})")

    side = _side_file(src, sc)
    if side:
        return _parse(side, sc, f"file {side.name}")

    if streams:
        report(None, "Only image subtitles (PGS/VobSub) in the file: they can't be read as text")
    if not sc.whisper_fallback:
        return Subs(source="none")
    return _whisper(src, job_dir, meta, sc, report, english=is_english_audio(meta))


def _side_file(src: Path, sc: SubsCfg) -> Path | None:
    cands = [p for p in src.parent.iterdir()
             if p.suffix.lower() in SIDE_EXTS and p.name.startswith(src.stem)]
    if not cands:
        return None

    def rank(p: Path) -> tuple[int, int]:
        tags = p.name[len(src.stem):].lower().split(".")
        lang = next((i for i, w in enumerate(sc.languages) if w.lower() in tags), len(sc.languages))
        return lang, SIDE_EXTS.index(p.suffix.lower())

    return sorted(cands, key=rank)[0]


def _parse(path: Path, sc: SubsCfg, source: str) -> Subs:
    import pysubs2

    ssa = pysubs2.load(str(path))
    cues: list[Cue] = []
    songs: list[tuple[float, float]] = []
    for ev in ssa:
        if ev.is_comment:
            continue
        style = (ev.style or "").lower()
        a, b = ev.start / 1000, ev.end / 1000
        if any(w in style for w in sc.drop_styles):
            if any(w in style for w in ("song", "op", "ed", "kara", "lyric")):
                songs.append((a, b))
            continue
        if re.search(r"\\pos\(|\\move\(|\\p[1-9]", ev.text):  # positioned text = a sign, not dialogue
            continue
        text = clean_text(ev.text)
        if text and b > a:
            cues.append(Cue(start=round(a, 3), end=round(b, 3), text=text))
    cues = _merge_dupes(sorted(cues, key=lambda c: c.start))
    return Subs(source=source, cues=cues, song_ranges=_ranges(songs))


def clean_text(raw: str) -> str:
    t = re.sub(r"\{[^}]*\}", "", raw)                  # ASS override tags
    t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    t = re.sub(r"</?[a-zA-Z][^>]*>", "", t)            # srt <i>, <font>
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _merge_dupes(cues: list[Cue]) -> list[Cue]:
    """Some releases repeat a line as several back-to-back events (typesetting): keep one."""
    out: list[Cue] = []
    for c in cues:
        if out and c.text == out[-1].text and c.start - out[-1].end < 0.1:
            out[-1].end = max(out[-1].end, c.end)
        else:
            out.append(c)
    return out


def _ranges(spans: list[tuple[float, float]], gap: float = 8.0) -> list[tuple[float, float]]:
    """Song lines -> continuous OP/ED ranges (at least 30 s long)."""
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out if b - a >= 30]


# ---------------------------------------------------------------- whisper fallback

def _whisper(src: Path, job_dir: Path, meta: EpisodeMeta, sc: SubsCfg, report: Reporter, *, english: bool) -> Subs:
    audio = job_dir / AUDIO
    if not audio.exists():
        report(None, "Extracting audio for Whisper…")
        ffmpeg.run(["-i", str(src), "-map", f"0:a:{meta.audio_index}", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(audio)])
    out = job_dir / "whisper.json"
    errors: list[str] = []

    def on_message(msg: tuple) -> None:
        kind, *rest = msg
        if kind == "progress":
            report(*rest)
        elif kind == "error":
            errors.append(rest[0])

    try:
        run_isolated(_whisper_worker, str(audio), str(out), meta.duration, sc.model_dump(), english,
                     on_message=on_message)
    except StageProcessError as e:
        raise StageProcessError(errors[0] if errors else str(e)) from e
    subs = load_json(Subs, out)
    out.unlink()
    return subs


def _whisper_worker(audio: str, out: str, duration: float, sc: dict, english: bool, q) -> None:
    """English audio: transcribe with word timings (small.en). Other audio: translate to English."""
    try:
        from faster_whisper import WhisperModel  # heavy import only in the child

        name = sc["dub_model"] if english else sc["whisper_model"]
        q.put(("progress", None, f"Loading Whisper {name}…"))
        model = WhisperModel(name, device="cpu", compute_type=sc["compute_type"], cpu_threads=sc["threads"])
        kw = dict(language="en", word_timestamps=True) if english else dict(task="translate")
        segments, _ = model.transcribe(audio, vad_filter=True, beam_size=5, condition_on_previous_text=False,
                                       vad_parameters={"min_silence_duration_ms": 400}, **kw)
        cues, t0 = [], time.monotonic()
        for seg in segments:
            if seg.text.strip():
                words = [Word(text=w.word.strip(), start=round(w.start, 3), end=round(w.end, 3))
                         for w in (seg.words or []) if w.word.strip()]
                if seg.end - seg.start > 12 and len(seg.text.split()) < 0.8 * (seg.end - seg.start):
                    continue  # a long, nearly empty "line" over music: Whisper hallucinating on a song
                cues.extend(split_cue(Cue(start=round(seg.start, 3), end=round(seg.end, 3),
                                          text=seg.text.strip(), words=words)))
            frac = min(seg.end / max(duration, 1), 0.999)
            el = time.monotonic() - t0
            eta = f" · ~{(el / frac - el) / 60:.0f} min left" if frac > 0.03 else ""
            q.put(("progress", frac, f"Whisper: {seg.text.strip()[:60]}{eta}"))
        what = "transcribed English audio" if english else "translated"
        save_json(Subs(source=f"whisper {name} ({what})", cues=cues), Path(out))
    except Exception as e:  # noqa: BLE001  report cleanly to the parent
        q.put(("error", f"{type(e).__name__}: {e}"))
        raise SystemExit(1)


def split_cue(c: Cue, max_words: int = 14, max_gap: float = 0.8) -> list[Cue]:
    """Whisper segments can run on: split long ones at sentence ends or pauses (keeps cuts on line edges)."""
    if len(c.words) <= max_words:
        return [c]
    out: list[Cue] = []
    cur: list[Word] = []
    for i, w in enumerate(c.words):
        cur.append(w)
        nxt = c.words[i + 1] if i + 1 < len(c.words) else None
        end_sentence = w.text[-1:] in ".?!" and len(cur) >= 4
        pause = nxt is not None and nxt.start - w.end > max_gap
        if nxt is None or end_sentence or pause or len(cur) >= max_words:
            out.append(Cue(start=cur[0].start, end=cur[-1].end, text=" ".join(x.text for x in cur), words=cur))
            cur = []
    return out
