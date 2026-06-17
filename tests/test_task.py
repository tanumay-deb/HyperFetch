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
    for live in (T.DOWNLOADING, T.QUEUED):
        d = T.DownloadTask("u", "p").to_dict()
        d["status"] = live
        assert T.DownloadTask.from_dict(d).status == T.PAUSED


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
