from pathlib import Path

import numpy as np

from shorts.config import AudioCfg, CaptionCfg, ColdOpenCfg, HookCfg
from shorts.models import Clip, ClipScores, Sentence, Transcript, Word
from shorts.stages import pacing
from shorts.stages.captions import CapWord, edit_words, to_ass
from shorts.stages.render import _audio_graph


def clip(start=100.0, end=130.0, payoff=3, show_hook=True) -> Clip:
    return Clip(id="c1", rank=1, start=start, end=end, start_sentence=0, end_sentence=4, title="t", hook="h",
                reason="r", scores=ClipScores(hook=7, standalone=7, payoff=7, energy=7), score=7, text="",
                payoff_sentence=payoff, show_hook=show_hook)


def transcript() -> Transcript:
    # five 5 s sentences starting at 100 s: 100-105, 106-111, 112-117, 118-123, 124-129
    ss = []
    for i in range(5):
        a = 100 + i * 6
        ss.append(Sentence(id=i, start=a, end=a + 5, text=f"s{i}", words=[Word(text=f"s{i}", start=a, end=a + 5, prob=1)]))
    return Transcript(language="en", model="x", duration=200, sentences=ss)


def audio_with_peak(peak_at: float, dur: float = 30.0) -> pacing.ClipAudio:
    a = pacing.ClipAudio(Path("/nonexistent.wav"), 0, dur)   # no file: empty, then inject samples
    a.sr = 1000
    x = np.full(int(dur * a.sr), 0.01, np.float32)
    x[int(peak_at * a.sr):int((peak_at + 0.4) * a.sr)] = 0.9   # a crash at peak_at
    a.x = x
    return a


ON = ColdOpenCfg(enabled=True)


def speech(spans: list[tuple[float, float]], sid: int = 3, at: float = 100.0) -> Transcript:
    """A payoff sentence made of short words at the given clip-relative spans (the clip starts at `at`)."""
    # a pause of 0.5 s+ ends a sentence ("w3."), so whole sentences are the runs between pauses
    ws = [Word(text=f"w{i}" + ("." if i + 1 == len(spans) or spans[i + 1][0] - b >= 0.5 else ""),
               start=at + a, end=at + b, prob=1) for i, (a, b) in enumerate(spans)]
    pre = Sentence(id=0, start=at, end=at + 2, text="intro", words=[Word(text="intro", start=at, end=at + 2, prob=1)])
    pay = Sentence(id=sid, start=ws[0].start, end=ws[-1].end, text="payoff", words=ws)
    return Transcript(language="en", model="x", duration=200, sentences=[pre, pay])


def audio_for(spans, peak_at: float, dur: float = 30.0) -> pacing.ClipAudio:
    """Speech at 0.2, silence at 0.01, a 0.4 s burst at 0.9 starting at peak_at."""
    a = audio_with_peak(peak_at, dur)
    for s, e in spans:
        a.x[int(s * a.sr):int(e * a.sr)] = np.maximum(a.x[int(s * a.sr):int(e * a.sr)], 0.2)
    return a


def _whole_words(flash, spans) -> bool:
    a, b = flash
    return all(e <= a or s >= b or (a <= s and e <= b) for s, e in spans)


def test_cold_open_is_off_by_default():
    assert pacing.flash_window(clip(), transcript(), audio_with_peak(21.0), ColdOpenCfg()) is None


def test_flash_never_cuts_through_a_word():
    # a run of words with no pause, then a pause, then a short shout: "...blah blah blah. Drop 'em!"
    run = [(18.0 + i * 0.3, 18.3 + i * 0.3) for i in range(10)]    # 18.0-21.0, back to back
    shout = [(21.6, 21.9), (21.9, 22.3)]
    spans = run + shout
    t = speech(spans)
    # loudest bit is in the middle of the run: the old code would have flashed 19.3-20.0, mid-word
    f = pacing.flash_window(clip(), t, audio_for(spans, 19.5), ON)
    assert f is not None and _whole_words(f, spans)
    assert f[0] >= 21.5 and f[1] <= 22.5                           # the shout, whole, with its pause around it


def test_flash_prefers_a_wordless_reaction():
    spans = [(18.0, 18.4), (18.5, 19.0), (22.0, 22.4)]           # 19.0-22.0: no words (crash, screaming crowd)
    t = speech(spans)
    a, b = pacing.flash_window(clip(), t, audio_for(spans, 20.3), ON)
    assert abs(b - a - 0.7) < 1e-6 and a <= 20.3 <= b and _whole_words((a, b), spans)
    assert a >= 19.0 + 0.15 and b <= 22.0 - 0.15                  # keeps clear of the words around it


def test_spoken_flash_is_a_whole_sentence():
    def words(second: str) -> Transcript:
        t = speech([(18.0, 18.2), (19.0, 19.2), (20.5, 20.8), (20.8, 21.2)])
        for w, text in zip(t.sentences[1].words, ["Wait.", second, "Drop", "him!"]):
            w.text = text
        return t
    # "Somehow, ... drop him!" is one sentence: its ending alone is half a thought
    assert pacing.flash_window(clip(), words("Somehow,"), None, ON) is None
    # after a sentence end it is a whole exclamation ("Wait." and "Hey!" are too short to read)
    assert pacing.flash_window(clip(), words("Hey!"), None, ON) == (20.45, 21.3)


