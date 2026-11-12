import numpy as np

from anime.config import Config, EditCfg, SignalsCfg
from anime.models import Cue, EpisodeMeta, Pick, Segment, Signals
from anime.stages.edit import EPS, avoid_excluded, finalize, out_length, snap_beat, snap_clip
from anime.stages.ingest import _ranges, clean_text
from anime.stages.render import map_cues, place, total_length
from anime.stages.select import clean_tweet, parse_time, validate
from anime.stages.signals import detect_cuts

META = EpisodeMeta(id="x", title="x", source="/x.mkv", duration=1440, width=1920, height=1080, fps="24000/1001")


def sig(**kw) -> Signals:
    n = 2880
    return Signals(bin_s=0.5, loudness=kw.get("loudness", [-30.0] * n), motion=[0.01] * n,
                   cuts=kw.get("cuts", []), excluded=kw.get("excluded", []))


# ---------------------------------------------------------------- subtitles

def test_clean_text_strips_ass_and_html_tags():
    assert clean_text(r"{\i1}Wait!{\i0}\NDon't go") == "Wait!\nDon't go"
    assert clean_text("<i>you  fool</i>") == "you fool"
    assert clean_text(r"{\an8}\h") == ""


def test_song_ranges_merge_and_drop_short():
    spans = [(60, 64), (66, 70), (71, 100), (500, 505)]
    assert _ranges(spans) == [(60, 100)]


# ---------------------------------------------------------------- select

def test_parse_time_formats():
    assert parse_time("03:15") == 195
    assert parse_time("1:02:03") == 3723
    assert parse_time("12:30.5") == 750.5
    assert parse_time(42) == 42
    assert parse_time("soon") is None


def test_clean_tweet_drops_emoji_and_hashtags():
    assert clean_tweet("he really said that 💀🔥 #jjk #anime") == "he really said that"


def test_validate_clip_drops_excluded_short_and_overlapping():
    cfg = Config()
    s = sig(excluded=[(0, 90, "opening")])
    raw = [
        {"start": "00:30", "end": "01:00", "title": "op", "tweet": "t", "score": 9},     # inside the OP
        {"start": "05:00", "end": "05:30", "title": "a", "tweet": "t", "score": 8},
        {"start": "05:10", "end": "05:40", "title": "dup", "tweet": "t", "score": 7},    # overlaps "a"
        {"start": "07:00", "end": "07:04", "title": "short", "tweet": "t", "score": 9},  # too short
        {"start": "09:00", "end": "11:00", "title": "long", "tweet": "t", "score": 6},   # trimmed to max
    ]
    picks = validate(raw, "clip", META, s, cfg)
    assert [p.title for p in picks] == ["a", "long"]
    assert [p.rank for p in picks] == [1, 2]
    assert picks[1].duration == cfg.select.max_clip_s


def test_validate_montage_keeps_known_fx_only():
    raw = [{"beats": [{"start": "02:00", "end": "02:04"},
                      {"start": "03:00", "end": "03:03", "transition_in": "flash", "effects": ["shake", "spin"]}],
            "title": "m", "tweet": "t", "score": 5}]
    (p,) = validate(raw, "montage", META, sig(), Config())
    assert p.segments[1].transition_in == "flash"
    assert p.segments[1].effects == ["shake"]


# ---------------------------------------------------------------- edit

def test_snap_clip_never_opens_mid_line_and_lands_on_cuts():
    cues = [Cue(start=99.0, end=102.0, text="a"), Cue(start=128.0, end=131.0, text="b")]
    ec = EditCfg(snap_s=0.8)
    a, b = snap_clip(100.0, 129.5, cuts=[98.6, 131.4], cues=cues, ec=ec, max_s=50)
    assert a == round(98.6 + EPS, 3)   # back to the line start, then onto the shot change before it
    assert b == round(131.4 - EPS, 3)  # extended to the end of the line, then onto the cut


def test_snap_clip_ignores_cut_inside_a_line():
    cues = [Cue(start=99.0, end=103.0, text="a")]
    a, _ = snap_clip(99.0, 120.0, cuts=[99.5], cues=cues, ec=EditCfg(), max_s=50)
    assert a == 99.0


def test_snap_beat_keeps_a_minimum_length():
    # snapping to either cut would leave less than 1.2 s
    assert snap_beat(10.0, 12.0, cuts=[10.9, 11.2], ec=EditCfg()) == (10.0, 12.0)
    assert snap_beat(10.0, 12.0, cuts=[10.5], ec=EditCfg()) == (10.53, 12.0)


def test_finalize_montage_punch_on_loudest_hit_and_first_is_cut():
    loud = [-30.0] * 2880
    loud[2 * 205] = -3.0  # a hit at ~205 s
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t", segments=[
        Segment(start=100, end=103, transition_in="flash"),
        Segment(start=204, end=207, transition_in="flash"),
    ])
    out = finalize(p, sig(loudness=loud), [], META.duration, Config())
    assert out.segments[0].transition_in == "cut"
    last = out.segments[-1]  # extra beats are inserted before Gemini's last beat
    assert (last.start, last.end) == (204, 207)
    assert last.effects == ["zoom_punch"]
    assert 205 <= last.punch_at <= 205.5


