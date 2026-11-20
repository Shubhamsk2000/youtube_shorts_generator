"""Manual framing from the clip editor: keys -> per-frame window, wide windows over blur, cache + captions."""
import numpy as np
import pytest

from shorts.config import Config, ReframeCfg
from shorts.models import Clip, ReframePlan, Shot, TextSample, VideoMeta, save_json
from shorts.stages import framing
from shorts.stages.framing import Framing, Key
from shorts.stages.reframe import plan_path, visible_text_boxes
from shorts.stages.render import clip_file, compose, wide_band

cv2 = pytest.importorskip("cv2")


def clip():
    return Clip(id="c1", rank=1, start=0, end=10, start_sentence=0, end_sentence=1, title="t", hook="h",
                reason="r", scores={"hook": 7, "standalone": 7, "payoff": 7, "energy": 7}, score=7, text="x")


def plan(shots):
    return ReframePlan(key="k", fps=30, n_frames=shots[-1].end, src_w=1920, src_h=1080, shots=shots)


def meta():
    return VideoMeta(id="v", title="v", duration=100, width=1920, height=1080, fps=30, file="source.mp4")


def test_keys_are_sorted_deduped_and_clamped():
    keys = framing.clean([Key(f=30, x=0.0, a=0.3), Key(f=0, x=0.5, a=9.0), Key(f=30, x=0.99, a=1.0)],
                         300, 1920, 1080)
    assert [k.f for k in keys] == [0, 30]
    assert keys[0].a == pytest.approx(16 / 9, abs=1e-3)   # never wider than the whole frame
    assert keys[0].x == 0.5                                 # the whole frame can only be centred
    half = 1.0 * 1080 / 1920 / 2
    assert keys[1].x == pytest.approx(1 - half, abs=1e-4)   # the last key on frame 30 wins, kept inside the frame
    with pytest.raises(ValueError):
        framing.clean([Key(f=300, x=0.5)], 300, 1920, 1080)


def test_keys_ease_between_and_hold_outside():
    xs, as_ = framing.interpolate([Key(f=10, x=0.3, a=0.5625), Key(f=20, x=0.7, a=1.0)], range(0, 30))
    assert xs[0] == xs[10] == 0.3 and xs[25] == 0.7
    assert xs[15] == pytest.approx(0.5, abs=1e-3)          # half way
    assert xs[11] - xs[10] < xs[15] - xs[14]               # eased: slow start
    assert as_[29] == 1.0


def test_only_shots_with_keys_become_manual():
    p = plan([Shot(start=0, end=90, mode="track", x=[0.4] * 90), Shot(start=90, end=300, mode="fit")])
    out = framing.apply(p, Framing(keys=[Key(f=120, x=0.6, a=0.8125)]))
    assert out.shots[0] == p.shots[0]
    s = out.shots[1]
    assert s.mode == "manual" and len(s.x) == len(s.a) == 210 and set(s.x) == {0.6}
    assert framing.apply(p, None) is p


