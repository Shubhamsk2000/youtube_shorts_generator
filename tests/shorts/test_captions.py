from shorts.config import CaptionCfg
from shorts.models import Clip, ClipScores, Sentence, Transcript, Word
from shorts.stages.captions import CapWord, build_lines, clip_words, display_text, fix_timing, mark_emphasis, to_ass

CC = CaptionCfg()


def cw(text, start, end=None, **kw):
    return CapWord(text, start, end if end is not None else start + 0.25, **kw)


def test_display_text():
    assert display_text("$100,000.", True) == "$100,000"
    assert display_text("really?", True) == "REALLY?"
    assert display_text("wait,", False) == "wait"
    assert display_text("{x}\\", False) == "(x)"


def test_fix_timing_min_duration_monotonic_and_clamped():
    ws = [cw("A", -0.5, 0.1), cw("B", 0.5, 0.5), cw("C", 0.45, 0.6), cw("D", 2.9, 3.4)]
    out = fix_timing(ws, 3.0, 0.08)
    assert out[0].start == 0.0
    assert out[1].end - out[1].start >= 0.08 - 1e-9              # zero-length word made visible
    assert all(a.start < b.start for a, b in zip(out, out[1:]))
    assert out[-1].end == 3.0                              # clamped to the clip


def test_lines_break_on_count_punctuation_pause_and_sentence():
    ws = [cw("ONE", 0), cw("TWO", 0.3), cw("THREE", 0.6), cw("FOUR", 0.9),           # max 3 words
          cw("FIVE", 1.2, ends_clause=True), cw("SIX", 1.5),                        # comma ends a line
          cw("SEVEN", 2.5),                                                          # long pause
          cw("EIGHT", 2.8, sentence_start=True)]                                     # new sentence
    lines = build_lines(ws, CC)
    assert [[w.text for w in l.words] for l in lines] == [
        ["ONE", "TWO", "THREE"], ["FOUR", "FIVE"], ["SIX"], ["SEVEN"], ["EIGHT"]]


def test_line_held_through_short_gap_never_overlaps_next():
    lines = build_lines([cw("A", 0, 0.3), cw("B", 0.6, 0.9, sentence_start=True), cw("C", 3, 3.2, sentence_start=True)], CC)
    assert lines[0].end == 0.6           # bridged to the next line
    assert abs(lines[1].end - 1.1) < 1e-6  # long gap: short tail only
    assert all(a.end <= b.start for a, b in zip(lines, lines[1:]))


def test_long_words_respect_max_chars():
    lines = build_lines([cw("EXTRAORDINARY", 0), cw("CHAMPIONSHIP", 0.3), cw("GO", 0.6)], CC)
    assert all(sum(len(w.text) for w in l.words) + len(l.words) - 1 <= CC.max_chars or len(l.words) == 1
               for l in lines)


def test_emphasis_phrase_matches_whole_sequence_only():
    ws = [cw(t, i) for i, t in enumerate(["A", "HUNDRED", "GRAND", "HUNDRED", "TIMES"])]
    mark_emphasis(ws, ["hundred grand"])
    assert [w.emphasis for w in ws] == [False, True, True, False, False]


def test_ass_has_one_event_per_word_with_highlight():
    lines = build_lines([cw("I", 0), cw("WON", 0.3), cw("$100", 0.6)], CC)
    ass = to_ass(lines, CC)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 3
    assert "\\c&H0000E6FF" in events[1] and "}WON{" in events[1]   # yellow #FFE600 as ASS BGR
    assert events[0].split(",")[1] == "0:00:00.00"
    assert "PlayResX: 1080" in ass and "Inter Black" in ass


def test_clip_words_relative_to_clip():
    words = [Word(text=t, start=10 + i, end=10.5 + i, prob=1) for i, t in enumerate(["Hi,", "there", "friend."])]
    t = Transcript(language="en", model="x", duration=100,
                   sentences=[Sentence(id=0, start=10, end=12.5, text="Hi, there friend.", words=words)])
    clip = Clip(id="x", rank=1, start=9.85, end=12.9, start_sentence=0, end_sentence=0, title="t", hook="h",
                reason="r", scores=ClipScores(hook=5, standalone=5, payoff=5, energy=5), score=5, text="")
    out = clip_words(t, clip, CaptionCfg(offset_ms=-100))
    assert [w.text for w in out] == ["HI", "THERE", "FRIEND"]
    assert abs(out[0].start - 0.05) < 1e-6 and out[0].ends_clause and out[0].sentence_start
