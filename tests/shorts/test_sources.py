"""Long videos: clip-by-clip video files, the audio-first download plan, alignment, chunked transcription."""
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from shorts.config import Config, DownloadCfg
from shorts.models import Clip, ClipScores, Selection, VideoMeta, Word, save_json
from shorts.stages import download as dl
from shorts.stages import sources as sr
from shorts.stages import transcribe as tr

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def speechlike(seconds: float, seed: int = 0, sr_: int = sr.SR) -> np.ndarray:
    """Noise bursts with pauses: distinctive envelope, like speech."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr_)
    x = rng.standard_normal(n).astype(np.float32) * 0.2
    env = np.repeat(rng.uniform(0.05, 1.0, n // 1600 + 1), 1600)[:n]
    env[rng.random(len(env)) < 0.0] = 0
    return x * env.astype(np.float32)


def clip(start: float, end: float, cid: str = "c1") -> Clip:
    return Clip(id=cid, rank=1, start=start, end=end, start_sentence=0, end_sentence=0, title="t", hook="h",
                reason="", scores=ClipScores(hook=5, standalone=5, payoff=5, energy=5), score=5, text="x")


def write_wav(path: Path, x: np.ndarray, sr_: int = sr.SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr_)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- alignment

def test_locate_finds_exact_offset():
    src = speechlike(300, seed=1)
    tpl = src[int(123.4567 * sr.SR): int(173.4567 * sr.SR)]
    got = sr.locate(tpl, src)
    assert got is not None
    pos, score = got
    assert abs(pos - 123.4567) < 1.5 / sr.SR
    assert score > 0.99


def test_locate_survives_gain_and_noise():
    src = speechlike(200, seed=2)
    tpl = src[int(40 * sr.SR): int(90 * sr.SR)].copy()
    noisy = 0.5 * src + np.random.default_rng(3).standard_normal(len(src)).astype(np.float32) * 0.01
    pos, score = sr.locate(tpl, noisy)
    assert abs(pos - 40) < 0.002 and score > 0.8


def test_locate_rejects_silence_and_unrelated_audio():
    src = speechlike(120, seed=4)
    assert sr.locate(np.zeros(sr.SR * 30, np.float32), src) is None
    assert sr.locate(speechlike(30, seed=99), src) is None
    assert sr.locate(src[:sr.SR], src) is None  # too short to trust


def test_calibration_interpolates():
    assert sr._calib_error([], 100) == 0
    cal = [[1000, 2.0], [3000, 6.0]]
    assert sr._calib_error(cal, 2000) == pytest.approx(4.0)
    assert sr._calib_error(cal, 10) == pytest.approx(2.0)      # nearest outside the range
    assert sr._calib_error(cal, 9000) == pytest.approx(6.0)


# ---------------------------------------------------------------- fragments

def test_frag_starts_spread_over_real_duration():
    """Post-live stream: 42697 fragments of nominally 2 s, but the stream is 85542 s long."""
    frags = [{"url": f"https://x/videoplayback?sq={i}"} for i in range(42697)]
    starts, step = dl.frag_starts({"fragments": frags, "target_duration": 2}, {"duration": 85542})
    assert step == pytest.approx(85542 / 42697)
    assert starts[-1] == pytest.approx(85542 - step)
    i0, i1 = dl.frag_slice(starts, 36000, 36060)
    assert starts[i0] <= 36000 < starts[i0 + 1]
    assert starts[i1] >= 36060 and starts[i1 - 1] < 36060


def test_frag_starts_uses_manifest_durations():
    frags = [{"url": "u", "duration": d} for d in (4.0, 4.0, 2.5, 4.0)]
    starts, _ = dl.frag_starts({"fragments": frags}, {"duration": 999})
    assert starts == [0, 4, 8, 10.5]
    assert dl.frag_slice(starts, 9, 11) == (2, 3)
    assert dl.frag_slice(starts, -5, 0.5) == (0, 1)
    assert dl.frag_slice(starts, 50, 60) == (3, 3)  # past the end: the last fragment


def test_sq_window_for_live_generator():
    def gen(ctx):
        for sq in range(100):
            yield {"url": f"https://x/videoplayback?itag=1&sq={sq}"}
    got = [int(f["url"].rsplit("=", 1)[1]) for f in dl._sq_window(gen, 40, 45)({})]
    assert got == list(range(40, 46))


def test_is_fragmented():
    assert dl.is_fragmented({"requested_formats": [{"fragments": []}, {"fragments": []}]})
    assert not dl.is_fragmented({"requested_formats": [{"url": "u"}, {"fragments": []}]})


# ---------------------------------------------------------------- download plan

def info(duration, video_tbr=9016, audio_tbr=144, size=None, live=None):
    i = {"duration": duration, "requested_formats": [
        {"format_id": "308", "vcodec": "vp9", "tbr": video_tbr, "filesize_approx": size},
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "tbr": audio_tbr}]}
    if live:
        i["live_status"] = live
    return i


def test_short_video_downloads_whole():
    mode, _ = dl.plan_mode(info(20 * 60), Config(), free_gb=16.8)
    assert mode == "full"


def test_24h_stream_goes_clip_by_clip():
    """The reported failure: ≈97.9 GB at 1440p with 16.8 GB free."""
    mode, why = dl.plan_mode(info(85542, size=96_405_834_000), Config(), free_gb=16.8)
    assert mode == "clips" and "audio" in why


def test_no_room_even_for_audio():
    with pytest.raises(dl.DownloadError, match="Not enough disk even for audio"):
        dl.plan_mode(info(85542, size=96_405_834_000), Config(), free_gb=4.0)


def test_explicit_full_mode_still_checks_disk():
    cfg = Config(download=DownloadCfg(mode="full"))
    with pytest.raises(dl.DownloadError, match="download.mode: auto"):
        dl.plan_mode(info(85542, size=96_405_834_000), cfg, free_gb=16.8)


def test_explicit_clips_mode_for_small_video():
    cfg = Config(download=DownloadCfg(mode="clips"))
    assert dl.plan_mode(info(600), cfg, free_gb=16.8)[0] == "clips"


def test_too_long_is_refused():
    with pytest.raises(dl.DownloadError, match="max_hours"):
        dl.plan_mode(info(40 * 3600), Config(), free_gb=500)


# ---------------------------------------------------------------- chunked transcription

def test_chunk_spans():
    assert tr.chunk_spans(100, 30) == [(0, 30), (30, 60), (60, 90), (90, 100)]
    assert tr.chunk_spans(60, 30) == [(0, 30), (30, 60)]


def test_stitch_drops_seam_duplicates():
    a = [Word(text="one", start=0.0, end=0.4, prob=1), Word(text="two", start=29.9, end=30.3, prob=1)]
    b = [Word(text="two", start=30.02, end=30.3, prob=1), Word(text="three", start=30.5, end=30.9, prob=1)]
    assert [w.text for w in tr.stitch([a, b])] == ["one", "two", "three"]


class FakeSeg:
    def __init__(self, words, end, text=""):
        self.words, self.end, self.text = words, end, text


class FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end, self.probability = word, start, end, 0.9


class FakeModel:
    """A 'Whisper' that hears one word every 1.5 s of the source (w0 at 0.2 s, w1 at 1.7 s, ...), given the
    sample offset of what it is fed: words cut by the edge of the audio are not heard."""
    calls = 0

    def __init__(self, sources_start):
        self.start = sources_start

    def transcribe(self, audio, **kw):
        FakeModel.calls += 1
        a = self.start.pop(0)
        dur = len(audio) / sr.SR
        segs = []
        k = int(np.ceil((a - 0.2) / 1.5)) if a > 0.2 else 0
        while True:
            t = 0.2 + 1.5 * k
            if t + 0.5 > a + dur:
                break
            if t >= a:
                segs.append(FakeSeg([FakeWord(f"w{k}", t - a, t - a + 0.5)], t - a + 0.5))
            k += 1
        return iter(segs), type("I", (), {"language": "en"})()


class Q:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


def test_chunked_transcription_resumes_and_has_every_word_once(tmp_path):
    from shorts.config import TranscribeCfg

    dur = 100.0
    write_wav(tmp_path / "audio.wav", speechlike(dur))
    tc = TranscribeCfg(chunk_min=0.5)  # 30 s chunks
    spans = tr.chunk_spans(dur, 30)
    starts = [max(0.0, s - tr.OVERLAP_S) for s, _ in spans]
    parts = tmp_path / "parts"
    words, _ = tr._transcribe_chunked(FakeModel(list(starts)), str(tmp_path / "audio.wav"), parts, dur, tc, None, Q())
    expect = [f"w{k}" for k in range(100) if 0.2 + 1.5 * k + 0.5 <= dur]
    assert [w.text for w in words] == expect
    assert all(abs(w.start - (0.2 + 1.5 * int(w.text[1:]))) < 1e-6 for w in words)

    # resume: chunk 3 lost (interrupted), the others come from disk
    (parts / "0002.json").unlink()
    FakeModel.calls = 0
    words2, _ = tr._transcribe_chunked(FakeModel([starts[2]]), str(tmp_path / "audio.wav"), parts, dur, tc, None, Q())
    assert FakeModel.calls == 1
    assert [w.text for w in words2] == expect

    # settings changed -> stale parts are redone
    FakeModel.calls = 0
    tr._transcribe_chunked(FakeModel(list(starts)), str(tmp_path / "audio.wav"), parts, dur,
                           TranscribeCfg(chunk_min=0.5, beam_size=1), None, Q())
    assert FakeModel.calls == len(spans)


# ---------------------------------------------------------------- real media: cut + fetch + resolve

def make_video(path: Path, seconds: float, seed: int = 0) -> np.ndarray:
    """A 30 fps test pattern with a frame counter and distinctive audio; returns that audio (16 kHz)."""
    audio = speechlike(seconds, seed)
    wav = path.with_suffix(".src.wav")
    write_wav(wav, audio)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=30:duration={seconds}", "-i", str(wav),
                    "-c:v", "libx264", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    wav.unlink()
    return audio


def frame_at(path: Path, t: float) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8)


@needs_ffmpeg
def test_cut_local_is_exact(tmp_path):
    src = tmp_path / "source.mp4"
    make_video(src, 30)
    e = sr.cut_local(src, 7.3, 15.1, tmp_path / "c.mkv")
    assert 5.0 <= e.offset <= 7.3 + 1e-3  # starts on the keyframe before 7.3 s (every 2 s)
    assert e.offset + e.duration >= 15.1 - 0.05
    assert e.duration < 15.1 - 5.0 + 0.5  # its real length, not "up to its last timestamp"
    for t in (7.3, 9.95, 14.9):  # the render's seek on the clip file shows the same frame as on the source
        assert np.array_equal(frame_at(tmp_path / "c.mkv", t - e.offset), frame_at(src, t))


def job(tmp_path: Path, duration: float, mode="full", url="https://www.youtube.com/watch?v=abc"):
    meta = VideoMeta(id="abc", title="t", source_url=url, duration=duration, width=320, height=180, fps=30,
                     file="source.mp4", mode=mode)
    save_json(meta, tmp_path / "meta.json")
    return meta


@needs_ffmpeg
def test_stage_cuts_then_deletes_full_download(tmp_path):
    src = tmp_path / "source.mp4"
    audio = make_video(src, 40)
    write_wav(tmp_path / "audio.wav", audio)
    meta = job(tmp_path, 40)
    clips = [clip(5.0, 12.0, "a"), clip(20.5, 33.0, "b")]
    save_json(Selection(source="gemini", clips=clips), tmp_path / "selection.json")
    msg = sr.run(tmp_path, meta, Config())
    assert "2 clip videos" in msg and "deleted" in msg
    assert not src.exists()
    meta2 = VideoMeta.model_validate_json((tmp_path / "meta.json").read_text())
    assert meta2.source_deleted
    for c in clips:
        f, lc = sr.resolve(tmp_path, meta2, c)
        assert f.parent.name == "sources" and lc.start >= 0 and lc.duration == pytest.approx(c.duration)
    # a new selection: stale clip files are removed
    save_json(Selection(source="gemini", clips=clips[:1]), tmp_path / "selection.json")
    sr.run(tmp_path, meta2, Config())
    assert sorted(p.name for p in (tmp_path / "sources").iterdir()) == ["a.mkv", "index.json"]


@needs_ffmpeg
def test_local_upload_is_never_deleted(tmp_path):
    src = tmp_path / "source.mp4"
    write_wav(tmp_path / "audio.wav", make_video(src, 20))
    meta = job(tmp_path, 20, url=None)
    save_json(Selection(source="gemini", clips=[clip(2, 9)]), tmp_path / "selection.json")
    sr.run(tmp_path, meta, Config())
    assert src.exists()


@needs_ffmpeg
def test_no_picks_keeps_the_download(tmp_path):
    src = tmp_path / "source.mp4"
    write_wav(tmp_path / "audio.wav", make_video(src, 10))
    meta = job(tmp_path, 10)
    save_json(Selection(source="heuristic", clips=[]), tmp_path / "selection.json")
    sr.run(tmp_path, meta, Config())
    assert src.exists()


@needs_ffmpeg
def test_keep_source_setting(tmp_path):
    src = tmp_path / "source.mp4"
    write_wav(tmp_path / "audio.wav", make_video(src, 20))
    meta = job(tmp_path, 20)
    save_json(Selection(source="gemini", clips=[clip(2, 9)]), tmp_path / "selection.json")
    sr.run(tmp_path, meta, Config(download=DownloadCfg(keep_source=True)))
    assert src.exists()


def test_resolve_falls_back_to_full_download(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"x")
    meta = job(tmp_path, 100)
    f, lc = sr.resolve(tmp_path, meta, clip(10, 20))
    assert f.name == "source.mp4" and lc.start == 10


def test_resolve_without_any_video_raises(tmp_path):
    meta = job(tmp_path, 100, mode="clips")
    with pytest.raises(sr.SourceError):
        sr.resolve(tmp_path, meta, clip(10, 20))


class FakeRemote:
    """Stands in for YouTube: 'downloads' ranges by cutting from a local file whose clock runs `shift` s
    behind the transcript (like a stream's drifting fragments), starting on a 2 s 'fragment' boundary."""

    def __init__(self, remote: Path, shift: float, step: float = 2.0):
        self.remote, self.shift, self.step, self.calls = remote, shift, step, []

    def download_range(self, info, cfg, x0, x1, dst, *, audio_only=False):
        x0 = max(0.0, x0)
        r0 = (x0 // self.step) * self.step
        self.calls.append((round(x0, 2), round(x1, 2), audio_only))
        out = Path(str(dst) + (".mka" if audio_only else ".mkv"))
        args = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{r0:.3f}", "-to", f"{x1:.3f}", "-i", str(self.remote)]
        # PCM: YouTube copies its streams, so no encoder delay may shift the audio here either
        args += (["-vn", "-c:a", "pcm_s16le"] if audio_only
                 else ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-c:a", "pcm_s16le"])
        subprocess.run([*args, str(out)], check=True)
        return out, r0


@needs_ffmpeg
@pytest.mark.parametrize("shift", [0.0, 37.25, -12.6])
def test_fetch_measures_where_the_download_really_starts(tmp_path, monkeypatch, shift):
    total = 160.0
    remote = tmp_path / "remote.mp4"
    audio = make_video(remote, total, seed=5)
    # transcript timeline = remote clock + shift
    lead = int(max(shift, 0) * sr.SR)
    trail = int(max(-shift, 0) * sr.SR)
    write_wav(tmp_path / "audio.wav", np.concatenate([np.zeros(lead, np.float32), audio[trail:]]))
    meta = job(tmp_path, total + shift, mode="clips")
    fake = FakeRemote(remote, shift)
    monkeypatch.setattr(dl, "download_range", fake.download_range)
    monkeypatch.setattr(dl, "is_fragmented", lambda info: True)
    monkeypatch.setattr(dl, "bytes_per_s", lambda info: 1e5)
    monkeypatch.setattr(sr.RangeFetcher, "info", lambda self: {})
    c = clip(70.0, 95.0, "x") if shift >= 0 else clip(40.0, 65.0, "x")
    n, approx = sr.ensure(tmp_path, meta, Config(), [c])
    assert n == 1 and approx == 0
    e = sr.load_index(tmp_path).clips["x"]
    assert e.aligned and e.score > 0.9
    assert sr.covers(e, c)
    assert abs(sr.load_index(tmp_path).calib[-1][1] - shift) < 0.01
    # the clip's audio in its own file is the transcript's audio at the same moment
    f, lc = sr.resolve(tmp_path, meta, c)
    got = sr.decode_audio(f)[int(lc.start * sr.SR): int(lc.start * sr.SR) + sr.SR * 10]
    ref = sr.read_wav(tmp_path / "audio.wav", c.start, c.start + 10)
    assert np.corrcoef(got, ref)[0, 1] > 0.95
    # ...and so is the picture: the frame the render seeks to is the remote's frame at that moment
    for t in (c.start + 1, c.end - 1):
        want = frame_at(remote, t - shift).astype(int)
        assert np.abs(frame_at(f, t - e.offset).astype(int) - want).mean() < 1.0
        assert np.abs(frame_at(remote, t - shift + 1 / 15).astype(int) - want).mean() > 1.0  # frames do differ
    assert fake.calls[0][2] is True  # audio probe first, then one video download
    assert len(fake.calls) == 2


@needs_ffmpeg
def test_fetch_silent_clip_uses_estimate(tmp_path, monkeypatch):
    remote = tmp_path / "remote.mp4"
    make_video(remote, 60)
    write_wav(tmp_path / "audio.wav", np.zeros(int(60 * sr.SR), np.float32))  # silent transcript audio
    meta = job(tmp_path, 60, mode="clips")
    fake = FakeRemote(remote, 0)
    monkeypatch.setattr(dl, "download_range", fake.download_range)
    monkeypatch.setattr(dl, "is_fragmented", lambda info: True)
    monkeypatch.setattr(dl, "bytes_per_s", lambda info: 1e5)
    monkeypatch.setattr(sr.RangeFetcher, "info", lambda self: {})
    c = clip(20, 30, "s")
    assert sr.ensure(tmp_path, meta, Config(), [c]) == (1, 1)
    e = sr.load_index(tmp_path).clips["s"]
    assert not e.aligned and sr.covers(e, c)


@needs_ffmpeg
def test_render_reads_the_clip_file(tmp_path):
    """End to end on one clip: the preview renders from sources/<id>.mkv after the full download is gone."""
    from shorts.stages import render

    src = tmp_path / "source.mp4"
    write_wav(tmp_path / "audio.wav", make_video(src, 30))
    meta = job(tmp_path, 30)
    c = clip(8.0, 14.0, "r")
    save_json(Selection(source="gemini", clips=[c]), tmp_path / "selection.json")
    cfg = Config.model_validate({"reframe": {"enabled": False}, "captions": {"enabled": False},
                                 "hook": {"enabled": False}, "pacing": {"enabled": False}})
    sr.run(tmp_path, meta, cfg)
    assert not src.exists()
    meta = VideoMeta.model_validate_json((tmp_path / "meta.json").read_text())
    out = tmp_path / "out.mp4"
    render.render_clip(tmp_path, meta, c, cfg, cfg.preview, out, lambda f: None)
    from common import ffmpeg
    assert ffmpeg.duration(out) == pytest.approx(6.0, abs=0.15)


def test_index_roundtrip(tmp_path):
    idx = sr.SourceIndex(clips={"a": sr.SourceEntry(file="a.mkv", offset=1.5, duration=9)}, calib=[[10, 0.5]])
    sr.save_index(tmp_path, idx)
    assert sr.load_index(tmp_path) == idx
    (tmp_path / "sources" / "index.json").write_text("{broken")
    assert sr.load_index(tmp_path) == sr.SourceIndex()
    json.dumps(idx.model_dump())


def test_older_finished_job_stays_done(tmp_path):
    """A job finished before the "Clip video" stage existed reads its clips from the full download: Studio must
    show it done, not ask to resume."""
    from shorts import server

    d = tmp_path / "old1"
    d.mkdir()
    (d / "source.mp4").write_bytes(b"x")
    save_json(VideoMeta(id="old1", title="t", source_url=None, file="source.mp4", duration=10.0, width=1920,
                        height=1080, fps=30.0), d / "meta.json")
    save_json(Selection(source="gemini", clips=[clip(1, 5)]), d / "selection.json")
    stages = [server.StageState(key=s.key, label=s.label, status="done") for s in server.pipeline.STAGES
              if s.key != "sources"]
    job = server.JobState(id="old1", source="x", title="t", status="done", created_at="", updated_at="",
                          stages=server._merge_stage_defs(stages))
    assert next(s for s in job.stages if s.key == "sources").status == "pending"
    server._reconcile(job, d, Config.model_validate({"reframe": {"enabled": False}, "captions": {"enabled": False}}))
    assert next(s for s in job.stages if s.key == "sources").status == "done"
