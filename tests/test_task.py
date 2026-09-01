"""DownloadTask model: ordering, percent, persistence, crash recovery."""
import json

import task as T


def test_percent_from_bytes():
    t = T.DownloadTask("u", "p")
    t.total_size = 1000
    t.downloaded = 250
    assert t.percent == 25


def test_percent_zero_when_unknown():
    t = T.DownloadTask("u", "p")
    assert t.percent == 0


def test_percent_prefers_segments_for_hls():
    t = T.DownloadTask("u", "p")
    t.seg_total = 10
    t.seg_done = 5
    assert t.percent == 50          # segment-based, ignores total_size==0
    t.total_size = 999_999
    assert t.percent == 50          # still segment-based for HLS


def test_to_from_dict_roundtrip():
    t = T.DownloadTask("https://x/f.zip", "C:/t/f.zip", filename="f.zip",
                       speed_limit=512000)
    t.total_size = 4096
    t.downloaded = 2048
    t.segments = [T.Segment(0, 0, 2047), T.Segment(1, 2048, 4095)]
    t.segments[0].downloaded = 2048
    t.segments[1].downloaded = 0
    d = json.loads(json.dumps(t.to_dict()))
    r = T.DownloadTask.from_dict(d)
    assert r.url == t.url and r.filename == "f.zip"
    assert r.total_size == 4096 and r.speed_limit == 512000
    assert r.id == t.id
    assert [(s.start, s.end, s.downloaded) for s in r.segments] == \
           [(0, 2047, 2048), (2048, 4095, 0)] and \
           [s.index for s in r.segments] == [0, 1]


def test_inflight_restores_as_paused():
    """A DOWNLOADING task comes back PAUSED and flagged for auto-resume. QUEUED
    is left alone — it was never mid-transfer, so the scheduler can pick it up."""
    d = T.DownloadTask("u", "p").to_dict()
    d["status"] = T.DOWNLOADING
    r = T.DownloadTask.from_dict(d)
    assert r.status == T.PAUSED
    assert getattr(r, "_auto_resume", False) is True

    d2 = T.DownloadTask("u", "p").to_dict()
    d2["status"] = T.QUEUED
    assert T.DownloadTask.from_dict(d2).status == T.QUEUED


def test_terminal_status_unchanged_on_restore():
    for s in (T.PAUSED, T.COMPLETED, T.ERROR, T.CANCELLED):
        d = T.DownloadTask("u", "p").to_dict()
        d["status"] = s
        assert T.DownloadTask.from_dict(d).status == s


def test_hls_seg_fields_persist_for_resume():
    """seg_total / seg_done round-trip through to_dict/from_dict so
    HlsDownloader can resume past already-fetched segments after a restart
    instead of restarting from segment 0."""
    t = T.DownloadTask("u", "p")
    t.seg_total = 10
    t.seg_done = 4
    r = T.DownloadTask.from_dict(json.loads(json.dumps(t.to_dict())))
    assert r.seg_total == 10 and r.seg_done == 4


def test_hls_seg_fields_default_zero_when_missing():
    """Old saved state without seg_total/seg_done loads cleanly as 0/0."""
    r = T.DownloadTask.from_dict({"url": "u", "save_path": "p"})
    assert r.seg_total == 0 and r.seg_done == 0


def test_added_unknown_for_legacy_tasks():
    """Legacy state (pre-`added` field) restores with added=0 — NOT time.time().
    humanize_age renders 0 as an empty cell, not a fake "just now"."""
    r = T.DownloadTask.from_dict({"url": "u", "save_path": "p"})
    assert r.added == 0


def test_added_brand_new_task_stamps_now():
    """Fresh tasks (no `added` kwarg passed) stamp the current time."""
    import time as _t
    before = _t.time()
    t = T.DownloadTask("u", "p")
    assert before <= t.added <= _t.time() + 1


def test_added_round_trips_through_persistence():
    """A saved timestamp survives to_dict/from_dict unchanged."""
    t = T.DownloadTask("u", "p")
    t.added = 1700000000.5
    r = T.DownloadTask.from_dict(json.loads(json.dumps(t.to_dict())))
    assert r.added == 1700000000.5


def test_control_flags():
    t = T.DownloadTask("u", "p")
    assert not t.pause_requested and not t.cancel_requested
    t.request_pause()
    assert t.pause_requested
    t.clear_pause()
    assert not t.pause_requested
    t.request_cancel()
    assert t.cancel_requested and t.pause_requested  # cancel also breaks loops


def test_speed_limit_setter():
    t = T.DownloadTask("u", "p")
    t.set_speed_limit(256 * 1024)
    assert t.speed_limit == 256 * 1024
    assert t._limiter.limit_bps == 256 * 1024


# ---- event timeline (drawer Logs tab) ----
def test_status_transitions_logged():
    t = T.DownloadTask("u", "p")
    assert t.events == []                     # initial status: no event
    t.status = T.DOWNLOADING
    t.status = T.DOWNLOADING                  # same value: no duplicate
    t.status = T.PAUSED
    assert [e["message"] for e in t.events] == ["Downloading", "Paused"]
    assert all(isinstance(e["time"], float) for e in t.events)


