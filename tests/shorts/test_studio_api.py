"""Studio API: stopping jobs, settings validation, clip edits, stats, and the channel card migration."""
import json
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

from common.runner import SerialRunner
from shorts import config as config_mod, server, settings
from shorts.config import Config
from shorts.models import Clip, ClipScores, Selection, VideoMeta, save_json


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "ws"
    w.mkdir()
    monkeypatch.setattr(Config, "workspace", lambda self: w)
    local = tmp_path / "shorts.local.yaml"
    monkeypatch.setattr(config_mod, "LOCAL", local)
    return w


def _clip(cid="c1", rank=1, start=10.0, end=40.0, **kw):
    return Clip(id=cid, rank=rank, start=start, end=end, start_sentence=0, end_sentence=1, title="A title",
                hook="hook", reason="why", scores=ClipScores(hook=8, standalone=7, payoff=7, energy=6), score=7.2,
                text="text", **kw)


def _job_dir(ws, vid="vid1", clips=None):
    d = ws / vid
    d.mkdir()
    save_json(VideoMeta(id=vid, title="Video", source_url="https://youtu.be/x", file="source.mp4", duration=600,
                        width=1920, height=1080, fps=30.0), d / "meta.json")
    save_json(Selection(source="gemini", model="m", candidates=3, clips=clips or [_clip()]), d / "selection.json")
    return d


class IdleRunner(SerialRunner):
    """Queues tasks but never runs them (no pipeline work in tests)."""

    def __init__(self):
        from collections import deque
        self._q = deque()
        self._cv = threading.Condition()
        self.current = None


def _client(runner=None):
    from fastapi import FastAPI
    app = FastAPI()
    r = server.make_router(runner or IdleRunner(), None)
    app.include_router(r, prefix="/api/shorts")
    return TestClient(app), r


# ---------------------------------------------------------------- runner / stop

def test_runner_drop_removes_only_that_jobs_waiting_tasks():
    r = IdleRunner()
    r.submit("shorts", "a", "pipeline", lambda: None)
    r.submit("shorts", "b", "pipeline", lambda: None)
    r.submit("shorts", "a", "export", lambda: None)
    assert r.drop("shorts", "a", "pipeline") == 1
    assert [(t["job_id"], t["kind"]) for t in r.snapshot()["queued"]] == [("b", "pipeline"), ("a", "export")]


def test_stopping_a_queued_job_removes_it_and_pauses(ws):
    _job_dir(ws)
    c, r = _client()
    r.store.put(server.JobState(id="vid1", source="s", title="t", created_at="2026-01-01T00:00:00+00:00",
                                updated_at="x", stages=server._fresh_stages()))
    r.worker.submit("vid1")
    assert c.get("/api/shorts/jobs/vid1").json()["status"] == "queued"
    j = c.post("/api/shorts/jobs/vid1/stop").json()
    assert j["status"] == "interrupted" and not j["stopping"]
    assert c.post("/api/shorts/jobs/vid1/stop").status_code == 409  # nothing to stop now


def test_a_stop_takes_effect_at_the_next_progress_update(ws):
    store = server.JobStore(Config())
    store.put(server.JobState(id="v", source="s", title="t", created_at="x", updated_at="x",
                              stages=server._fresh_stages()))
    flag = {"stop": False}
    hooks = server.StoreHooks(store, "v", cancelled=lambda: flag["stop"])
    hooks.stage_start("download")
    report = hooks.reporter("download")
    report(0.5, "half")
    flag["stop"] = True
    with pytest.raises(server.JobCancelled):
        report(0.6)


def test_progress_is_weighted_by_stage(ws):
    stages = server._fresh_stages()
    for s in stages[:2]:  # download + transcribe done: most of a run
        s.status = "done"
    assert 0.4 < server.weighted_progress(stages) < 0.6


# ---------------------------------------------------------------- stats / clips

