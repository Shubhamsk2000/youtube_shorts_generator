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


def test_flash_window_is_the_loudest_moment_of_the_payoff():
    co = ColdOpenCfg()
    # payoff sentence 3 = 118-123 s -> 18-23 s into the clip; crash at 21.0 s
    a, b = pacing.flash_window(clip(), transcript(), audio_with_peak(21.0), co)
    assert abs(b - a - 0.7) < 1e-6 and a <= 21.0 <= b
    # reaction right after the line still counts (up to 3 s past its end)
    a, b = pacing.flash_window(clip(), transcript(), audio_with_peak(24.0), co)
    assert a <= 24.0 <= b
    # no audio: the start of the payoff line
    assert pacing.flash_window(clip(), transcript(), None, co) == (18.0, 18.7)


def test_flash_window_rules():
    t = transcript()
    assert pacing.flash_window(clip(payoff=None), t, None, ColdOpenCfg()) is None
    assert pacing.flash_window(clip(payoff=0), t, None, ColdOpenCfg()) is None          # payoff too early to tease
    assert pacing.flash_window(clip(), t, None, ColdOpenCfg(enabled=False)) is None
    assert pacing.flash_window(clip(show_hook=False), t, None, ColdOpenCfg(when="no_premise")) is None
    assert pacing.flash_window(clip(show_hook=True), t, None, ColdOpenCfg(when="no_premise")) is not None


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
    assert graph.startswith("[1:a:0]") and "[2:a:0]aselect=" in graph and "[fl][mn]concat=n=2:v=0:a=1,loudnorm[aout]" in graph
