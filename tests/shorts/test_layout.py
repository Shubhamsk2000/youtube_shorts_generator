"""Per-video layout: stacked cam size, smart per-shot framing, tweet-card canvas, overrides."""
import json

import numpy as np

from shorts.config import Config, ReframeCfg, with_job_layout
from shorts.models import Clip, ReframePlan, Shot, VideoMeta
from shorts.stages.canvas import Canvas, canvas
from shorts.stages.reframe import effective_plan, split_panel_frac
from shorts.stages.render import Framer

CAM = [0.75, 0.0, 1.0, 0.3]  # a 16:9-ish facecam in the top-right corner of a 16:9 stream


def cam_shot(a, b, cams=None):
    return Shot(start=a, end=b, mode="split", cams=cams or [CAM], face_x=[0.5], face_y=[0.45], why="cam")


def plan(shots, hints=None):
    return ReframePlan(key="k", fps=30, n_frames=shots[-1].end, src_w=1920, src_h=1080, shots=shots,
                       cam_hints=hints or [])


def clip():
    return Clip(id="c1", rank=1, start=0, end=10, start_sentence=0, end_sentence=1, title="t", hook="h",
                reason="r", scores={"hook": 7, "standalone": 7, "payoff": 7, "energy": 7}, score=7, text="x",
                hook_text="nobody saw this coming")


def test_cam_panel_stays_in_the_chosen_range():
    rc = ReframeCfg(split_cam_min=0.2, split_cam_max=0.3)
    # a wide cam wants a short panel, a tall one a high panel: both clamped to 20-30%
    assert split_panel_frac([[0, 0, 0.5, 0.1]], 1920, 1080, rc) == 0.2
    assert split_panel_frac([[0, 0, 0.2, 0.5]], 1920, 1080, rc) == 0.3
    fixed = ReframeCfg(split_cam_min=0.25, split_cam_max=0.25)
    assert split_panel_frac([CAM], 1920, 1080, fixed) == 0.25


def test_smart_layout_shows_gameplay_only_shots_whole_in_a_stream():
    p = plan([cam_shot(0, 90), Shot(start=90, end=300, mode="center", x=[0.5] * 210)])
    eff = effective_plan(p, ReframeCfg(screen_layout="split"))
    assert [s.mode for s in eff.shots] == ["split", "fit"]
    assert p.shots[1].mode == "center"  # the saved analysis is untouched
    off = effective_plan(p, ReframeCfg(screen_layout="split", smart_layout=False))
    assert off.shots[1].mode == "center"


def test_smart_layout_leaves_non_streams_alone():
    p = plan([Shot(start=0, end=90, mode="center", x=[0.5] * 90),
              Shot(start=90, end=200, mode="track", x=[0.4] * 110)])
    assert effective_plan(p, ReframeCfg(screen_layout="split")).shots == p.shots


def test_smart_layout_keeps_the_cam_through_a_blip():
    p = plan([cam_shot(0, 90), Shot(start=90, end=110, mode="center", x=[0.5] * 20), cam_shot(110, 300)])
    eff = effective_plan(p, ReframeCfg(screen_layout="split"))
    assert [s.mode for s in eff.shots] == ["split", "split", "split"]
    assert (eff.shots[1].start, eff.shots[1].end) == (90, 110)


def test_job_layout_overrides(tmp_path):
    cfg = Config()
    assert with_job_layout(cfg, tmp_path) is cfg
    (tmp_path / "layout.json").write_text(json.dumps(
        {"screen_layout": "split", "split_cam_min": 0.25, "split_cam_max": 0.25, "background": "tweet",
         "unknown": 1}))
    rc = with_job_layout(cfg, tmp_path).reframe
    assert (rc.screen_layout, rc.split_cam_min, rc.background) == ("split", 0.25, "tweet")


def test_tweet_canvas_window_fits_on_screen():
    cfg = Config(reframe=ReframeCfg(background="tweet"))
    cv = canvas(cfg, 1080, 1920, clip())
    x, y, w, h = cv.win
    assert cv.framed and w > 800 and h >= 0.4 * w
    assert y > 0.09 * 1920 and y + h < 0.8 * 1920
    assert not canvas(Config(), 1080, 1920, clip()).framed


def test_framer_draws_layout_into_the_window():
    import cv2

    meta = VideoMeta(id="v", title="t", file="v.mp4", duration=10, width=320, height=180, fps=30)
    frame = np.full((180, 320, 3), 200, np.uint8)
    cv = Canvas(540, 960, (40, 200, 460, 400))
    out = Framer(cv, meta, ReframeCfg(screen_layout="split"), cv2)(frame, cam_shot(0, 10), 0)
    assert out.shape == (960, 540, 3)
    assert out[400, 270].tolist() == [200, 200, 200]   # inside the window: the video
    assert out[50, 10].max() < 200                      # outside: the dimmed blur
