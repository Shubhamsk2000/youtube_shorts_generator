from anime.config import Config, merge
from anime.models import Cue, Pick, Segment, Word
from anime.server import Options, job_cfg
from anime.stages.ingest import split_cue
from anime.card import layout
from anime.stages.render import captions_ass, map_cues, place, word_groups


def words(text: str, start: float = 0.0, step: float = 0.3) -> list[Word]:
    return [Word(text=t, start=round(start + i * step, 3), end=round(start + i * step + 0.25, 3))
            for i, t in enumerate(text.split())]


def test_split_cue_at_sentence_ends_and_pauses():
    ws = words("You came all this way. Just to lose? I won't let that happen to any of them ever again okay")
    ws[-1].start, ws[-1].end = 30.0, 30.3  # a long pause before the last word
    parts = split_cue(Cue(start=0, end=30.3, text="x", words=ws), max_words=14)
    assert [p.text for p in parts] == [
        "You came all this way.", "Just to lose? I won't let that happen to any of them ever again",
        "okay",
    ]
    assert parts[0].start == ws[0].start and parts[0].end == ws[4].end


def test_word_groups_respect_size_pauses_and_sentence_ends():
    ws = words("Run! I said run now")
    ws[3].start, ws[3].end = 5.0, 5.2  # pause before "run"
    ws[4].start, ws[4].end = 5.3, 5.5
    assert [[w.text for w in g] for g in word_groups(ws, 3, 18)] == [["Run!"], ["I", "said"], ["run", "now"]]


def test_map_cues_moves_words_with_slowmo():
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t",
             segments=[Segment(start=10, end=12, effects=["slowmo"])])
    c = Cue(start=10.5, end=11.5, text="go now", words=[Word(text="go", start=10.5, end=10.8),
                                                         Word(text="now", start=11.0, end=11.4)])
    (out,) = map_cues(place(p, Config()), [c])
    assert [(w.text, w.start, w.end) for w in out.words] == [("go", 1.0, 1.6), ("now", 2.0, 2.8)]


def test_map_cues_drops_words_cut_off_by_the_segment():
    p = Pick(id="c", rank=1, style="clip", score=5, title="t", tweet="t", segments=[Segment(start=0, end=1.0)])
    c = Cue(start=0.2, end=1.6, text="hello there friend", words=words("hello there friend", 0.2, 0.5))
    (out,) = map_cues(place(p, Config()), [c])
    assert out.text == "hello there" and len(out.words) == 2  # "friend" (1.2-1.45 s) falls after the cut


def test_local_settings_override_nested_keys_only():
    base = {"card": {"name": "A", "handle": "@a", "theme": "dark"}, "style": "clip"}
    assert merge(base, {"card": {"name": "B"}}) == {"card": {"name": "B", "handle": "@a", "theme": "dark"},
                                                    "style": "clip"}


def test_job_options_shape_the_config():
    cfg = job_cfg(Config(), Options(style="montage", count=3, audio="jpn", captions=False))
    assert cfg.style == "montage" and cfg.select.count == 3 and cfg.captions.enabled is False
    assert cfg.subs.audio_languages[:2] == ["jpn", "ja"] and "eng" in cfg.subs.audio_languages
    assert Config().subs.audio_languages[0] == "eng"  # default prefers the English dub


def test_word_captions_never_overlap_between_groups():
    """Fast speech: a group's last word must be gone before the next group's first word shows."""
    ws = words("Oh it's going way faster now", 0.0, 0.2)  # 0.25 s words every 0.2 s
    cfg = Config()
    ass = captions_ass([Cue(start=0, end=1.5, text="x", words=ws)], cfg, layout(cfg.card, 540, 960, 1920, 1080, "x"))

    def secs(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    ev = sorted((secs(l.split(",")[1]), secs(l.split(",")[2])) for l in ass.splitlines() if l.startswith("Dialogue"))
    assert all(b0 <= a1 + 1e-9 for (a0, b0), (a1, b1) in zip(ev, ev[1:]))
