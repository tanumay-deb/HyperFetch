"""A running torrent's per-file numbers are read once a minute.

aria2.getFiles ran every 2 s for EVERY torrent - a third of all torrent RPC,
and aria2 serves RPC from the thread that downloads. Then it ran only while
the Files tab was open, and a torrent that finished or paused with the tab
shut kept the numbers of its start (reported 2026-10-29). Now: once a minute,
open tab or not (asked: the tab counts down to it), and once more at a pause.
"""
import threading
import time

import pytest

import aria2d
import task as T
import torrent
import utils


class _Daemon:
    def __init__(self, total=1000):
        self.total = total
        self.counts = {}

    def ensure(self):
        return True

    def call(self, method, *a, **kw):
        self.counts[method] = self.counts.get(method, 0) + 1
        if method == "aria2.pause":
            self.at_pause = dict(self.counts)      # what the run asked for while it ran
        if method == "aria2.tellStatus":
            return {"status": "active", "completedLength": "1",
                    "totalLength": str(self.total), "connections": "3",
                    "files": [{"path": "x"}]}
        if method == "aria2.getFiles":
            return [{"path": "a.mkv", "length": "1000", "completedLength": "10",
                     "selected": "true"}]
        if method in ("aria2.addUri", "aria2.addTorrent"):
            return "gid1"
        return {}


def _run(tmp_path, monkeypatch, seconds=1.2):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.01)
    d = _Daemon()
    monkeypatch.setattr(aria2d, "DAEMON", d)
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "out"))
    td = torrent.TorrentDownloader(t)
    th = threading.Thread(target=td._run_rpc, daemon=True)
    th.start()
    time.sleep(seconds)
    t.request_pause()
    th.join(timeout=5)
    return d, t


def test_they_are_read_as_soon_as_the_list_is_known_then_once_a_minute(tmp_path, monkeypatch):
    d, t = _run(tmp_path, monkeypatch)
    assert d.at_pause.get("aria2.getFiles", 0) == 1, "read more than once in a second"
    assert t.file_progress and t.files_at, "no file list at all, so the tab opens blank"


def test_the_minute_comes_round(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "FILES_POLL", 0.2)          # a minute, faster
    d, t = _run(tmp_path, monkeypatch)
    assert d.at_pause.get("aria2.getFiles", 0) >= 3


def test_a_pause_reads_the_files_once_more(tmp_path, monkeypatch):
    """Read once a minute, a paused torrent's files would read as they were up
    to a minute before it stopped; pausing reads them once more."""
    real = _Daemon.call

    def call(self, method, *a, **kw):          # 10 at the first fetch, 900 by the pause
        out = real(self, method, *a, **kw)
        if method == "aria2.getFiles" and self.counts[method] > 1:
            out[0]["completedLength"] = "900"
        return out

    monkeypatch.setattr(_Daemon, "call", call)
    d, t = _run(tmp_path, monkeypatch)
    assert d.counts.get("aria2.getFiles", 0) == 2
    assert [f["completed"] for f in t.file_progress] == [900]


def test_status_polling_is_unaffected(tmp_path, monkeypatch):
    """Gating the file list must not slow progress or speed."""
    d, _ = _run(tmp_path, monkeypatch)
    assert d.counts.get("aria2.tellStatus", 0) > 2
