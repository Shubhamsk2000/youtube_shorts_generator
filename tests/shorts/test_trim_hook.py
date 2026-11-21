"""Clip editor: moving the hook banner, and trimming a clip's start / end with 5 s of review on each side."""
import pytest

from shorts import editor
from shorts.models import Clip, ClipScores, Sentence, Transcript, VideoMeta, Word, save_json
from shorts.stages import framing, pacing
from shorts.stages.captions import to_ass
from shorts.stages.framing import Framing, Key
from shorts.stages.reframe import plan_path


def clip(start=10.0, end=30.0):
    return Clip(id="c1", rank=1, start=start, end=end, start_sentence=1, end_sentence=2, title="t", hook="h",
                reason="r", scores=ClipScores(hook=8, standalone=8, payoff=8, energy=8), score=8, text="x",
                payoff_sentence=2)


def meta(duration=60.0):
    return VideoMeta(id="v", title="v", duration=duration, width=1920, height=1080, fps=30.0, file="v.mp4")


def transcript():
    def sent(i, a, b):
        return Sentence(id=i, start=a, end=b, text=f"s{i}",
                        words=[Word(text=f"w{i}a", start=a, end=(a + b) / 2, prob=1),
                               Word(text=f"w{i}b", start=(a + b) / 2, end=b, prob=1)])
    return Transcript(language="en", model="m", duration=60,
                      sentences=[sent(0, 2, 8), sent(1, 10, 18), sent(2, 19, 29), sent(3, 31, 40)])


def hook_y_px(ass: str) -> int:
    line = next(x for x in ass.splitlines() if ",Hook," in x)
    return int(line.split("\\pos(")[1].split(")")[0].split(",")[1])


def test_hook_position_from_the_editor_wins_over_the_cam_push():
    from shorts.config import CaptionCfg
    assert hook_y_px(to_ass([], CaptionCfg(), "a hook", hook_min_y=600)) > 600
    placed = to_ass([], CaptionCfg(), "a hook", hook_min_y=600, hook_y=0.5)
    assert hook_y_px(placed) == 960


def test_hook_position_keeps_the_box_inside_the_frame():
    from shorts.config import CaptionCfg
    assert hook_y_px(to_ass([], CaptionCfg(), "a hook", hook_y=0.0)) > 0
    assert hook_y_px(to_ass([], CaptionCfg(), "a hook", hook_y=1.0)) < 1920


def test_review_range_is_5s_each_side_inside_the_video():
    assert editor.review_range(meta(), clip()) == (5.0, 35.0)
    assert editor.review_range(meta(32), clip(2, 30)) == (0.0, 32.0)


def test_trim_follows_sentences_and_drops_a_payoff_outside():
    t = transcript()
    c = editor.trimmed(t, meta(), clip(), 6.0, 18.5)
    assert (c.start, c.end, c.start_sentence, c.end_sentence) == (6.0, 18.5, 0, 1)
    assert c.payoff_sentence is None and c.id == "c1"
    assert c.text.split() == ["w0b", "w1a", "w1b"]  # w0a ends before 6 s
    assert editor.trimmed(t, meta(), clip(), 12, 35).payoff_sentence == 2


@pytest.mark.parametrize("a,b", [(10, 11), (0, 300), (-1, 20), (10, 61)])
def test_trim_rejects_bad_ranges(a, b):
    with pytest.raises(ValueError):
        editor.trimmed(transcript(), meta(), clip(), a, b)


def test_trim_moves_keys_with_the_footage_and_clears_the_old_analysis(tmp_path):
    old, new = clip(10, 30), clip(8, 20)
    framing.save(tmp_path, old, Framing(keys=[Key(f=0, x=0.3), Key(f=300, x=0.6), Key(f=500, x=0.5)]))
    for p in (plan_path(tmp_path, old), pacing.edits_path(tmp_path, old)):
        p.parent.mkdir(exist_ok=True)
        p.write_text("{}")
    dropped = editor.apply_trim(tmp_path, meta(), old, new)
    # the new clip starts 2 s (60 frames) earlier and is 360 frames long: 300 + 60 and 500 + 60 fall outside
    assert dropped == 2
    assert [k.f for k in framing.load(tmp_path, new).keys] == [0]  # the first-frame key stays on the start
    assert not plan_path(tmp_path, old).exists() and not pacing.edits_path(tmp_path, old).exists()


def test_trim_keeps_the_window_at_the_start(tmp_path):
    later = clip(10, 30)
    framing.save(tmp_path, later, Framing(keys=[Key(f=0, x=0.3, a=1.0), Key(f=90, x=0.6)]))
    editor.apply_trim(tmp_path, meta(), later, clip(6, 30))   # starts 4 s earlier: the first-frame key stays
    assert [k.f for k in framing.load(tmp_path, clip(6, 30)).keys] == [0, 210]

    framing.save(tmp_path, later, Framing(keys=[Key(f=30, x=0.3), Key(f=60, x=0.4), Key(f=300, x=0.6)]))
    dropped = editor.apply_trim(tmp_path, meta(), later, clip(13, 30))  # starts 3 s later
    keys = framing.load(tmp_path, clip(13, 30)).keys
    assert [(k.f, k.x) for k in keys] == [(0, 0.4), (210, 0.6)] and dropped == 1
