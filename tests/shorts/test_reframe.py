import numpy as np

from shorts.config import ReframeCfg
from shorts.models import FaceSample
from shorts.stages.reframe import classify, crop_path, crop_width, plan_shots

RC = ReframeCfg()
CW = crop_width(2560, 1440)  # 0.316 of the width


def face(cx, w=0.08, cy=0.3, h=0.15, score=0.9):
    return [cx, cy, w, h, score]


def test_crop_width_16x9():
    assert abs(CW - 0.3164) < 1e-3
    assert crop_width(1080, 1920) == 1.0  # already vertical


def test_classify_single_group_dominant_spread_and_tiny():
    assert classify([face(0.2)], CW, RC) == ("track", 0.2)
    kind, x = classify([face(0.4), face(0.55)], CW, RC)  # two people close together: frame both
    assert kind == "track" and abs(x - 0.475) < 1e-9
    assert classify([face(0.1, w=0.2, h=0.3), face(0.8)], CW, RC)[0] == "track"  # one clearly dominates
    assert classify([face(0.1), face(0.8)], CW, RC) == ("fit", None)            # equal, far apart
    assert classify([face(0.5, h=0.02)], CW, RC) == ("none", None)              # crowd-size face ignored


def samples(frames_faces):
    return [FaceSample(f=f, faces=fs) for f, fs in frames_faces]


def test_shot_modes_and_jump_at_cut():
    ss = samples([(0, [face(0.2)]), (5, [face(0.21)]),                 # shot 1: subject on the left
                  (10, [face(0.8)]), (15, [face(0.8)]),                # shot 2: subject on the right
                  (20, []), (25, []),                                  # shot 3: no faces
                  (30, [face(0.1), face(0.9)]), (35, [face(0.1), face(0.9)])])  # shot 4: two far apart
    shots = plan_shots([0, 10, 20, 30], 40, ss, 30, CW, RC)
    assert [s.mode for s in shots] == ["track", "track", "center", "fit"]
    assert shots[0].x[-1] < 0.3 and shots[1].x[0] > 0.7                # hard jump at the cut, no drift
    assert len(set(shots[0].x)) == 1 and shots[0].why == "subject locked"  # tiny movement -> locked
    assert [len(s.x) if s.x else s.end - s.start for s in shots] == [10, 10, 10, 10]


def test_crop_path_smooth_speed_limited_and_clamped():
    n, fps = 90, 30
    path = crop_path([0, 30, 60, 89], [0.3, 0.3, 0.7, 0.7], n, fps, CW, RC)
    steps = np.abs(np.diff(path))
    assert len(path) == n
    assert steps.max() <= RC.max_speed / fps + 1e-4     # never faster than max_speed
    assert path[0] < path[-1]
    edge = crop_path([0, 89], [0.0, 0.02], n, fps, CW, RC)  # subject at the very edge
    assert min(edge) >= CW / 2 - 1e-9                   # window never leaves the frame


def test_scoreboard_headshots_are_ignored():
    rng = np.random.default_rng(0)
    board = [face(0.055, w=0.046, cy=0.89, h=0.104), face(0.942, w=0.046, cy=0.89, h=0.1)]
    ss = []
    for f in range(0, 120, 5):
        real = face(0.3 + 0.4 * (f >= 60) + rng.normal(0, 0.01), cy=0.4 + rng.normal(0, 0.01))
        ss.append(FaceSample(f=f, faces=[*board, real]))
    from shorts.stages.reframe import find_overlays
    overlays = find_overlays(ss, [0, 30, 60, 90])
    assert len(overlays) == 2
    shots = plan_shots([0, 30, 60, 90], 120, ss, 30, CW, RC, overlays)
    assert [s.mode for s in shots] == ["track"] * 4  # without the fix: all "fit"


def test_static_real_face_in_one_shot_is_not_an_overlay():
    from shorts.stages.reframe import find_overlays
    ss = [FaceSample(f=f, faces=[face(0.5)]) for f in range(0, 100, 5)]
    assert find_overlays(ss, [0]) == []  # a locked-off talking head with no cuts stays tracked


def test_cut_score_separates_cut_from_motion():
    from shorts.stages.reframe import cut_score
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, (36, 64, 3), dtype=np.uint8)
    shifted = np.roll(a, 1, axis=1)                    # small pan: same scene
    other = rng.integers(0, 255, (36, 64, 3), dtype=np.uint8)
    g = lambda x: x.mean(axis=2).astype(np.float32)    # noqa: E731
    assert cut_score(a, a, g(a), g(a)) < 0.01
    assert cut_score(other, a, g(other), g(a)) > 0.25
