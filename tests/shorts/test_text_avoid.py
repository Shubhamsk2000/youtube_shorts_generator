from shorts.config import CaptionCfg, ReframeCfg
from shorts.models import ReframePlan, Shot, TextSample
from shorts.stages.captions import H, avoid_y
from shorts.stages.reframe import visible_text_boxes

RC = ReframeCfg()


def plan(mode: str, boxes, x=0.5, f=30) -> ReframePlan:
    shot = Shot(start=0, end=300, mode=mode, x=[x] * 300 if mode == "track" else None)
    return ReframePlan(key="k", fps=30, n_frames=300, src_w=1920, src_h=1080, shots=[shot],
                       texts=[TextSample(f=f, boxes=boxes)])


def test_tracked_crop_keeps_only_text_inside_the_window():
    # 9:16 window of a 16:9 frame is 31.6% of its width; centred at 0.5 it spans 0.342-0.658
    p = plan("track", [[0.40, 0.80, 0.60, 0.90], [0.00, 0.80, 0.20, 0.90]])
    out = visible_text_boxes(p, 35, RC)
    assert len(out) == 1
    x0, y0, x1, y1 = out[0]
    assert (y0, y1) == (0.80, 0.90) and 0.15 < x0 < 0.2 and 0.8 < x1 < 0.85
    assert visible_text_boxes(p, 200, RC) == []            # no sample near that frame
    assert visible_text_boxes(plan("track", []), 30, RC) == []


def test_fit_shot_maps_y_into_the_letterboxed_foreground():
    (x0, y0, x1, y1), = visible_text_boxes(plan("fit", [[0.1, 0.0, 0.9, 1.0]]), 30, RC)
    fg = 9 / 16 * 1080 / 1920                               # foreground is 31.6% of the output height
    assert abs((y1 - y0) - fg) < 1e-6 and abs((y0 + y1) / 2 - RC.fit_y) < 1e-6 and (x0, x1) == (0.1, 0.9)


def test_split_shots_are_left_alone():
    assert visible_text_boxes(plan("split", [[0.1, 0.8, 0.9, 0.9]]), 30, RC) == []


def test_caption_moves_off_burned_in_text():
    cc = CaptionCfg()                                       # default band: bottom at 0.6 H, ~115 px tall
    assert avoid_y([], cc) is None
    assert avoid_y([(0.1, 0.10, 0.9, 0.15)], cc) is None    # text far away: default stays
    y = avoid_y([(0.1, 0.55, 0.9, 0.62)], cc)               # text under the default band -> move above it
    assert y is not None and y + cc.size * 1.25 / 2 <= 0.55 * H - cc.text_margin
    y = avoid_y([(0.1, 0.45, 0.9, 0.58)], cc)               # text just above the band -> below it, still in the safe zone
    assert y is not None and y - cc.size * 1.25 / 2 >= 0.58 * H + cc.text_margin and y <= cc.safe_bottom * H
    assert avoid_y([(0.0, 0.28, 1.0, 0.80)], cc) is None    # covered everywhere: nothing to do
    assert avoid_y([(0.1, 0.55, 0.9, 0.62)], CaptionCfg(avoid_text=False)) is None
