import wave

import numpy as np

from shorts.config import SelectCfg
from shorts.models import AudioSignals, Sentence, Transcript, Word
from shorts.stages import signals
from shorts.stages.select import select_heuristic, validate
from shorts.stages.signals import Mark, analyze, energy01, sentence_marks


def sentence(i: int, start: float, end: float, text: str = "words here now") -> Sentence:
    ws = text.split()
    step = (end - start) / len(ws)
    return Sentence(id=i, start=start, end=end, text=text,
                    words=[Word(text=w, start=start + k * step, end=start + (k + 1) * step, prob=1) for k, w in enumerate(ws)])


def synthetic() -> tuple[AudioSignals, list[Sentence]]:
    """50 s at 0.25 s bins: speech at -30 dB, one shouted line, one crash right after a line, one late noise."""
    db = np.full(200, -45.0)                       # room tone
    ss = [sentence(0, 0, 5), sentence(1, 10, 15), sentence(2, 20, 25), sentence(3, 35, 40)]
    for s in ss:
        db[int(s.start * 4):int(s.end * 4)] = -30  # speech level
    db[50] = -10                                   # sentence 1 contains a shout
    db[60:66] = -8                                 # crash 15.0-16.5 s, right after sentence 1
    db[108:112] = -8                               # noise at 27-28 s: two seconds after sentence 2 ends
    return AudioSignals(bin_s=0.25, loudness=db.tolist(), speech_db=-30.0), ss


def test_marks_loud_lines_and_reactions_right_after():
    sig, ss = synthetic()
    m = sentence_marks(sig, ss, loud_top=0.25)
    assert m[1].loud and not m[0].loud and not m[2].loud
    assert m[1].burst_after_s == 1.5
    assert m[2].burst_after_s == 0 and m[0].burst_after_s == 0 and m[3].burst_after_s == 0


def test_short_bursts_are_ignored():
    sig, ss = synthetic()
    assert sentence_marks(sig, ss, burst_min_s=2.0)[1].burst_after_s == 0


def test_energy01_is_relative_to_the_video():
    sig, _ = synthetic()
    assert energy01(sig, 15, 16.5) > 0.9 > energy01(sig, 0, 5) > energy01(sig, 6, 9) == 0.0


def test_clip_end_is_extended_over_the_reaction():
    sig, ss = synthetic()
    marks = sentence_marks(sig, ss, loud_top=0.25)
    raw = [{"start_id": 0, "end_id": 1, "title": "t", "hook": "h", "reason": "r",
            "hook_score": 7, "standalone_score": 7, "payoff_score": 7, "energy_score": 7}]
    plain = validate(raw, ss, 50, SelectCfg())[0][0]
    kept = validate(raw, ss, 50, SelectCfg(), marks=marks)[0][0]
    assert abs(plain.end - 15.4) < 1e-6              # sentence end + PAD_TAIL
    assert abs(kept.end - 16.9) < 1e-6               # ... + the 1.5 s crash
    # never past the next sentence or the max length
    tight = validate(raw, ss, 50, SelectCfg(max_clip_s=16.0), marks=marks)[0][0]
    assert tight.end <= tight.start + 16.0


def test_heuristic_uses_loudness():
    sig, ss = synthetic()
    t = Transcript(language="en", model="x", duration=50, sentences=ss)
    sc = SelectCfg(min_clip_s=5, max_clip_s=20, max_clips=10, min_score=0, max_overlap_s=100)  # keep every window
    quiet = {c.start_sentence: c for c in select_heuristic(t, sc).clips}
    loud = {c.start_sentence: c for c in select_heuristic(t, sc, sig=sig, marks=sentence_marks(sig, ss, loud_top=0.25)).clips}
    assert loud[1].scores.energy > quiet[1].scores.energy
    assert loud[1].scores.payoff == 7 and quiet[1].scores.payoff == 5
    assert "reaction" in loud[1].reason


def test_analyze_reads_a_real_wav(tmp_path):
    sr = 16000
    t = np.arange(20 * sr) / sr
    x = np.random.default_rng(0).normal(0, 0.003, len(t))            # floor ~ -50 dB
    for a, b in ((2, 6), (10, 14)):
        x[a * sr:b * sr] += 0.05 * np.sin(2 * np.pi * 220 * t[a * sr:b * sr])   # "speech" ~ -29 dB
    x[6 * sr:7 * sr] += 0.5 * np.sin(2 * np.pi * 90 * t[6 * sr:7 * sr])          # crash right after line 0
    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    ss = [sentence(0, 2, 6), sentence(1, 10, 14)]
    sig = analyze(wav, Transcript(language="en", model="x", duration=20, sentences=ss))
    assert len(sig.loudness) == 80 and -32 < sig.speech_db < -26
    m = sentence_marks(sig, ss)
    assert abs(m[0].burst_after_s - 1.0) <= 0.25 and m[1].burst_after_s == 0
    assert not m[0].loud and not m[1].loud          # both lines are plain speech level


def test_run_is_cached_and_tolerates_missing_audio(tmp_path):
    t = Transcript(language="en", model="x", duration=1, sentences=[])
    assert signals.run(tmp_path, t) is None
    (tmp_path / "signals.json").write_text(AudioSignals(bin_s=0.25, loudness=[-20.0], speech_db=-20.0).model_dump_json())
    assert signals.run(tmp_path, t).loudness == [-20.0]
