"""Unticking files in the Files tab must stick.

It did not: change_torrent_files returned early unless the torrent happened to
be running, and stored nothing — so closing the drawer lost the choice, and the
next start selected everything again.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import aria2d
import task as T
import torrent
import utils
from queue_manager import QueueManager
from test_aria2d import _FakeDaemon, _drive_with


def _q_with(task):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    q.shutdown()
    q.tasks.append(task)
    return q


def _task():
    return T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Show", "C:/dl/x",
                          filename="Show")


def test_the_choice_is_recorded_even_when_nothing_is_running():
    """The old code returned early with no gid and stored nothing at all."""
    t = _task()
    q = _q_with(t)
    q.change_torrent_files(t.id, "1,3,5")
    assert t.selected_files == "1,3,5"


def test_a_running_torrent_is_told_immediately(monkeypatch):
    t = _task()
    t.gid = "livegid"
    q = _q_with(t)
    sent = []

    class _D:
        def call(self, method, *params, **kw):
            sent.append((method, params))
            return {}

    monkeypatch.setattr(aria2d, "DAEMON", _D())
    q.change_torrent_files(t.id, "2,4")
    assert t.selected_files == "2,4"
    assert sent and sent[0][0] == "aria2.changeOption"
    assert sent[0][1][1] == {"select-file": "2,4"}


def test_the_choice_is_re_applied_on_the_next_start(tmp_path, monkeypatch):
    """A paused torrent used to come back with everything selected: aria2 only
    ever knew about the selection through a live changeOption."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie",
                       str(tmp_path / "download.bin"))
    t.selected_files = "1,4,7"
    _drive_with(tmp_path, monkeypatch, daemon, task=t)
    adds = [p for m, p in daemon.calls
            if m in ("aria2.addUri", "aria2.addTorrent")]
    assert adds and adds[0][-1].get("select-file") == "1,4,7"


def test_no_selection_means_every_file(tmp_path, monkeypatch):
    """aria2 reads an absent select-file as 'all', which is what we want — but
    it must not be sent as an empty string."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    _drive_with(tmp_path, monkeypatch, daemon)
    adds = [p for m, p in daemon.calls
            if m in ("aria2.addUri", "aria2.addTorrent")]
    assert "select-file" not in adds[0][-1]


def test_the_choice_survives_save_and_restore():
    t = _task()
    t.selected_files = "1,2,9"
    restored = T.DownloadTask.from_dict(t.to_dict())
    assert restored.selected_files == "1,2,9"
