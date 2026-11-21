"""Window sizes of the cam-on-top layout set in the editor: each window centred in its place over blur."""
import numpy as np
import pytest

from shorts.config import ReframeCfg
from shorts.models import ReframePlan, Shot, VideoMeta
from shorts.stages import framing
from shorts.stages.framing import Framing, SplitSize
from shorts.stages.render import compose_split, split_boxes, split_geometry

cv2 = pytest.importorskip("cv2")
META = VideoMeta(id="v", title="v", duration=100, width=1920, height=1080, fps=30, file="s.mp4")
RC = ReframeCfg(screen_layout="split")


def cam_shot(**kw):
    return Shot(start=0, end=90, mode="split", cams=[[0.75, 0.05, 0.98, 0.35]], face_x=[0.5], face_y=[0.45], **kw)


def test_sizes_place_each_window_centred():
    s = cam_shot(split=[0.3, 0.6, 0.8, 0.5])
    (tx, tw), (gx, gy, gw, gh) = split_boxes(s, 1080, 1920, 576)
    assert (tx, tw) == (216, 648)                       # 60% wide, centred
    assert (gw, gh) == (864, 672) and gx == 108         # 80% of the width, 50% of the 1344 px area
    assert gy == 576 + (1344 - 672) // 2                # centred in the area under the cam


def test_crops_take_their_windows_shape():
    s = cam_shot(split=[0.3, 0.6, 0.8, 0.5])
    panel_h, cams, (gl, gt, gr, gb) = split_geometry(s, 1920, 1080, 1080, 1920, META, RC)
    assert panel_h == 576
    a, b, c, d = cams[0]
    assert abs((c - a) / (d - b) - 648 / 576) < 0.02
    assert abs((gr - gl) / (gb - gt) - 864 / 672) < 0.02


def test_unsized_layout_is_unchanged():
    s = cam_shot()
    assert split_boxes(s, 1080, 1920, 500) == ((0, 1080), (0, 500, 1080, 1420))


def test_sized_compose_fills_the_rest_with_blur_and_keeps_the_size():
    frame = np.full((1080, 1920, 3), 200, np.uint8)
    frame[:, :960] = 40  # left half dark, so the blurred fill differs from the windows
    out = compose_split(frame, cam_shot(split=[0.25, 0.5, 0.7, 0.6]), 540, 960, META, RC, cv2)
    assert out.shape == (960, 540, 3)
    assert not np.array_equal(out[5, 2], out[5, 270])   # outside the cam window: blur, not the cam


def test_sizes_apply_to_every_facecam_shot_only():
    plan = ReframePlan(key="k", fps=30, n_frames=180, src_w=1920, src_h=1080,
                       shots=[cam_shot(), Shot(start=90, end=180, mode="center", x=[0.5] * 90)])
    out = framing.apply(plan, Framing(split=SplitSize(cam_h=0.3, cam_w=0.7)))
    assert out.shots[0].split == [0.3, 0.7, 1.0, 1.0] and out.shots[1].split == []


def test_sizes_are_clamped_and_saved(tmp_path):
    from shorts.models import Clip
    c = Clip(id="c1", rank=1, start=0, end=10, start_sentence=0, end_sentence=1, title="t", hook="h", reason="r",
             scores={"hook": 7, "standalone": 7, "payoff": 7, "energy": 7}, score=7, text="x")
    sz = framing.clean_split(SplitSize(cam_h=0.95, cam_w=0.1, game_w=2, game_h=0.5))
    assert (sz.cam_h, sz.cam_w, sz.game_w, sz.game_h) == (0.7, 0.25, 1.0, 0.5)
    framing.save(tmp_path, c, Framing(split=sz))
    assert framing.load(tmp_path, c).split == sz
