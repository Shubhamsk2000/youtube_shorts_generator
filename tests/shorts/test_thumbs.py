"""Clip card thumbnails: one still per rendered preview, cached, older renders' stills removed."""
import shutil
import subprocess

import pytest

from shorts.server import _thumb

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _preview(path, secs):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=540x960:rate=30:duration={secs}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def test_thumbnail_is_made_once_and_replaces_an_older_render(tmp_path):
    (tmp_path / "clips").mkdir()
    old, new = tmp_path / "clips" / "abc123-aaaaaa.mp4", tmp_path / "clips" / "abc123-bbbbbb.mp4"
    _preview(old, 2)
    _preview(new, 0.5)  # shorter than the 1 s the still is taken at: its first frame instead
    t_old = _thumb(tmp_path, old)
    assert t_old.exists() and t_old.read_bytes()[:2] == b"\xff\xd8"
    t_new = _thumb(tmp_path, new)
    assert t_new.exists() and t_new.stat().st_size > 0 and not t_old.exists()
    mtime = t_new.stat().st_mtime_ns
    assert _thumb(tmp_path, new).stat().st_mtime_ns == mtime  # cached