def test_finalize_shortens_slowmo_beats():
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t",
             segments=[Segment(start=10, end=20, effects=["slowmo"]), Segment(start=30, end=33)])
    out = finalize(p, sig(), [], META.duration, Config())
    assert out.segments[0].duration == 3.0


# ---------------------------------------------------------------- render timeline

def test_place_overlaps_transitions_and_counts_slowmo_and_freeze():
    cfg = Config()
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t", segments=[
        Segment(start=0, end=4),
        Segment(start=10, end=12, transition_in="flash", effects=["slowmo"]),
        Segment(start=20, end=23, transition_in="cut", effects=["freeze_end"]),
    ])
    pl = place(p, cfg)
    assert pl[1].length == 4.0 and pl[1].overlap == 0.25 and pl[1].out_start == 3.75
    assert pl[2].overlap == 0 and abs(pl[2].length - 3.6) < 1e-9
    assert abs(total_length(pl) - (3.75 + 4.0 + 3.6)) < 1e-9


def test_map_cues_follows_segments_and_speed():
    cfg = Config()
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t", segments=[
        Segment(start=100, end=104),
        Segment(start=200, end=202, effects=["slowmo"]),
    ])
    cues = [Cue(start=101, end=103, text="one"), Cue(start=150, end=152, text="unused"),
            Cue(start=200.5, end=201.5, text="slow")]
    out = map_cues(place(p, cfg), cues)
    assert [(c.text, c.start, c.end) for c in out] == [("one", 1.0, 3.0), ("slow", 5.0, 7.0)]


# ---------------------------------------------------------------- signals

def test_detect_cuts_finds_spikes_not_motion():
    rng = np.random.default_rng(0)
    d = np.abs(rng.normal(0.02, 0.01, 480))  # steady motion
    d[100] = d[300] = 0.5                     # two cuts
    d[200:230] = 0.12                         # a fast pan: high but not a spike
    fps = 24.0
    assert detect_cuts(d, fps, SignalsCfg()) == [100 / fps, 300 / fps]


def test_segments_never_run_into_the_op_or_ed():
    ed = [(1435.0, 1495.0, "ed"), (52.0, 142.0, "op")]
    assert avoid_excluded(1389.0, 1442.7, ed) == (1389.0, round(1435.0 - EPS, 3))  # cut before the ED
    assert avoid_excluded(130.0, 180.0, ed) == (round(142.0 + EPS, 3), 180.0)      # start after the OP
    p = Pick(id="c", rank=1, style="clip", score=5, title="t", tweet="t", segments=[Segment(start=1389, end=1442.7)])
    out = finalize(p, sig(excluded=ed), [], META.duration, Config())
    assert out.segments[0].end < 1435.0


def test_short_montage_is_topped_up_to_the_minimum_without_reusing_footage():
    cfg = Config()
    loud = [-30.0 + (i % 37) for i in range(2880)]  # some variation so intensity has peaks
    s = sig(loudness=loud, excluded=[(52.0, 142.0, "op")])
    p = Pick(id="m", rank=1, style="montage", score=5, title="t", tweet="t",
             segments=[Segment(start=300, end=303.5), Segment(start=600, end=601.5, transition_in="flash")])
    taken = []
    out = finalize(p, s, [], META.duration, cfg, taken)
    assert out_length(out.segments) >= cfg.select.montage_min_s
    assert (out.segments[-1].start + out.segments[-1].end) / 2 == 600.75  # Gemini's last beat still closes it
    assert all(seg.duration >= 2.5 for seg in out.segments)
    spans = sorted((seg.start, seg.end) for seg in out.segments)
    assert all(a2 >= b1 for (a1, b1), (a2, b2) in zip(spans, spans[1:]))  # no footage shown twice
    assert not any(a < 142 and b > 52 for a, b in spans)                  # never the OP
    second = finalize(p.model_copy(update={"id": "m2"}), s, [], META.duration, cfg, taken)
    extra1 = {(x.start, x.end) for x in out.segments[1:-1]}
    extra2 = {(x.start, x.end) for x in second.segments[1:-1]}
    assert not extra1 & extra2  # the next montage gets different extra beats


def test_generic_chapters_reveal_op_ed_and_preview():
    from anime.stages.signals import chapter_layout

    chs = [{"start": a, "end": b, "title": f"Chapter 0{i}"} for i, (a, b) in enumerate(
        [(0, 83), (83, 173.05), (173.05, 605.1), (605.1, 1351.1), (1351.1, 1441.07), (1441.07, 1472.05)], 1)]
    assert chapter_layout(chs, 1472.1, []) == [
        (83, 173.05, "opening"), (1351.1, 1441.07, "ending"), (1441.07, 1472.05, "next-episode preview")]


def test_montage_length_counts_transition_overlaps():
    segs = [Segment(start=0, end=4), Segment(start=10, end=14, transition_in="flash"),
            Segment(start=20, end=24, transition_in="fade")]
    assert abs(out_length(segs, 0.25) - (12 - 0.25 - 0.4)) < 1e-9