def test_no_clean_moment_means_no_flash():
    wall = [(18.0 + i * 0.25, 18.25 + i * 0.25) for i in range(44)]   # 18-29 s of non-stop talk
    assert pacing.flash_window(clip(), speech(wall), audio_for(wall, 20.0), ON) is None


def test_flash_window_without_audio_takes_the_earliest_clean_moment():
    spans = [(18.0, 18.3), (18.3, 18.6), (19.2, 19.6), (19.6, 20.0), (25.0, 25.4)]
    f = pacing.flash_window(clip(), speech(spans), None, ON)
    assert f is not None and _whole_words(f, spans) and abs(f[0] - 17.95) < 1e-6


def test_flash_window_rules():
    t = speech([(18.0, 18.4), (18.4, 18.9), (20.0, 20.5)])
    assert pacing.flash_window(clip(payoff=None), t, None, ON) is None
    assert pacing.flash_window(clip(payoff=0), t, None, ON) is None          # payoff too early to tease
    assert pacing.flash_window(clip(), t, None, ColdOpenCfg(enabled=False)) is None
    nop = ColdOpenCfg(enabled=True, when="no_premise")
    assert pacing.flash_window(clip(show_hook=False), t, None, nop) is None
    assert pacing.flash_window(clip(show_hook=True), t, None, nop) is not None


def test_stale_flash_is_rebuilt_when_the_cold_open_is_off(tmp_path, monkeypatch):
    from shorts.config import Config
    from shorts.stages import render
    c, calls = clip(), []
    monkeypatch.setattr(render, "refresh_clip", lambda job, cfg, cl: calls.append(cl.id))
    pacing.save_edits(tmp_path, c, None, (18.0, 18.7))             # captions were made behind a 0.7 s flash
    on = Config.model_validate({"cold_open": {"enabled": True}})
    assert not render.refresh_if_stale(tmp_path, on, c) and calls == []
    assert render.refresh_if_stale(tmp_path, Config(), c) and calls == ["c1"]
    pacing.save_edits(tmp_path, c, [(0, 5)], None)                 # no flash saved: nothing to fix
    assert not render.refresh_if_stale(tmp_path, Config(), c)


def test_edits_roundtrip_keep_and_flash(tmp_path):
    c = clip()
    pacing.save_edits(tmp_path, c, [(0, 5), (6, 10)], (18.0, 18.7))
    assert pacing.load_edits(tmp_path, c) == ([(0.0, 5.0), (6.0, 10.0)], (18.0, 18.7))
    assert pacing.load_keep(tmp_path, c) == [(0.0, 5.0), (6.0, 10.0)]
    pacing.save_edits(tmp_path, c, None, (18.0, 18.7))
    assert pacing.load_edits(tmp_path, c) == (None, (18.0, 18.7))
    pacing.save_edits(tmp_path, c, None, None)
    assert not pacing.edits_path(tmp_path, c).exists() and pacing.load_edits(tmp_path, c) == (None, None)


def test_captions_shift_behind_the_cold_open():
    words = [CapWord("A", 0.0, 0.3), CapWord("B", 2.0, 2.3)]
    cc = CaptionCfg()
    shifted = edit_words(words, None, 10.0, cc, lead=0.7)
    assert [round(w.start, 2) for w in shifted] == [0.7, 2.7]
    cut = edit_words(words, [(0, 0.5), (1.5, 10)], 10.0, cc, lead=0.7)   # a pause removed and the lead added
    assert [round(w.start, 2) for w in cut] == [0.7, 1.7]
    ass = to_ass([], cc, "Can trucks stop a train?", HookCfg(duration_s=2.8), lead=0.7)
    assert "0:00:00.00,0:00:03.50,Hook" in ass


def test_audio_graph_concatenates_the_flash_in_front():
    c, ac = clip(), AudioCfg()
    inputs, af = _audio_graph(Path("/v.mkv"), c, [(0, 10), (12, 30)], None, ac, ["loudnorm"], in0=1)
    assert inputs[:4] == ["-ss", "100.000", "-t", "30.000"] and af[:2] == ["-map", "1:a:0?"] and "aselect" in af[3]
    inputs, af = _audio_graph(Path("/v.mkv"), c, [(0, 10), (12, 30)], (18.0, 18.7), ac, ["loudnorm"], in0=1)
    assert inputs[:4] == ["-ss", "118.000", "-t", "0.700"] and inputs[6:10] == ["-ss", "100.000", "-t", "30.000"]
    graph = af[1]
    assert af[0] == "-filter_complex" and af[2:] == ["-map", "[aout]"]
    assert graph.startswith("[1:a:0]") and "afade=t=in:d=0.030" in graph and "afade=t=out:st=0.670" in graph and "[2:a:0]aselect=" in graph and "[fl][mn]concat=n=2:v=0:a=1,loudnorm[aout]" in graph