def test_stats_count_reviews_and_ignore_unfinished_hd_renders(ws):
    d = _job_dir(ws, clips=[_clip("c1"), _clip("c2", rank=2, start=50, end=80)])
    (d / "reviews.json").write_text(json.dumps({"c1": "approved", "c2": "approved"}))
    (d / "final").mkdir()
    (d / "final" / "c1-abc123.mp4").write_bytes(b"x")
    (d / "final" / "c2-abc123.tmp.mp4").write_bytes(b"x")  # a stopped export
    st = server.job_stats(d)
    assert (st.clips, st.approved, st.exported, st.clip_seconds) == (2, 2, 1, 60.0)


def test_edit_clip_text_saves_without_rerender_and_validates(ws):
    d = _job_dir(ws)
    c, r = _client()
    r.store.put(server.JobState(id="vid1", source="s", title="t", created_at="x", updated_at="x",
                                status="done", stages=server._fresh_stages()))
    res = c.patch("/api/shorts/jobs/vid1/clips/c1", json={"description": " new text ", "hashtags": ["#a", "b", "a", "#c-d"]})
    assert res.status_code == 200 and res.json()["rerender"] is False
    saved = json.loads((d / "selection.json").read_text())["clips"][0]
    assert saved["description"] == "new text" and saved["hashtags"] == ["#a", "#b", "#cd"]
    assert c.patch("/api/shorts/jobs/vid1/clips/c1", json={"title": "  "}).status_code == 422
    assert c.patch("/api/shorts/jobs/vid1/clips/nope", json={"title": "x"}).status_code == 404
    assert c.patch("/api/shorts/jobs/vid1/clips/c1", json={}).status_code == 422


def test_on_screen_text_cant_change_while_processing(ws):
    _job_dir(ws)
    c, r = _client()
    r.store.put(server.JobState(id="vid1", source="s", title="t", created_at="x", updated_at="x",
                                status="running", stages=server._fresh_stages()))
    assert c.patch("/api/shorts/jobs/vid1/clips/c1", json={"hook_text": "new hook"}).status_code == 409


def test_bulk_reviews_skip_unknown_clips(ws):
    d = _job_dir(ws)
    c, r = _client()
    r.store.put(server.JobState(id="vid1", source="s", title="t", created_at="x", updated_at="x",
                                status="done", stages=server._fresh_stages()))
    assert c.put("/api/shorts/jobs/vid1/reviews", json={"clip_ids": ["c1", "zz"], "review": "approved"}).json()["updated"] == 1
    assert json.loads((d / "reviews.json").read_text()) == {"c1": "approved"}


# ---------------------------------------------------------------- settings

def test_settings_are_validated_and_saved_as_overrides(ws, tmp_path):
    local = tmp_path / "shorts.local.yaml"
    out = settings.update({"captions.size": 100, "captions.text_color": "#ffaa00", "download.max_height": "1080"})
    vals = {f["key"]: f for g in out["groups"] for f in g["fields"]}
    assert vals["captions.size"]["value"] == 100 and vals["captions.size"]["custom"]
    assert yaml.safe_load(local.read_text())["captions"]["text_color"] == "#FFAA00"
    assert yaml.safe_load(local.read_text())["download"]["max_height"] == 1080
    for bad in ({"captions.size": 5}, {"captions.text_color": "red"}, {"nope.x": 1}, {"captions.enabled": "yes"},
                {"select.min_clip_s": 60, "select.max_clip_s": 40}):
        with pytest.raises(settings.SettingsError):
            settings.update(bad)
    settings.update({"captions.size": None})  # back to shorts.yaml
    assert "size" not in (yaml.safe_load(local.read_text()).get("captions") or {})


def test_legacy_channel_card_is_migrated_once(ws, tmp_path, monkeypatch):
    cfgdir = tmp_path / "configs"
    cfgdir.mkdir()
    (cfgdir / "anime.local.yaml").write_text(yaml.safe_dump({"card": {"name": "Me", "handle": "@me", "avatar": "missing.png"}}))
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    local = tmp_path / "shorts.local.yaml"
    assert config_mod.migrate_legacy_card(local)
    card = yaml.safe_load(local.read_text())["card"]
    assert card["name"] == "Me" and card["avatar"] is None
    assert not config_mod.migrate_legacy_card(local)  # already there
