"""Live-stream snapshot download and the gameplay PIP layout."""
import re
import time

from shorts.config import CaptionCfg, HookCfg, ReframeCfg
from shorts.models import Shot
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
