import numpy as np

from anime import music
from anime.config import Config
from anime.models import Pick, Segment, Signals
from anime.music import FPS, SR, Track, best_section, onset_envelopes, rank, tempo, track_beats
from anime.stages import amv


def click_track(bpm: float, seconds: float, quiet_until: float = 0.0) -> np.ndarray:
    """A kick on every beat (quiet hi-hats only before quiet_until)."""
    t = np.arange(int(seconds * SR)) / SR
    period = 60 / bpm
    phase = np.mod(t, period)
    kick = np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)
    rng = np.random.default_rng(0)
    hats = 0.05 * rng.standard_normal(len(t)) * np.exp(-90 * np.mod(t, period / 2))
    return (np.where(t >= quiet_until, 0.8 * kick, 0) + hats).astype(np.float32)


def test_tempo_and_beats_on_a_click_track():
    x = click_track(128, 30)
    full, low, _ = onset_envelopes(x)
    bpm = tempo(full)
    assert abs(bpm - 128) < 2
    beats = track_beats(full, bpm)
    assert abs(np.median(np.diff(beats)) - 60 / 128) < 2 / FPS  # within two analysis frames


def fake_track(bpm=120.0, seconds=90.0, drop_at=20.0) -> Track:
    period = 60 / bpm
    beats = [round(i * period, 3) for i in range(int(seconds / period))]
    energy = [0.15 if b < drop_at else 0.9 for b in beats[:-1]]
    hits = [(b, 1.0 if b >= drop_at else 0.2) for b in beats]
    return Track(id="hype/x.mp3", title="x", duration=seconds, bpm=bpm, beats=beats, downbeats=beats[::4],
                 energy=energy, hits=hits, mood="hype", mood_source="folder")


def test_best_section_opens_with_energy():
    a, b = best_section(fake_track(drop_at=20.0), 40, 60)
    assert 18 <= a <= 22 and 40 <= b - a <= 60


def test_rank_prefers_mood_and_fresh_songs(monkeypatch):
    monkeypatch.setattr(music, "usage", lambda: {"hype/used.mp3": 5})
    hype = fake_track()
    used = hype.model_copy(update={"id": "hype/used.mp3"})
    sad = hype.model_copy(update={"id": "sad/s.mp3", "mood": "sad"})
    order = [t.id for t, _, _ in rank([sad, used, hype], "fight", 40)]
    assert order[0] == "hype/x.mp3" and order[-1] == "sad/s.mp3"
    assert [t.id for t, _, _ in rank([sad, hype], "emotional", 40)][0] == "sad/s.mp3"


def test_amv_cuts_on_beats_faster_on_the_drop_and_never_reuses_footage():
    cfg = Config()
    t = fake_track(bpm=120, drop_at=20.0)
    a, b = 8.0, 56.0
    n = 2880
    sig = Signals(bin_s=0.5, loudness=[-30.0] * n, motion=[0.01] * n, cuts=[], excluded=[(0.0, 90.0, "op")])
    pool = [Segment(start=200 + 20 * i, end=212 + 20 * i, intensity=5 if i % 2 else 2) for i in range(10)]
    p = Pick(id="s", rank=1, style="song", score=5, title="t", tweet="t", kind="fight", segments=pool)
    segs = amv.build(p, t, a, b, sig, 1440.0, cfg)
    beat_times = {round(x - a, 3) for x in t.beats}
    assert segs[0].out_start == 0.0
    assert all(round(s.out_start, 3) in beat_times for s in segs)            # every cut on a beat
    calm = [y.out_start - x.out_start for x, y in zip(segs, segs[1:]) if y.out_start <= 20 - a]
    loud = [y.out_start - x.out_start for x, y in zip(segs, segs[1:]) if x.out_start >= 20 - a]
    assert min(calm) > max(loud)                                             # faster cuts on the drop
    assert min(loud) >= cfg.music.min_shot_s
    spans = sorted((s.start, s.end) for s in segs)
    assert all(a2 >= b1 - 1e-6 for (a1, b1), (a2, b2) in zip(spans, spans[1:]))  # no footage twice
    assert not any(s.start < 90 for s in segs)                               # never the OP
    drop = [s for s in segs if s.out_start >= 20 - a]
    assert sum(s.intensity >= 4 for s in drop) > len(drop) / 2               # big moments on the drop
    punched = [i for i, s in enumerate(segs) if "zoom_punch" in s.effects]
    assert punched and all(j - i >= 2 for i, j in zip(punched, punched[1:]))  # never back to back