def test_events_capped():
    t = T.DownloadTask("u", "p")
    for i in range(80):
        t.log_event(f"e{i}")
    assert len(t.events) == T.DownloadTask.EVENTS_MAX
    assert t.events[-1]["message"] == "e79"


def test_events_and_sha256_roundtrip():
    import json
    t = T.DownloadTask("u", "p")
    t.status = T.DOWNLOADING
    t.status = T.COMPLETED
    t.sha256 = "ab" * 32
    r = T.DownloadTask.from_dict(json.loads(json.dumps(t.to_dict())))
    assert [e["message"] for e in r.events] == ["Downloading", "Completed"]
    assert r.sha256 == "ab" * 32


def test_inflight_restore_migrates_legacy_events_and_notes_the_resume():
    """Legacy [ts, message] entries must survive as dicts, or every reader that
    does ev["message"] would raise on a task saved before the format changed."""
    d = T.DownloadTask("u", "p").to_dict()
    d["status"] = T.DOWNLOADING
    d["events"] = [[1.0, "Downloading"]]
    r = T.DownloadTask.from_dict(d)
    assert r.status == T.PAUSED
    assert [e["message"] for e in r.events] == ["Downloading", "Restored for auto-resume"]
    assert all(isinstance(e, dict) for e in r.events)
    assert r.events[0]["time"] == 1.0 and r.events[0]["level"] == "INFO"


def test_events_missing_or_malformed_tolerated():
    d = T.DownloadTask("u", "p").to_dict()
    d["status"] = T.COMPLETED          # terminal: no forced-pause event on restore
    d.pop("events", None)
    assert T.DownloadTask.from_dict(d).events == []
    d["events"] = ["junk", [1.0], [2.0, "Paused"]]
    assert [e["message"] for e in T.DownloadTask.from_dict(d).events] == ["Paused"]


def test_reset_progress(tmp_path, monkeypatch):
    import utils, os
    t = T.DownloadTask("u", "p", total_size=100)
    t.segments = [T.Segment(0, 0, 99)]
    t.segments[0].downloaded = 50
    t.downloaded = 50
    t.error = "x"; t.hash_status = "fail"; t.sha256 = "d" * 64
    t.request_pause(); t.request_cancel()
    tmp = tmp_path / f"{t.id}.hfdownload"
    tmp.write_bytes(b"x" * 10)
    monkeypatch.setattr(utils, "temp_download_path", lambda tid: str(tmp))
    t.reset_progress()
    assert t.segments == [] and t.downloaded == 0
    assert t.error == "" and t.hash_status == "" and t.sha256 == ""
    assert not t.pause_requested and not t.cancel_requested
    assert not tmp.exists()
    assert t.events[-1]["message"] == "Restarted"


# ---- a failure has to reach the drawer's Logs tab ----
def _t(**kw):
    return T.DownloadTask("https://example.test/v.mp4", "C:/dl/v.mp4",
                          filename="v.mp4", **kw)


def test_setting_an_error_records_it_on_the_timeline():
    """Only torrent.py ever called log_event, so an HTTP/HLS/yt-dlp failure
    left the Logs tab empty — the one place the user looks to find out what
    went wrong."""
    t = _t()
    t.error = "yt-dlp: Requested format is not available"
    assert len(t.events) == 1
    ev = t.events[-1]
    assert ev["level"] == "ERROR"
    assert ev["message"] == "yt-dlp: Requested format is not available"
    assert t.error == "yt-dlp: Requested format is not available"


def test_constructing_a_task_is_not_an_event():
    assert _t(error="carried over from disk").events == []


def test_restoring_does_not_relog_a_saved_failure():
    """from_dict runs on every launch. Going through the property there would
    add a fresh copy of the same error each time the app started."""
    t = _t()
    t.error = "Connection lost — Resume to retry"
    d = t.to_dict()
    back = T.DownloadTask.from_dict(d)
    assert len(back.events) == len(d["events"]) == 1
    assert back.error == "Connection lost — Resume to retry"


def test_clearing_the_error_is_not_an_event():
    """Every engine assigns "" at the start of a run."""
    t = _t()
    t.error = ""
    assert t.events == []
    t.error = "boom"
    t.error = ""
    assert len(t.events) == 1 and t.error == ""


def test_the_same_failure_repeated_is_recorded_once():
    """A segment loop sets the same message once per segment; the timeline
    should read as one failure, not eight."""
    t = _t()
    for _ in range(8):
        t.error = "Connection lost — Resume to retry"
    assert len(t.events) == 1


def test_a_different_failure_after_one_is_recorded_too():
    t = _t()
    t.error = "HTTP 403 — the server refused the download"
    t.error = "Connection lost — Resume to retry"
    assert [e["message"] for e in t.events] == [
        "HTTP 403 — the server refused the download",
        "Connection lost — Resume to retry"]


def test_the_timeline_stays_capped():
    t = _t()
    for i in range(T.DownloadTask.EVENTS_MAX + 50):
        t.error = "failure number %d" % i
    assert len(t.events) == T.DownloadTask.EVENTS_MAX
