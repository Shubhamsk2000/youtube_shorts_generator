"""Live-stream snapshot download and the gameplay PIP layout."""
import re
import time

from shorts.config import CaptionCfg, HookCfg, ReframeCfg
from shorts.models import FaceSample, Shot
from shorts.stages.captions import H, to_ass
from shorts.stages.download import _live_elapsed, _snapshot_live, _stop_at_now
from shorts.stages.reframe import pip_bottom, pip_geometry, screen_crop

RC = ReframeCfg()


def fake_live(polls):
    """Mimics yt-dlp's _live_adaptive_fragments: each poll yields known_idx..head-1, all carrying
    fragment_count=head (the X-Head-Seqnum + 1 at that poll)."""
    def gen(ctx):
        known = 0
        for head in polls:
            for sq in range(known, head):
                yield {"url": f"https://x.googlevideo.com/videoplayback?itag=1&sq={sq}", "fragment_count": head}
            known = head
    return gen


def sqs(frags):
    return [int(f["url"].rsplit("=", 1)[1]) for f in frags]


def test_stop_at_now_ends_at_live_edge():
    got = sqs(_stop_at_now(fake_live([100, 103, 106]))({}))
    assert got == list(range(100))


def test_stop_at_now_last_n():
    got = sqs(_stop_at_now(fake_live([100, 103]), 30)({}))
    assert got == list(range(70, 100))


def test_snapshot_live_uses_fragment_length():
    fmt = {"fragments": fake_live([1000, 1010]), "target_duration": 2.0}
    _snapshot_live({"requested_formats": [fmt]}, last_s=30 * 60)
    got = sqs(fmt["fragments"]({}))
    assert len(got) == 900 and got[-1] == 999  # 30 min of 2 s fragments, ending at "now"


def test_live_elapsed():
    now = time.time()
    assert abs(_live_elapsed({"release_timestamp": now - 3600}) - 3600) < 5
    assert _live_elapsed({"release_timestamp": now - 3600}, 1800) == 1800
    assert _live_elapsed({"release_timestamp": now - 600}, 1800) <= 601  # stream shorter than the window


def cam_shot(cams, fx=None):
    return Shot(start=0, end=100, mode="split", cams=cams,
                face_x=fx or [0.5] * len(cams), face_y=[0.45] * len(cams))


def test_screen_crop_avoids_cam():
    for cam in ([0.0, 0.6, 0.22, 1.0], [0.78, 0.0, 1.0, 0.35], [0.7, 0.65, 0.95, 0.98]):
        l, t, r, b = screen_crop([cam], 2560, 1440, 1080 / 1920)
        assert abs((r - l) / (b - t) - 9 / 16) < 0.01
        assert r <= cam[0] * 2560 + 1 or l >= cam[2] * 2560 - 1, cam  # the cam is not shown twice


def test_pip_geometry_corner_and_size():
    shot = cam_shot([[0.78, 0.68, 1.0, 1.0]])  # cam bottom-right in the source
    screen, cams = pip_geometry(shot, 2560, 1440, 1080, 1920, RC)
    (src, (x, y, w, h)), = cams
    assert x + w > 1080 / 2 and y == round(RC.pip_top * 1920)  # top-right corner
    assert w <= RC.pip_width * 1080 + 2 and h <= RC.pip_max_h * 1920 + 2
    assert x + w <= 1080 and x >= 0
    shot2 = cam_shot([[0.0, 0.0, 0.2, 0.3], [0.05, 0.6, 0.25, 0.9]])
    _, cams2 = pip_geometry(shot2, 2560, 1440, 1080, 1920, RC)
    assert {c[1][0] < 540 for c in cams2} == {True, False}  # two cams take both corners


def test_hook_goes_below_corner_cam():
    shot = cam_shot([[0.78, 0.68, 1.0, 1.0]])
    bottom = pip_bottom(shot, 2560, 1440, RC)
    hc = HookCfg()
    ass = to_ass([], CaptionCfg(), "THIS IS THE HOOK", hc, hook_min_y=bottom)
    y = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
    assert y - hc.size * 1.25 / 2 > bottom  # top of the hook box clears the cam
    assert y < H / 2