def test_wide_window_is_full_width_over_blur():
    rc = ReframeCfg()
    frame = np.full((1080, 1920, 3), 200, np.uint8)
    frame[:, :200] = 0  # a dark strip on the far left: outside a centred 13:16 window
    shot = Shot(start=0, end=10, mode="manual", x=[0.5] * 10, a=[13 / 16] * 10)
    out = compose(frame, shot, 0, 1080, 1920, None, rc, cv2)
    assert out.shape == (1920, 1080, 3)
    y, h = wide_band(13 / 16, 1080, 1920, rc)
    assert h == pytest.approx(1080 / (13 / 16), abs=2)
    assert out[y + h // 2, 540].mean() == pytest.approx(200, abs=3)   # the window itself
    assert out[5, 540].mean() < 150                                    # blurred + dimmed above it
    # 9:16 still fills the screen
    full = compose(frame, shot.model_copy(update={"a": [9 / 16] * 10}), 0, 1080, 1920, None, rc, cv2)
    assert full[5, 540].mean() == pytest.approx(200, abs=3)


def test_burned_in_text_maps_through_a_wide_window():
    rc = ReframeCfg()
    p = plan([Shot(start=0, end=30, mode="manual", x=[0.5] * 30, a=[16 / 9] * 30)])
    p.texts = [TextSample(f=0, boxes=[[0.4, 0.8, 0.6, 0.9]])]
    (x0, y0, x1, y1), = visible_text_boxes(p, 0, rc)
    assert (x0, x1) == pytest.approx((0.4, 0.6))
    fg = 9 / 16 / (16 / 9)  # the whole frame is this share of the output height
    top = rc.fit_y - fg / 2
    assert y0 == pytest.approx(top + 0.8 * fg) and y1 == pytest.approx(top + 0.9 * fg)


def test_load_plan_and_cache_key(tmp_path):
    cfg, c = Config(), clip()
    p = plan([Shot(start=0, end=300, mode="track", x=[0.4] * 300)])
    plan_path(tmp_path, c).parent.mkdir()
    save_json(p, plan_path(tmp_path, c))
    before = clip_file(tmp_path, c, cfg)
    assert framing.load_plan(tmp_path, c, cfg, meta()).shots[0].mode == "track"
    framing.save(tmp_path, c, Framing(keys=[Key(f=0, x=0.55, a=1.0)]))
    assert framing.load_plan(tmp_path, c, cfg, meta()).shots[0].mode == "manual"
    assert clip_file(tmp_path, c, cfg) != before                    # a save re-renders the clip
    framing.save(tmp_path, c, Framing())                            # reset to auto
    assert not framing.framing_path(tmp_path, c).exists() and clip_file(tmp_path, c, cfg) == before


def test_manual_keys_without_an_analysis():
    cfg = Config()
    cfg.reframe.enabled = False
    c = clip()
    out = framing.apply(framing.base_plan(meta(), c), Framing(keys=[Key(f=0, x=0.3)]))
    assert out.shots[0].mode == "manual" and out.shots[0].end == 300


def test_a_key_without_x_widens_but_keeps_tracking():
    path = [0.1 + 0.001 * i for i in range(300)]
    p = plan([Shot(start=0, end=300, mode="track", x=path)])
    s = framing.apply(p, Framing(keys=[Key(f=0, x=None, a=0.8125)])).shots[0]
    assert s.mode == "manual" and set(s.a) == {0.8125}
    half = 0.8125 * 1080 / 1920 / 2
    assert s.x[200] == pytest.approx(max(path[200], half), abs=1e-4)  # still follows the face path
    assert s.x[0] == pytest.approx(half, abs=1e-4)                   # pushed inward: the wider window must fit


CAM = [0.75, 0.05, 0.98, 0.35]  # facecam in the top right of the source


def cam_shot(a=0, b=90):
    return Shot(start=a, end=b, mode="split", cams=[CAM], face_x=[0.5], face_y=[0.45], why="cam")


def test_keys_in_a_facecam_shot_keep_the_cam_by_default():
    p = plan([cam_shot()])
    s = framing.apply(p, Framing(keys=[Key(f=0, x=None, a=1.0)])).shots[0]
    assert s.mode == "split" and s.cams == [CAM]             # the facecam layout stays
    assert s.x is None and set(s.a) == {1.0}                 # only the shape: automatic position
    s = framing.apply(p, Framing(keys=[Key(f=0, x=0.3, a=1.0)])).shots[0]
    assert s.mode == "split" and set(s.x) == {0.3}
    # "edit whole short": the window replaces the layout, as before
    s = framing.apply(p, Framing(keys=[Key(f=0, x=0.3, a=1.0)], keep_cam=False)).shots[0]
    assert s.mode == "manual" and not s.cams


def test_kept_cam_render_uses_the_window_for_the_gameplay_only():
    frame = np.full((1080, 1920, 3), 200, np.uint8)
    frame[:, :300] = 0                                       # dark left edge: only a wide window shows it
    m = meta()
    for layout in ("split", "pip"):
        rc = ReframeCfg(screen_layout=layout)
        auto = compose(frame, cam_shot(), 0, 1080, 1920, m, rc, cv2)
        wide = cam_shot().model_copy(update={"x": [0.5] * 90, "a": [16 / 9] * 90})
        out = compose(frame, wide, 0, 1080, 1920, m, rc, cv2)
        assert out.shape == (1920, 1080, 3)
        assert out[1150, 5].mean() < 20                      # the whole frame: its dark edge is in view
        assert auto[1150, 5].mean() > 150                    # the automatic crop doesn't reach it
        if layout == "split":
            assert np.array_equal(out[:200], auto[:200])     # the cam panel is untouched


def test_framing_file_keeps_the_choice(tmp_path):
    c = clip()
    framing.save(tmp_path, c, Framing(keys=[Key(f=0, x=0.5)], keep_cam=False))
    assert framing.load(tmp_path, c).keep_cam is False
