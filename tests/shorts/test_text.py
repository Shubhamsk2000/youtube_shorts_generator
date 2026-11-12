from shorts.models import Word
from shorts.text import group_sentences


def words(spec: list[tuple[str, float]], dur: float = 0.3) -> list[Word]:
    return [Word(text=t, start=s, end=s + dur, prob=1.0) for t, s in spec]


def test_splits_on_terminal_punctuation():
    ws = words([("I", 0), ("won.", 0.4), ("Did", 0.8), ("you?", 1.2), ("Yes!", 1.6)])
    out = group_sentences(ws)
    assert [s.text for s in out] == ["I won.", "Did you?", "Yes!"]
    assert [s.id for s in out] == [0, 1, 2]
    assert out[1].start == 0.8 and out[1].end == 1.5


def test_abbreviation_does_not_split():
    ws = words([("Mr.", 0), ("Beast", 0.4), ("is", 0.8), ("here.", 1.2)])
    assert [s.text for s in group_sentences(ws)] == ["Mr. Beast is here."]


def test_long_pause_splits():
    ws = words([("one", 0), ("two", 0.4), ("three", 3.0)])
    assert [s.text for s in group_sentences(ws, max_gap_s=1.0)] == ["one two", "three"]


def test_run_on_split_at_comma_then_hard_limit():
    ws = words([(f"w{i}," if i == 6 else f"w{i}", i * 0.4) for i in range(25)])
    out = group_sentences(ws, max_words=10)
    assert len(out[0].words) == 7  # comma after 60% of max_words
    assert all(len(s.words) <= 10 for s in out)
    assert sum(len(s.words) for s in out) == 25


def test_empty():
    assert group_sentences([]) == []
