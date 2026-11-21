"""The project's activity log (the Pipeline console)."""
from shorts import joblog


def test_entries_are_numbered_and_read_after(tmp_path):
    joblog.log(tmp_path, "Download started", stage="download")
    joblog.log(tmp_path, "Download done", "ok", "download")
    r = joblog.read(tmp_path)
    assert [e["msg"] for e in r["entries"]] == ["Download started", "Download done"] and r["last"] == 1
    assert joblog.read(tmp_path, after=0)["entries"][0]["level"] == "ok"
    assert joblog.read(tmp_path, after=1) == {"entries": [], "last": 1, "truncated": False}


def test_progress_that_only_moves_its_numbers_is_rate_limited(tmp_path):
    for pct in range(10):
        joblog.progress(tmp_path, "download", f"Downloading {pct}% at 3.1 MB/s")
    joblog.progress(tmp_path, "download", "Merging audio and video")
    assert [e["msg"] for e in joblog.read(tmp_path)["entries"]] == ["Downloading 0% at 3.1 MB/s", "Merging audio and video"]


def test_levels_from_the_words():
    assert joblog.level_of("nvidia: nemotron unavailable (HTTP 503), trying gemini: gemini-3.8-flash…") == "warn"
    assert joblog.level_of("Editor pass failed (timeout); keeping first-pass picks") == "warn"
    assert joblog.level_of("Download failed: video is private") == "error"
    assert joblog.level_of("Part 2/12: 13 moments from nemotron in 94s") == "info"


def test_the_sequence_continues_after_a_restart(tmp_path):
    joblog.log(tmp_path, "a")
    joblog._next.clear()  # a new server process
    joblog.log(tmp_path, "b")
    assert [e["i"] for e in joblog.read(tmp_path)["entries"]] == [0, 1]
