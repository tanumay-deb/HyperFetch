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


# ---- the drawer shows the choice, not aria2's last report ------------------------
# Reported 2026-10-29 on 2.8.4: untick files, open the drawer again - every
# box ticked. The choice was kept (selected_files); the boxes were drawn from
# file_progress, aria2's last report, read once a minute while running and
# never while paused - or, with no report yet, every file ticked.
def _drawer_for(t):
    from PySide6.QtWidgets import QApplication, QWidget
    from gui2.details_drawer import DetailsDrawer
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.queue = _q_with(t)
    d = DetailsDrawer(host)
    d._host_ref = host                     # or the host is collected, and the drawer with it
    d.open_for(t)
    app.processEvents()
    return d


def _boxes(d):
    return [cb.isChecked() for _i, cb, _s in sorted(d._file_row_widgets, key=lambda w: w[0])]


def test_an_unticked_file_is_unticked_when_the_drawer_opens_again(tmp_path):
    t = _task()
    t.status = T.PAUSED
    t.file_progress = [{"index": i, "path": str(tmp_path / ("e%d.mkv" % i)), "length": 10,
                        "completed": 0, "selected": True} for i in (1, 2, 3)]
    t.save_path = str(tmp_path / "magnet_.bin")
    d = _drawer_for(t)
    d._file_row_widgets[1][1].setChecked(False)
    assert t.selected_files == "1,3"
    d.close_drawer()
    d.open_for(t)
    assert _boxes(d) == [True, False, True]


def test_the_choice_shows_before_aria2_has_reported_any_file(tmp_path):
    t = _task()
    t.status = T.PAUSED
    t.files = [["Show/e1.mkv", 10], ["Show/e2.mkv", 10], ["Show/e3.mkv", 10]]
    t.selected_files = "2"
    d = _drawer_for(t)
    assert _boxes(d) == [False, True, False]
