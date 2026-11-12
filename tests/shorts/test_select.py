from shorts.config import SelectCfg
from shorts.models import Sentence, Transcript, Word
from shorts.stages.select import fit_span, make_windows, select_heuristic, validate
from shorts.text import merge_fragments


def sents(n: int, length: float = 5.0, gap: float = 0.5) -> list[Sentence]:
    out = []
    for i in range(n):
        s = i * (length + gap)
        out.append(Sentence(id=i, start=s, end=s + length, text=f"sentence {i}.",
                            words=[Word(text=f"s{i}", start=s, end=s + length, prob=1)]))
    return out


def pick(a, b, score=7, **kw):
    return {"start_id": a, "end_id": b, "title": f"t{a}", "hook": "h", "reason": "r",
            "hook_score": score, "standalone_score": score, "payoff_score": score, "energy_score": score, **kw}


CFG = SelectCfg()


def test_valid_pick_is_padded_within_neighbours():
    ss = sents(20)
    clips, rej = validate([pick(2, 5)], ss, 200, CFG)
    assert not rej and len(clips) == 1
    c = clips[0]
    assert ss[1].end <= c.start <= ss[2].start      # lead-in never reaches the previous sentence
    assert ss[5].end <= c.end <= ss[6].start
    assert c.rank == 1 and c.score == 7.0 and (c.start_sentence, c.end_sentence) == (2, 5)


def test_too_long_is_trimmed_and_too_short_extended():
    ss = sents(40)  # 5.5 s per sentence
    clips, _ = validate([pick(0, 20), pick(30, 30)], ss, 400, CFG)
    by_start = sorted(clips, key=lambda c: c.start)
    assert by_start[0].duration <= 60 and by_start[0].end_sentence < 20
    assert by_start[1].duration >= 15


def test_low_score_unknown_ids_and_duplicates_rejected():
    ss = sents(40)
    raw = [pick(0, 4, score=9), pick(1, 5, score=6), pick(10, 14, score=2), pick(99, 100), {"bad": 1}]
    clips, rej = validate(raw, ss, 400, CFG)
    assert [c.start_sentence for c in clips] == [0]
    assert rej == {"duplicate / overlapping": 1, "score below threshold": 1,
                   "unknown sentence id": 1, "malformed": 1}


def test_ranked_by_score_and_capped():
    ss = sents(100)
    raw = [pick(i, i + 3, score=4 + i % 6) for i in range(0, 96, 6)]
    clips, rej = validate(raw, ss, 1000, SelectCfg(max_clips=5))
    assert len(clips) == 5 and rej["over max_clips"] == len(raw) - 5
    assert [c.rank for c in clips] == [1, 2, 3, 4, 5]
    assert all(a.score >= b.score for a, b in zip(clips, clips[1:]))


def test_fit_span_impossible():
    ss = [Sentence(id=0, start=0, end=90, text="x", words=[])]
    assert fit_span(ss, 0, 0, 15, 60) == (None, None)


def test_windows_cover_everything_with_overlap():
    ss = sents(500, length=5, gap=0.5)  # ~46 min
    wins = make_windows(ss, 20 * 60, 2 * 60)
    assert len(wins) == 2
    assert wins[0][0].id == 0 and wins[-1][-1].id == 499
    assert wins[0][-1].id > wins[1][0].id  # overlap
    assert len(make_windows(ss[:200], 20 * 60, 120)) == 1


def test_heuristic_returns_valid_clips():
    ss = sents(60)
    ss[10].text = "Whoever wins gets $100,000?"
    clips = select_heuristic(Transcript(language="en", model="x", duration=400, sentences=ss), CFG).clips
    assert clips and all(15 <= c.duration <= 60 for c in clips)
    assert clips[0].start_sentence == 10


def test_merge_fragments():
    ws = [Word(text=t, start=i, end=i + 1, prob=0.9) for i, t in enumerate(["$100", ",000", "is", "50", "%", "x", ".5"])]
    assert [w.text for w in merge_fragments(ws)] == ["$100,000", "is", "50%", "x", ".5"]