def _synthetic_stream(n=120, H=360, W=640, box=(25, 152, 128, 232), static_frac=0.7, seed=0):
    """Edge maps of a stream: a fixed cam overlay (no drawn border; its left edge fades in every other
    frame) over gameplay that holds still for most of the clip (a menu / a player standing), then moves."""
    import numpy as np

    rng = np.random.default_rng(seed)
    l, t, r, b = box
    static = np.zeros((H, W), bool)
    for _ in range(300):  # a still scene: ~15% of pixels are steady edges, as in real footage
        y, x = rng.integers(0, H), rng.integers(0, W - 80)
        static[y, x:x + 80] = True
        x2, y2 = rng.integers(0, W), rng.integers(0, H - 60)
        static[y2:y2 + 60, x2] = True
    static[t - 1:b + 2, l - 1:r + 2] = False
    edges, samples = [], []
    for i in range(n):
        f = i * 10
        if i < static_frac * n:
            m = static.copy()
        else:
            m = rng.random((H, W)) < 0.04  # moving gameplay: edges everywhere, nowhere steady
        m[t:b, l:r] = False
        # the cam's sides; edge detection finds no line along the frame's own edge
        if t > 0:
            m[t, l:r] = True
        if b < H - 1:
            m[b, l:r] = True
        if r < W - 1:
            m[t:b, r] = True
        if l > 0 and i % 2 == 0:
            m[t:b, l] = True
        m[t + 18:t + 24, l + 35:l + 70] = True  # the streamer's room behind the face: static detail inside the cam
        edges.append((f, np.packbits(m)))
        # the streamer sits left of centre, small in the cam (far from the camera)
        samples.append(FaceSample(f=f, faces=[[(l + 0.4 * (r - l)) / W, (t + b) / 2 / H - 0.02, 0.035, 0.075, 0.9]]))
    return edges, samples


def test_cam_overlay_found_over_still_gameplay():
    from shorts.stages.reframe import detect_cams

    edges, samples = _synthetic_stream()
    cuts, cams = detect_cams(edges, (360, 640), samples, [0], [], RC)
    assert cams, "cam overlay not found"
    covered = sum(((cuts + [1200])[k + 1] - c) for k, c in enumerate(cuts) if k in cams)
    assert covered >= 0.95 * 1200  # on screen the whole clip: no flicker between layouts
    (x0, y0, x1, y1), = {tuple(b) for v in cams.values() for b in v}
    assert abs(x0 - 25 / 640) < 0.01 and abs(y0 - 152 / 360) < 0.015 and abs(x1 - 128 / 640) < 0.01
    assert abs(y1 - 232 / 360) < 0.015


def test_no_cam_in_still_footage_without_overlay():
    """A still scene with a face and no moving content anywhere is not a facecam stream."""
    from shorts.stages.reframe import detect_cams

    edges, samples = _synthetic_stream(static_frac=1.0)
    _, cams = detect_cams(edges, (360, 640), samples, [0], [], RC)
    assert not cams


def _cam_share(cuts, cams, n=1200):
    return sum(((cuts + [n])[k + 1] - c) for k, c in enumerate(cuts) if k in cams) / n


def test_corner_cam_flush_with_the_frame_edges():
    """A cam tucked into the top-left corner has only two drawn sides (right, bottom)."""
    from shorts.stages.reframe import detect_cams

    edges, samples = _synthetic_stream(box=(0, 0, 163, 92))
    cuts, cams = detect_cams(edges, (360, 640), samples, [0], [], RC)
    assert _cam_share(cuts, cams) >= 0.95
    (x0, y0, x1, y1), = {tuple(b) for v in cams.values() for b in v}
    assert x0 < 0.01 and y0 < 0.01 and abs(x1 - 163 / 640) < 0.01 and abs(y1 - 92 / 360) < 0.015


def test_cam_from_the_videos_other_clips():
    """A clip that is a still screen all along (a map) can't find the cam alone; the cam found in the
    video's other clips is used if this clip confirms it, and a hint elsewhere changes nothing."""
    from shorts.stages.reframe import detect_cams

    edges, samples = _synthetic_stream(box=(0, 0, 163, 92), static_frac=1.0)
    assert detect_cams(edges, (360, 640), samples, [0], [], RC)[1] == {}
    cuts, cams = detect_cams(edges, (360, 640), samples, [0], [], RC, [[0.0, 0.0, 0.2547, 0.2556]])
    assert _cam_share(cuts, cams) >= 0.95
    assert detect_cams(edges, (360, 640), samples, [0], [], RC, [[0.6, 0.6, 0.9, 0.9]])[1] == {}
