"""Studio picks up jobs the CLI made, without clobbering ones it is working on."""
import time

from shorts import server
from shorts.config import Config
from shorts.models import VideoMeta, save_json


def _cli_job(ws, vid="vid1"):
    d = ws / vid
    d.mkdir(parents=True)
    save_json(VideoMeta(id=vid, title="t", source_url=None, file="source.mp4", duration=10.0,
                        width=1920, height=1080, fps=30.0), d / "meta.json")
    return d


def test_rescan_finds_new_cli_job_and_keeps_taken_over_state(tmp_path, monkeypatch):
    cfg = Config()
    monkeypatch.setattr(Config, "workspace", lambda self: tmp_path)
    store = server.JobStore(cfg)
    assert store.all() == []
    _cli_job(tmp_path)
    store._last_scan = 0
    store.rescan()
    (job,) = store.all()
    assert job.id == "vid1"
    # the server takes the job over (e.g. an export): it now has job.json and live state in memory
    store.update("vid1", status="running")
    assert (tmp_path / "vid1" / server.JOB_FILE).exists()
    store._last_scan = 0
    store.rescan()
    assert store.get("vid1").status == "running"  # not reset to "interrupted" from disk
